from django.contrib import admin

from .models import Building, Facility, FacilityType, MaintenanceRecord


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "floors", "is_active")
    search_fields = ("code", "name")


@admin.register(FacilityType)
class FacilityTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ("facility_code", "name", "building", "facility_type", "capacity", "status", "is_active")
    list_filter = ("building", "facility_type", "status", "is_active")
    search_fields = ("facility_code", "name")


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = ("facility", "reason", "start_at", "end_at", "status")
    list_filter = ("status",)
