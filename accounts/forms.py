"""Login form with brute-force protection (OWASP A07: identification and authentication failures)."""
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.exceptions import ValidationError

from audit.middleware import get_client_ip

MAX_FAILURES_PER_IP = 5      # per username + IP address
MAX_FAILURES_PER_USER = 20   # per username from any address (slows distributed guessing)
LOCKOUT_SECONDS = 15 * 60


def _keys(username, request):
    name = (username or "").strip().lower()
    ip = get_client_ip(request) if request else "unknown"
    return f"login-fail:{name}:{ip}", f"login-fail:{name}"


class ThrottledAuthenticationForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "throttled": "Too many failed sign-in attempts. Please wait 15 minutes and try again.",
    }

    def clean(self):
        ip_key, user_key = _keys(self.data.get("username"), self.request)
        if cache.get(ip_key, 0) >= MAX_FAILURES_PER_IP or cache.get(user_key, 0) >= MAX_FAILURES_PER_USER:
            raise ValidationError(self.error_messages["throttled"], code="throttled")
        try:
            cleaned = super().clean()
        except ValidationError:
            for key in (ip_key, user_key):
                cache.add(key, 0, LOCKOUT_SECONDS)
                cache.incr(key)
            raise
        cache.delete_many([ip_key, user_key])
        return cleaned
