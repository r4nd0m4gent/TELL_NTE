import os
import base64
import datetime
import json
import threading
from collections import Counter
from html import escape as escape_html
import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import pandas as pd
import pgeocode
import classification

# ── Colors ────────────────────────────────────────────────────────────────────
nte_violet   = '#513773'
nte_darkblue = '#54639E'

FONT_STACK = 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
INK        = '#2c2c2c'   # primary text
MUTED      = '#6b6b76'   # labels, axis ticks
GRID       = '#eeecf2'   # gridlines, row dividers

# Brand ramps for the donut charts (dark → light). Plotly picks black or white
# slice text automatically, so light shades stay readable.
VIOLET_RAMP = ['#513773', '#7a5a9e', '#a58cc4', '#cbbbe0', '#e7dff0', '#f3eff8']
BLUE_RAMP   = ['#2f3c70', '#54639E', '#8390c0', '#b3bcdc', '#dde1f0', '#eef0f7']
MIXED_RAMP  = ['#54639E', '#513773', '#8390c0', '#a58cc4', '#b3bcdc', '#cbbbe0',
               '#dde1f0', '#e7dff0']

# The legend is laid out in two fixed columns (see entrywidth below), so its
# height no longer depends on how wide the card happens to be: six entries are
# always three rows. That makes the band under the donut predictable - wide
# enough never to be hit, tight enough not to leave a hole.
LEGEND_BAND = 0.26
LEGEND_GAP = 0.03

# ── Plotly theme ──────────────────────────────────────────────────────────────
# One shared template so every chart gets the same font, white background,
# light gridlines and hover style; individual figures only set what differs.
# Chart titles are HTML headings above each card (see _graph), not in the figure.
pio.templates['tell'] = go.layout.Template(layout={
    'font':          {'family': FONT_STACK, 'size': 12, 'color': MUTED},
    'paper_bgcolor': 'white',
    'plot_bgcolor':  'white',
    'hoverlabel':    {'bgcolor': 'white', 'bordercolor': GRID,
                      'font': {'family': FONT_STACK, 'size': 12, 'color': INK}},
    'xaxis':         {'gridcolor': GRID, 'linecolor': GRID, 'zeroline': False,
                      'ticks': '', 'automargin': True},
    'yaxis':         {'gridcolor': GRID, 'linecolor': GRID, 'zeroline': False,
                      'ticks': '', 'automargin': True},
    'barcornerradius': 4,
})
pio.templates.default = 'plotly_white+tell'


def _fade(hex_color, amount=0.75):
    """Blend a hex colour towards white, for slices that are not selected."""
    h = hex_color.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(round(c + (255 - c) * amount))
    return f'#{mix(r):02x}{mix(g):02x}{mix(b):02x}'


def _donut(df, names, colors, selected=None):
    """Donut chart with the total in the centre.

    Slices are labelled in place; labels that don't fit (small slices) are
    hidden rather than shrunk, and every slice still shows details on hover.
    No legend: some fields have ~10 categories, which would squash the chart.

    `selected` is the slice the user clicked to filter by: it is pulled out of
    the ring and the others are dimmed, so the active filter is visible on the
    chart itself.
    """
    total = int(df['count'].sum())
    fig = px.pie(df, names=names, values='count', hole=0.42,
                 color_discrete_sequence=colors)
    labels = df[names].astype(str).tolist()
    marker = {'line': {'color': 'white', 'width': 2}}
    pull   = None
    if selected is not None:
        # pie markers have no opacity, so a dimmed slice is its own colour
        # faded towards the card background instead.
        slice_colors = [colors[i % len(colors)] for i in range(len(labels))]
        marker['colors'] = [c if l == selected else _fade(c)
                            for c, l in zip(slice_colors, labels)]
        pull = [0.06 if l == selected else 0 for l in labels]
    fig.update_traces(
        sort=True, direction='clockwise', rotation=0,
        # The names live in the legend, so slices only carry their share; a
        # thin slice could never hold its name anyway.
        textposition='inside', textinfo='percent', insidetextorientation='auto',
        texttemplate='%{percent:.0%}',
        pull=pull,
        marker=marker,
        domain={'y': [LEGEND_BAND, 1.0]},
        # The pie's own centre title rather than a layout annotation: it
        # follows the donut, where paper coordinates do not once the legend
        # takes its share of the figure.
        title={'text': f"<b>{total:,}</b><br><span style='font-size:11px'>companies</span>",
               'position': 'middle center', 'font': {'size': 16, 'color': INK}},
        hovertemplate='<b>%{label}</b><br>%{value:,} companies (%{percent})'
                      '<br><i>click to filter</i><extra></extra>',
    )
    fig.update_layout(
        uniformtext_minsize=10, uniformtext_mode='hide',
        # A legend so the small slices can be read and clicked at all: 27
        # companies make a sliver no label fits in and no finger can hit.
        # itemclick off: Plotly's own legend click hides a slice, which would
        # fight with the click-to-filter the dashboard puts on these charts.
        showlegend=True,
        # The donut is confined to the top of the plot (see the trace's domain
        # below) and the legend sits in the band under it. Left to themselves
        # they share the same space and the legend lands on the chart.
        legend={'orientation': 'h', 'yanchor': 'top', 'y': LEGEND_BAND - LEGEND_GAP,
                'xanchor': 'center', 'x': 0.5, 'font': {'size': 10, 'color': INK},
                'entrywidthmode': 'fraction', 'entrywidth': 0.5,
                'itemclick': False, 'itemdoubleclick': False, 'traceorder': 'normal'},
        margin={'t': 8, 'b': 8, 'l': 16, 'r': 16},
    )
    return fig


