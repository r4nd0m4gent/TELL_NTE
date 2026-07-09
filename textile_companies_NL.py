import os
import json
import threading
import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx
import plotly.express as px
import pandas as pd
import pgeocode
import classification

# ── Colors ────────────────────────────────────────────────────────────────────
nte_violet   = '#513773'
nte_darkblue = '#54639E'

# ── Map basemap ───────────────────────────────────────────────────────────────
# Plotly.js 3.x renders maps with MapLibre. The built-in "open-street-map"
# preset resolves to HTTP tile URLs, which the browser blocks as mixed content
# once the app is served over HTTPS — leaving only the empty gridded placeholder.
# Defining the style explicitly with HTTPS tiles avoids the mixed-content block.
OSM_HTTPS_STYLE = {
    'version': 8,
    'sources': {
        'osm': {
            'type': 'raster',
            'tiles': ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            'tileSize': 256,
            'attribution': '© OpenStreetMap contributors',
        }
    },
    'layers': [{'id': 'osm', 'type': 'raster', 'source': 'osm'}],
}

# ── Data path — works on both the local machine and the server ────────────────
# Resolution order (first existing path wins):
#   1. COMPANIES_XLSX environment variable (explicit override)
#   2. ./data/companies.xlsx next to this script (server / repo layout)
#   3. local dev machine location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_PATHS = [
    os.environ.get('COMPANIES_XLSX'),
    os.path.join(BASE_DIR, 'data', 'companies.xlsx'),          # server: /home/tell/app/data/companies.xlsx
    r'C:\Users\fsollit\Desktop\Data\TELL\companies.xlsx',      # local dev machine
]
EXCEL_PATH = next((p for p in _CANDIDATE_PATHS if p and os.path.exists(p)),
                  _CANDIDATE_PATHS[1])


# ── Data query from DB ────────────────────────────────────────────────────────
# Columns are aliased to match the names the dashboard expects (same as the
# Excel layout): `trade_name`, `value`, `Predicted_Category`, `Predicted_Tier`.
# Coordinates come from the geographies table (one point per city).
ENV_PATH = os.path.join(BASE_DIR, 'db', 'mysql', '.env')

query_org = """
    SELECT
        o.id,
        o.trade_name,
        o.city,
        g.region,
        g.latitude,
        g.longitude,
        o.status,
        o.website,
        o.employees,
        o.surface,
        o.year_start,
        t.tags,
        t.category    AS Predicted_Category,
        t.tier        AS Predicted_Tier
    FROM organizations AS o
    JOIN (
        SELECT city, MAX(region) AS region,
               AVG(latitude) AS latitude, AVG(longitude) AS longitude
        FROM geographies GROUP BY city
    ) AS g ON g.city = o.city
    JOIN tags AS t ON t.id = o.id
    WHERE o.status = 'Active'
"""


def _get_engine():
    """Build a SQLAlchemy engine from db/mysql/.env, or None if unavailable."""
    try:
        from sqlalchemy import create_engine
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
        user = os.getenv('DB_USER')
        password = os.getenv('DB_PASSWORD')
        host = os.getenv('DB_HOST')
        name = os.getenv('DB_NAME')
        port = os.getenv('DB_PORT', '25060')
        ca_cert = os.getenv('DB_CA_CERT')
        if not all([user, password, host, name]):
            return None
        connect_args = {'ssl': {'ca': ca_cert}} if ca_cert else {}
        return create_engine(
            f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}",
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    except Exception:
        return None


def load_companies_db():
    """Primary source: load active companies from the MySQL database."""
    engine = _get_engine()
    if engine is None:
        return None
    try:
        df = pd.read_sql(query_org, engine)
        if df.empty:
            return None
        df['employees'] = pd.to_numeric(df['employees'], errors='coerce').fillna(0).astype(int)
        return df
    except Exception:
        return None


def load_companies_excel():
    """Fallback source: load companies from the local Excel file."""
    df = pd.read_excel(EXCEL_PATH)
    df = df.rename(columns={
        'visiting address_city':     'city',
        'visiting address_postcode': 'postcode',
        'number_employees':          'employees',
    })
    df['employees'] = pd.to_numeric(df['employees'], errors='coerce').fillna(0).astype(int)
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        nomi      = pgeocode.Nominatim('nl')
        # Dutch postcodes look like "1011 AB"; pgeocode's NL dataset is keyed by
        # the 4-digit numeric part only, so extract that before querying.
        postcodes = df['postcode'].astype(str).str.extract(r'(\d{4})')[0]
        geo       = nomi.query_postal_code(postcodes.tolist())
        df['latitude']  = geo['latitude'].values
        df['longitude'] = geo['longitude'].values
    return df


