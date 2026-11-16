from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.status, name="status"),
    path("users/", views.user_list, name="user_list"),
    path("users/add/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/toggle-active/", views.user_toggle_active, name="user_toggle_active"),
    path("departments/", views.departments, name="departments"),
    path("departments/<int:pk>/", views.departments, name="department_edit"),
    path("settings/", views.system_settings, name="settings"),
]
