from werkzeug.middleware.dispatcher import DispatcherMiddleware

import textile_companies_NL as main_app
import tell_app as original_dashboard
import contribution_form as contrib_app

# Create a dispatcher that serves:
# - The new multi-page app at /
# - The original dashboard at /original-dashboard/
# - The contribution form at /contribute/

application = DispatcherMiddleware(
    main_app.server,
    {
        '/original-dashboard': original_dashboard.server,
        '/contribute': contrib_app.server
    }
)