def load_companies():
    """Load companies from the database, falling back to Excel."""
    df = load_companies_db()
    if df is not None:
        return df
    return load_companies_excel()

data = load_companies()

# ── Consortium affiliations ────────────────────────────────────────────────
# The `affiliations` table maps org_id → one flag column per consortium (1 =
# member). We build {consortium_column: {org_id, ...}} so companies can be
# filtered by consortium membership. Failures fall back to an empty mapping so
# the feature degrades gracefully (empty dropdown, no filtering).
_CONSORTIUM_LABELS = {
    'newtexeco2026': 'NewTexEco 2026',
    'newtexeco2023': 'NewTexEco 2023',
}


def _consortium_label(col):
    return _CONSORTIUM_LABELS.get(col, col)


def load_affiliations():
    """Return {consortium_column: set(org_ids)} from the affiliations table."""
    engine = _get_engine()
    if engine is None:
        return {}
    try:
        df = pd.read_sql("SELECT * FROM affiliations", engine)
    except Exception:
        return {}
    mapping = {}
    for col in df.columns:
        if col == 'org_id':
            continue
        mapping[col] = set(pd.to_numeric(df.loc[df[col] == 1, 'org_id'],
                                         errors='coerce').dropna().astype(int).tolist())
    return mapping


affiliations = load_affiliations()
_consortium_options = [{'label': _consortium_label(c), 'value': c}
                       for c in affiliations.keys()]

# ── Usage tracking ────────────────────────────────────────────────────────────
# Session starts, click events, and filter changes are written to the
# `tracking_events` table. Writes run on a background thread so they never block
# the UI, and any failure is swallowed so tracking can't break the dashboard.
_TRACK_DDL = (
    "CREATE TABLE IF NOT EXISTS tracking_events ("
    " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    " session_id VARCHAR(64),"
    " event_type VARCHAR(32) NOT NULL,"
    " details JSON,"
    " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
    ")"
)

_track_engine = None
_track_schema_ready = False
_track_lock = threading.Lock()


def _get_track_engine():
    """Reuse a single engine for tracking writes."""
    global _track_engine
    if _track_engine is None:
        _track_engine = _get_engine()
    return _track_engine


def _write_event(session_id, event_type, details):
    """Insert one tracking row. Runs on a worker thread; errors are ignored."""
    global _track_schema_ready
    try:
        from sqlalchemy import text
        engine = _get_track_engine()
        if engine is None:
            return
        with engine.begin() as conn:
            if not _track_schema_ready:
                with _track_lock:
                    if not _track_schema_ready:
                        conn.execute(text(_TRACK_DDL))
                        _track_schema_ready = True
            conn.execute(
                text("INSERT INTO tracking_events (session_id, event_type, details)"
                     " VALUES (:sid, :etype, :details)"),
                {'sid': session_id, 'etype': event_type,
                 'details': json.dumps(details or {}, default=str)},
            )
    except Exception:
        pass


def log_event(session_id, event_type, details=None):
    """Fire-and-forget tracking write (non-blocking)."""
    threading.Thread(target=_write_event,
                     args=(session_id, event_type, details or {}),
                     daemon=True).start()


def _request_meta():
    """Collect request metadata (IP, user agent, language, referrer)."""
    try:
        from flask import request
        xff = request.headers.get('X-Forwarded-For', '')
        ip = xff.split(',')[0].strip() if xff else (
            request.headers.get('X-Real-IP') or request.remote_addr)
        return {
            'ip': ip,
            'user_agent': request.headers.get('User-Agent'),
            'language': request.headers.get('Accept-Language'),
            'referrer': request.headers.get('Referer'),
        }
    except Exception:
        return {}

# ── App ───────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=['https://fonts.googleapis.com/css2?family=Inter&display=swap'],
    suppress_callback_exceptions=True,
    url_base_pathname='/dashboard/',
    meta_tags=[{'name': 'viewport',
                'content': 'width=device-width, initial-scale=1'}],
)
server = app.server  # WSGI entry point for Gunicorn

_card = {
    'flex': '1', 'backgroundColor': 'white', 'borderRadius': '8px',
    'padding': '16px 20px', 'boxShadow': '0 1px 4px rgba(0,0,0,0.1)',
    'textAlign': 'center',
}


