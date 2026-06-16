import os
import time

import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx
import plotly.express as px
import pandas as pd
import pgeocode
import classification

# ── Colors ────────────────────────────────────────────────────────────────────
nte_violet    = '#513773'
nte_darkblue  = '#54639E'
nte_lightblue = '#88C0E0'

# ── Load data from Excel ──────────────────────────────────────────────────────
EXCEL_PATH = os.environ.get('EXCEL_PATH', os.path.join(os.path.dirname(__file__), 'data', 'companies.xlsx'))
ASSETS_PATH = os.path.join(os.path.dirname(__file__), 'assets')

def load_companies():
    df = pd.read_excel(EXCEL_PATH)
    df = df.rename(columns={
        'visiting address_city':     'city',
        'visiting address_postcode': 'postcode',
        'number_employees':          'value',
    })
    df['value'] = pd.to_numeric(df['value'], errors='coerce').fillna(0).astype(int)
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        nomi = pgeocode.Nominatim('nl')
        postcodes = df['postcode'].astype(str).str.replace(' ', '', regex=False)
        geo = nomi.query_postal_code(postcodes.tolist())
        df['latitude']  = geo['latitude'].values
        df['longitude'] = geo['longitude'].values
    return df

data = load_companies()

# ── Write initial empty map HTML so the iframe src exists on startup ──────────
os.makedirs(ASSETS_PATH, exist_ok=True)

def write_map_html(fig):
    path = os.path.join(ASSETS_PATH, 'map.html')
    fig.write_html(path, include_plotlyjs='cdn', full_html=True)

# Generate initial map
_init_geo = (
    data.dropna(subset=['latitude', 'longitude'])
    .groupby('city')
    .agg(count=('latitude', 'count'), lat=('latitude', 'median'), lon=('longitude', 'median'))
    .reset_index()
)
_init_geo['display_size'] = _init_geo['count'].apply(lambda x: max(x ** 0.5, 1))
_init_geo['_sel'] = 'all'
_init_fig = px.scatter_mapbox(
    _init_geo, lat='lat', lon='lon', size='display_size',
    hover_name='city', mapbox_style='open-street-map', zoom=6,
    center={'lat': 52.3, 'lon': 5.3}, size_max=40,
    title='Number of Companies per City', height=500
)
_init_fig.update_layout(margin={'r': 0, 't': 40, 'l': 0, 'b': 0})
write_map_html(_init_fig)

# ── App ───────────────────────────────────────────────────────────────────────
app = dash.Dash(__name__, external_stylesheets=[
    'https://fonts.googleapis.com/css2?family=Inter&display=swap'
], suppress_callback_exceptions=True)

server = app.server

_card_style = {
    'flex': '1', 'backgroundColor': 'white', 'borderRadius': '8px',
    'padding': '16px 20px', 'boxShadow': '0 1px 4px rgba(0,0,0,0.1)',
    'textAlign': 'center',
}

