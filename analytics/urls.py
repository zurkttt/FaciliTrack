from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("recalculate/", views.recalculate, name="recalculate"),
    path("definitions/", views.definitions, name="definitions"),
    path("definitions/<int:pk>/activate/", views.activate_definition, name="activate_definition"),
]
