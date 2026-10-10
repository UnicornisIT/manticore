from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/enrollment_candidates/sync", "enrollment_candidates_sync", ("POST",)),
    RouteSpec("/abiturients_to_students", "abiturients_to_students", ("GET", "POST")),
    RouteSpec("/migration_wizard", "migration_wizard"),
)


def build(handlers):
    return create_blueprint("migration", ROUTES, handlers)