def _options(values):
    return sorted([{'label': str(v), 'value': str(v)} for v in pd.unique(values.dropna())],
                  key=lambda o: o['label'].lower())


def _filter_col(label, dd_id, placeholder, values):
    return html.Div([
        html.Label(label),
        dcc.Dropdown(id=dd_id, options=_options(values), value=None,
                     placeholder=placeholder, multi=True),
    ], className='tell-filter-col',
       style={'flex': '1', 'minWidth': '0', 'padding': '10px'})


app.layout = html.Div([

    # ── Filters: region / city / company (above the map) ──────────────────────
    html.Div([
        _filter_col("Filter by Region:",  'region-dropdown',  "Select a region...",  data['region']),
        _filter_col("Filter by City:",    'city-dropdown',    "Select a city...",    data['city']),
        _filter_col("Filter by Company:", 'company-dropdown', "Search a company...", data['trade_name']),
        html.Div([
            html.Label("Filter by Consortium:"),
            dcc.Dropdown(id='consortium-dropdown', options=_consortium_options, value=None,
                         placeholder="Select a consortium...", multi=True),
        ], className='tell-filter-col', style={'flex': '1', 'minWidth': '0', 'padding': '10px'}),
    ], className='tell-filters', style={'backgroundColor': '#ecf0f1', 'borderRadius': '8px', 'marginBottom': '20px'}),

    # ── KPI cards ─────────────────────────────────────────────────────────────
    html.Div([
        html.Div([html.P('Total Records',      style={'margin': '0 0 4px', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}), html.H2(id='kpi-total',  style={'margin': 0})], style=_card),
        html.Div([html.P('Active Businesses',  style={'margin': '0 0 4px', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}), html.H2(id='kpi-active', style={'margin': 0})], style=_card),
        html.Div([html.P('Registered Websites',style={'margin': '0 0 4px', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}), html.H2(id='kpi-web',    style={'margin': 0})], style=_card),
    ], className='tell-kpis', style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),

    html.Div(id='city-filter-label', style={'minHeight': '22px', 'marginBottom': '4px', 'fontSize': '13px', 'color': '#832394', 'fontWeight': '600'}),

    # ── Map + Region chart ────────────────────────────────────────────────────
    html.Div([
        html.Div([dcc.Graph(id='map-graph', config={'scrollZoom': True}, style={'height': '500px'})],
                 style={'flex': '1.5', 'minWidth': 0}),
        html.Div([dcc.Graph(id='region-chart', style={'height': '500px'})],
                 style={'flex': '1.5', 'minWidth': 0}),
    ], className='tell-map-row', style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),
    # ── Filters: category / tier / keywords (between map and table) ─────────
    html.Div([
        _filter_col("Filter by Category:",       'category-dropdown', "Select a category...", data['Predicted_Category']),
        _filter_col("Filter by Tier:",           'tier-dropdown',     "Select a tier...",     data['Predicted_Tier']),
        _filter_col("Filter by Keywords/Tags:",  'keywords-dropdown', "Select keywords...",   data['tags']),
    ], className='tell-filters', style={'backgroundColor': '#ecf0f1', 'borderRadius': '8px', 'marginBottom': '20px'}),
    # ── Data table ────────────────────────────────────────────────────────────
    dash_table.DataTable(
        id='company-table',
        columns=[
            {'name': 'Company',            'id': 'trade_name'},
            {'name': 'Predicted Category', 'id': 'Predicted_Category'},
            {'name': 'Predicted Tier',     'id': 'Predicted_Tier'},
            {'name': 'Tags',               'id': 'tags'},
            {'name': '# Employees',        'id': 'employees'},
        ],
        page_size=10, sort_action='native', filter_action='none',
        style_table={'overflowX': 'auto', 'overflowY': 'auto', 'maxHeight': '380px', 'marginTop': '20px'},
        style_header={'backgroundColor': nte_darkblue, 'color': 'white', 'fontWeight': 'bold'},
        style_cell={'padding': '8px', 'textAlign': 'left', 'fontSize': '13px',
                    'overflow': 'hidden', 'textOverflow': 'ellipsis', 'maxWidth': '250px'},
        style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f9f9f9'}],
        style_data={'cursor': 'pointer'},
        active_cell=None, tooltip_data=[], tooltip_duration=None,
    ),

    # ── Pie charts ────────────────────────────────────────────────────────────
    html.Div([
        dcc.Graph(id='pie-category', style={'flex': '1', 'minWidth': 0}),
        dcc.Graph(id='pie-tier',     style={'flex': '1', 'minWidth': 0}),
    ], className='tell-pies', style={'display': 'flex', 'gap': '16px', 'marginTop': '20px'}),

    # ── Buttons ───────────────────────────────────────────────────────────────
    html.Div([
        html.A('✏️ Contribute (add/edit data)', href='/contribute/',
               style={'display': 'inline-block', 'padding': '10px 24px',
                      'backgroundColor': nte_violet, 'color': 'white',
                      'borderRadius': '6px', 'textDecoration': 'none',
                      'fontSize': '14px', 'fontWeight': '600'}),
        classification.get_button(),
    ], className='tell-buttons', style={'display': 'flex', 'gap': '12px', 'justifyContent': 'center',
              'marginTop': '32px', 'paddingBottom': '32px'}),

    classification.get_modal(),
    *classification.get_stores(),
    dcc.Store(id='selected-city', data=None),

    # ── Usage tracking plumbing (hidden) ──────────────────────────────────────
    dcc.Store(id='session-id', storage_type='session'),
    dcc.Interval(id='trk-init', interval=400, max_intervals=1),
    dcc.Store(id='trk-session-sink'),
    dcc.Store(id='trk-filter-sink'),
    dcc.Store(id='trk-click-sink'),

], className='tell-app', style={'fontFamily': 'Inter, sans-serif', 'padding': '20px', 'maxWidth': '1400px', 'margin': 'auto'})


