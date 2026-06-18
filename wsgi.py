from werkzeug.middleware.dispatcher import DispatcherMiddleware

import textile_companies_NL as main_app
import contribution_form as contrib_app

application = DispatcherMiddleware(
    main_app.server,
    {'/contribute': contrib_app.server}
)
