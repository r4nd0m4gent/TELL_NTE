"""
TELL Multi-Perspective Application

This is the main entry point for the Textile Ecosystem Living Lab (TELL) application.
It provides a multi-page interface with three different perspectives:
1. Dashboard - The original comprehensive dashboard (collapsible)
2. Map Explorer - Geographic-focused view
3. Data Explorer - Advanced data filtering

The application uses Dash with multi-page support.
"""

import os
import sys
import dash
from dash import dcc, html, Input, Output, State, callback_context as ctx

# ── Colors and Styles ────────────────────────────────────────────────────────
NTE_VIOLET = '#513773'
NTE_DARKBLUE = '#54639E'
FONT_STACK = 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
INK = '#2c2c2c'
MUTED = '#6b6b76'
GRID = '#eeecf2'

# Initialize the Dash app
app = dash.Dash(
    __name__,
    external_stylesheets=[
        'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
    ],
    suppress_callback_exceptions=True,
    url_base_pathname='/',
    meta_tags=[{'name': 'viewport', 'content': 'width=device-width, initial-scale=1'}],
    assets_folder='assets',
)

server = app.server  # WSGI entry point

# Import the original dashboard app
import tell_app


# ── CSS Styles ───────────────────────────────────────────────────────────────
NAVBAR_STYLE = {
    'background-color': NTE_VIOLET,
    'padding': '10px 40px',
    'position': 'fixed',
    'top': '0',
    'left': '0',
    'right': '0',
    'z-index': '1000',
    'box-shadow': '0 2px 8px rgba(0,0,0,0.1)',
}

NAV_BRAND_STYLE = {
    'display': 'flex',
    'align-items': 'center',
    'gap': '16px',
    'color': 'white',
    'text-decoration': 'none',
    'font-weight': '600',
}

NAV_LINKS_STYLE = {
    'display': 'flex',
    'gap': '20px',
    'margin-left': 'auto',
    'align-items': 'center',
}

NAV_LINK_BASE_STYLE = {
    'color': 'white',
    'text-decoration': 'none',
    'padding': '10px 20px',
    'border-radius': '6px',
    'font-size': '14px',
    'font-weight': '600',
    'transition': 'all 0.2s',
}


def nav_link_style(pathname, link_path):
    """Get the style for a nav link based on current pathname."""
    style = NAV_LINK_BASE_STYLE.copy()
    if pathname and pathname.startswith(link_path):
        style['background-color'] = 'rgba(255,255,255,0.15)'
    return style


# ── Navigation Bar ──────────────────────────────────────────────────────────
def create_navbar(current_path='/'):
    """Create the navigation bar with perspective tabs."""
    return html.Nav(
        [
            # Logo/Brand
            html.A(
                [
                    html.Img(
                        src="https://newtexeco.nl/wp-content/uploads/2023/12/logo_2x.png",
                        height="40px",
                        style={'object-fit': 'contain'},
                    ),
                    html.Span(
                        "NewTexEco",
                        style={
                            'color': 'rgba(255,255,255,0.75)',
                            'font-size': '11px',
                            'font-weight': '600',
                            'letter-spacing': '1.5px',
                            'text-transform': 'uppercase',
                        },
                    ),
                ],
                href="/",
                style=NAV_BRAND_STYLE,
            ),
            # Navigation Links
            html.Div(
                [
                    html.A("📊 Dashboard", href="/dashboard", 
                          style=nav_link_style(current_path, '/dashboard')),
                    html.A("🗺️ Map Explorer", href="/map",
                          style=nav_link_style(current_path, '/map')),
                    html.A("🔍 Data Explorer", href="/explore",
                          style=nav_link_style(current_path, '/explore')),
                ],
                style=NAV_LINKS_STYLE,
            ),
        ],
        style=NAVBAR_STYLE,
    )


# ── Page Layouts ────────────────────────────────────────────────────────────

