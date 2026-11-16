from django.urls import path

from . import views

app_name = "usage"

urlpatterns = [
    path("checkin/<str:code>/", views.checkin, name="checkin"),
    path("records/<int:pk>/end/", views.record_end_now, name="record_end_now"),
    path("records/<int:pk>/headcount/", views.record_update_headcount, name="record_update_headcount"),
    path("qr/", views.qr_codes, name="qr_codes"),
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/mark/", views.attendance_mark, name="attendance_mark"),
    path("records/", views.record_list, name="record_list"),
    path("records/add/", views.record_create, name="record_create"),
    path("records/<int:pk>/delete/", views.record_delete, name="record_delete"),
    path("import/", views.usage_import, name="import"),
    path("import/template.csv", views.usage_import_template, name="import_template"),
    path("comparison/", views.comparison, name="comparison"),
]
