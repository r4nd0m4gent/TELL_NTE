import os
import json
from datetime import datetime

import dash
from dash import dcc, html, Input, Output, State, ALL
import pandas as pd

# ── Data sources ─────────────────────────────────────────────────────────────
# Company names are loaded from the MySQL database (primary). If the database
# is unavailable, the app falls back to reading the local Excel file.
EXCEL_PATH = os.environ.get('EXCEL_PATH', os.path.join(os.path.dirname(__file__), 'data', 'companies.xlsx'))
CONTRIBUTIONS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'contributions.json')
ENV_PATH = os.path.join(os.path.dirname(__file__), 'db', 'mysql', '.env')


def _get_engine():
    """Build a SQLAlchemy engine from the db/mysql/.env config.

    Returns None if the configuration or required packages are unavailable.
    """
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


def get_company_names_db():
    """Load distinct company names from the organizations table."""
    from sqlalchemy import text

    engine = _get_engine()
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT trade_name FROM organizations "
                     "WHERE trade_name IS NOT NULL ORDER BY trade_name")
            ).fetchall()
        names = [r[0] for r in rows if r[0]]
        return [{'label': n, 'value': n} for n in names] if names else None
    except Exception:
        return None


def get_company_names_excel():
    """Fallback: load company names from the local Excel file."""
    try:
        df = pd.read_excel(EXCEL_PATH)
        col = 'trade_name' if 'trade_name' in df.columns else 'trade name'
        names = sorted(df[col].dropna().unique().tolist())
        return [{'label': n, 'value': n} for n in names]
    except Exception:
        return []


def get_company_names():
    """Company names from the database, falling back to Excel."""
    names = get_company_names_db()
    if names:
        return names
    return get_company_names_excel()

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


# ── Persistence: store submissions in the database ───────────────────────────
# Fields the user can flag for editing in the "Suggest edit" flow.
_editable_fields = [
    {'label': 'Company name',         'value': 'trade_name'},
    {'label': 'City',                 'value': 'city'},
    {'label': 'Postcode',             'value': 'postcode'},
    {'label': 'Region',               'value': 'region'},
    {'label': 'Website',              'value': 'website'},
    {'label': 'Number of employees',  'value': 'employees'},
    {'label': 'Value chain tier(s)',  'value': 'tier'},
    {'label': 'Category',             'value': 'category'},
    {'label': 'Description / Tags',   'value': 'tags'},
    {'label': 'Status (active/inactive)', 'value': 'status'},
]

_DDL = {
    'additions': (
        "CREATE TABLE IF NOT EXISTS additions ("
        " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        " trade_name VARCHAR(255),"
        " city VARCHAR(255) NOT NULL,"
        " postcode VARCHAR(45),"
        " website VARCHAR(512),"
        " employees VARCHAR(45),"
        " value_tier TEXT NOT NULL,"
        " given_tags TEXT NOT NULL,"
        " add_notes TEXT"
        ")"
    ),
    'edits': (
        "CREATE TABLE IF NOT EXISTS edits ("
        " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
        " trade_name VARCHAR(255) NOT NULL,"
        " suggestion JSON NOT NULL"
        ")"
    ),
}

# Idempotent upgrades so pre-existing tables can hold the form's inputs
# (auto-increment id + text columns wide enough for the free-text fields).
_MIGRATIONS = [
    "ALTER TABLE additions MODIFY id INT NOT NULL AUTO_INCREMENT",
    "ALTER TABLE additions MODIFY website VARCHAR(512)",
    "ALTER TABLE additions MODIFY value_tier TEXT NOT NULL",
    "ALTER TABLE additions MODIFY given_tags TEXT NOT NULL",
    "ALTER TABLE additions MODIFY add_notes TEXT",
    "ALTER TABLE edits MODIFY id INT NOT NULL AUTO_INCREMENT",
]

_schema_ready = False


def _ensure_schema(conn):
    """Create the tables if missing and widen columns (runs once per process)."""
    from sqlalchemy import text

    global _schema_ready
    if _schema_ready:
        return
    for ddl in _DDL.values():
        conn.execute(text(ddl))
    for stmt in _MIGRATIONS:
        try:
            conn.execute(text(stmt))
        except Exception:
            pass  # column already in the desired shape
    _schema_ready = True