app.layout = html.Div([

    # ── Header ────────────────────────────────────────────────────────────────
    html.Header([
        html.Img(src='https://newtexeco.nl/wp-content/uploads/2023/12/logo_2x.png', alt='NewTexEco'),
        html.Span('NewTexEco', className='site-label'),
    ], className='tell-header'),

    # ── Intro ─────────────────────────────────────────────────────────────────
    html.Div([
        html.H1('Textile Ecosystem Living Lab'),
        html.P('The TCLF sector in the Netherlands includes more than 10,000 companies. '
               'The Textile Ecosystem Living Lab gives visibility to all companies across '
               'the value chain, from fibre producers to recyclers, showing their geographic '
               'distribution and specializations through the use of tags/keywords.'),
    ], className='tell-intro'),

    # ── Dashboard body ────────────────────────────────────────────────────────
    html.Div([

        # ── Filters ───────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.Label("Filter by Company:"),
                dcc.Dropdown(
                    id='company-dropdown',
                    options=sorted([{'label': str(c), 'value': str(c)} for c in data['trade name'].dropna().unique()], key=lambda x: x['label']),
                    value=None, placeholder="Search a company...", multi=True
                )
            ], style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
            html.Div([
                html.Label("Filter by Region:"),
                dcc.Dropdown(
                    id='region-dropdown',
                    options=sorted([{'label': str(r), 'value': str(r)} for r in data['region'].dropna().unique()], key=lambda x: x['label']),
                    value=None, placeholder="Select a region...", multi=True
                )
            ], style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
            html.Div([
                html.Label("Filter by Keywords/Tags:"),
                dcc.Dropdown(
                    id='keywords-dropdown',
                    options=sorted([{'label': str(k), 'value': str(k)} for k in data['tags'].dropna().unique()], key=lambda x: x['label']),
                    value=None, placeholder="Select keywords...", multi=True
                )
            ], style={'width': '32%', 'display': 'inline-block', 'padding': '10px'}),
        ], style={'backgroundColor': '#ecf0f1', 'borderRadius': '8px', 'marginBottom': '20px'}),

        # ── KPI cards ─────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.P('Total Records', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
                html.H2(id='kpi-total', style={'margin': 0, 'color': '#000000'}),
            ], style=_card_style),
            html.Div([
                html.P('Active Businesses', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
                html.H2(id='kpi-active', style={'margin': 0, 'color': '#000000'}),
            ], style=_card_style),
            html.Div([
                html.P('Registered Websites', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
                html.H2(id='kpi-web', style={'margin': 0, 'color': '#000000'}),
            ], style=_card_style),
        ], style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),

        html.Div(id='city-filter-label', style={'minHeight': '22px', 'marginBottom': '4px', 'fontSize': '13px', 'color': '#832394', 'fontWeight': '600'}),

        # ── Map (iframe) + Region chart ───────────────────────────────────────
        html.Div([
            html.Div([
                html.Iframe(id='map-iframe', src=f'/assets/map.html?t={int(time.time())}',
                            style={'height': '500px', 'width': '100%', 'border': 'none',
                                   'borderRadius': '8px'})
            ], style={'flex': '1.5', 'minWidth': 0}),
            html.Div([
                dcc.Graph(id='region-chart', style={'height': '500px', 'width': '100%'})
            ], style={'flex': '1.5', 'minWidth': 0}),
        ], style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),

        # ── Data table ────────────────────────────────────────────────────────
        dash_table.DataTable(
            id='company-table',
            columns=[
                {'name': 'Company',            'id': 'trade name'},
                {'name': 'Predicted Category', 'id': 'Predicted_Category'},
                {'name': 'Predicted Tier',     'id': 'Predicted_Tier'},
                {'name': 'Tags',               'id': 'tags'},
                {'name': '# Employees',        'id': 'value'},
            ],
            page_size=10,
            sort_action='native',
            filter_action='none',
            style_table={'overflowX': 'auto', 'overflowY': 'auto', 'maxHeight': '380px', 'marginTop': '20px'},
            style_header={'backgroundColor': nte_darkblue, 'color': 'white', 'fontWeight': 'bold'},
            style_cell={'padding': '8px', 'textAlign': 'left', 'fontSize': '13px',
                        'overflow': 'hidden', 'textOverflow': 'ellipsis', 'maxWidth': '250px'},
            style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f9f9f9'}],
            style_data={'cursor': 'pointer'},
            active_cell=None, tooltip_data=[], tooltip_duration=None),

        # ── Pie charts ────────────────────────────────────────────────────────
        html.Div([
            dcc.Graph(id='pie-category', style={'flex': '1', 'minWidth': 0}),
            dcc.Graph(id='pie-tier',     style={'flex': '1', 'minWidth': 0}),
        ], style={'display': 'flex', 'gap': '16px', 'marginTop': '20px'}),

        # ── Action buttons ────────────────────────────────────────────────────
        html.Div([
            html.A(
                '✏️ Contribute to the Textile Ecosystem Mapping',
                href='/contribute/',
                style={
                    'display': 'inline-block', 'padding': '10px 24px',
                    'backgroundColor': nte_violet, 'color': 'white',
                    'borderRadius': '6px', 'textDecoration': 'none',
                    'fontSize': '14px', 'fontWeight': '600',
                },
            ),
            classification.get_button(),
        ], style={'display': 'flex', 'gap': '12px', 'justifyContent': 'center',
                  'marginTop': '32px', 'paddingBottom': '32px'}),

        classification.get_modal(),
        *classification.get_stores(),

    ], style={'fontFamily': 'Inter, sans-serif', 'padding': '20px', 'maxWidth': '1400px', 'margin': 'auto'}),

    # ── Footer ────────────────────────────────────────────────────────────────
    html.Footer([
        html.A('newtexeco.nl', href='https://newtexeco.nl', target='_blank'),
        html.Span(' · Textile Ecosystem Living Lab © 2026 NewTexEco'),
    ], className='tell-footer'),

])


# ── Filter helper ─────────────────────────────────────────────────────────────
def filter_data(regions, companies, keywords=None):
    filtered = data.copy()
    if regions:
        filtered = filtered[filtered['region'].isin(regions)]
    if companies:
        filtered = filtered[filtered['trade name'].isin(companies)]
    if keywords:
        pattern = '|'.join([str(k) for k in keywords])
        filtered = filtered[filtered['tags'].astype(str).str.contains(pattern, case=False, na=False)]
    return filtered


