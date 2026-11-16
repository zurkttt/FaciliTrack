"""Navigation: builds the sidebar for the signed-in user's role and marks the current page."""
from fnmatch import fnmatch

from django.urls import reverse


def _item(label, icon, url_name, *patterns, query=""):
    return {"label": label, "icon": icon, "url": reverse(url_name) + query, "patterns": patterns or (url_name,)}


def nav_permissions(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from analytics.views import ANALYTICS_VIEWERS
    from console.views import SETTINGS_EDITORS
    from risks.views import RISK_VIEWERS
    from usage.views import USAGE_STAFF, USAGE_VIEWERS

    flags = {
        "nav_can_record_usage": user.has_role(*USAGE_STAFF),
        "nav_can_view_usage": user.has_role(*USAGE_VIEWERS),
        "nav_can_view_analytics": user.has_role(*ANALYTICS_VIEWERS),
        "nav_can_view_risks": user.has_role(*RISK_VIEWERS),
        "nav_can_edit_settings": user.has_role(*SETTINGS_EDITORS),
    }

    sections = [("", [_item("Dashboard", "grid-1x2", "core:dashboard")])]
    sections.append(("Facilities", [
        _item("Facilities", "building", "facilities:list", "facilities:*"),
        _item("Schedules", "calendar3", "scheduling:schedule_list", "scheduling:schedule_*", "scheduling:activity_*"),
        _item("Reservations", "calendar-check", "scheduling:reservation_list",
              "scheduling:reservation_*", "scheduling:availability", "scheduling:facility_calendar"),
    ]))
    if flags["nav_can_view_usage"]:
        monitoring = []
        if flags["nav_can_record_usage"]:
            monitoring.append(_item("Attendance", "clipboard-check", "usage:attendance", "usage:attendance*"))
        monitoring += [
            _item("Usage Records", "list-ul", "usage:record_list", "usage:record_*", "usage:import*"),
            _item("Scheduled vs Actual", "bar-chart", "usage:comparison"),
        ]
        if flags["nav_can_record_usage"]:
            monitoring.append(_item("Room QR Codes", "qr-code", "usage:qr_codes"))
        sections.append(("Monitoring", monitoring))
    analytics = []
    if flags["nav_can_view_analytics"]:
        analytics.append(_item("Utilization", "graph-up-arrow", "analytics:dashboard", "analytics:dashboard"))
    if flags["nav_can_view_risks"]:
        analytics.append(_item("Risks", "shield-exclamation", "risks:register", "risks:*"))
    if flags["nav_can_view_analytics"]:
        analytics.append(_item("Reports", "file-earmark-bar-graph", "reports:index", "reports:*"))
    if analytics:
        sections.append(("Analytics", analytics))
    admin = []
    if user.is_system_admin:
        admin += [
            _item("System Status", "heart-pulse", "console:status"),
            _item("Users", "people", "console:user_list", "console:user_*"),
            _item("Departments", "diagram-3", "console:departments", "console:department*"),
        ]
    if flags["nav_can_edit_settings"]:
        admin += [
            _item("Thresholds", "sliders", "console:settings"),
            _item("Metric Definitions", "rulers", "analytics:definitions", "analytics:definitions", "analytics:activate_definition"),
        ]
    if user.is_system_admin:
        admin.append(_item("Audit Log", "journal-text", "audit:list", "audit:*"))
    if admin:
        sections.append(("Administration", admin))

    view_name = request.resolver_match.view_name if getattr(request, "resolver_match", None) else ""
    active_label, active_section = "", ""
    for heading, items in sections:
        for item in items:
            item["active"] = any(fnmatch(view_name, p) for p in item["patterns"])
            if item["active"] and not active_label:
                active_label, active_section = item["label"], heading

    unread = user.notifications.filter(read_at__isnull=True)
    return {
        **flags,
        "nav_sections": sections,
        "nav_active_label": active_label,
        "nav_active_section": active_section,
        "nav_unread_count": unread.count(),
        "nav_recent_notifications": user.notifications.all()[:6],
    }
