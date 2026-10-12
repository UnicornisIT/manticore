from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/login", "login", ("GET", "POST")),
    RouteSpec("/change-password", "change_password", ("GET", "POST")),
    RouteSpec("/logout", "logout"),
    RouteSpec("/register", "register", ("GET", "POST")),
    RouteSpec("/setup", "login_generation_setup", ("GET", "POST")),
)


def build(handlers):
    return create_blueprint("auth", ROUTES, handlers)
