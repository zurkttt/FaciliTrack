from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "level", "title", "read_at")
    list_filter = ("level",)
    search_fields = ("title", "user__username")