def save_to_db(kind, payload):
    """Persist a submission to the `additions` or `edits` table.

    Returns True on success, False if the database is unavailable or the
    write fails (callers should fall back to the local JSON file).
    """
    from sqlalchemy import text

    engine = _get_engine()
    if engine is None:
        return False
    try:
        with engine.begin() as conn:
            _ensure_schema(conn)
            if kind == 'add':
                conn.execute(
                    text(
                        "INSERT INTO additions"
                        " (trade_name, city, postcode, website, employees, value_tier, given_tags, add_notes)"
                        " VALUES (:trade_name, :city, :postcode, :website, :employees, :value_tier, :given_tags, :add_notes)"
                    ),
                    {
                        'trade_name': payload.get('trade_name'),
                        'city': payload.get('city'),
                        'postcode': payload.get('postcode'),
                        'website': payload.get('website'),
                        'employees': str(payload['employees']) if payload.get('employees') is not None else None,
                        'value_tier': json.dumps(payload.get('tiers') or []),
                        'given_tags': payload.get('tags') or '',
                        'add_notes': payload.get('notes'),
                    },
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO edits (trade_name, suggestion)"
                        " VALUES (:trade_name, :suggestion)"
                    ),
                    {
                        'trade_name': payload.get('trade_name'),
                        'suggestion': json.dumps(payload.get('changes') or {}),
                    },
                )
        return True
    except Exception:
        return False


def persist_contribution(kind, payload):
    """Store a submission in the DB, falling back to the local JSON file."""
    if not save_to_db(kind, payload):
        save_contribution(kind, payload)

# ── App ───────────────────────────────────────────────────────────────────────
_company_options = get_company_names()

app = dash.Dash(__name__,
    external_stylesheets=['https://fonts.googleapis.com/css2?family=Inter&display=swap'],
    suppress_callback_exceptions=True,
    requests_pathname_prefix='/contribute/',
)

server = app.server

_tier_options = [
    {'label': 'Fiber producer', 'value': 'fibre_textile'},
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
                _lbl('Which field(s) need changing? *'),
                dcc.Dropdown(id='edit-fields', options=_editable_fields, multi=True,
                             placeholder='Select one or more fields…', searchable=True,
                             style={'fontSize': '14px'}),
            ]),
            html.Div(id='edit-fields-inputs'),
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
    Output('edit-fields-inputs', 'children'),
    Input('edit-fields', 'value'),
)
def render_edit_fields(selected_fields):
    """Render one input per selected field for the suggested new value."""
    if not selected_fields:
        return []
    label_map = {o['value']: o['label'] for o in _editable_fields}
    children = []
    for field in selected_fields:
        children.append(html.Div(style=_field_wrap, children=[
            _lbl(f"New value for “{label_map.get(field, field)}” *"),
            dcc.Input(
                id={'type': 'edit-field-input', 'field': field},
                type='text', placeholder='Suggested value…',
                debounce=True, style=_input_style,
            ),
        ]))
    return children

@app.callback(
    Output('confirmation', 'children'),
    Output('confirmation', 'style'),
    Input('submit-btn', 'n_clicks'),
    State('contribution-type', 'value'),
    State('add-name',        'value'),
    State('add-city',        'value'),
    State('add-postcode',    'value'),
    State('add-website',     'value'),
    State('add-employees',   'value'),
    State('add-tiers',       'value'),
    State('add-tier-other',  'value'),
    State('add-category',    'value'),
    State('add-notes',       'value'),
    State('edit-name',       'value'),
    State({'type': 'edit-field-input', 'field': ALL}, 'value'),
    State({'type': 'edit-field-input', 'field': ALL}, 'id'),
    prevent_initial_call=True,
)
def on_submit(n_clicks, choice, add_name, add_city, add_postcode, add_website,
              add_employees, add_tiers, add_tier_other, add_category, add_notes,
              edit_name, edit_values, edit_ids):
    _style_base = {'marginTop': '20px', 'fontWeight': '600', 'fontSize': '14px'}

    # Map each selected edit field to its suggested value (key = field).
    changes = {}
    for value, ident in zip(edit_values or [], edit_ids or []):
        if value not in (None, ''):
            changes[ident['field']] = value

    if choice == 'add':
        required = [(add_name, 'Company name'), (add_city, 'City'),
                    (add_tiers, 'Value chain tier(s)'), (add_category, 'Description / Tags')]
    else:
        required = [(edit_name, 'Company name'),
                    (changes, 'At least one field with a suggested value')]
    missing = [lbl for val, lbl in required if not val]
    if missing:
        return (f'Please fill in the required field(s): {", ".join(missing)}.',
                {**_style_base, 'color': '#c0392b'})

    try:
        if choice == 'add':
            tiers = list(add_tiers or [])
            if 'other' in tiers and add_tier_other:
                tiers = [add_tier_other if t == 'other' else t for t in tiers]
            persist_contribution('add', {
                'trade_name': add_name,
                'city': add_city,
                'postcode': add_postcode,
                'website': add_website,
                'employees': int(add_employees) if add_employees not in (None, '') else None,
                'tiers': tiers,
                'tags': add_category,
                'notes': add_notes,
            })
        else:
            persist_contribution('edit', {
                'trade_name': edit_name,
                'changes': changes,
            })
    except Exception as exc:
        return (f'Submission failed: {exc}', {**_style_base, 'color': '#e67e22'})
    label = 'new company info' if choice == 'add' else 'an edit suggestion'
    return (f'Thank you! Your {label} has been received and will be reviewed.',
            {**_style_base, 'color': nte_violet})

if __name__ == '__main__':
    app.run(debug=True, port=8051)