def _hbar(df, names, colour, selected=None):
    """Horizontal bars, largest at the top, each labelled with its count.

    Used where a donut's slices would be too many or too thin to read: the
    category sits on the axis and the number beside the bar, so nothing has to
    be hovered. Clicking a bar filters, like the donuts (see CHART_FILTERS).
    `selected` is the clicked category: the others fade.
    """
    df = df.sort_values('count')                 # plotly draws the first at the bottom
    labels = df[names].astype(str).tolist()
    bars = [colour if selected is None or l == selected else _fade(colour, 0.65)
            for l in labels]
    fig = px.bar(df, x='count', y=names, orientation='h', text='count')
    fig.update_traces(
        marker={'color': bars}, marker_cornerradius=0,
        texttemplate='%{x:,}', textposition='outside', cliponaxis=False,
        textfont={'color': MUTED, 'size': 11},
        hovertemplate='<b>%{y}</b><br>%{x:,} companies'
                      '<br><i>click to filter</i><extra></extra>',
    )
    fig.update_xaxes(title=None, showgrid=True, showticklabels=False)
    fig.update_yaxes(title=None, showgrid=False, tickfont={'color': INK, 'size': 11})
    fig.update_layout(bargap=0.3, showlegend=False,
                      margin={'l': 16, 'r': 46, 't': 16, 'b': 16})
    return fig

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
        o.legal_form,
        -- Small-Medium Enterprise / Multinational / Frontrunner /
        -- Unclassified, from db/classify_companies.py
        COALESCE(cc.company_class, 'Unclassified') AS company_class,
        -- The merged keyword list (curated tags + scraped keywords and bigrams,
        -- built by db/build_dashboard_tags.py); organizations that were not
        -- scraped keep their curated tags.
        COALESCE(ts.tags_new, t.tags) AS tags,
        -- The curated tags on their own. The semantic classification embeds
        -- these: the merged list above is ~4x longer, which pushed a run from
        -- 100 to 370 seconds and past Nginx's timeout.
        t.tags AS curated_tags,
        t.category    AS Predicted_Category,
        -- db/reclassify_tiers.py writes into tags.tier and keeps what a
        -- company had before in tags.tier_original.
        COALESCE(t.tier, 'No match') AS Predicted_Tier,
        COALESCE(sc.has_contact, 0) AS has_contact,
        sc.website_emails
    FROM organizations AS o
    JOIN (
        SELECT city, MAX(region) AS region,
               AVG(latitude) AS latitude, AVG(longitude) AS longitude
        FROM geographies GROUP BY city
    ) AS g ON g.city = o.city
    JOIN tags AS t ON t.id = o.id
    LEFT JOIN tags_scraped AS ts ON ts.id = o.id
    LEFT JOIN company_class AS cc ON cc.id = o.id
    -- Contacts found on the company's public website by the scraper. Grouped
    -- by website first: a site can appear on several scrape rows, and a plain
    -- join would then duplicate the organization.
    LEFT JOIN (
        SELECT website,
               MAX(`Website emails` IS NOT NULL AND `Website emails` <> '') AS has_contact,
               MIN(NULLIF(`Website emails`, '')) AS website_emails
        FROM scraping17092026 GROUP BY website
    ) AS sc ON sc.website = o.website
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
        df['has_contact'] = pd.to_numeric(df.get('has_contact'), errors='coerce').fillna(0).astype(int)
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
    " source VARCHAR(16),"
    " resolves_to VARCHAR(255),"
    " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
    ")"
)

# Where a tracked event came from, so test and development traffic can be told
# apart from real visitors (db/mysql/tracking_source.py labels older rows):
#   public     a visitor on the live site
#   automated  a headless (scripted) browser on the live site - tests, bots
#   local      the dashboard running anywhere but the live host, e.g. a laptop
PUBLIC_HOSTS = {'tell.newtexeco.nl'}


def _visit_source():
    """Classify the current request; None outside a request."""
    try:
        from flask import request
        host = (request.host or '').split(':')[0].lower()
        agent = request.headers.get('User-Agent', '')
    except Exception:
        return None
    if host not in PUBLIC_HOSTS:
        return 'local'
    return 'automated' if 'Headless' in agent else 'public'

_track_engine = None
_track_schema_ready = False
_track_lock = threading.Lock()


def _get_track_engine():
    """Reuse a single engine for tracking writes."""
    global _track_engine
    if _track_engine is None:
        _track_engine = _get_engine()
    return _track_engine


def _write_event(session_id, event_type, details, source=None, ip=None):
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
                text("INSERT INTO tracking_events"
                     " (session_id, event_type, details, source, resolves_to)"
                     " VALUES (:sid, :etype, :details, :source, :host)"),
                {'sid': session_id, 'etype': event_type,
                 'details': json.dumps(details or {}, default=str), 'source': source,
                 'host': _resolves_to(ip)},
            )
    except Exception:
        pass


def log_event(session_id, event_type, details=None):
    """Fire-and-forget tracking write (non-blocking)."""
    # Read the request here: the worker thread has no request context. The
    # reverse lookup happens on that thread, where it cannot delay the page.
    threading.Thread(target=_write_event,
                     args=(session_id, event_type, details or {},
                           _visit_source(), _client_ip()),
                     daemon=True).start()


def _client_ip():
    """The visitor's address, read through Nginx's forwarding headers."""
    try:
        from flask import request
        xff = request.headers.get('X-Forwarded-For', '')
        return (xff.split(',')[0].strip() if xff else
                request.headers.get('X-Real-IP') or request.remote_addr)
    except Exception:
        return None


def _request_meta():
    """Collect request metadata. The referrer is not kept: it is this page on
    every event. The browser's language is, since visitors differ there."""
    try:
        from flask import request
        return {
            'ip': _client_ip(),
            'user_agent': request.headers.get('User-Agent'),
            'language': request.headers.get('Accept-Language'),
        }
    except Exception:
        return {}


# Reverse DNS per address, looked up once per process. A hostname says more
# about a visitor than the number does ('...wireless.hva.nl'); '-' marks an
# address that does not resolve, so it is not looked up again.
_HOSTNAMES = {}


def _resolves_to(ip):
    if not ip:
        return None
    if ip not in _HOSTNAMES:
        import socket
        try:
            _HOSTNAMES[ip] = socket.gethostbyaddr(ip)[0]
        except Exception:
            _HOSTNAMES[ip] = '-'
    return _HOSTNAMES[ip]

# ── App ───────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=['https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'],
    suppress_callback_exceptions=True,
    url_base_pathname='/dashboard/',
    meta_tags=[{'name': 'viewport',
                'content': 'width=device-width, initial-scale=1'}],
)
server = app.server  # WSGI entry point for Gunicorn

_kpi_label = {'margin': '0 0 6px', 'color': MUTED, 'fontSize': '12px', 'fontWeight': '600',
              'textTransform': 'uppercase', 'letterSpacing': '0.6px'}
_kpi_value = {'margin': 0, 'color': nte_violet, 'fontSize': '28px', 'fontWeight': '700'}


def _kpi(label, value_id):
    return html.Div([html.P(label, style=_kpi_label), html.H2(id=value_id, style=_kpi_value)],
                    className='tell-card tell-kpi')


def _block_head(title, subtitle=None, subtitle_id=None, info=None):
    """Section heading shown above a card (title + optional grey subtitle).

    `info` adds a mark beside the title that explains the chart on hover,
    for what does not fit in a subtitle.
    """
    sub = None
    if subtitle is not None or subtitle_id is not None:
        sub = html.P(subtitle, id=subtitle_id, className='tell-block-sub') if subtitle_id \
            else html.P(subtitle, className='tell-block-sub')
    heading = [html.H3(title, className='tell-block-title')]
    if info:
        heading.append(html.Span('i', className='tell-info', title=info,
                                 **{'data-note': info, 'aria-label': info, 'tabIndex': 0}))
    return html.Div([html.Div(heading, className='tell-block-title-row'), sub],
                    className='tell-block-head')


def _graph(graph_id, height, title, subtitle=None, config=None, info=None, **wrapper_style):
    """A titled block: heading above, dcc.Graph inside a white rounded card."""
    return html.Div([
        _block_head(title, subtitle, info=info),
        html.Div(
            dcc.Graph(id=graph_id, style={'height': height},
                      config={'displayModeBar': False, 'responsive': True, **(config or {})}),
            className='tell-card'),
    ], className='tell-block', style=wrapper_style)


def _options(values):
    return sorted([{'label': str(v), 'value': str(v)} for v in pd.unique(values.dropna())],
                  key=lambda o: o['label'].lower())


def split_tags(value):
    """'fashion, shop, fashion' -> ['fashion', 'shop'] - one entry per keyword."""
    if pd.isna(value):
        return []
    out = []
    for part in str(value).split(','):
        tag = ' '.join(part.split()).lower()
        if tag and tag not in out:
            out.append(tag)
    return out


