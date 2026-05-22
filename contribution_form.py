import os
import dash
from dash import dcc, html, Input, Output, State
import pandas as pd

# Data path – override with TEXTILE_DATA_PATH env variable on the server
_DATA_PATH = os.environ.get(
    'TEXTILE_DATA_PATH',
    r'C:\Users\fsollit\Desktop\Data\Supply chain\Modint KvK\KvK textile.xlsx',
)

# ── Load dataset (same source as textile_companies_NL.py) ──────────────────────
try:
    _data = pd.read_excel(_DATA_PATH, sheet_name=0)
    _company_options = sorted(
        [{'label': str(n), 'value': str(n)}
         for n in _data['trade name'].dropna().unique()],
        key=lambda x: x['label'],
    )
except Exception:
    _company_options = []

app = dash.Dash(__name__,
    external_stylesheets=['https://fonts.googleapis.com/css2?family=Inter&display=swap'],
    suppress_callback_exceptions=True,
    requests_pathname_prefix='/contribute/',
)
server = app.server  # expose WSGI app for gunicorn

# ── Value chain tier options ───────────────────────────────────────────────────
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

# ── Colour palette (matches textile_companies_NL.py) ──────────────────────────
nte_violet    = '#513773'
nte_darkblue  = '#54639E'
nte_lightblue = '#88C0E0'

_base_style = {
    'fontFamily': 'Inter, sans-serif',
    'maxWidth': '640px',
    'margin': '60px auto',
    'padding': '40px',
    'backgroundColor': 'white',
    'borderRadius': '12px',
    'boxShadow': '0 2px 12px rgba(0,0,0,0.12)',
}

_label_style = {
    'display': 'block',
    'fontWeight': '600',
    'marginBottom': '12px',
    'color': nte_violet,
    'fontSize': '16px',
}

_radio_style = {
    'display': 'flex',
    'flexDirection': 'column',
    'gap': '10px',
}

_btn_style = {
    'marginTop': '28px',
    'padding': '10px 28px',
    'backgroundColor': nte_violet,
    'color': 'white',
    'border': 'none',
    'borderRadius': '6px',
    'fontSize': '15px',
    'cursor': 'pointer',
}

# ── Shared field styles (used in static layout below) ─────────────────────────
_input_style = {
    'width': '100%', 'padding': '8px 10px', 'borderRadius': '6px',
    'border': '1px solid #ccc', 'fontSize': '14px', 'boxSizing': 'border-box',
}
_field_wrap = {'marginBottom': '20px'}
_sub_label  = {**_label_style, 'fontWeight': '500', 'fontSize': '14px'}


def _lbl(text):
    return html.Label(text, style=_sub_label)


def _inp(field_id, placeholder='', input_type='text'):
    return dcc.Input(id=field_id, type=input_type, placeholder=placeholder,
                     debounce=True, style=_input_style)


def _field(label, field_id, placeholder='', input_type='text'):
    return html.Div(style=_field_wrap, children=[
        _lbl(label), _inp(field_id, placeholder, input_type),
    ])