# ── Filter helper ─────────────────────────────────────────────────────────────
def filter_data(regions, companies, keywords=None, cities=None, categories=None,
                tiers=None, consortiums=None):
    filtered = data.copy()
    if regions:
        filtered = filtered[filtered['region'].isin(regions)]
    if companies:
        filtered = filtered[filtered['trade_name'].isin(companies)]
    if cities:
        filtered = filtered[filtered['city'].isin(cities)]
    if categories:
        filtered = filtered[filtered['Predicted_Category'].isin(categories)]
    if tiers:
        filtered = filtered[filtered['Predicted_Tier'].isin(tiers)]
    if keywords:
        pattern  = '|'.join([str(k) for k in keywords])
        filtered = filtered[filtered['tags'].astype(str).str.contains(pattern, case=False, na=False)]
    if consortiums and 'id' in filtered.columns:
        member_ids = set()
        for c in consortiums:
            member_ids |= affiliations.get(c, set())
        filtered = filtered[filtered['id'].isin(member_ids)]
    return filtered


# ── Cascading filter options ──────────────────────────────────────────────────
# Each dropdown's options are derived from the subset defined by the OTHER two
# selections, so after picking one filter the others only show what's available.
# A dropdown is not constrained by its own value, so the user can still broaden it.
@app.callback(
    Output('region-dropdown',   'options'),
    Output('city-dropdown',     'options'),
    Output('company-dropdown',  'options'),
    Output('category-dropdown', 'options'),
    Output('tier-dropdown',     'options'),
    Output('keywords-dropdown', 'options'),
    Input('region-dropdown',    'value'),
    Input('city-dropdown',      'value'),
    Input('company-dropdown',   'value'),
    Input('category-dropdown',  'value'),
    Input('tier-dropdown',      'value'),
    Input('keywords-dropdown',  'value'),
    Input('consortium-dropdown','value'),
)
def update_filter_options(regions, cities, companies, categories, tiers, keywords, consortiums):
    region_opts   = _options(filter_data(None,    companies, keywords, cities, categories, tiers, consortiums)['region'])
    city_opts     = _options(filter_data(regions, companies, keywords, None,   categories, tiers, consortiums)['city'])
    company_opts  = _options(filter_data(regions, None,      keywords, cities, categories, tiers, consortiums)['trade_name'])
    category_opts = _options(filter_data(regions, companies, keywords, cities, None,       tiers, consortiums)['Predicted_Category'])
    tier_opts     = _options(filter_data(regions, companies, keywords, cities, categories, None,  consortiums)['Predicted_Tier'])
    keyword_opts  = _options(filter_data(regions, companies, None,     cities, categories, tiers, consortiums)['tags'])
    return region_opts, city_opts, company_opts, category_opts, tier_opts, keyword_opts


