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

from dash import dcc, html, Input, Output, State, ctx, ALL
from dash.exceptions import PreventUpdate
import pandas as pd

# ── Brand colours (keep in sync with textile_companies_NL.py) ─────────────────
NTE_VIOLET   = '#513773'
NTE_DARKBLUE = '#54639E'

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
                "Define classes and keywords (comma-separated). "
                "Each company is matched against its trade name and tags.",
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
            html.Div(id='classif-result-area', style={'marginTop': '14px'}),

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
    """Return the dcc.Store and dcc.Download components needed by the modal."""
    return [
        dcc.Store(id='classif-rows-store', data=[{'name': '', 'keywords': ''}]),
        dcc.Store(id='classif-result-store', data=None),
        dcc.Download(id='classif-download'),
        # Dummy target for the clientside scroll callback (kept out of view).
        html.Div(id='classif-scroll-anchor', style={'display': 'none'}),
    ]


# ── Callback registration ─────────────────────────────────────────────────────

def register_callbacks(app, filter_data_fn):
    """
    Register all classification callbacks on *app*.

    Parameters
    ----------
    app : dash.Dash
        The application instance.
    filter_data_fn : callable
        ``filter_data(regions, companies) -> pd.DataFrame``
        from the main module, used to apply the current dashboard filters.
    """

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
        Output('classif-result-store', 'data'),
        Input('run-classif-btn', 'n_clicks'),
        State({'type': 'class-name',     'index': ALL}, 'value'),
        State({'type': 'class-keywords', 'index': ALL}, 'value'),
        State('region-dropdown',  'value'),
        State('company-dropdown', 'value'),
        prevent_initial_call=True,
    )
    def run_classification(_, names, keywords_list, selected_regions, selected_companies):
        classes = [
            (name, [kw.strip().lower() for kw in kws.split(',') if kw.strip()])
            for name, kws in zip(names or [], keywords_list or [])
            if name and kws
        ]
        if not classes:
            return (html.P("⚠ Define at least one class with keywords.",
                           style={'color': '#e67e22', 'fontSize': '13px'}), None)

        filtered = filter_data_fn(selected_regions, selected_companies)
        counts = {name: 0 for name, _ in classes}
        counts['Unclassified'] = 0
        per_row_data = []

        for _, row in filtered.iterrows():
            text = (f"{row.get('trade name', '') or ''} "
                    f"{row.get('tags', '') or ''}").lower()
            assigned = 'Unclassified'
            matched_kws = ''
            for class_name, kws in classes:
                hitting = [kw for kw in kws if kw in text]
                if hitting:
                    assigned = class_name
                    matched_kws = ', '.join(hitting)
                    break
            counts[assigned] += 1
            per_row_data.append({
                'Company Name':    row.get('trade name', ''),
                'Keywords Matched': matched_kws,
                'Predicted Class': assigned,
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
            html.Div([
                html.Button("⬇ Download Excel", id='download-classif-btn', n_clicks=0,
                            style={
                                'marginTop': '10px', 'padding': '7px 18px',
                                'backgroundColor': '#217346', 'color': 'white',
                                'border': 'none', 'borderRadius': '6px',
                                'cursor': 'pointer', 'fontSize': '13px', 'fontWeight': '600',
                            }),
            ], style={'display': 'flex', 'justifyContent': 'flex-end'}),
        ], style={'backgroundColor': '#f8f9fa', 'borderRadius': '8px',
                  'padding': '12px', 'border': '1px solid #eee'})

        return summary, per_row_data

    # ── Excel download ────────────────────────────────────────────────────────
    @app.callback(
        Output('classif-download', 'data'),
        Input('download-classif-btn', 'n_clicks'),
        State('classif-result-store', 'data'),
        prevent_initial_call=True,
    )
    def download_classification(_, store_data):
        if not store_data:
            raise PreventUpdate
        df = pd.DataFrame(store_data,
                          columns=['Company Name', 'Keywords Matched', 'Predicted Class'])
        return dcc.send_data_frame(
            df.to_excel, 'classification_results.xlsx', index=False, sheet_name='Results'
        )