# ── Main dashboard callback ───────────────────────────────────────────────────
@app.callback(
    Output('kpi-total',         'children'),
    Output('kpi-active',        'children'),
    Output('kpi-web',           'children'),
    Output('map-iframe',        'src'),
    Output('region-chart',      'figure'),
    Output('pie-category',      'figure'),
    Output('pie-tier',          'figure'),
    Output('company-table',     'data'),
    Output('company-table',     'tooltip_data'),
    Output('city-filter-label', 'children'),
    Input('region-dropdown',    'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
)
def update_dashboard(selected_regions, selected_companies, selected_keywords):
    filtered = filter_data(selected_regions, selected_companies, selected_keywords)

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
        _sqrt_max    = city_geo['count'].apply(lambda x: x ** 0.5).max()
        _min_display = max(_sqrt_max * 0.04, 1)
        city_geo['display_size'] = city_geo['count'].apply(lambda x: max(x ** 0.5, _min_display))
    else:
        city_geo['display_size'] = city_geo['count']

    city_geo['_sel'] = 'all'

    map_fig = px.scatter_mapbox(
        city_geo, lat='lat', lon='lon', size='display_size',
        color='_sel', color_discrete_map={'all': 'rgba(138, 43, 226, 0.45)'},
        hover_name='city',
        hover_data={'count': True, 'display_size': False, 'lat': False, 'lon': False, '_sel': False},
        mapbox_style='open-street-map', zoom=6,
        center={'lat': 52.3, 'lon': 5.3},
        size_max=40, title='Number of Companies per City', height=500
    )
    map_fig.update_layout(margin={'r': 0, 't': 40, 'l': 0, 'b': 0}, showlegend=False)
    write_map_html(map_fig)
    map_src = f'/assets/map.html?t={int(time.time())}'

    region_counts = filtered.groupby('region', as_index=False).size().rename(columns={'size': 'count'})
    region_counts = region_counts.sort_values('count', ascending=True)
    region_fig = px.bar(
        region_counts, x='count', y='region',
        title='Companies per Region', height=500, orientation='h',
        color_discrete_sequence=[nte_darkblue]
    )
    region_fig.update_layout(margin={'l': 120, 'r': 20, 't': 40, 'b': 20})

    _cat_counts = filtered['Predicted_Category'].fillna('Unknown').value_counts().reset_index()
    _cat_counts.columns = ['Predicted_Category', 'count']
    pie_category = px.pie(_cat_counts, names='Predicted_Category', values='count',
                          title='Product Category',
                          color_discrete_sequence=px.colors.sequential.Purples_r)
    pie_category.update_traces(textposition='inside', textinfo='percent+label')
    pie_category.update_layout(showlegend=False, margin={'t': 50, 'b': 10, 'l': 10, 'r': 10})

    _tier_counts = filtered['Predicted_Tier'].fillna('Unknown').value_counts().reset_index()
    _tier_counts.columns = ['Predicted_Tier', 'count']
    pie_tier = px.pie(_tier_counts, names='Predicted_Tier', values='count',
                      title='Lifecycle Stage',
                      color_discrete_sequence=px.colors.sequential.Blues_r)
    pie_tier.update_traces(textposition='inside', textinfo='percent+label')
    pie_tier.update_layout(showlegend=False, margin={'t': 50, 'b': 10, 'l': 10, 'r': 10})

    table_cols = ['trade name', 'Predicted_Category', 'Predicted_Tier', 'tags', 'value']
    table_df   = filtered[table_cols].copy()
    table_df['value'] = table_df['value'].astype(int)
    table_records = table_df.to_dict('records')
    tooltip_data  = [
        {'tags': {'value': str(row.get('tags', '') or ''), 'type': 'markdown'}}
        for row in table_records
    ]

    return kpi_total, kpi_active, kpi_web, map_src, region_fig, pie_category, pie_tier, table_records, tooltip_data, ""


# ── Classification callbacks ──────────────────────────────────────────────────
classification.register_callbacks(app, filter_data)

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ssl_certfile = os.environ.get('SSL_CERTFILE', '/etc/ssl/tell/fullchain.pem')
    ssl_keyfile  = os.environ.get('SSL_KEYFILE',  '/etc/ssl/tell/privkey.pem')
    use_ssl = os.path.isfile(ssl_certfile) and os.path.isfile(ssl_keyfile)
    app.run(
        host='0.0.0.0',
        port=443 if use_ssl else 8050,
        debug=False,
        ssl_context=(ssl_certfile, ssl_keyfile) if use_ssl else None,
    )
