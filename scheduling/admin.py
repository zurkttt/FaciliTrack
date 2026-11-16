from django.contrib import admin

from .models import Activity, Reservation, Schedule


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "activity_type", "department")
    list_filter = ("activity_type", "department")
    search_fields = ("code", "title")


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("activity", "facility", "day_of_week", "start_time", "end_time", "expected_attendees", "source")
    list_filter = ("day_of_week", "source", "facility__building")


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("facility", "purpose", "requested_by", "start_at", "end_at", "status")
    list_filter = ("status",)