# ── Layout ─────────────────────────────────────────────────────────────────────
# Both form sections are always present; visibility is toggled via callbacks.
# This ensures every component ID always exists, avoiding nonexistent-State errors.
app.layout = html.Div(style={'backgroundColor': '#f4f3f8', 'minHeight': '100vh'}, children=[
    html.Div(style=_base_style, children=[

        # Header
        html.H2(
            'Textile Ecosystem Mapping — Contribution Form',
            style={'color': nte_violet, 'marginBottom': '32px', 'fontSize': '22px'},
        ),

        # ── Question 1: contribution type ──────────────────────────────────────
        html.Label(
            'What kind of contribution would you like to bring the Textile Ecosystem Mapping?',
            style=_label_style,
        ),
        dcc.RadioItems(
            id='contribution-type',
            options=[
                {'label': 'Add new company info', 'value': 'add'},
                {'label': 'Suggest edit',          'value': 'edit'},
            ],
            value=None,
            labelStyle={'display': 'flex', 'alignItems': 'center', 'gap': '8px',
                        'fontSize': '15px', 'color': '#333'},
            style=_radio_style,
        ),

        # ── "Add new company" section (hidden until selected) ─────────────────
        html.Div(id='add-section', style={'display': 'none', 'marginTop': '32px'}, children=[
            html.H3('New company information',
                    style={'color': nte_violet, 'marginBottom': '20px', 'fontSize': '17px'}),
            _field('Company name *',     'add-name',      'e.g. Textile BV'),
            _field('City *',             'add-city',      'e.g. Amsterdam'),
            _field('Postcode',           'add-postcode',  'e.g. 1012 AB'),
            _field('Website',            'add-website',   'https://'),
            _field('Number of employees','add-employees', 'e.g. 25', 'number'),
            html.Div(style=_field_wrap, children=[
                _lbl('Value chain tier(s) *'),
                dcc.Dropdown(
                    id='add-tiers',
                    options=_tier_options,
                    multi=True,
                    placeholder='Select one or more tiers…',
                    searchable=True,
                    style={'fontSize': '14px'},
                ),
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

        # ── "Suggest edit" section (hidden until selected) ────────────────────
        html.Div(id='edit-section', style={'display': 'none', 'marginTop': '32px'}, children=[
            html.H3('Suggest an edit',
                    style={'color': nte_violet, 'marginBottom': '20px', 'fontSize': '17px'}),
            html.Div(style=_field_wrap, children=[
                _lbl('Company name *'),
                dcc.Dropdown(
                    id='edit-name',
                    options=_company_options,
                    placeholder='Search or select a company…',
                    searchable=True,
                    clearable=True,
                    style={'fontSize': '14px'},
                ),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('What needs to be changed? *'),
                dcc.Textarea(id='edit-change',
                             placeholder='Describe the current incorrect information…',
                             style={**_input_style, 'height': '80px', 'resize': 'vertical'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Suggested correction *'),
                dcc.Textarea(id='edit-correction',
                             placeholder='Provide the correct information…',
                             style={**_input_style, 'height': '80px', 'resize': 'vertical'}),
            ]),
            html.Div(style=_field_wrap, children=[
                _lbl('Source / reference (optional)'),
                dcc.Input(id='edit-source', type='url',
                          placeholder='https://source.example.com',
                          debounce=True, style=_input_style),
            ]),
        ]),

        # ── Submit button (hidden until a choice is made) ─────────────────────
        html.Div(id='submit-wrapper', style={'display': 'none'}, children=[
            html.Button('Submit contribution', id='submit-btn', n_clicks=0, style=_btn_style),
        ]),

        # ── Confirmation message ───────────────────────────────────────────────
        html.Div(id='confirmation', style={'marginTop': '20px', 'fontWeight': '600',
                                           'fontSize': '14px'}),
    ])
])


# ── Callbacks ──────────────────────────────────────────────────────────────────

@app.callback(
    Output('add-section',    'style'),
    Output('edit-section',   'style'),
    Output('submit-wrapper', 'style'),
    Input('contribution-type', 'value'),
)
def toggle_sections(choice):
    show = {'display': 'block', 'marginTop': '32px'}
    hide = {'display': 'none',  'marginTop': '32px'}
    if choice == 'add':
        return show, hide, {'display': 'block'}
    if choice == 'edit':
        return hide, show, {'display': 'block'}
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
    # ── add fields ────────────────────────────────────────────────────────────
    State('add-name',     'value'),
    State('add-city',     'value'),
    State('add-tiers',    'value'),
    State('add-category', 'value'),
    # ── edit fields ───────────────────────────────────────────────────────────
    State('edit-name',       'value'),
    State('edit-change',     'value'),
    State('edit-correction', 'value'),
    prevent_initial_call=True,
)
def on_submit(n_clicks, choice, add_name, add_city, add_tiers, add_category,
              edit_name, edit_change, edit_correction):
    _style_base = {'marginTop': '20px', 'fontWeight': '600', 'fontSize': '14px'}

    if choice == 'add':
        required = [
            (add_name,     'Company name'),
            (add_city,     'City'),
            (add_tiers,    'Value chain tier(s)'),
            (add_category, 'Description / Tags'),
        ]
    else:
        required = [
            (edit_name,       'Company name'),
            (edit_change,     'What needs to be changed?'),
            (edit_correction, 'Suggested correction'),
        ]

    missing = [lbl for val, lbl in required if not val]
    if missing:
        return (
            f'Please fill in the required field(s): {", ".join(missing)}.',
            {**_style_base, 'color': '#c0392b'},
        )

    label = 'new company info' if choice == 'add' else 'an edit suggestion'
    return (
        f'Thank you! Your {label} has been received and will be reviewed.',
        {**_style_base, 'color': nte_violet},
    )


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True, port=8051)
