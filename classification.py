"""
classification.py
─────────────────
Self-contained module for the keyword-based ad-hoc classification feature.

Usage in the main app
─────────────────────
    import classification

    # In layout, include:
    #   classification.get_button()      ← the "Generate Classification" button
    #   classification.get_modal()       ← modal overlay + stores + download

    # After defining filter_data(), register callbacks:
    #   classification.register_callbacks(app, filter_data)
"""

import os
import json
import threading
import datetime

from dash import dcc, html, Input, Output, State, ctx, ALL
from dash.exceptions import PreventUpdate
import pandas as pd

# ── Brand colours (keep in sync with textile_companies_NL.py) ─────────────────
NTE_VIOLET   = '#513773'
NTE_DARKBLUE = '#54639E'

# ── Exported result files ──────────────────────────────────────────────────
# Each run's results are written to disk here so they persist server-side; the
# bare filename (which fits `classifications.file_path` VARCHAR(45)) is what we
# store in the DB. Override the location with the CLASSIF_EXPORT_DIR env var.
_EXPORT_DIR = os.environ.get(
    'CLASSIF_EXPORT_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'classifications'),
)


def _save_export(df, filename):
    """Write *df* to ``_EXPORT_DIR/filename`` and return the full path (or None)."""
    try:
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        path = os.path.join(_EXPORT_DIR, filename)
        df.to_excel(path, index=False, sheet_name='Results')
        return path
    except Exception:
        return None


# ── Input tracking ─────────────────────────────────────────────────────────
# Every "Run Classification" stores one row in the `classifications` table:
# the classes + keywords the user defined (as JSON), the request date, and the
# name of the Excel file offered for download. Writes run on a background thread
# so they never block the UI, and any failure is swallowed so tracking can't
# break the feature. The DB engine is supplied by the host app via
# ``register_callbacks(..., engine_fn=...)``.
_CLASSIF_DDL = (
    "CREATE TABLE IF NOT EXISTS classifications ("
    " id_class INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
    " class_keywords JSON,"
    " time_request DATE,"
    " file_path VARCHAR(45)"
    ")"
)
# The table may pre-exist with `id_class` not set to AUTO_INCREMENT; ensure it
# is so inserts don't need to supply a primary key.
_CLASSIF_MIGRATIONS = (
    "ALTER TABLE classifications MODIFY id_class INT NOT NULL AUTO_INCREMENT",
)

_engine_fn = None
_classif_engine = None
_classif_schema_ready = False
_classif_lock = threading.Lock()


def _get_classif_engine():
    """Reuse a single engine for classification-input writes."""
    global _classif_engine
    if _classif_engine is None and _engine_fn is not None:
        _classif_engine = _engine_fn()
    return _classif_engine


def _ensure_classif_schema(engine):
    """Create the table if missing and make sure id_class auto-increments."""
    global _classif_schema_ready
    if _classif_schema_ready:
        return
    with _classif_lock:
        if _classif_schema_ready:
            return
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text(_CLASSIF_DDL))
        for mig in _CLASSIF_MIGRATIONS:
            try:
                with engine.begin() as conn:
                    conn.execute(text(mig))
            except Exception:
                pass
        _classif_schema_ready = True


def _write_classification(class_keywords, file_path):
    """Insert one run row. Runs on a worker thread; errors are ignored."""
    try:
        from sqlalchemy import text
        engine = _get_classif_engine()
        if engine is None:
            return
        _ensure_classif_schema(engine)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO classifications (class_keywords, time_request, file_path)"
                     " VALUES (:kw, CURDATE(), :fp)"),
                {'kw': json.dumps(class_keywords, default=str), 'fp': file_path},
            )
    except Exception:
        pass


def log_classification(class_keywords, file_path):
    """Fire-and-forget write of the user's class/keyword input (non-blocking)."""
    threading.Thread(target=_write_classification,
                     args=(class_keywords, file_path),
                     daemon=True).start()

