"""Navigation flags so templates show only the menu items a user's role can open."""


def nav_permissions(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from analytics.views import ANALYTICS_VIEWERS
    from console.views import SETTINGS_EDITORS
    from risks.views import RISK_VIEWERS
    from usage.views import USAGE_STAFF, USAGE_VIEWERS

    unread = user.notifications.filter(read_at__isnull=True)
    return {
        "nav_unread_count": unread.count(),
        "nav_recent_notifications": user.notifications.all()[:6],
        "nav_can_record_usage": user.has_role(*USAGE_STAFF),
        "nav_can_view_usage": user.has_role(*USAGE_VIEWERS),
        "nav_can_view_analytics": user.has_role(*ANALYTICS_VIEWERS),
        "nav_can_view_risks": user.has_role(*RISK_VIEWERS),
        "nav_can_edit_settings": user.has_role(*SETTINGS_EDITORS),
    }
