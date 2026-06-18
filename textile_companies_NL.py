import os
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

# ── Data path — same on local machine and server ──────────────────────────────
# Place companies.xlsx in a folder called "data" next to this script.
# On local:  C:\Users\fsollit\Desktop\Data\TELL\data\companies.xlsx
# On server: /home/tell/app/data/companies.xlsx
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(BASE_DIR, 'data', 'companies.xlsx')

def load_companies():
    df = pd.read_excel(EXCEL_PATH)
    df = df.rename(columns={
        'visiting address_city':     'city',
        'visiting address_postcode': 'postcode',
        'number_employees':          'value',
    })
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0).astype(int)
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        nomi      = pgeocode.Nominatim('nl')
        # Dutch postcodes look like "1011 AB"; pgeocode's NL dataset is keyed by
        # the 4-digit numeric part only, so extract that before querying.
        postcodes = df['postcode'].astype(str).str.extract(r'(\d{4})')[0]
        geo       = nomi.query_postal_code(postcodes.tolist())
        df['latitude']  = geo['latitude'].values
        df['longitude'] = geo['longitude'].values
    return df

data = load_companies()

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

app.layout = html.Div([

    # ── Filters ───────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.Label("Filter by Company:"),
            dcc.Dropdown(
                id='company-dropdown',
                options=sorted([{'label': str(c), 'value': str(c)}
                                for c in data['trade name'].dropna().unique()],
                               key=lambda x: x['label']),
                value=None, placeholder="Search a company...", multi=True,
            ),
        ], className='tell-filter-col', style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
        html.Div([
            html.Label("Filter by Region:"),
            dcc.Dropdown(
                id='region-dropdown',
                options=sorted([{'label': str(r), 'value': str(r)}
                                for r in data['region'].dropna().unique()],
                               key=lambda x: x['label']),
                value=None, placeholder="Select a region...", multi=True,
            ),
        ], className='tell-filter-col', style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
        html.Div([
            html.Label("Filter by Keywords/Tags:"),
            dcc.Dropdown(
                id='keywords-dropdown',
                options=sorted([{'label': str(k), 'value': str(k)}
                                for k in data['tags'].dropna().unique()],
                               key=lambda x: x['label']),
                value=None, placeholder="Select keywords...", multi=True,
            ),
        ], className='tell-filter-col', style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
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

    # ── Data table ────────────────────────────────────────────────────────────
    dash_table.DataTable(
        id='company-table',
        columns=[
            {'name': 'Company',            'id': 'trade name'},
            {'name': 'Predicted Category', 'id': 'Predicted_Category'},
            {'name': 'Predicted Tier',     'id': 'Predicted_Tier'},
            {'name': 'Tags',               'id': 'tags'},
            {'name': '# Employees',        'id': 'value'},
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
        html.A('✏️ Contribute to the Textile Ecosystem Mapping', href='/contribute/',
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

], className='tell-app', style={'fontFamily': 'Inter, sans-serif', 'padding': '20px', 'maxWidth': '1400px', 'margin': 'auto'})


# ── Filter helper ─────────────────────────────────────────────────────────────
def filter_data(regions, companies, keywords=None):
    filtered = data.copy()
    if regions:
        filtered = filtered[filtered['region'].isin(regions)]
    if companies:
        filtered = filtered[filtered['trade name'].isin(companies)]
    if keywords:
        pattern  = '|'.join([str(k) for k in keywords])
        filtered = filtered[filtered['tags'].astype(str).str.contains(pattern, case=False, na=False)]
    return filtered


def _options(values):
    return sorted([{'label': str(v), 'value': str(v)} for v in pd.unique(values.dropna())],
                  key=lambda o: o['label'].lower())


# ── Cascading filter options ──────────────────────────────────────────────────
# Each dropdown's options are derived from the subset defined by the OTHER two
# selections, so after picking one filter the others only show what's available.
# A dropdown is not constrained by its own value, so the user can still broaden it.
@app.callback(
    Output('company-dropdown',  'options'),
    Output('region-dropdown',   'options'),
    Output('keywords-dropdown', 'options'),
    Input('company-dropdown',   'value'),
    Input('region-dropdown',    'value'),
    Input('keywords-dropdown',  'value'),
)
def update_filter_options(sel_companies, sel_regions, sel_keywords):
    company_opts = _options(filter_data(sel_regions, None, sel_keywords)['trade name'])
    region_opts  = _options(filter_data(None, sel_companies, sel_keywords)['region'])
    keyword_opts = _options(filter_data(sel_regions, sel_companies, None)['tags'])
    return company_opts, region_opts, keyword_opts


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
        company_name = row.get('trade name')
        if company_name:
            match = data[data['trade name'] == company_name]
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
    Input('selected-city',      'data'),
)
def update_dashboard(selected_regions, selected_companies, selected_keywords, selected_city):
    filtered = filter_data(selected_regions, selected_companies, selected_keywords)
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

    table_df      = filtered[['trade name', 'Predicted_Category', 'Predicted_Tier', 'tags', 'value']].copy()
    table_df['value'] = table_df['value'].astype(int)
    records       = table_df.to_dict('records')
    tooltip_data  = [{'tags': {'value': str(r.get('tags', '') or ''), 'type': 'markdown'}} for r in records]

    city_label = f"City filter: {selected_city} — click the same bubble again to clear" if selected_city else ""
    return kpi_total, kpi_active, kpi_web, map_fig, region_fig, pie_cat, pie_tier, records, tooltip_data, city_label


classification.register_callbacks(app, filter_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8050, debug=False)
