"""Stores the current request's user and IP so model signals can record who made a change."""
from contextvars import ContextVar

_current_user = ContextVar("current_user", default=None)
_current_ip = ContextVar("current_ip", default=None)


def get_current_user():
    user = _current_user.get()
    return user if user is not None and user.is_authenticated else None


def get_current_ip():
    return _current_ip.get()


def get_client_ip(request):
    # REMOTE_ADDR only: X-Forwarded-For can be spoofed unless a trusted proxy sets it.
    return request.META.get("REMOTE_ADDR")


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user_token = _current_user.set(getattr(request, "user", None))
        ip_token = _current_ip.set(get_client_ip(request))
        try:
            return self.get_response(request)
        finally:
            _current_user.reset(user_token)
            _current_ip.reset(ip_token)
