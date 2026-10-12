from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/", "index", ("GET", "POST")),
    RouteSpec("/documentation", "documentation", provide_automatic_options=False),
    RouteSpec("/desktop_settings", "desktop_settings"),
    RouteSpec("/desktop/fallback-changes", "desktop_fallback_changes"),
    RouteSpec("/desktop/fallback-changes/export", "export_desktop_fallback_changes"),
    RouteSpec("/management", "management"),
    RouteSpec("/search", "search"),
    RouteSpec("/search_overlay", "search_overlay"),
    RouteSpec("/data_checks", "data_checks"),
    RouteSpec("/healthz", "healthz"),
    RouteSpec("/person/<kind>/<path:record_id>", "person_card"),
)


def build(handlers):
    return create_blueprint("management", ROUTES, handlers)
