from django.shortcuts import redirect
from django.urls import reverse

# Paths a user with a temporary password may still open.
ALLOWED_PREFIXES = ("/accounts/password/", "/accounts/logout/", "/static/")


class ForcePasswordChangeMiddleware:
    """Users holding a temporary password must choose their own before using the system."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and user.must_change_password
                and not request.path.startswith(ALLOWED_PREFIXES)):
            return redirect(reverse("accounts:password_change"))
        return self.get_response(request)