# ── City click callback ───────────────────────────────────────────────────────
@app.callback(
    Output('selected-city', 'data'),
    Input('map-graph', 'clickData'),
    Input('company-table', 'active_cell'),
    State('selected-city', 'data'),
    State('company-table', 'data'),
    prevent_initial_call=True,
)
def update_selected_city(click_data, active_cell, current_city, table_data):
    triggered = ctx.triggered_id
    if triggered == 'map-graph' and click_data:
        point   = click_data['points'][0]
        clicked = point.get('hovertext')
        if clicked is None:
            custom = point.get('customdata')
            if isinstance(custom, list) and custom:
                clicked = custom[-1]
        if clicked is None:
            return current_city
        return None if clicked == current_city else clicked
    if triggered == 'company-table' and active_cell and table_data:
        row          = table_data[active_cell['row']]
        company_name = row.get('trade_name')
        if company_name:
            match = data[data['trade_name'] == company_name]
            if not match.empty:
                city = match.iloc[0]['city']
                return None if city == current_city else city
    return current_city


# ── Main dashboard callback ───────────────────────────────────────────────────
@app.callback(
    Output('kpi-total',         'children'),
    Output('kpi-active',        'children'),
    Output('kpi-web',           'children'),
    Output('map-graph',         'figure'),
    Output('region-chart',      'figure'),
    Output('pie-category',      'figure'),
    Output('pie-tier',          'figure'),
    Output('company-table',     'data'),
    Output('company-table',     'tooltip_data'),
    Output('city-filter-label', 'children'),
    Input('region-dropdown',    'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
    Input('city-dropdown',      'value'),
    Input('category-dropdown',  'value'),
    Input('tier-dropdown',      'value'),
    Input('consortium-dropdown','value'),
    Input('selected-city',      'data'),
)
def update_dashboard(selected_regions, selected_companies, selected_keywords,
                     selected_cities, selected_categories, selected_tiers,
                     selected_consortiums, selected_city):
    filtered = filter_data(selected_regions, selected_companies, selected_keywords,
                           selected_cities, selected_categories, selected_tiers,
                           selected_consortiums)
    if selected_city:
        filtered = filtered[filtered['city'] == selected_city]

    kpi_total  = f"{len(filtered):,}"
    kpi_active = f"{(filtered['status'].str.lower() == 'active').sum():,}"
    kpi_web    = f"{filtered['website'].notna().sum():,}"

    _valid   = filtered.dropna(subset=['latitude', 'longitude'])
    city_geo = (
        _valid.groupby('city')
        .agg(count=('latitude', 'count'), lat=('latitude', 'median'), lon=('longitude', 'median'))
        .reset_index()
    )
    if not city_geo.empty:
        _sqrt_max            = city_geo['count'].apply(lambda x: x ** 0.5).max()
        _min_d               = max(_sqrt_max * 0.04, 1)
        city_geo['disp']     = city_geo['count'].apply(lambda x: max(x ** 0.5, _min_d))
    else:
        city_geo['disp'] = city_geo['count']

    if selected_city:
        city_geo['_sel'] = city_geo['city'].apply(lambda c: 'selected' if c == selected_city else 'default')
        cmap = {'selected': 'rgba(220,80,0,0.85)', 'default': 'rgba(138,43,226,0.25)'}
    else:
        city_geo['_sel'] = 'all'
        cmap = {'all': 'rgba(138,43,226,0.45)'}

    map_fig = px.scatter_map(
        city_geo, lat='lat', lon='lon', size='disp',
        color='_sel', color_discrete_map=cmap,
        hover_name='city',
        hover_data={'count': True, 'disp': False, 'lat': False, 'lon': False, '_sel': False},
        custom_data=['city'],
        zoom=6,
        center={'lat': 52.3, 'lon': 5.3},
        size_max=40, title='Number of Companies per City', height=500,
    )
    map_fig.update_layout(
        map={'style': OSM_HTTPS_STYLE},
        margin={'r': 0, 't': 40, 'l': 0, 'b': 0}, showlegend=False,
    )

    region_counts = filtered.groupby('region', as_index=False).size().rename(columns={'size': 'count'})
    region_fig    = px.bar(
        region_counts.sort_values('count'), x='count', y='region',
        orientation='h', title='Companies per Region', height=500,
        color_discrete_sequence=[nte_darkblue],
    )
    region_fig.update_layout(margin={'l': 120, 'r': 20, 't': 40, 'b': 20})

    _cat  = filtered['Predicted_Category'].fillna('Unknown').value_counts().reset_index()
    _cat.columns = ['Predicted_Category', 'count']
    pie_cat = px.pie(_cat, names='Predicted_Category', values='count', title='Product Category',
                     color_discrete_sequence=px.colors.sequential.Purples_r)
    pie_cat.update_traces(textposition='inside', textinfo='percent+label')
    pie_cat.update_layout(showlegend=False, margin={'t': 50, 'b': 10, 'l': 10, 'r': 10})

    _tier = filtered['Predicted_Tier'].fillna('Unknown').value_counts().reset_index()
    _tier.columns = ['Predicted_Tier', 'count']
    pie_tier = px.pie(_tier, names='Predicted_Tier', values='count', title='Lifecycle Stage',
                      color_discrete_sequence=px.colors.sequential.Blues_r)
    pie_tier.update_traces(textposition='inside', textinfo='percent+label')
    pie_tier.update_layout(showlegend=False, margin={'t': 50, 'b': 10, 'l': 10, 'r': 10})

    table_df      = filtered[['trade_name', 'Predicted_Category', 'Predicted_Tier', 'tags', 'employees']].copy()
    table_df['employees'] = table_df['employees'].astype(int)
    records       = table_df.to_dict('records')
    tooltip_data  = [{'tags': {'value': str(r.get('tags', '') or ''), 'type': 'markdown'}} for r in records]

    city_label = f"City filter: {selected_city} — click the same bubble again to clear" if selected_city else ""
    return kpi_total, kpi_active, kpi_web, map_fig, region_fig, pie_cat, pie_tier, records, tooltip_data, city_label

# ── Usage tracking callbacks ──────────────────────────────────────────────────
# Generate a stable per-browser-session id (kept in sessionStorage) on load.
app.clientside_callback(
    """
    function(n) {
        let sid = window.sessionStorage.getItem('tell_sid');
        if (!sid) {
            sid = (window.crypto && crypto.randomUUID)
                ? crypto.randomUUID()
                : 'sid-' + Date.now() + '-' + Math.random().toString(16).slice(2);
            window.sessionStorage.setItem('tell_sid', sid);
        }
        window.__sid = sid;
        return sid;
    }
    """,
    Output('session-id', 'data'),
    Input('trk-init', 'n_intervals'),
)


@app.callback(
    Output('trk-session-sink', 'data'),
    Input('session-id', 'data'),
    prevent_initial_call=True,
)
def track_session(session_id):
    """Record a session start once the browser session id is set."""
    if session_id:
        log_event(session_id, 'session_start', _request_meta())
    return dash.no_update


@app.callback(
    Output('trk-filter-sink', 'data'),
    Input('region-dropdown',   'value'),
    Input('city-dropdown',     'value'),
    Input('company-dropdown',  'value'),
    Input('category-dropdown', 'value'),
    Input('tier-dropdown',     'value'),
    Input('keywords-dropdown', 'value'),
    Input('consortium-dropdown','value'),
    State('session-id', 'data'),
    prevent_initial_call=True,
)
def track_filters(regions, cities, companies, categories, tiers, keywords, consortiums, session_id):
    """Record every filter dropdown change."""
    log_event(session_id, 'filter_change', {
        'changed': ctx.triggered_id,
        'filters': {
            'region': regions, 'city': cities, 'company': companies,
            'category': categories, 'tier': tiers, 'keywords': keywords,
            'consortium': consortiums,
        },
    })
    return dash.no_update


@app.callback(
    Output('trk-click-sink', 'data'),
    Input('map-graph', 'clickData'),
    Input('company-table', 'active_cell'),
    State('company-table', 'data'),
    State('session-id', 'data'),
    prevent_initial_call=True,
)
def track_clicks(click_data, active_cell, table_data, session_id):
    """Record map bubble clicks and table cell clicks."""
    trig = ctx.triggered_id
    if trig == 'map-graph' and click_data:
        pt = click_data['points'][0]
        city = pt.get('hovertext')
        if city is None:
            cd = pt.get('customdata')
            if isinstance(cd, list) and cd:
                city = cd[-1]
        log_event(session_id, 'map_click', {'city': city})
    elif trig == 'company-table' and active_cell and table_data:
        row = table_data[active_cell['row']]
        log_event(session_id, 'table_click', {
            'column': active_cell.get('column_id'),
            'company': row.get('trade_name'),
        })
    return dash.no_update


classification.register_callbacks(app, filter_data, engine_fn=_get_engine)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8050, debug=False)
