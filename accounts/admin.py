from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Department, User


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(User)
class FaciliTrackUserAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "email", "role", "department", "is_active")
    list_filter = ("role", "department", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (("FaciliTrack", {"fields": ("role", "department")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("FaciliTrack", {"fields": ("role", "department")}),)
