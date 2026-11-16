from django.urls import path

from . import views

app_name = "facilities"

urlpatterns = [
    path("", views.FacilityListView.as_view(), name="list"),
    path("add/", views.FacilityCreateView.as_view(), name="create"),
    path("<int:pk>/", views.FacilityDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.FacilityUpdateView.as_view(), name="update"),
    path("<int:pk>/status/", views.facility_change_status, name="change_status"),
    path("<int:pk>/toggle-active/", views.facility_toggle_active, name="toggle_active"),
    path("<int:pk>/maintenance/add/", views.maintenance_create, name="maintenance_create"),
    path("buildings/", views.BuildingListView.as_view(), name="building_list"),
    path("buildings/add/", views.BuildingCreateView.as_view(), name="building_create"),
    path("buildings/<int:pk>/edit/", views.BuildingUpdateView.as_view(), name="building_update"),
    path("types/", views.FacilityTypeListView.as_view(), name="type_list"),
    path("types/add/", views.FacilityTypeCreateView.as_view(), name="type_create"),
    path("types/<int:pk>/edit/", views.FacilityTypeUpdateView.as_view(), name="type_update"),
]
