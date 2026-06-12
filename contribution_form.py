import os
import json
from datetime import datetime

import dash
from dash import dcc, html, Input, Output, State
import pandas as pd

# ── Load company names from Excel for the dropdown ───────────────────────────
EXCEL_PATH = os.environ.get('EXCEL_PATH', os.path.join(os.path.dirname(__file__), 'data', 'companies.xlsx'))
CONTRIBUTIONS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'contributions.json')

def get_company_names():
    try:
        df = pd.read_excel(EXCEL_PATH)
        names = sorted(df['trade name'].dropna().unique().tolist())
        return [{'label': n, 'value': n} for n in names]
    except Exception:
        return []

def save_contribution(kind, payload):
    """Append contribution to a local JSON file."""
    os.makedirs(os.path.dirname(CONTRIBUTIONS_PATH), exist_ok=True)
    entry = {'type': kind, 'timestamp': datetime.utcnow().isoformat(), **payload}
    existing = []
    if os.path.isfile(CONTRIBUTIONS_PATH):
        with open(CONTRIBUTIONS_PATH, 'r') as f:
            existing = json.load(f)
    existing.append(entry)
    with open(CONTRIBUTIONS_PATH, 'w') as f:
        json.dump(existing, f, indent=2)

# ── App ───────────────────────────────────────────────────────────────────────
_company_options = get_company_names()

app = dash.Dash(__name__,
    external_stylesheets=['https://fonts.googleapis.com/css2?family=Inter&display=swap'],
    suppress_callback_exceptions=True,
    requests_pathname_prefix='/contribute/',
)

server = app.server

_tier_options = [
    {'label': 'Yarn & Textile producer (semi-finished products)', 'value': 'yarn_textile'},
    {'label': 'Garment production (finished product)',            'value': 'garment'},
    {'label': 'Wholesale',                                        'value': 'wholesale'},
    {'label': 'Retail',                                           'value': 'retail'},
    {'label': 'Brand',                                            'value': 'brand'},
    {'label': 'Collection & sorting of used textiles',            'value': 'collection_sorting'},
    {'label': 'Repair & Re-manufacturing',                        'value': 'repair_remanufacturing'},
    {'label': 'Recycling',                                        'value': 'recycling'},
    {'label': 'Research & network organization',                  'value': 'research_network'},
    {'label': 'Other',                                            'value': 'other'},
]

nte_violet    = '#513773'
nte_darkblue  = '#54639E'
nte_lightblue = '#88C0E0'

_base_style = {
    'fontFamily': 'Inter, sans-serif', 'maxWidth': '640px',
    'margin': '60px auto', 'padding': '40px',
    'backgroundColor': 'white', 'borderRadius': '12px',
    'boxShadow': '0 2px 12px rgba(0,0,0,0.12)',
}
_label_style = {'display': 'block', 'fontWeight': '600', 'marginBottom': '12px',
                'color': nte_violet, 'fontSize': '16px'}
_radio_style = {'display': 'flex', 'flexDirection': 'column', 'gap': '10px'}
_btn_style   = {'marginTop': '28px', 'padding': '10px 28px', 'backgroundColor': nte_violet,
                'color': 'white', 'border': 'none', 'borderRadius': '6px',
                'fontSize': '15px', 'cursor': 'pointer'}
_input_style = {'width': '100%', 'padding': '8px 10px', 'borderRadius': '6px',
                'border': '1px solid #ccc', 'fontSize': '14px', 'boxSizing': 'border-box'}
_field_wrap  = {'marginBottom': '20px'}
_sub_label   = {**_label_style, 'fontWeight': '500', 'fontSize': '14px'}

def _lbl(text):
    return html.Label(text, style=_sub_label)

def _inp(field_id, placeholder='', input_type='text'):
    return dcc.Input(id=field_id, type=input_type, placeholder=placeholder,
                     debounce=True, style=_input_style)

def _field(label, field_id, placeholder='', input_type='text'):
    return html.Div(style=_field_wrap, children=[_lbl(label), _inp(field_id, placeholder, input_type)])

