from django.urls import path

from . import views

app_name = "risks"

urlpatterns = [
    path("", views.register, name="register"),
    path("add/", views.create, name="create"),
    path("run/", views.run_detection, name="run_detection"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/update/", views.update, name="update"),
    path("<int:pk>/actions/add/", views.add_action, name="add_action"),
    path("actions/<int:pk>/status/", views.action_status, name="action_status"),
]