# Split every company's keywords once at load: the filter, the dropdown and the
# table all work on the list, and re-splitting ~11k strings per callback adds up.
data['tag_list'] = data['tags'].map(split_tags)

# The merged keyword vocabulary runs to tens of thousands of terms, too many to
# ship to the browser as dropdown options. The dropdown instead asks the server
# for the terms matching what the user types, most-used first.
KEYWORD_OPTIONS_SHOWN = 100
TABLE_PAGE_SIZE = 10
# Tiers that say only that the classification failed. After
# db/reclassify_tiers.py there should be none left; any that appear are kept
# out of the table and the tier chart rather than shown as a bucket.
HIDDEN_TIERS = {'No match'}
# Same for the product category: a company whose category could not be decided
# is left out rather than shown under a label that means "we could not tell".
HIDDEN_CATEGORIES = {'No match'}


def visible_companies(df):
    """Drop companies whose tier or category could not be decided.

    The table, the two charts and the export all show the same set, so they all
    go through here.
    """
    return df[~df['Predicted_Tier'].isin(HIDDEN_TIERS)
              & ~df['Predicted_Category'].isin(HIDDEN_CATEGORIES)]
# Founding-year periods for the bar chart, oldest first.
YEAR_BINS = [-float('inf'), 1969, 1979, 1989, 1999, 2009, 2015, float('inf')]
YEAR_LABELS = ['Before 1970', '1970-1979', '1980-1989', '1990-1999',
               '2000-2009', '2010-2015', 'After 2015']


def _keyword_options(tag_lists, search=None, selected=None):
    """Keyword dropdown options among the companies in `tag_lists`.

    Only terms containing `search` are returned, capped at
    KEYWORD_OPTIONS_SHOWN; the counts tell the user how much each narrows the
    list. Selected keywords are always kept, or the dropdown could no longer
    display them.
    """
    counts = Counter(tag for tags in tag_lists for tag in tags)
    search = (search or '').strip().lower()
    matches = [(t, n) for t, n in counts.items() if search in t] if search \
        else list(counts.items())
    matches.sort(key=lambda kv: (-kv[1], kv[0]))
    options = [{'label': f'{t}  ({n:,})', 'value': t}
               for t, n in matches[:KEYWORD_OPTIONS_SHOWN]]
    shown = {o['value'] for o in options}
    for t in selected or ():
        if t not in shown:
            options.insert(0, {'label': f'{t}  ({counts.get(t, 0):,})', 'value': t})
    return options


def _company_options(companies, search=None, selected=None):
    """Company dropdown options among the rows of `companies`.

    With nothing typed, the largest companies; otherwise names containing the
    search, those starting with it first. Capped like the keyword options, and
    selected companies are always kept so the dropdown can still show them.
    """
    names = companies.sort_values('employees', ascending=False)['trade_name'].dropna()
    names = names.drop_duplicates()
    search = (search or '').strip().lower()
    if search:
        lowered = names.str.lower()
        names = pd.concat([names[lowered.str.startswith(search)],
                           names[lowered.str.contains(search, regex=False)
                                 & ~lowered.str.startswith(search)]])
    shown = names.head(KEYWORD_OPTIONS_SHOWN).tolist()
    for name in reversed(selected or []):
        if name not in shown:
            shown.insert(0, name)
    return [{'label': n, 'value': n} for n in shown]


def tag_chips(tags, selected=()):
    """Render a company's keywords as separate chips, all of them. Keywords the
    user is filtering on are pulled to the front and highlighted, so it is
    visible why a row matched; the cell scrolls when the list is long."""
    if not tags:
        return ''
    selected = {str(s).strip().lower() for s in (selected or ())}
    hits     = [t for t in tags if t in selected]
    rest     = [t for t in tags if t not in selected]
    chips = [
        f'<span class="tell-chip{" tell-chip-on" if t in selected else ""}">'
        f'{escape_html(t)}</span>'
        for t in hits + rest
    ]
    return f'<span class="tell-chips">{"".join(chips)}</span>'


# ── Company profile ───────────────────────────────────────────────────────────
# Clicking a company name opens a profile over the dashboard. The table rows
# carry most of what it shows; the rest (activities, postcode, what the scraper
# found on the website) is fetched per company on demand, so none of it slows
# the dashboard's own load.
_profile_cache = {}
_profile_engine = None


def load_profile_details(org_id):
    """Extra fields for one organization, {} when the database is unreachable.
    Successful lookups are cached; failures are not, so a hiccup can recover."""
    global _profile_engine
    if org_id in _profile_cache:
        return _profile_cache[org_id]
    try:
        from sqlalchemy import text
        if _profile_engine is None:
            _profile_engine = _get_engine()
        if _profile_engine is None:
            return {}
        with _profile_engine.connect() as conn:
            org = conn.execute(text(
                "SELECT main_activity, new_main, activity_2, activity_3, postcode "
                "FROM organizations WHERE id = :id"), {'id': org_id}).mappings().first()
            scrape = conn.execute(text(
                "SELECT s.`Website emails` AS emails, s.`Website languages` AS languages "
                "FROM scraping17092026 AS s JOIN organizations AS o ON o.website = s.website "
                "WHERE o.id = :id LIMIT 1"), {'id': org_id}).mappings().first()
    except Exception:
        return {}
    details = {**(org or {}), **(scrape or {})}
    _profile_cache[org_id] = details
    return details


