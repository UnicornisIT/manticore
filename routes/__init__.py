"""Blueprint registration for the public Manticore route contract."""

from . import abiturients, admin, auth, files, management, migration, students
from ._shared import add_legacy_build_aliases


ROUTE_MODULES = (auth, abiturients, students, files, admin, migration, management)


def register_blueprints(app, handlers):
    for module in ROUTE_MODULES:
        app.register_blueprint(module.build(handlers))
        add_legacy_build_aliases(app, module.ROUTES)