app.layout = html.Div(style={'backgroundColor': '#f4f3f8', 'minHeight': '100vh'}, children=[
    html.Div(style=_base_style, children=[
        html.H2('Textile Ecosystem Mapping — Contribution Form',
                style={'color': nte_violet, 'marginBottom': '32px', 'fontSize': '22px'}),
        html.Label('What kind of contribution would you like to bring the Textile Ecosystem Mapping?',
                   style=_label_style),
        dcc.RadioItems(
            id='contribution-type',
            options=[
                {'label': 'Add new company info', 'value': 'add'},
                {'label': 'Suggest edit',         'value': 'edit'},
            ],
            value=None,
            labelStyle={'display': 'flex', 'alignItems': 'center', 'gap': '8px',
                        'fontSize': '15px', 'color': '#333'},
            style=_radio_style,
        ),
        html.Div(id='add-section', style={'display': 'none', 'marginTop': '32px'}, children=[
            html.H3('New company information',
                    style={'color': nte_violet, 'marginBottom': '20px', 'fontSize': '17px'}),
            _field('Company name *',      'add-name',      'e.g. Textile BV'),
            _field('City *',              'add-city',      'e.g. Amsterdam'),
            _field('Postcode',            'add-postcode',  'e.g. 1012 AB'),
            _field('Website',             'add-website',   'https://'),
            _field('Number of employees', 'add-employees', 'e.g. 25', 'number'),
            html.Div(style=_field_wrap, children=[
                _lbl('Value chain tier(s) *'),
                dcc.Dropdown(id='add-tiers', options=_tier_options, multi=True,
                             placeholder='Select one or more tiers…', searchable=True,
                             style={'fontSize': '14px'}),
                html.Div(id='add-tier-other-wrap', style={'display': 'none', 'marginTop': '10px'},
                         children=[_inp('add-tier-other', 'Please specify…')]),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Description / Tags *'),
                dcc.Textarea(id='add-category', placeholder='Describe the company category…',
                             style={**_input_style, 'height': '80px', 'resize': 'vertical'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Additional notes'),
                dcc.Textarea(id='add-notes', placeholder='Any other relevant information…',
                             style={**_input_style, 'height': '100px', 'resize': 'vertical'}),
            ]),
        ]),
        html.Div(id='edit-section', style={'display': 'none', 'marginTop': '32px'}, children=[
            html.H3('Suggest an edit',
                    style={'color': nte_violet, 'marginBottom': '20px', 'fontSize': '17px'}),
            html.Div(style=_field_wrap, children=[
                _lbl('Company name *'),
                dcc.Dropdown(id='edit-name', options=_company_options,
                             placeholder='Search or select a company…',
                             searchable=True, clearable=True, style={'fontSize': '14px'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('What needs to be changed? *'),
                dcc.Textarea(id='edit-change', placeholder='Describe the current incorrect information…',
                             style={**_input_style, 'height': '80px', 'resize': 'vertical'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Suggested correction *'),
                dcc.Textarea(id='edit-correction', placeholder='Provide the correct information…',
                             style={**_input_style, 'height': '80px', 'resize': 'vertical'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Source / reference (optional)'),
                dcc.Input(id='edit-source', type='url', placeholder='https://source.example.com',
                          debounce=True, style=_input_style),
            ]),
        ]),
        html.Div(id='submit-wrapper', style={'display': 'none'}, children=[
            html.Button('Submit contribution', id='submit-btn', n_clicks=0, style=_btn_style),
        ]),
        html.Div(id='confirmation', style={'marginTop': '20px', 'fontWeight': '600', 'fontSize': '14px'}),
    ])
])

@app.callback(
    Output('add-section',    'style'),
    Output('edit-section',   'style'),
    Output('submit-wrapper', 'style'),
    Input('contribution-type', 'value'),
)
def toggle_sections(choice):
    show = {'display': 'block', 'marginTop': '32px'}
    hide = {'display': 'none',  'marginTop': '32px'}
    if choice == 'add':  return show, hide, {'display': 'block'}
    if choice == 'edit': return hide, show, {'display': 'block'}
    return hide, hide, {'display': 'none'}

@app.callback(
    Output('add-tier-other-wrap', 'style'),
    Input('add-tiers', 'value'),
)
def toggle_tier_other(tiers):
    if tiers and 'other' in tiers:
        return {'display': 'block', 'marginTop': '10px'}
    return {'display': 'none'}

@app.callback(
    Output('confirmation', 'children'),
    Output('confirmation', 'style'),
    Input('submit-btn', 'n_clicks'),
    State('contribution-type', 'value'),
    State('add-name',        'value'),
    State('add-city',        'value'),
    State('add-tiers',       'value'),
    State('add-category',    'value'),
    State('edit-name',       'value'),
    State('edit-change',     'value'),
    State('edit-correction', 'value'),
    prevent_initial_call=True,
)
def on_submit(n_clicks, choice, add_name, add_city, add_tiers, add_category,
              edit_name, edit_change, edit_correction):
    _style_base = {'marginTop': '20px', 'fontWeight': '600', 'fontSize': '14px'}
    if choice == 'add':
        required = [(add_name, 'Company name'), (add_city, 'City'),
                    (add_tiers, 'Value chain tier(s)'), (add_category, 'Description / Tags')]
    else:
        required = [(edit_name, 'Company name'), (edit_change, 'What needs to be changed?'),
                    (edit_correction, 'Suggested correction')]
    missing = [lbl for val, lbl in required if not val]
    if missing:
        return (f'Please fill in the required field(s): {", ".join(missing)}.',
                {**_style_base, 'color': '#c0392b'})
    try:
        if choice == 'add':
            save_contribution('add', {'trade_name': add_name, 'city': add_city,
                                      'tiers': add_tiers, 'tags': add_category})
        else:
            save_contribution('edit', {'trade_name': edit_name, 'what_change': edit_change,
                                       'correction': edit_correction})
    except Exception as exc:
        return (f'Submission failed: {exc}', {**_style_base, 'color': '#e67e22'})
    label = 'new company info' if choice == 'add' else 'an edit suggestion'
    return (f'Thank you! Your {label} has been received and will be reviewed.',
            {**_style_base, 'color': nte_violet})

if __name__ == '__main__':
    app.run(debug=True, port=8051)