def _present(value):
    """The value, or None when it is missing, NaN or blank."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _profile_stat(label, value):
    return html.Div([html.Span(label, className='tell-profile-stat-label'),
                     html.Span(value if value is not None else '—',
                               className='tell-profile-stat-value')],
                    className='tell-profile-stat')


def _profile_section(title, *children):
    return html.Div([html.H4(title, className='tell-profile-section-title'), *children],
                    className='tell-profile-section')


def company_profile(row):
    """Build the profile body for one row of `data`."""
    org_id  = _present(row.get('id'))
    details = load_profile_details(int(org_id)) if org_id is not None else {}

    website = _present(row.get('website'))
    href    = None
    if website:
        href = website if website.startswith(('http://', 'https://')) else f'https://{website}'
    place = ' · '.join(p for p in (
        ' '.join(p for p in (_present(details.get('postcode')), _present(row.get('city'))) if p),
        _present(row.get('region')),
    ) if p)

    employees = _present(row.get('employees'))
    founded   = _present(row.get('year_start'))
    surface   = _present(row.get('surface'))
    status    = _present(row.get('status'))

    head = html.Div([
        html.Div([
            html.H2(row.get('trade_name'), className='tell-profile-name'),
            html.Div([
                html.Span(status, className='tell-profile-status') if status else None,
                html.Span(place, className='tell-profile-place') if place else None,
                html.A(website, href=href, target='_blank', rel='noopener noreferrer',
                       className='tell-profile-link') if website else None,
            ], className='tell-profile-meta'),
        ]),
    ], className='tell-profile-head')

    stats = html.Div([
        _profile_stat('Employees', f'{int(employees):,}' if employees is not None else None),
        _profile_stat('Founded', str(int(founded)) if founded else None),
        _profile_stat('Legal form', _present(row.get('legal_form'))),
        _profile_stat('Surface', f'{int(surface):,} m²' if surface else None),
    ], className='tell-profile-stats')

    sections = []

    main_activity = _present(details.get('main_activity'))
    other = [a for a in (_present(details.get('activity_2')),
                         _present(details.get('activity_3'))) if a]
    if main_activity or other:
        lines = []
        if main_activity:
            sector = _present(details.get('new_main'))
            lines.append(html.P([html.Strong(main_activity),
                                 html.Span(f' · {sector}', className='tell-profile-muted') if sector else None]))
        if other:
            lines.append(html.P('Also: ' + ', '.join(other), className='tell-profile-muted'))
        sections.append(_profile_section('Activity', *lines))

    category, tier = _present(row.get('Predicted_Category')), _present(row.get('Predicted_Tier'))
    company_class = _present(row.get('company_class'))
    if category or tier or company_class:
        sections.append(_profile_section('Classification', html.Div([
            _profile_stat('Product category', category),
            _profile_stat('Supply chain tier', tier),
            _profile_stat('Company class', company_class),
        ], className='tell-profile-stats tell-profile-stats-2')))

    if org_id is not None:
        member_of = [_consortium_label(c) for c, ids in affiliations.items() if org_id in ids]
        if member_of:
            sections.append(_profile_section('Consortia', html.Div(
                [html.Span(m, className='tell-chip tell-chip-on') for m in member_of],
                className='tell-chips')))

    emails = [e.strip() for e in str(_present(details.get('emails')) or '').split(';') if e.strip()]
    languages = _present(details.get('languages'))
    if emails or languages:
        contact = []
        if emails:
            contact.append(html.Div([html.A(e, href=f'mailto:{e}', className='tell-profile-email')
                                     for e in emails], className='tell-profile-emails'))
        if languages:
            contact.append(html.P(f'Website languages: {languages}', className='tell-profile-muted'))
        sections.append(_profile_section('Contacts found on the website', *contact))

    tags = row.get('tag_list') or []
    if tags:
        sections.append(_profile_section(f'Keywords ({len(tags)})', html.Div(
            [html.Span(t, className='tell-chip') for t in tags],
            className='tell-chips tell-profile-keywords')))

    return [head, stats, *sections]


def _filter_col(label, dd_id, placeholder, values, options=None):
    return html.Div([
        html.Label(label),
        dcc.Dropdown(id=dd_id, options=_options(values) if options is None else options,
                     value=None, placeholder=placeholder, multi=True),
    ], className='tell-filter-col',
       style={'flex': '1', 'minWidth': '0', 'padding': '10px'})


app.layout = html.Div([

    # ── Filters: region / city / company (above the map) ──────────────────────
    html.Div([
        _filter_col("Filter by Region:",  'region-dropdown',  "Select a region...",  data['region']),
        _filter_col("Filter by City:",    'city-dropdown',    "Select a city...",    data['city']),
        html.Div([
            html.Label("Filter by Consortium:"),
            dcc.Dropdown(id='consortium-dropdown', options=_consortium_options, value=None,
                         placeholder="Select a consortium...", multi=True),
        ], className='tell-filter-col', style={'flex': '1', 'minWidth': '0', 'padding': '10px'}),
    ], className='tell-filters tell-panel', style={'marginBottom': '20px'}),

    # ── KPI cards ─────────────────────────────────────────────────────────────
    html.Div([
        _kpi('Active Businesses',       'kpi-active'),
        _kpi('Registered Websites',     'kpi-web'),
        _kpi('Public Website Contacts', 'kpi-contacts'),
    ], className='tell-kpis', style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),

    html.Div(id='city-filter-label', style={'minHeight': '22px', 'marginBottom': '4px', 'fontSize': '13px', 'color': nte_violet, 'fontWeight': '600'}),

    # ── Map + Region chart ────────────────────────────────────────────────────
    html.Div([
        _graph('map-graph', '500px', 'Companies per City',
               'Bubble size shows the number of companies · click a bubble to filter by city',
               config={'scrollZoom': True}, flex='1.5', minWidth=0),
        _graph('region-chart', '500px', 'Companies per Region',
               'Number of companies in each province', flex='1.5', minWidth=0),
    ], className='tell-map-row', style={'display': 'flex', 'gap': '16px', 'marginBottom': '20px'}),
    # ── Filters: company / keywords (between map and table) ─────────────────
    html.Div([
        _filter_col("Filter by Company:",        'company-dropdown',  "Type to search a company...", data['trade_name'],
                    options=_company_options(data)),
        _filter_col("Filter by Keywords/Tags:",  'keywords-dropdown', "Type to search keywords...", data['tags'],
                    options=_keyword_options(data['tag_list'])),
    ], className='tell-filters tell-panel', style={'marginBottom': '20px'}),
    # ── Data table ────────────────────────────────────────────────────────────
    _block_head('Companies', subtitle_id='table-count'),
    html.Div([
        dash_table.DataTable(
            id='company-table',
            columns=[
                {'name': 'Company',            'id': 'trade_name'},
                {'name': 'Keywords',           'id': 'tags', 'presentation': 'markdown'},
                {'name': 'Employees',          'id': 'employees', 'type': 'numeric'},
            ],
            # Paged and sorted on the server (update_table): the browser only
            # ever holds the visible page.
            page_action='custom', page_current=0, page_size=TABLE_PAGE_SIZE,
            sort_action='custom', sort_mode='single', filter_action='none',
            # Biggest employers first, until the user sorts on another column.
            sort_by=[{'column_id': 'employees', 'direction': 'desc'}],
            style_as_list_view=True,
            markdown_options={'html': True},
            style_table={'overflowX': 'auto'},
            style_header={'backgroundColor': nte_violet, 'color': 'white', 'fontWeight': '600',
                          'fontSize': '11px', 'textTransform': 'uppercase', 'letterSpacing': '0.7px',
                          'padding': '14px 16px', 'border': 'none'},
            style_cell={'fontFamily': FONT_STACK, 'fontSize': '13px', 'color': INK,
                        'padding': '11px 16px', 'textAlign': 'left', 'backgroundColor': 'white',
                        'border': 'none', 'borderBottom': f'1px solid {GRID}',
                        'overflow': 'hidden', 'textOverflow': 'ellipsis', 'maxWidth': '260px'},
            style_cell_conditional=[
                # Company names open the profile, so they read as links.
                {'if': {'column_id': 'trade_name'}, 'fontWeight': '600', 'minWidth': '180px',
                 'color': nte_violet},
                # Chips need room to wrap, so this cell does not clip like the others.
                {'if': {'column_id': 'tags'}, 'color': MUTED, 'minWidth': '260px',
                 'maxWidth': '420px', 'whiteSpace': 'normal', 'overflow': 'visible',
                 'textOverflow': 'clip', 'padding': '8px 16px'},
                {'if': {'column_id': 'employees'},  'textAlign': 'right', 'width': '110px'},
            ],
            style_data={'cursor': 'pointer'},
            style_data_conditional=[
                {'if': {'state': 'active'},   'backgroundColor': '#efe9f6',
                 'border': 'none', 'borderBottom': f'1px solid {GRID}'},
                {'if': {'state': 'selected'}, 'backgroundColor': '#efe9f6',
                 'border': 'none', 'borderBottom': f'1px solid {GRID}'},
            ],
            css=[
                {'selector': 'tr:hover td.dash-cell', 'rule': 'background-color: #faf8fc !important;'},
                {'selector': 'td.dash-cell[data-dash-column="trade_name"]:hover',
                 'rule': 'text-decoration: underline;'},
                {'selector': '.column-header--sort', 'rule': 'color: rgba(255,255,255,0.55); margin-right: 6px;'},
                {'selector': 'th.dash-header:hover .column-header--sort', 'rule': 'color: #fff;'},
            ],
            active_cell=None,
        ),
    ], className='tell-card tell-table-card'),

    # ── Distribution charts ─────────────────────────────────────────────────
    html.Div([
        _graph('pie-category', '360px', 'Product Category',
               'Click a slice or legend entry to filter',
               flex='1 1 300px', minWidth='300px'),
        _graph('tier-bar',     '360px', 'Supply Chain Tier',
               'Click a bar or its name to filter',
               info='"No tier" covers organizations that support the sector without '
                    'sitting in the supply chain: industry associations, NGOs, cultural '
                    'organizations, museums, research & education, and other supporting '
                    'activities.',
               flex='1 1 300px', minWidth='300px'),
        _graph('year-bar',     '360px', 'Founding Year',    'Companies founded per period',
               flex='1 1 300px', minWidth='300px'),
        _graph('pie-class',    '360px', 'Company Class',
               'Click a slice or legend entry to filter',
                info='The classification is based primarily on keywords, ' 
                'assigining organization to the "frontrunner" class when '
                'keywords related to circularity and digitalization are present. '
                'Website languages, employee count, and other factors are also considered. ' \
                'To learn more about the classification, please contact f.sollitto@hva.nl.',
               flex='1 1 300px', minWidth='300px'),
    ], className='tell-pies', style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '16px', 'marginTop': '20px'}),

    # ── Buttons ───────────────────────────────────────────────────────────────
    html.Div([
        html.A('✏️ Contribute (add/edit data)', href='/contribute/',
               style={'display': 'inline-block', 'padding': '10px 24px',
                      'backgroundColor': nte_violet, 'color': 'white',
                      'borderRadius': '6px', 'textDecoration': 'none',
                      'fontSize': '14px', 'fontWeight': '600'}),
        classification.get_button(),
        html.A('⬇ Export filtered data', id='export-filtered-link',
               href='', target='_blank',
               style={'display': 'inline-block', 'padding': '10px 24px',
                      'backgroundColor': '#217346', 'color': 'white',
                      'borderRadius': '6px', 'textDecoration': 'none',
                      'fontSize': '14px', 'fontWeight': '600'}),
    ], className='tell-buttons', style={'display': 'flex', 'gap': '12px', 'justifyContent': 'center',
              'marginTop': '32px', 'paddingBottom': '32px'}),

    classification.get_modal(),
    *classification.get_stores(),
    dcc.Store(id='selected-city', data=None),
    dcc.Store(id='chart-filters', data={}),

    # ── Company profile overlay ──────────────────────────────────────────────
    # The backdrop is a sibling of the dialog, not its parent: a click inside
    # the dialog would otherwise also count as a click on the backdrop.
    html.Div([
        html.Div(id='profile-backdrop', n_clicks=0, className='tell-profile-backdrop'),
        html.Div([
            html.Button('×', id='profile-close', n_clicks=0, className='tell-profile-close',
                        title='Close'),
            dcc.Loading(html.Div(id='profile-body'), type='dot', color=nte_violet),
        ], className='tell-profile', role='dialog'),
    ], id='profile-overlay', className='tell-profile-overlay', style={'display': 'none'}),

    # ── Usage tracking plumbing (hidden) ──────────────────────────────────────
    dcc.Store(id='session-id', storage_type='session'),
    dcc.Interval(id='trk-init', interval=400, max_intervals=1),
    dcc.Store(id='trk-session-sink'),
    dcc.Store(id='trk-filter-sink'),
    dcc.Store(id='trk-click-sink'),
    dcc.Store(id='axis-click-sink'),

], className='tell-app', style={'fontFamily': 'Inter, sans-serif', 'padding': '20px', 'maxWidth': '1400px', 'margin': 'auto'})


# ── Filter helper ─────────────────────────────────────────────────────────────
def filter_data(regions, companies, keywords=None, cities=None, consortiums=None):
    filtered = data.copy()
    if regions:
        filtered = filtered[filtered['region'].isin(regions)]
    if companies:
        filtered = filtered[filtered['trade_name'].isin(companies)]
    if cities:
        filtered = filtered[filtered['city'].isin(cities)]
    if keywords:
        # Match whole keywords, not substrings: 'wear' used to also pull in
        # 'workwear' and 'swimwear', and a keyword containing a regex character
        # used to raise. A company matches when it has any selected keyword.
        wanted   = {str(k).strip().lower() for k in keywords}
        filtered = filtered[filtered['tag_list'].map(
            lambda tags: not wanted.isdisjoint(tags))]
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
    Input('region-dropdown',    'value'),
    Input('city-dropdown',      'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
    Input('consortium-dropdown','value'),
)
def update_filter_options(regions, cities, companies, keywords, consortiums):
    region_opts   = _options(filter_data(None,    companies, keywords, cities, consortiums)['region'])
    city_opts     = _options(filter_data(regions, companies, keywords, None,   consortiums)['city'])
    return region_opts, city_opts


# Like the keywords, the ~11k company names are searched on the server instead
# of all being sent to the browser (~0.7 MB) on every filter change.
@app.callback(
    Output('company-dropdown',  'options'),
    Input('company-dropdown',   'search_value'),
    Input('region-dropdown',    'value'),
    Input('city-dropdown',      'value'),
    Input('keywords-dropdown',  'value'),
    Input('consortium-dropdown','value'),
    State('company-dropdown',   'value'),
)
def update_company_options(search, regions, cities, keywords, consortiums, companies):
    subset = filter_data(regions, None, keywords, cities, consortiums)
    return _company_options(subset, search, companies)


# The keyword dropdown has its own callback because it also follows what the
# user types: the server searches the full vocabulary and returns the top hits.
@app.callback(
    Output('keywords-dropdown', 'options'),
    Input('keywords-dropdown',  'search_value'),
    Input('region-dropdown',    'value'),
    Input('city-dropdown',      'value'),
    Input('company-dropdown',   'value'),
    Input('consortium-dropdown','value'),
    State('keywords-dropdown',  'value'),
)
def update_keyword_options(search, regions, cities, companies, consortiums, keywords):
    subset = filter_data(regions, companies, None, cities, consortiums)
    return _keyword_options(subset['tag_list'], search, keywords)


# ── Chart click callback ──────────────────────────────────────────────────────
# Each chart filters on its own column. Clicking a slice or bar selects it,
# clicking the same one again clears it, so the charts work like the dropdowns.
CHART_FILTERS = {
    'pie-category': 'Predicted_Category',
    'tier-bar':     'Predicted_Tier',
    'pie-class':    'company_class',
}
CHART_LABELS = {
    'Predicted_Category': 'Product category',
    'Predicted_Tier':     'Supply chain tier',
    'company_class':      'Company class',
}


def apply_chart_filters(df, chart_filters, skip=None):
    """Narrow `df` by the slices and bars clicked in the charts.

    `skip` leaves one column out, so a chart is never filtered by its own
    selection and keeps showing every slice the user can switch to.
    """
    for column, value in (chart_filters or {}).items():
        if value is None or column == skip or column not in df.columns:
            continue
        series = df[column].fillna('Unknown')
        df = df[series == value]
    return df


@app.callback(
    Output('chart-filters', 'data'),
    Input('pie-category', 'clickData'),
    Input('tier-bar',     'clickData'),
    Input('pie-class',    'clickData'),
    State('chart-filters', 'data'),
    prevent_initial_call=True,
)
def update_chart_filters(cat_click, tier_click, class_click, current):
    column = CHART_FILTERS.get(ctx.triggered_id)
    click  = {'pie-category': cat_click, 'tier-bar': tier_click,
              'pie-class': class_click}.get(ctx.triggered_id)
    if not column or not click:
        return dash.no_update
    label   = click['points'][0].get('label')
    current = dict(current or {})
    if label is None or current.get(column) == label:
        current.pop(column, None)          # same slice again clears the filter
    else:
        current[column] = label
    return current


# ── City click callback ───────────────────────────────────────────────────────
@app.callback(
    Output('selected-city', 'data'),
    Input('map-graph', 'clickData'),
    Input('company-table', 'active_cell'),
    State('selected-city', 'data'),
    prevent_initial_call=True,
)
def update_selected_city(click_data, active_cell, current_city):
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
    if triggered == 'company-table' and active_cell:
        # A click on the company name opens its profile instead.
        if active_cell.get('column_id') == 'trade_name':
            return dash.no_update
        row_id = active_cell.get('row_id')
        if row_id in data.index:
            city = data.at[row_id, 'city']
            return None if city == current_city else city
    # no_update rather than the same city: an unchanged value would still make
    # Dash re-run the whole dashboard (e.g. when the profile clears the cell).
    return dash.no_update


# ── Company profile callback ──────────────────────────────────────────────────
@app.callback(
    Output('profile-overlay', 'style'),
    Output('profile-body',    'children'),
    Output('company-table',   'active_cell'),
    Input('company-table',    'active_cell'),
    Input('profile-close',    'n_clicks'),
    Input('profile-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def toggle_profile(active_cell, _close, _backdrop):
    hidden = {'display': 'none'}
    if ctx.triggered_id != 'company-table':
        return hidden, dash.no_update, dash.no_update
    if not active_cell or active_cell.get('column_id') != 'trade_name':
        return dash.no_update, dash.no_update, dash.no_update
    row_id = active_cell.get('row_id')
    if row_id not in data.index:
        return dash.no_update, dash.no_update, dash.no_update
    # Clearing the active cell lets the same name be clicked again after the
    # profile is closed; the table only reports a click that changes it.
    return {'display': 'flex'}, company_profile(data.loc[row_id]), None


def select_companies(regions, companies, keywords, cities, consortiums,
                     selected_city, chart_filters):
    """The companies every view shows for the current filters.

    Returns (unsliced, filtered): `filtered` also applies the clicked donut
    slices; `unsliced` does not, for the donuts themselves (see
    apply_chart_filters).
    """
    unsliced = filter_data(regions, companies, keywords, cities, consortiums)
    if selected_city:
        unsliced = unsliced[unsliced['city'] == selected_city]
    return unsliced, apply_chart_filters(unsliced, chart_filters)


# ── Main dashboard callback ───────────────────────────────────────────────────
@app.callback(
    Output('kpi-active',        'children'),
    Output('kpi-web',           'children'),
    Output('kpi-contacts',      'children'),
    Output('map-graph',         'figure'),
    Output('region-chart',      'figure'),
    Output('pie-category',      'figure'),
    Output('tier-bar',          'figure'),
    Output('year-bar',          'figure'),
    Output('pie-class',         'figure'),
    Output('city-filter-label', 'children'),
    Input('region-dropdown',    'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
    Input('city-dropdown',      'value'),
    Input('consortium-dropdown','value'),
    Input('selected-city',      'data'),
    Input('chart-filters',      'data'),
)
def update_dashboard(selected_regions, selected_companies, selected_keywords,
                     selected_cities, selected_consortiums, selected_city,
                     chart_filters):
    unsliced, filtered = select_companies(selected_regions, selected_companies,
                                          selected_keywords, selected_cities,
                                          selected_consortiums, selected_city, chart_filters)

    kpi_active = f"{(filtered['status'].str.lower() == 'active').sum():,}"
    kpi_web    = f"{filtered['website'].notna().sum():,}"
    # Companies whose public website gave up at least one e-mail address.
    # The Excel fallback has no scrape data, hence the guard.
    kpi_contacts = (f"{int(filtered['has_contact'].sum()):,}"
                    if 'has_contact' in filtered.columns else '—')

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
        cmap = {'selected': 'rgba(220,80,0,0.85)', 'default': 'rgba(81,55,115,0.22)'}
    else:
        city_geo['_sel'] = 'all'
        cmap = {'all': 'rgba(81,55,115,0.50)'}

    map_fig = px.scatter_map(
        city_geo, lat='lat', lon='lon', size='disp',
        color='_sel', color_discrete_map=cmap,
        hover_name='city',
        hover_data={'count': True, 'disp': False, 'lat': False, 'lon': False, '_sel': False},
        custom_data=['count', 'city'],   # city must stay last: the click callbacks read customdata[-1]
        zoom=6,
        center={'lat': 52.3, 'lon': 5.3},
        size_max=40,
    )
    map_fig.update_traces(hovertemplate='<b>%{hovertext}</b><br>%{customdata[0]:,} companies<extra></extra>')
    map_fig.update_layout(
        map={'style': OSM_HTTPS_STYLE},
        margin={'r': 0, 't': 0, 'l': 0, 'b': 0}, showlegend=False,
    )

    region_counts = filtered.groupby('region', as_index=False).size().rename(columns={'size': 'count'})
    region_fig    = px.bar(
        region_counts.sort_values('count'), x='count', y='region',
        orientation='h',
        color_discrete_sequence=[nte_violet], text='count',
    )
    region_fig.update_traces(texttemplate='%{x:,}', textposition='outside', cliponaxis=False,
                             textfont={'color': MUTED, 'size': 11},
                             hovertemplate='<b>%{y}</b><br>%{x:,} companies<extra></extra>')
    region_fig.update_xaxes(title=None, showgrid=True)
    region_fig.update_yaxes(title=None, showgrid=False, tickfont={'color': INK, 'size': 12})
    region_fig.update_layout(bargap=0.35, margin={'l': 16, 'r': 40, 't': 16, 'b': 16})

    # A donut is not narrowed by its own slice, so the other slices stay
    # visible and clickable; the selected one is pulled out of the ring.
    _cat_src = apply_chart_filters(unsliced, chart_filters, skip='Predicted_Category')
    _cat  = (_cat_src.loc[~_cat_src['Predicted_Category'].isin(HIDDEN_CATEGORIES),
                          'Predicted_Category'].fillna('Unknown').value_counts().reset_index())
    _cat.columns = ['Predicted_Category', 'count']
    pie_cat = _donut(_cat, 'Predicted_Category', VIOLET_RAMP,
                     selected=(chart_filters or {}).get('Predicted_Category'))

    _tier_src = apply_chart_filters(unsliced, chart_filters, skip='Predicted_Tier')
    _tier = (_tier_src.loc[~_tier_src['Predicted_Tier'].isin(HIDDEN_TIERS), 'Predicted_Tier']
             .value_counts().reset_index())
    _tier.columns = ['Predicted_Tier', 'count']
    tier_bar = _hbar(_tier, 'Predicted_Tier', nte_darkblue,
                     selected=(chart_filters or {}).get('Predicted_Tier'))

    # Grouped into periods: one bar per year left a long tail of single
    # companies and a spike at the recent years, which reads as noise.
    _year = pd.to_numeric(filtered.get('year_start'), errors='coerce').dropna().astype(int)
    _period = pd.cut(_year, bins=YEAR_BINS, labels=YEAR_LABELS, right=True)
    _year_counts = (_period.value_counts().reindex(YEAR_LABELS, fill_value=0)
                    .rename_axis('period').reset_index(name='count'))
    year_bar = px.bar(_year_counts, x='period', y='count',
                      color_discrete_sequence=[nte_darkblue])
    year_bar.update_traces(marker_cornerradius=0,
                           hovertemplate='<b>%{x}</b><br>%{y:,} companies founded<extra></extra>')
    year_bar.update_xaxes(title=None, showgrid=False)
    year_bar.update_yaxes(title=None)
    year_bar.update_layout(bargap=0.1, margin={'t': 20, 'b': 20, 'l': 16, 'r': 16})

    _class_src = apply_chart_filters(unsliced, chart_filters, skip='company_class')
    _class = (_class_src['company_class'] if 'company_class' in _class_src.columns
              else pd.Series(dtype=object))
    _class = _class.fillna('Unclassified').replace('', 'Unclassified').value_counts().reset_index()
    _class.columns = ['company_class', 'count']
    pie_class = _donut(_class, 'company_class', VIOLET_RAMP,
                       selected=(chart_filters or {}).get('company_class'))

    # One line for every filter that was set by clicking a chart rather than a
    # dropdown, since those have no visible control to read the value off.
    active = []
    if selected_city:
        active.append(f"City: {selected_city}")
    for column, value in (chart_filters or {}).items():
        if value is not None:
            active.append(f"{CHART_LABELS.get(column, column)}: {value}")
    city_label = (" · ".join(active) + " — click the same slice or bubble again to clear"
                  if active else "")
    return (kpi_active, kpi_web, kpi_contacts, map_fig, region_fig,
            pie_cat, tier_bar, year_bar, pie_class, city_label)


# ── Companies table callback ──────────────────────────────────────────────────
# The table is paged and sorted here on the server, and only the visible page
# goes to the browser. Sending every row made each update ~7.5 MB with the
# keyword chips, and any callback that read the rows back (the city click did)
# was refused by Nginx's 1 MB request limit.
TABLE_SORT_KEYS = {
    'trade_name': lambda s: s.astype(str).str.lower(),
    'employees':  None,
    'tags':       lambda s: s.map(len),      # sorted by number of keywords
}
DEFAULT_SORT = [{'column_id': 'employees', 'direction': 'desc'}]


@app.callback(
    Output('company-table', 'data'),
    Output('company-table', 'page_count'),
    Output('company-table', 'page_current'),
    Output('table-count',   'children'),
    Input('region-dropdown',    'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
    Input('city-dropdown',      'value'),
    Input('consortium-dropdown','value'),
    Input('selected-city',      'data'),
    Input('chart-filters',      'data'),
    Input('company-table',      'page_current'),
    Input('company-table',      'sort_by'),
)
def update_table(selected_regions, selected_companies, selected_keywords,
                 selected_cities, selected_consortiums, selected_city,
                 chart_filters, page_current, sort_by):
    _, filtered = select_companies(selected_regions, selected_companies, selected_keywords,
                                   selected_cities, selected_consortiums, selected_city,
                                   chart_filters)
    filtered = visible_companies(filtered)
    # Only a page change keeps the page; a new filter or sort starts again at 1.
    if 'company-table.page_current' not in ctx.triggered_prop_ids:
        page_current = 0
    page_current = page_current or 0

    sort = (sort_by or DEFAULT_SORT)[0]
    column = sort['column_id'] if sort['column_id'] in TABLE_SORT_KEYS else 'employees'
    sort_col = 'tag_list' if column == 'tags' else column
    ordered = filtered.sort_values(sort_col, ascending=sort['direction'] == 'asc',
                                   key=TABLE_SORT_KEYS[column], kind='stable')

    size  = TABLE_PAGE_SIZE
    pages = max(1, -(-len(ordered) // size))
    page_current = min(page_current, pages - 1)
    page = ordered.iloc[page_current * size:(page_current + 1) * size]

    records = [{
        # 'id' becomes the row id the table reports on click: the row's index
        # in `data`, so a click finds the right company on any page or sort.
        'id':         int(idx),
        'trade_name': row['trade_name'],
        'tags':       tag_chips(row['tag_list'], selected_keywords),
        'employees':  int(row['employees']),
    } for idx, row in page.iterrows()]

    table_count = (f"{len(ordered):,} results · click a company name for its profile, "
                   f"or another cell to show its city on the map")
    return records, pages, page_current, table_count


# ── Export the filtered companies ─────────────────────────────────────────────
# A real URL rather than a dcc.Download blob: the dashboard is served inside an
# iframe, where blob downloads are blocked. The filters travel in the link, so
# the file always matches what the page is showing when it is clicked.
EXPORT_COLUMNS = [
    ('trade_name', 'Company'),
    ('city', 'City'),
    ('region', 'Region'),
    ('website', 'Website'),
    ('employees', 'Employees'),
    ('surface', 'Surface (m2)'),
    ('year_start', 'Founded'),
    ('legal_form', 'Legal form'),
    ('status', 'Status'),
    ('Predicted_Category', 'Product category'),
    ('Predicted_Tier', 'Supply chain tier'),
    ('company_class', 'Company class'),
    ('website_emails', 'Email contacts'),
    ('tags', 'Keywords'),
]


def _export_state(regions, companies, keywords, cities, consortiums,
                  selected_city, chart_filters):
    """Pack the current filters into something that fits in a URL."""
    state = {'regions': regions, 'companies': companies, 'keywords': keywords,
             'cities': cities, 'consortiums': consortiums,
             'city': selected_city, 'charts': chart_filters}
    packed = json.dumps({k: v for k, v in state.items() if v}, separators=(',', ':'))
    return base64.urlsafe_b64encode(packed.encode()).decode()


@app.callback(
    Output('export-filtered-link', 'href'),
    Output('export-filtered-link', 'children'),
    Input('region-dropdown',    'value'),
    Input('company-dropdown',   'value'),
    Input('keywords-dropdown',  'value'),
    Input('city-dropdown',      'value'),
    Input('consortium-dropdown','value'),
    Input('selected-city',      'data'),
    Input('chart-filters',      'data'),
)
def update_export_link(regions, companies, keywords, cities, consortiums,
                       selected_city, chart_filters):
    _, filtered = select_companies(regions, companies, keywords, cities,
                                   consortiums, selected_city, chart_filters)
    filtered = visible_companies(filtered)
    href = app.get_relative_path('/export/companies.xlsx') + '?f=' + _export_state(
        regions, companies, keywords, cities, consortiums, selected_city, chart_filters)
    return href, f'⬇ Export filtered data ({len(filtered):,})'


@server.route(app.config.requests_pathname_prefix + 'export/companies.xlsx')
def export_filtered_companies():
    """Send the filtered companies as an Excel file, keywords and classes included."""
    import io
    from flask import request, send_file

    try:
        packed = request.args.get('f', '')
        state = json.loads(base64.urlsafe_b64decode(packed).decode()) if packed else {}
    except Exception:
        state = {}          # an unreadable link exports everything rather than failing

    _, filtered = select_companies(
        state.get('regions'), state.get('companies'), state.get('keywords'),
        state.get('cities'), state.get('consortiums'), state.get('city'),
        state.get('charts'))
    filtered = visible_companies(filtered)

    columns = [(col, label) for col, label in EXPORT_COLUMNS if col in filtered.columns]
    export = filtered[[col for col, _ in columns]].rename(columns=dict(columns))
    if 'Email contacts' in export.columns:
        # The scraper separates addresses with '; '; keep that in the cell.
        export['Email contacts'] = export['Email contacts'].fillna('')

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        export.to_excel(writer, index=False, sheet_name='Companies')
    buffer.seek(0)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(buffer, as_attachment=True,
                     download_name=f'tell_companies_{stamp}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.'
                              'spreadsheetml.sheet')


# ── Clickable category labels ─────────────────────────────────────────────────
# Plotly raises no event for an axis tick label, so a click on one is forwarded
# to its graph as an ordinary point click carrying that label. The chart's
# callback (update_chart_filters) then cannot tell the two apart.
#
# The listener sits on the document: Plotly redraws the axis on every update,
# which would drop a listener attached to the labels themselves. It is
# installed once, from the interval that already runs at start-up.
app.clientside_callback(
    """
    function(n) {
        if (window.__tellAxisClick) { return window.dash_clientside.no_update; }
        window.__tellAxisClick = true;
        // Graphs whose category labels filter: the bar chart's axis labels and
        // the donuts' legend entries.
        var CLICKABLE = ['tier-bar', 'pie-category', 'pie-class'];
        var LABELS = '.yaxislayer-above text, .legend text';
        // The column each chart filters on; mirrors CHART_FILTERS in Python.
        var COLUMN = {'tier-bar': 'Predicted_Tier',
                      'pie-category': 'Predicted_Category',
                      'pie-class': 'company_class'};

        // Plotly covers its plot with an overlay that swallows pointer events,
        // so a click never comes FROM the label: find the label the pointer is
        // over by position instead.
        function labelAt(event) {
            var graph = event.target.closest('.js-plotly-plot');
            var holder = graph && graph.closest('[id]');
            if (!holder || CLICKABLE.indexOf(holder.id) === -1) { return null; }
            var labels = graph.querySelectorAll(LABELS);
            for (var i = 0; i < labels.length; i++) {
                var box = labels[i].getBoundingClientRect();
                if (event.clientX >= box.left - 4 && event.clientX <= box.right + 4 &&
                    event.clientY >= box.top - 2 && event.clientY <= box.bottom + 2) {
                    var node = labels[i];
                    // Plotly keeps the full text here; textContent can be cut short.
                    var value = node.getAttribute('data-unformatted') || node.textContent;
                    return value ? {graph: graph, value: value} : null;
                }
            }
            return null;
        }

        document.addEventListener('click', function(event) {
            var hit = labelAt(event);
            if (hit) { hit.graph.emit('plotly_click', {points: [{label: hit.value}]}); }
        });
        document.addEventListener('mousemove', function(event) {
            var graph = event.target.closest('.js-plotly-plot');
            var holder = graph && graph.closest('[id]');
            if (!holder || CLICKABLE.indexOf(holder.id) === -1) { return; }
            graph.style.cursor = labelAt(event) ? 'pointer' : '';
        });

        // The label of the category being filtered on is shown in bold. Plotly
        // styles a legend as a whole, and putting <b> in the label would change
        // the value a click reports, so the weight is set on the drawn text.
        // Plotly rewrites that text on every redraw, hence the observer.
        window.__tellBoldLabels = function() {
            var filters = window.__tellChartFilters || {};
            CLICKABLE.forEach(function(id) {
                var holder = document.getElementById(id);
                if (!holder) { return; }
                var selected = filters[COLUMN[id]];
                holder.querySelectorAll(LABELS).forEach(function(node) {
                    var value = node.getAttribute('data-unformatted') || node.textContent;
                    node.style.fontWeight = (selected && value === selected) ? '700' : '';
                });
            });
        };
        var pending = false;
        new MutationObserver(function() {
            if (pending) { return; }
            pending = true;
            requestAnimationFrame(function() { pending = false; window.__tellBoldLabels(); });
        }).observe(document.body, {childList: true, subtree: true});
        return window.dash_clientside.no_update;
    }
    """,
    Output('axis-click-sink', 'data'),
    Input('trk-init', 'n_intervals'),
)


# Hand the current selection to the code above, which draws that category's
# label in bold wherever it appears.
app.clientside_callback(
    """
    function(filters) {
        window.__tellChartFilters = filters || {};
        if (window.__tellBoldLabels) { window.__tellBoldLabels(); }
        return window.dash_clientside.no_update;
    }
    """,
    Output('axis-click-sink', 'data', allow_duplicate=True),
    Input('chart-filters', 'data'),
    prevent_initial_call=True,
)


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
        // This runs twice per page load - once on render, once when the
        // interval fires - and each store update logs a session_start. Only
        // the first run reports anything.
        if (window.__sid === sid) {
            return window.dash_clientside.no_update;
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
    Input('keywords-dropdown', 'value'),
    Input('consortium-dropdown','value'),
    State('session-id', 'data'),
    prevent_initial_call=True,
)
def track_filters(regions, cities, companies, keywords, consortiums, session_id):
    """Record every filter dropdown change."""
    log_event(session_id, 'filter_change', {
        'changed': ctx.triggered_id,
        'filters': {
            'region': regions, 'city': cities, 'company': companies,
            'keywords': keywords, 'consortium': consortiums,
        },
    })
    return dash.no_update


@app.callback(
    Output('trk-click-sink', 'data'),
    Input('map-graph', 'clickData'),
    Input('company-table', 'active_cell'),
    State('session-id', 'data'),
    prevent_initial_call=True,
)
def track_clicks(click_data, active_cell, session_id):
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
    elif trig == 'company-table' and active_cell:
        row_id = active_cell.get('row_id')
        log_event(session_id, 'table_click', {
            'column': active_cell.get('column_id'),
            'company': data.at[row_id, 'trade_name'] if row_id in data.index else None,
        })
    return dash.no_update


classification.register_callbacks(app, filter_data, engine_fn=_get_engine)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8050, debug=False)
