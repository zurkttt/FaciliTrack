from django.contrib import admin

from .models import MitigationAction, Risk


class MitigationActionInline(admin.TabularInline):
    model = MitigationAction
    extra = 0


@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ("facility", "category", "likelihood", "impact", "score", "priority", "status", "owner")
    list_filter = ("priority", "category", "status")
    readonly_fields = ("score", "priority")
    inlines = [MitigationActionInline]
