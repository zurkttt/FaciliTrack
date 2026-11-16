from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import ThrottledAuthenticationForm
from .views import FaciliTrackPasswordChangeView

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="accounts/login.html", redirect_authenticated_user=True,
            authentication_form=ThrottledAuthenticationForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", FaciliTrackPasswordChangeView.as_view(), name="password_change"),
]