# ── Semantic model cache ──────────────────────────────────────────────────
# The sentence-embedding model is expensive to load, so keep a single instance
# for the lifetime of the process and only rebuild the (cheap) class centroids
# on each run.
_EMBED_MODEL = None


def _build_semantic_classifier(classes):
    """Return a ``SemanticClassifier`` for *classes* (list of ``(name, [keywords])``).

    ``confidence_threshold=0`` makes every company fall into its closest class,
    so the whole dataset is classified (no "unknown").
    """
    global _EMBED_MODEL
    from semantic_classifier import SemanticClassifier, ClassDefinition

    if _EMBED_MODEL is None:
        clf = SemanticClassifier(confidence_threshold=0.0)
        _EMBED_MODEL = clf.model
    else:
        clf = SemanticClassifier(confidence_threshold=0.0, model=_EMBED_MODEL)

    clf.add_classes([ClassDefinition(name=name, keywords=keywords)
                     for name, keywords in classes])
    clf.build()
    return clf
# ── Modal style helpers ───────────────────────────────────────────────────────
_MODAL_HIDDEN = {
    'display': 'none', 'position': 'fixed', 'inset': '0',
    'backgroundColor': 'rgba(0,0,0,0.45)', 'zIndex': '1000',
    'justifyContent': 'center', 'alignItems': 'flex-start',
    'paddingTop': '24px', 'overflowY': 'auto',
}
_MODAL_SHOWN = {**_MODAL_HIDDEN, 'display': 'flex'}


# ── Public layout helpers ─────────────────────────────────────────────────────

def get_button():
    """Return the 'Generate Classification' toolbar button."""
    return html.Button(
        '🏷  Generate Classification',
        id='open-classif-btn',
        n_clicks=0,
        style={
            'padding': '10px 24px',
            'backgroundColor': NTE_DARKBLUE,
            'color': 'white',
            'borderRadius': '6px',
            'border': 'none',
            'fontSize': '14px',
            'fontWeight': '600',
            'cursor': 'pointer',
        },
    )


