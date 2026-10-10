from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/students_upload", "students_upload", ("GET", "POST")),
    RouteSpec("/students", "students"),
    RouteSpec("/students_duplicates", "students_duplicates"),
    RouteSpec("/students_list", "students_list"),
    RouteSpec("/students/expelled", "expelled_students"),
    RouteSpec("/api/students/migration-calendar", "students_migration_calendar"),
    RouteSpec("/students/download", "download_students"),
    RouteSpec("/students/expelled/download", "download_expelled_students"),
    RouteSpec("/api/student-transfer/groups", "student_transfer_campaign_groups"),
    RouteSpec("/edit_student/<username>", "edit_student", ("GET", "POST")),
    RouteSpec("/edit_student/<username>/transfer_group", "transfer_student_group", ("POST",)),
    RouteSpec("/student_transfer_orders/<int:transfer_id>/download", "download_student_transfer_order"),
    RouteSpec("/delete_student", "delete_student", ("POST",)),
)


def build(handlers):
    return create_blueprint("students", ROUTES, handlers)
