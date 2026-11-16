from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "FaciliTrack Administration"
admin.site.site_title = "FaciliTrack Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("audit/", include("audit.urls")),
    path("facilities/", include("facilities.urls")),
    path("scheduling/", include("scheduling.urls")),
    path("usage/", include("usage.urls")),
    path("analytics/", include("analytics.urls")),
    path("risks/", include("risks.urls")),
    path("notifications/", include("notifications.urls")),
    path("reports/", include("reports.urls")),
    path("console/", include("console.urls")),
    path("", include("core.urls")),
]
