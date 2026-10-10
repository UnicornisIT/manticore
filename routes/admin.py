from ._shared import RouteSpec, create_blueprint


ROUTES = (
    RouteSpec("/set_campaign", "set_campaign", ("POST",)),
    RouteSpec("/approve_users", "approve_users", ("GET", "POST")),
    RouteSpec("/admin_panel", "admin_panel"),
    RouteSpec("/management/administration", "administration_tools"),
    RouteSpec("/api/desktop/releases/windows", "desktop_release_api"),
    RouteSpec("/admin/desktop-release/status", "desktop_release_status_api"),
    RouteSpec("/admin/desktop-release/approve", "approve_desktop_release", ("POST",)),
    RouteSpec("/admin/app-update/status", "app_update_status_api"),
    RouteSpec("/admin/app-update/start", "start_app_update", ("POST",)),
    RouteSpec("/backups", "backups"),
    RouteSpec("/backups/download/<backup_name>", "download_backup"),
    RouteSpec("/backups/restore", "restore_backup", ("POST",)),
    RouteSpec("/audit_logs", "audit_logs"),
    RouteSpec("/campaigns", "campaigns", ("GET", "POST")),
    RouteSpec("/delete_user", "delete_user", ("POST",)),
    RouteSpec("/edit_user/<int:user_id>", "edit_user", ("GET", "POST")),
    RouteSpec("/add_user", "add_user", ("GET", "POST")),
    RouteSpec("/clear_abiturients", "clear_abiturients", ("POST",)),
    RouteSpec("/add_group", "add_group", ("GET", "POST")),
    RouteSpec("/groups_template/download", "download_groups_template"),
)


def build(handlers):
    return create_blueprint("admin", ROUTES, handlers)
