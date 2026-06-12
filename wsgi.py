# wsgi.py
# Single WSGI entry point for Gunicorn.
# Mounts the main dashboard at / and the contribution form at /contribute/
# so both are served from the same port 443.

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.exceptions import NotFound

import textile_companies_NL as main_app
import contribution_form as contrib_app

application = DispatcherMiddleware(
    main_app.server,          # handles everything except /contribute/
    {
        '/contribute': contrib_app.server,   # handles /contribute and /contribute/*
    }
)
