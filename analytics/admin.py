from django.contrib import admin

from .models import MetricDefinition, UtilizationMetric


@admin.register(MetricDefinition)
class MetricDefinitionAdmin(admin.ModelAdmin):
    list_display = ("version", "name", "count_reservations_as_use", "exclude_maintenance", "is_active")


@admin.register(UtilizationMetric)
class UtilizationMetricAdmin(admin.ModelAdmin):
    list_display = ("facility", "period_start", "period_end", "used_hours", "available_hours", "utilization_rate")
    list_filter = ("definition",)
