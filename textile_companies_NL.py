import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx
import plotly.express as px
import pandas as pd
import pgeocode
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

# Load data from Excel – first sheet only
data = pd.read_excel(
    r'C:\Users\fsollit\Desktop\Data\Supply chain\Modint KvK\KvK textile.xlsx',
    sheet_name=0
)

data = data.rename(columns={
    'visiting address_city': 'city',
    'new main': 'derived_category',
    'tag_category': 'category',
    'number_employees': 'value'
})
data = data.dropna(subset=['city', 'region'])
data['category'] = data['category'].fillna('Unknown')
data['value'] = pd.to_numeric(data['value'], errors='coerce').fillna(0)

# Colors
nte_violet = '#832394'
nte_dark_violet = '#533a74'

# Geocode cities via Dutch postcode (offline, no API key needed)
_nomi = pgeocode.Nominatim('nl')
_pc4 = data['visiting address_postcode'].str.extract(r'^(\d{4})').iloc[:, 0]
_unique_pc4 = _pc4.dropna().unique().tolist()
_geo = _nomi.query_postal_code(_unique_pc4)[['postal_code', 'latitude', 'longitude']]
_geo['postal_code'] = _geo['postal_code'].astype(str)
data['_pc4'] = _pc4.values
data = data.merge(_geo.rename(columns={'postal_code': '_pc4'}), on='_pc4', how='left')
data = data.drop(columns=['_pc4'])

app = dash.Dash(__name__)

_card_style = {
    'flex': '1', 'backgroundColor': 'white', 'borderRadius': '8px',
    'padding': '16px 20px', 'boxShadow': '0 1px 4px rgba(0,0,0,0.1)',
    'textAlign': 'center',
}

