from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/file_work", "file_work", ("GET", "POST"), {"section": None}),
    RouteSpec("/file_work/<section>", "file_work", ("GET", "POST")),
    RouteSpec("/file_work/abiturients/import-result", "download_abiturients_import_result"),
    RouteSpec("/enrollment_order_upload", "enrollment_order_upload", ("POST",)),
    RouteSpec(
        "/enrollment_order_preview/fix_candidate_fio",
        "fix_enrollment_candidate_fio_from_order",
        ("POST",),
    ),
    RouteSpec("/enrollment_order_uploads/<int:upload_id>", "enrollment_order_upload_detail"),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/student_roster",
        "enrollment_order_student_roster",
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/student_roster/issues/<issue_code>",
        "enrollment_order_student_roster_issue",
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/student_roster/issues/<issue_code>/download",
        "download_enrollment_order_student_roster_issue",
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/student_roster/fio_review",
        "review_enrollment_order_student_roster_fio",
        ("POST",),
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/student_roster/download",
        "download_enrollment_order_student_roster",
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/download", "download_enrollment_order_upload"
    ),
    RouteSpec(
        "/enrollment_order_uploads/<int:upload_id>/delete",
        "delete_enrollment_order_upload_route",
        ("POST",),
    ),
    RouteSpec("/enrollment_order_template/download", "download_enrollment_order_template"),
    RouteSpec("/abiturients_updates_upload", "abiturients_updates_upload", ("POST",)),
    RouteSpec("/abiturients_email_source_upload", "abiturients_email_source_upload", ("POST",)),
    RouteSpec("/abiturients_updates_template/download", "download_abiturients_updates_template"),
)


def build(handlers):
    return create_blueprint("files", ROUTES, handlers)