def create_home_page():
    """Create the home/landing page."""
    return html.Div(
        [
            html.Div(
                [
                    html.H1("Textile Ecosystem Living Lab",
                           style={'font-size': '2rem', 'font-weight': '700', 
                                  'color': NTE_VIOLET, 'line-height': '1.25', 
                                  'margin-bottom': '18px'}),
                    html.P(
                        "The TELL aims to improve the visibility of the companies operating in the "
                        "Textile, Clothing, Leather, and Footwear (TCLF) sector by showing geographic "
                        "distribution and market specializations through keywords (or tags). "
                        "Explore the data we gathered for more than 11,000 active companies across "
                        "the Netherlands using a dataset from Modint-KvK (2021).",
                        style={'font-size': '1rem', 'line-height': '1.75', 'color': '#444'},
                    ),
                    html.P(
                        "Choose a perspective to start exploring:",
                        style={'margin-top': '20px', 'font-size': '1.1rem', 'color': NTE_VIOLET, 
                               'font-weight': '600'},
                    ),
                    # Quick navigation cards
                    html.Div(
                        [
                            html.A(
                                html.Div(
                                    [
                                        html.H4("📊 Dashboard", style={'color': NTE_VIOLET, 'margin-bottom': '10px'}),
                                        html.P("Comprehensive overview with charts, maps, and statistics"),
                                    ],
                                    style={'text-align': 'center', 'padding': '20px'},
                                ),
                                href="/dashboard",
                                style={'text-decoration': 'none', 'margin': '10px', 
                                       'border': f'2px solid {NTE_VIOLET}', 'cursor': 'pointer',
                                       'border-radius': '8px', 'display': 'block'},
                            ),
                            html.A(
                                html.Div(
                                    [
                                        html.H4("🗺️ Map Explorer", style={'color': NTE_VIOLET, 'margin-bottom': '10px'}),
                                        html.P("Geographic-focused view with interactive maps"),
                                    ],
                                    style={'text-align': 'center', 'padding': '20px'},
                                ),
                                href="/map",
                                style={'text-decoration': 'none', 'margin': '10px',
                                       'border': f'2px solid {NTE_VIOLET}', 'cursor': 'pointer',
                                       'border-radius': '8px', 'display': 'block'},
                            ),
                            html.A(
                                html.Div(
                                    [
                                        html.H4("🔍 Data Explorer", style={'color': NTE_VIOLET, 'margin-bottom': '10px'}),
                                        html.P("Advanced filtering and data exploration"),
                                    ],
                                    style={'text-align': 'center', 'padding': '20px'},
                                ),
                                href="/explore",
                                style={'text-decoration': 'none', 'margin': '10px',
                                       'border': f'2px solid {NTE_VIOLET}', 'cursor': 'pointer',
                                       'border-radius': '8px', 'display': 'block'},
                            ),
                        ],
                        style={'display': 'flex', 'justify-content': 'center', 'gap': '20px', 
                               'margin-top': '30px', 'flex-wrap': 'wrap'},
                    ),
                ],
                style={'max-width': '860px', 'margin': '0 auto', 'padding': '52px 40px 36px'},
            ),
        ],
        className='home-page',
    )


def create_dashboard_page():
    """Create the Dashboard perspective with collapsible sections."""
    return html.Div(
        [
            # Dashboard header with collapse control
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("📊 Dashboard Overview", 
                                   style={'margin': '0', 'color': NTE_VIOLET, 
                                          'display': 'inline-block', 'font-size': '18px'}),
                            html.Button(
                                "⌄ Collapse",
                                id="toggle-dashboard",
                                n_clicks=0,
                                style={'margin-left': 'auto', 'background-color': NTE_VIOLET, 
                                       'color': 'white', 'border': 'none', 'border-radius': '6px',
                                       'padding': '8px 16px', 'cursor': 'pointer', 'font-size': '12px',
                                       'font-weight': '600'},
                            ),
                        ],
                        style={'display': 'flex', 'align-items': 'center', 'width': '100%',
                               'padding': '10px 20px', 'background-color': '#f8f9fa',
                               'border-bottom': f'1px solid {GRID}', 'border-radius': '8px 8px 0 0'},
                    ),
                    html.Div(
                        [
                            # Description
                            html.P(
                                "Comprehensive overview with charts, maps, and statistics. "
                                "Click on chart elements to filter the data.",
                                style={'margin': '20px', 'color': MUTED, 'font-size': '14px'},
                            ),
                            # Embed the original dashboard
                            html.Iframe(
                                src="/original-dashboard/",
                                style={
                                    'width': '100%',
                                    'height': '2000px',
                                    'border': 'none',
                                    'background': 'white',
                                    'border-radius': '0 0 8px 8px',
                                },
                                loading="lazy",
                                id='dashboard-iframe',
                            ),
                        ],
                        id="collapse-dashboard",
                        style={'border': f'1px solid {GRID}', 'border-top': 'none',
                               'border-radius': '0 0 8px 8px', 'overflow': 'hidden'},
                    ),
                ],
                style={'margin-bottom': '20px', 'box-shadow': '0 1px 2px rgba(30, 20, 50, 0.04)'},
            ),
        ],
        className='perspective-page',
    )


def create_map_page():
    """Create the Map Explorer perspective."""
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        html.H3("🗺️ Map Explorer", 
                               style={'margin': '0', 'color': NTE_VIOLET, 'font-size': '18px'}),
                        style={'padding': '15px 20px', 'background-color': '#f8f9fa',
                               'border-bottom': f'1px solid {GRID}', 'border-radius': '8px 8px 0 0'},
                    ),
                    html.Div(
                        [
                            html.P(
                                "Interactive geographic visualization of companies across the Netherlands. "
                                "This perspective will be enhanced in future updates.",
                                style={'margin': '20px', 'color': MUTED, 'font-size': '14px'},
                            ),
                            # For now, embed the dashboard
                            html.Iframe(
                                src="/original-dashboard/",
                                style={
                                    'width': '100%',
                                    'height': '1500px',
                                    'border': 'none',
                                    'border-radius': '0 0 8px 8px',
                                },
                                loading="lazy",
                            ),
                        ],
                        style={'border': f'1px solid {GRID}', 'border-top': 'none',
                               'border-radius': '0 0 8px 8px'},
                    ),
                ],
                style={'box-shadow': '0 1px 2px rgba(30, 20, 50, 0.04)'},
            ),
        ],
        className='perspective-page',
    )