app.layout = html.Div([
    html.H1("Textile Companies – Netherlands", style={'textAlign': 'center', 'color': '#2c3e50'}),

    html.Div([
        html.Div([
            html.Label("Filter by Region:"),
            dcc.Dropdown(
                id='region-dropdown',
                options=sorted([{'label': str(r), 'value': str(r)} for r in data['region'].dropna().unique()], key=lambda x: x['label']),
                value=None,
                placeholder="Select a region...",
                multi=True
            )
        ], style={'width': '48%', 'display': 'inline-block', 'padding': '10px'}),

        html.Div([
            html.Label("Filter by Company:"),
            dcc.Dropdown(
                id='company-dropdown',
                options=sorted([{'label': str(c), 'value': str(c)} for c in data['trade name'].dropna().unique()], key=lambda x: x['label']),
                value=None,
                placeholder="Search a company...",
                multi=True
            )
        ], style={'width': '48%', 'display': 'inline-block', 'padding': '10px'})
    ], style={'backgroundColor': '#ecf0f1', 'borderRadius': '8px', 'marginBottom': '20px'}),

    html.Div([
        html.Div([
            html.P('Total Records', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(id='kpi-total', style={'margin': 0, 'color': "#000000"}),
        ], style=_card_style),
        html.Div([
            html.P('Active Businesses', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(id='kpi-active', style={'margin': 0, 'color': "#000000"}),
        ], style=_card_style),
        html.Div([
            html.P('Registered Websites', style={'margin': '0 0 4px 0', 'color': '#7f8c8d', 'fontSize': '13px', 'textTransform': 'uppercase', 'letterSpacing': '0.5px'}),
            html.H2(id='kpi-web', style={'margin': 0, 'color': "#000000"}),
        ], style=_card_style),
    ], style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),

    html.Div(id='city-filter-label', style={'minHeight': '22px', 'marginBottom': '4px', 'fontSize': '13px', 'color': '#832394', 'fontWeight': '600'}),

    html.Div([
        html.Div([
            dcc.Graph(id='map-graph', config={'scrollZoom': True}),
        ], style={'flex': '2', 'minWidth': 0}),
        html.Div([
            dcc.Graph(id='region-chart'),
        ], style={'flex': '1', 'minWidth': 0, 'color': nte_violet}),
    ], style={'display': 'flex', 'gap': '16px', 'alignItems': 'stretch'}),

    dash_table.DataTable(
        id='company-table',
        columns=[
            {'name': 'Company', 'id': 'trade name'},
            {'name': 'Predicted Category', 'id': 'Predicted_Category'},
            {'name': 'Predicted Tier', 'id': 'Predicted_Tier'},
            {'name': 'Tags', 'id': 'tags'},
            {'name': '# Employees', 'id': 'value'},
        ],
        page_size=20,
        sort_action='native',
        filter_action='none',
        style_table={'overflowX': 'auto', 'marginTop': '20px'},
        style_header={'backgroundColor': nte_dark_violet, 'color': 'white', 'fontWeight': 'bold'},
        style_cell={'padding': '8px', 'textAlign': 'left', 'fontSize': '13px',
                    'overflow': 'hidden', 'textOverflow': 'ellipsis', 'maxWidth': '250px'},
        style_data_conditional=[{'if': {'row_index': 'odd'}, 'backgroundColor': '#f9f9f9'}],
        style_data={'cursor': 'pointer'},
        active_cell=None,
        tooltip_data=[],
        tooltip_duration=None),

    dcc.Graph(id='pie-chart'),

    dcc.Store(id='selected-city', data=None),

], style={'fontFamily': 'Arial, sans-serif', 'padding': '20px', 'maxWidth': '1400px', 'margin': 'auto'})


def filter_data(regions, companies):
    filtered = data.copy()
    if regions:
        filtered = filtered[filtered['region'].isin(regions)]
    if companies:
        filtered = filtered[filtered['trade name'].isin(companies)]
    return filtered


@app.callback(
    Output('selected-city', 'data'),
    Input('map-graph', 'clickData'),
    Input('company-table', 'active_cell'),
    State('selected-city', 'data'),
    State('company-table', 'data'),
    prevent_initial_call=True
)
def update_selected_city(click_data, active_cell, current_city, table_data):
    triggered = ctx.triggered_id
    if triggered == 'map-graph':
        if click_data:
            clicked = click_data['points'][0].get('hovertext')
            return None if clicked == current_city else clicked
    elif triggered == 'company-table':
        if active_cell and table_data:
            row = table_data[active_cell['row']]
            company_name = row.get('trade name')
            if company_name:
                match = data[data['trade name'] == company_name]
                if not match.empty:
                    city = match.iloc[0]['city']
                    return None if city == current_city else city
    return current_city


@app.callback(
    Output('kpi-total', 'children'),
    Output('kpi-active', 'children'),
    Output('kpi-web', 'children'),
    Output('map-graph', 'figure'),
    Output('region-chart', 'figure'),
    Output('pie-chart', 'figure'),
    Output('company-table', 'data'),
    Output('company-table', 'tooltip_data'),
    Output('city-filter-label', 'children'),
    Input('region-dropdown', 'value'),
    Input('company-dropdown', 'value'),
    Input('selected-city', 'data'),
)
def update_dashboard(selected_regions, selected_companies, selected_city):
    filtered = filter_data(selected_regions, selected_companies)
    if selected_city:
        filtered = filtered[filtered['city'] == selected_city]

    kpi_total = f"{len(filtered):,}"
    kpi_active = f"{(filtered['status'].str.lower() == 'active').sum():,}"
    kpi_web = f"{filtered['website'].notna().sum():,}"

    """Bubble map: companies per city"""
    _valid = filtered.dropna(subset=['latitude', 'longitude'])
    city_geo = (
        _valid.groupby('city')
        .agg(count=('latitude', 'count'), lat=('latitude', 'median'), lon=('longitude', 'median'))
        .reset_index()
    )
    # Square-root scale so small cities stay visible; enforce a minimum display size
    if not city_geo.empty:
        _sqrt_max = city_geo['count'].apply(lambda x: x ** 0.5).max()
        _min_display = max(_sqrt_max * 0.04, 1)
        city_geo['display_size'] = city_geo['count'].apply(lambda x: max(x ** 0.5, _min_display))
    else:
        city_geo['display_size'] = city_geo['count']
    if selected_city:
        city_geo['_sel'] = city_geo['city'].apply(lambda c: 'selected' if c == selected_city else 'default')
        _color_map = {'selected': 'rgba(220, 80, 0, 0.85)', 'default': 'rgba(138, 43, 226, 0.25)'}
    else:
        city_geo['_sel'] = 'all'
        _color_map = {'all': 'rgba(138, 43, 226, 0.45)'}
    map_fig = px.scatter_mapbox(
        city_geo, lat='lat', lon='lon', size='display_size',
        color='_sel', color_discrete_map=_color_map,
        hover_name='city', hover_data={'count': True, 'display_size': False, 'lat': False, 'lon': False, '_sel': False},
        mapbox_style='open-street-map', zoom=6,
        center={'lat': 52.3, 'lon': 5.3},
        size_max=40,
        title='Number of Companies per City',
        height=500
    )
    map_fig.update_layout(margin={'r': 0, 't': 40, 'l': 0, 'b': 0}, showlegend=False)

    """Bar plot: companies per region"""
    region_counts = filtered.groupby('region', as_index=False).size().rename(columns={'size': 'count'})
    region_counts = region_counts.sort_values('count', ascending=True)
    region_fig = px.bar(
        region_counts, x='count', y='region',
        title='Companies per Region', height=500,
        orientation='h',
        labels={'region': 'Region', 'count': 'Number of Companies'},
        color_discrete_sequence=[nte_dark_violet]
    )
    region_fig.update_layout(margin={'l': 120, 'r': 20, 't': 40, 'b': 20})

    """Pie chart: distribution by category"""
    pie_fig = px.pie(
        filtered, names='derived_category', values='value',
        title='Distribution by Category'
    )

    """ Companies table"""
    table_cols = ['trade name', 'Predicted_Category', 'Predicted_Tier', 'tags', 'value']
    table_df = filtered[table_cols].copy()
    table_df['value'] = table_df['value'].astype(int)
    table_records = table_df.to_dict('records')
    tooltip_data = [
        {'tags': {'value': str(row.get('tags', '') or ''), 'type': 'markdown'}}
        for row in table_records
    ]

    city_label = f"City filter: {selected_city}  —  click the same bubble again to clear" if selected_city else ""
    return kpi_total, kpi_active, kpi_web, map_fig, region_fig, pie_fig, table_records, tooltip_data, city_label


if __name__ == '__main__':
    app.run(debug=True)
    
