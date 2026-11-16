from django.urls import path

from . import views

app_name = "scheduling"

urlpatterns = [
    path("schedules/", views.ScheduleListView.as_view(), name="schedule_list"),
    path("schedules/add/", views.ScheduleCreateView.as_view(), name="schedule_create"),
    path("schedules/<int:pk>/edit/", views.ScheduleUpdateView.as_view(), name="schedule_update"),
    path("schedules/<int:pk>/delete/", views.schedule_delete, name="schedule_delete"),
    path("schedules/import/", views.schedule_import, name="schedule_import"),
    path("schedules/import/template.csv", views.schedule_import_template, name="schedule_import_template"),
    path("activities/add/", views.ActivityCreateView.as_view(), name="activity_create"),
    path("reservations/", views.ReservationListView.as_view(), name="reservation_list"),
    path("reservations/new/", views.reservation_create, name="reservation_create"),
    path("reservations/<int:pk>/", views.reservation_detail, name="reservation_detail"),
    path("reservations/<int:pk>/approve/", views.reservation_approve, name="reservation_approve"),
    path("reservations/<int:pk>/reject/", views.reservation_reject, name="reservation_reject"),
    path("reservations/<int:pk>/cancel/", views.reservation_cancel, name="reservation_cancel"),
    path("find-room/", views.availability, name="availability"),
    path("facility/<int:pk>/calendar/", views.facility_calendar, name="facility_calendar"),
]