def create_explore_page():
    """Create the Data Explorer perspective."""
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        html.H3("🔍 Data Explorer", 
                               style={'margin': '0', 'color': NTE_VIOLET, 'font-size': '18px'}),
                        style={'padding': '15px 20px', 'background-color': '#f8f9fa',
                               'border-bottom': f'1px solid {GRID}', 'border-radius': '8px 8px 0 0'},
                    ),
                    html.Div(
                        [
                            html.P(
                                "Advanced filtering and data exploration tools. "
                                "This perspective will be enhanced in future updates.",
                                style={'margin': '20px', 'color': MUTED, 'font-size': '14px'},
                            ),
                            # For now, embed the dashboard
                            html.Iframe(
                                src="/original-dashboard/",
                                style={
                                    'width': '100%',
                                    'height': '2000px',
                                    'border': 'none',
                                    'border-radius': '0 0 8px 8px',
                                },
                                loading="lazy",
                            ),
                        ],
                        style={'border': f'1px solid {GRID}', 'border-top': 'none',
                               'border-radius': '0 0 8px 8px'},
                    ),
                ],
                style={'box-shadow': '0 1px 2px rgba(30, 20, 50, 0.04)'},
            ),
        ],
        className='perspective-page',
    )


# ── Main App Layout ────────────────────────────────────────────────────────
app.layout = html.Div(
    [
        # Store the current perspective
        dcc.Location(id='url', refresh=False),
        dcc.Store(id='current-perspective', data='home'),
        dcc.Store(id='navbar-path', data='/'),
        
        # Navigation bar - will be updated based on URL
        html.Div(id='navbar'),
        
        # Page content container
        html.Div(id='page-content', style={'padding': '20px', 'padding-top': '80px'}),
        
        # Footer
        html.Footer(
            [
                html.P(
                    [
                        html.A("newtexeco.nl", href="https://newtexeco.nl", target="_blank",
                              style={'color': NTE_DARKBLUE, 'text-decoration': 'none'}),
                        " · Textile Ecosystem Living Lab &copy; 2026 NewTexEco",
                    ],
                    style={'text-align': 'center', 'padding': '24px 40px', 'font-size': '12px',
                           'color': '#999', 'border-top': f'1px solid {GRID}', 'margin-top': '40px'},
                ),
            ],
        ),
    ],
    style={'font-family': FONT_STACK, 'min-height': '100vh', 'display': 'flex', 
           'flex-direction': 'column', 'background-color': '#f7f7f5'},
)


# ── Callbacks ───────────────────────────────────────────────────────────────

@app.callback(
    [Output('navbar', 'children'),
     Output('page-content', 'children')],
    Input('url', 'pathname'),
)
def display_page(pathname):
    """Route to the appropriate page based on the URL and update navbar."""
    if pathname is None:
        pathname = '/'
    
    navbar = create_navbar(pathname)
    
    if pathname == '/' or pathname == '':
        page_content = create_home_page()
    elif pathname.startswith('/dashboard'):
        page_content = create_dashboard_page()
    elif pathname.startswith('/map'):
        page_content = create_map_page()
    elif pathname.startswith('/explore'):
        page_content = create_explore_page()
    else:
        # 404 or redirect to home
        page_content = create_home_page()
    
    return navbar, page_content


@app.callback(
    [Output('collapse-dashboard', 'style'),
     Output('toggle-dashboard', 'children')],
    Input('toggle-dashboard', 'n_clicks'),
    State('collapse-dashboard', 'style'),
    prevent_initial_call=True,
)
def toggle_dashboard(n_clicks, current_style):
    """Toggle the dashboard collapse state."""
    if n_clicks:
        # Check if currently collapsed
        is_collapsed = (current_style and 
                       (current_style.get('display') == 'none' or 
                        current_style.get('max-height') == '0px'))
        
        if is_collapsed:
            # Expand
            new_style = {**current_style, 
                        'display': 'block', 
                        'max-height': '2000px',
                        'overflow': 'visible'}
            button_text = "⌄ Collapse"
        else:
            # Collapse
            new_style = {**current_style,
                        'display': 'block',
                        'max-height': '0px',
                        'overflow': 'hidden'}
            button_text = "⌃ Expand"
        return new_style, button_text
    return current_style, current_style.get('children', '⌄ Collapse')


# ── Run the app ────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8050, debug=True)
