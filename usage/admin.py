from django.contrib import admin

from .models import UsageRecord


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("facility", "start_at", "end_at", "headcount", "source", "recorded_by")
    list_filter = ("source", "facility__building")
