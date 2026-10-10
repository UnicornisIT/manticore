from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/abiturients", "abiturients"),
    RouteSpec("/duplicates", "duplicates", ("GET", "POST")),
    RouteSpec("/abiturients/bulk", "bulk_abiturients", ("POST",)),
    RouteSpec("/duplicates_abiturients", "duplicates_abiturients", ("GET", "POST")),
    RouteSpec("/delete_abiturient", "delete_abiturient", ("POST",)),
    RouteSpec("/abiturients/withdraw-documents", "withdraw_abiturient_documents", ("POST",)),
    RouteSpec("/toggle_abiturient_paid", "toggle_abiturient_paid", ("POST",)),
    RouteSpec("/abiturients/download", "download_abiturients"),
    RouteSpec("/download_template", "download_template"),
    RouteSpec("/login_conflicts", "login_conflicts"),
    RouteSpec("/edit_conflict/<int:conflict_id>", "edit_conflict", ("GET", "POST")),
    RouteSpec("/delete_conflict/<int:conflict_id>", "delete_conflict", ("POST",)),
    RouteSpec("/manual_create", "manual_create", ("GET", "POST")),
    RouteSpec("/edit_abiturient/<login>", "edit_abiturient", ("GET", "POST")),
)


def build(handlers):
    return create_blueprint("abiturients", ROUTES, handlers)