def get_modal():
    """Return the classification modal overlay + supporting dcc.Store / dcc.Download components."""
    _inp_col_style = {
        'flex': '1', 'fontWeight': '600', 'fontSize': '11px',
        'color': '#aaa', 'textTransform': 'uppercase', 'letterSpacing': '0.5px',
    }
    return html.Div([
        html.Div([
            # ── Header row ──────────────────────────────────────────────────
            html.Div([
                html.H3("Generate Classification",
                        style={'margin': '0', 'color': '#2c3e50', 'fontSize': '18px'}),
                html.Button("×", id='close-classif-btn', n_clicks=0, style={
                    'background': 'none', 'border': 'none', 'fontSize': '24px',
                    'cursor': 'pointer', 'color': '#7f8c8d', 'lineHeight': '1', 'padding': '0',
                }),
            ], style={'display': 'flex', 'justifyContent': 'space-between',
                      'alignItems': 'center', 'marginBottom': '12px'}),

            html.P(
                "Define classes with example keywords (comma-separated). "
                "Every company is assigned to its closest class using semantic "
                "(sentence-embedding) similarity on its trade name and tags.",
                style={'color': '#7f8c8d', 'fontSize': '13px',
                       'marginBottom': '16px', 'marginTop': '0'},
            ),

            # ── Column headers ───────────────────────────────────────────────
            html.Div([
                html.Span("Class name", style={**_inp_col_style, 'flex': '1'}),
                html.Span("Keywords",   style={**_inp_col_style, 'flex': '2'}),
                html.Span("", style={'width': '34px'}),
            ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '6px',
                      'paddingBottom': '6px', 'borderBottom': '1px solid #eee'}),

            # ── Dynamic class rows ───────────────────────────────────────────
            html.Div(id='class-rows-container'),

            html.Button("+ Add class", id='add-class-btn', n_clicks=0, style={
                'marginTop': '10px', 'padding': '6px 14px', 'backgroundColor': 'transparent',
                'border': f'1px dashed {NTE_DARKBLUE}', 'color': NTE_DARKBLUE,
                'borderRadius': '6px', 'cursor': 'pointer', 'fontSize': '13px', 'fontWeight': '600',
            }),

            # ── Results area ─────────────────────────────────────────────────
            dcc.Loading(
                id='classif-loading',
                type='dot',
                color=NTE_VIOLET,
                children=html.Div(id='classif-result-area', style={'marginTop': '14px'}),
            ),

            # ── Download (revealed after a successful run) ───────────────────
            html.Div([
                html.A("⬇ Download Excel", id='download-classif-link',
                       href='', target='_blank',
                       style={
                           'padding': '7px 18px',
                           'backgroundColor': '#217346', 'color': 'white',
                           'border': 'none', 'borderRadius': '6px',
                           'cursor': 'pointer', 'fontSize': '13px', 'fontWeight': '600',
                           'textDecoration': 'none', 'display': 'inline-block',
                       }),
            ], id='download-classif-wrap',
               style={'display': 'none', 'justifyContent': 'flex-end', 'marginTop': '10px'}),

            # ── Run button ───────────────────────────────────────────────────
            html.Div([
                html.Button("Run Classification", id='run-classif-btn', n_clicks=0, style={
                    'padding': '9px 22px', 'backgroundColor': NTE_VIOLET, 'color': 'white',
                    'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer',
                    'fontSize': '14px', 'fontWeight': '600',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-end', 'marginTop': '20px'}),

        ], style={
            'backgroundColor': 'white', 'borderRadius': '12px', 'padding': '28px',
            'width': '640px', 'maxWidth': '92vw', 'maxHeight': '80vh', 'overflowY': 'auto',
            'boxShadow': '0 8px 32px rgba(0,0,0,0.2)',
        }),
    ], id='classif-modal-overlay', style=_MODAL_HIDDEN)


def get_stores():
    """Return the dcc.Store components needed by the modal."""
    return [
        dcc.Store(id='classif-rows-store', data=[{'name': '', 'keywords': ''}]),
        # Dummy target for the clientside scroll callback (kept out of view).
        html.Div(id='classif-scroll-anchor', style={'display': 'none'}),
    ]


# ── Callback registration ─────────────────────────────────────────────────────

def register_callbacks(app, filter_data_fn, engine_fn=None):
    """
    Register all classification callbacks on *app*.

    Parameters
    ----------
    app : dash.Dash
        The application instance.
    filter_data_fn : callable
        ``filter_data(regions, companies) -> pd.DataFrame``
        from the main module, used to apply the current dashboard filters.
    engine_fn : callable, optional
        ``() -> sqlalchemy.Engine | None`` used to persist the class/keyword
        input a user runs to the ``classifications`` table. If omitted, no
        input tracking is performed.
    """
    global _engine_fn
    _engine_fn = engine_fn

    # Serve saved classification exports as a direct download. Routing through a
    # real URL (rather than a dcc.Download blob) is reliable even when the
    # dashboard is embedded in an iframe, where blob downloads can be blocked.
    _prefix = app.config.requests_pathname_prefix or '/'

    def _serve_classif_export(filename):
        from flask import send_from_directory, abort
        safe = os.path.basename(filename)
        if not safe.endswith('.xlsx') or not os.path.exists(os.path.join(_EXPORT_DIR, safe)):
            abort(404)
        return send_from_directory(_EXPORT_DIR, safe, as_attachment=True)

    app.server.add_url_rule(_prefix + 'exports/<path:filename>',
                            endpoint='classif_export',
                            view_func=_serve_classif_export)

    @app.callback(
        Output('classif-modal-overlay', 'style'),
        Input('open-classif-btn', 'n_clicks'),
        Input('close-classif-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def toggle_classif_modal(open_n, close_n):
        return _MODAL_SHOWN if ctx.triggered_id == 'open-classif-btn' else _MODAL_HIDDEN

    # ── Bring the modal into view when it opens ───────────────────────────────
    # The dashboard is usually embedded in an iframe; a position:fixed modal
    # lands at the centre of the tall iframe, which can be far from the button.
    # Ask the host page to scroll the iframe into view, and scroll the overlay
    # into view for the standalone (non-embedded) case.
    app.clientside_callback(
        """
        function(openN) {
            if (openN) {
                try { window.parent.postMessage({tellFocus: true}, '*'); } catch (e) {}
                var el = document.getElementById('classif-modal-overlay');
                if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
            }
            return '';
        }
        """,
        Output('classif-scroll-anchor', 'children'),
        Input('open-classif-btn', 'n_clicks'),
        prevent_initial_call=True,
    )

    # ── Add a class row ───────────────────────────────────────────────────────
    @app.callback(
        Output('classif-rows-store', 'data'),
        Input('add-class-btn', 'n_clicks'),
        State({'type': 'class-name',     'index': ALL}, 'value'),
        State({'type': 'class-keywords', 'index': ALL}, 'value'),
        prevent_initial_call=True,
    )
    def add_class_row(_, names, keywords):
        rows = [{'name': n or '', 'keywords': k or ''}
                for n, k in zip(names or [], keywords or [])]
        rows.append({'name': '', 'keywords': ''})
        return rows

    # ── Remove a class row ────────────────────────────────────────────────────
    @app.callback(
        Output('classif-rows-store', 'data', allow_duplicate=True),
        Input({'type': 'remove-class-btn', 'index': ALL}, 'n_clicks'),
        State({'type': 'class-name',       'index': ALL}, 'value'),
        State({'type': 'class-keywords',   'index': ALL}, 'value'),
        prevent_initial_call=True,
    )
    def remove_class_row(n_clicks_list, names, keywords):
        if not any(n for n in (n_clicks_list or []) if n):
            raise PreventUpdate
        triggered = ctx.triggered_id
        if (not triggered or not isinstance(triggered, dict)
                or triggered.get('type') != 'remove-class-btn'):
            raise PreventUpdate
        rows = [{'name': n or '', 'keywords': k or ''}
                for n, k in zip(names or [], keywords or [])]
        if len(rows) > 1:
            rows.pop(triggered['index'])
        return rows

    # ── Render class rows from store ──────────────────────────────────────────
    @app.callback(
        Output('class-rows-container', 'children'),
        Input('classif-rows-store', 'data'),
    )
    def render_class_rows(rows):
        rows = rows or [{'name': '', 'keywords': ''}]
        _inp = {'padding': '7px 10px', 'border': '1px solid #ddd',
                'borderRadius': '6px', 'fontSize': '13px', 'width': '100%'}
        result = []
        for i, row in enumerate(rows):
            result.append(html.Div([
                dcc.Input(
                    id={'type': 'class-name', 'index': i},
                    value=row.get('name', ''),
                    placeholder='e.g. Recycler',
                    style={**_inp, 'flex': '1'},
                ),
                dcc.Input(
                    id={'type': 'class-keywords', 'index': i},
                    value=row.get('keywords', ''),
                    placeholder='e.g. recycle, upcycle, waste',
                    style={**_inp, 'flex': '2'},
                ),
                html.Button('✕', id={'type': 'remove-class-btn', 'index': i}, n_clicks=0, style={
                    'width': '32px', 'height': '34px', 'border': '1px solid #fcc',
                    'borderRadius': '6px', 'backgroundColor': '#fff5f5', 'color': '#c00',
                    'cursor': 'pointer', 'fontSize': '13px', 'flexShrink': '0',
                }),
            ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '8px',
                      'alignItems': 'center'}))
        return result

    # ── Run classification ────────────────────────────────────────────────────
    @app.callback(
        Output('classif-result-area', 'children'),
        Output('download-classif-wrap', 'style'),
        Output('download-classif-link', 'href'),
        Input('run-classif-btn', 'n_clicks'),
        State({'type': 'class-name',     'index': ALL}, 'value'),
        State({'type': 'class-keywords', 'index': ALL}, 'value'),
        State('region-dropdown',  'value'),
        State('company-dropdown', 'value'),
        prevent_initial_call=True,
    )
    def run_classification(_, names, keywords_list, selected_regions, selected_companies):
        _hidden = {'display': 'none'}
        _shown = {'display': 'flex', 'justifyContent': 'flex-end', 'marginTop': '10px'}
        classes = [
            (name.strip(), [kw.strip() for kw in (kws or '').split(',') if kw.strip()])
            for name, kws in zip(names or [], keywords_list or [])
            if name and name.strip()
        ]
        if not classes:
            return (html.P("⚠ Define at least one class (a name, plus optional keywords).",
                           style={'color': '#e67e22', 'fontSize': '13px'}), _hidden, '')

        filtered = filter_data_fn(selected_regions, selected_companies)
        # The DB-backed data uses `trade_name`; the Excel fallback uses `trade name`.
        name_col = 'trade_name' if 'trade_name' in filtered.columns else 'trade name'
        texts = [
            f"{row.get(name_col, '') or ''} {row.get('tags', '') or ''}".strip()
            for _, row in filtered.iterrows()
        ]

        try:
            clf = _build_semantic_classifier(classes)
            results = clf.classify_batch(texts) if texts else []
        except Exception as exc:  # model download / embedding failure
            return (html.P(f"⚠ Semantic model unavailable: {exc}",
                           style={'color': '#c0392b', 'fontSize': '13px'}), _hidden, '')

        counts = {name: 0 for name, _ in classes}
        per_row_data = []
        for (_, row), result in zip(filtered.iterrows(), results):
            assigned = result.label  # threshold 0 → always the closest class
            counts[assigned] = counts.get(assigned, 0) + 1
            per_row_data.append({
                'Company Name':    row.get(name_col, ''),
                'Tags':            row.get('tags', ''),
                'Predicted Class': assigned,
                'Confidence':      round(result.score, 3),
            })

        total = len(filtered)
        result_rows = []
        for class_name, count in counts.items():
            pct = f"{100 * count / total:.1f}" if total > 0 else '0.0'
            result_rows.append(html.Div([
                html.Span(class_name,
                          style={'fontWeight': '600', 'flex': '1', 'fontSize': '13px'}),
                html.Span(f"{count} companies ({pct}%)",
                          style={'color': '#555', 'fontSize': '13px'}),
            ], style={'display': 'flex', 'padding': '5px 0',
                      'borderBottom': '1px solid #f0f0f0'}))

        summary = html.Div([
            html.P(f"Results — {total} companies analysed:",
                   style={'fontWeight': '600', 'marginBottom': '8px',
                          'marginTop': '0', 'fontSize': '13px'}),
            *result_rows,
        ], style={'backgroundColor': '#f8f9fa', 'borderRadius': '8px',
                  'padding': '12px', 'border': '1px solid #eee'})

        # Unique filename per run; also stored in the DB as `file_path`.
        filename = f"classification_{datetime.datetime.now():%Y%m%d_%H%M%S}.xlsx"
        # Save the results to disk (server-side) so they persist beyond the session.
        export_df = pd.DataFrame(
            per_row_data,
            columns=['Company Name', 'Tags', 'Predicted Class', 'Confidence'],
        )
        _save_export(export_df, filename)
        # Persist the run (classes + keywords as JSON, date, filename) — best-effort.
        class_keywords = [{'name': name, 'keywords': kws} for name, kws in classes]
        log_classification(class_keywords, filename)

        # Direct download URL to the saved file (served by the Flask route above).
        href = app.get_relative_path('/exports/' + filename)
        return summary, _shown, href
