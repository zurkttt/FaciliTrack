"""Security tests mapped to the OWASP Top 10 (see docs/TESTING.md).

The URL sweep walks EVERY route in the project, so a page added later without a login
check fails here automatically.
"""
from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.forms import MAX_FAILURES_PER_IP
from accounts.models import User
from facilities.models import Building, Facility, FacilityType

PASSWORD = "Pass@12345"
PUBLIC = {"accounts:login"}
SAMPLE_KWARGS = {"int": 1, "str": "X", "slug": "utilization", "path": "x"}


def all_routes(patterns=None, namespace=None):
    """Yield (name, kwargs) for every named route outside Django's own admin site."""
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(p, URLResolver):
            if p.namespace == "admin":
                continue
            ns = p.namespace if not namespace else (f"{namespace}:{p.namespace}" if p.namespace else namespace)
            yield from all_routes(p.url_patterns, ns)
        elif isinstance(p, URLPattern) and p.name:
            converters = getattr(p.pattern, "converters", {})
            kwargs = {k: SAMPLE_KWARGS.get(type(c).__name__.replace("Converter", "").lower(), 1) for k, c in converters.items()}
            yield (f"{namespace}:{p.name}" if namespace else p.name), kwargs


class AccessControlSweep(TestCase):
    """OWASP A01 Broken Access Control."""

    def test_every_route_requires_login(self):
        routes = list(all_routes())
        self.assertGreater(len(routes), 60)
        login_url = reverse(settings.LOGIN_URL)
        for name, kwargs in routes:
            url = reverse(name, kwargs=kwargs)
            response = self.client.get(url)
            if name in PUBLIC:
                self.assertEqual(response.status_code, 200, name)
                continue
            self.assertIn(response.status_code, (302, 405), f"{name} answered {response.status_code} without login")
            if response.status_code == 302:
                self.assertTrue(response["Location"].startswith(login_url), f"{name} redirected to {response['Location']}")

    def test_viewer_is_refused_on_every_restricted_page(self):
        User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER)
        self.client.login(username="viewer", password=PASSWORD)
        restricted = [
            ("audit:list", {}), ("facilities:create", {}), ("facilities:update", {"pk": 1}),
            ("facilities:building_list", {}), ("facilities:type_list", {}), ("facilities:maintenance_create", {"pk": 1}),
            ("scheduling:schedule_create", {}), ("scheduling:schedule_update", {"pk": 1}), ("scheduling:schedule_import", {}),
            ("scheduling:schedule_import_template", {}), ("scheduling:activity_create", {}),
            ("usage:qr_codes", {}), ("usage:attendance", {}), ("usage:record_list", {}), ("usage:record_create", {}),
            ("usage:import", {}), ("usage:import_template", {}), ("usage:comparison", {}),
            ("analytics:dashboard", {}), ("analytics:definitions", {}),
            ("risks:register", {}), ("risks:create", {}), ("risks:detail", {"pk": 1}),
            ("reports:index", {}), ("reports:report", {"slug": "risks"}),
            ("console:status", {}), ("console:user_list", {}), ("console:user_create", {}), ("console:user_edit", {"pk": 1}),
            ("console:departments", {}), ("console:settings", {}),
        ]
        for name, kwargs in restricted:
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 403, name)

    def test_viewer_can_use_self_service_pages(self):
        User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER)
        self.client.login(username="viewer", password=PASSWORD)
        for name in ("core:dashboard", "facilities:list", "scheduling:schedule_list", "scheduling:reservation_list",
                     "scheduling:reservation_create", "scheduling:availability", "notifications:list", "accounts:password_change"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)


class CsrfAndHeaders(TestCase):
    """OWASP A01/A05: CSRF protection and security headers."""

    def setUp(self):
        User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER)

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username="viewer", password=PASSWORD)
        self.assertEqual(client.post(reverse("notifications:mark_all_read")).status_code, 403)

    def test_security_headers(self):
        self.client.login(username="viewer", password=PASSWORD)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_session_cookie_settings(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 12 * 60 * 60)


class InjectionTests(TestCase):
    """OWASP A03 Injection: user input is escaped in HTML and treated as data in queries."""

    def setUp(self):
        self.user = User.objects.create_user("fac", password=PASSWORD, role=User.Role.FACILITIES)
        self.client.login(username="fac", password=PASSWORD)
        b = Building.objects.create(code="B", name="B")
        t = FacilityType.objects.create(name="Room")
        Facility.objects.create(facility_code="XSS-1", name="<script>alert('x')</script>", building=b, facility_type=t, capacity=5)

    def test_script_in_data_is_escaped(self):
        response = self.client.get(reverse("facilities:list"))
        self.assertNotContains(response, "<script>alert('x')</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_sql_like_search_is_harmless(self):
        response = self.client.get(reverse("facilities:list"), {"q": "' OR 1=1; DROP TABLE facilities_facility; --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Facility.objects.count(), 1)


class LoginThrottleTests(TestCase):
    """OWASP A07 Identification and Authentication Failures."""

    def setUp(self):
        cache.clear()
        User.objects.create_user("victim", password=PASSWORD)

    def attempt(self, password):
        return self.client.post(reverse("accounts:login"), {"username": "victim", "password": password})

    def test_lockout_after_repeated_failures_even_with_correct_password(self):
        for _ in range(MAX_FAILURES_PER_IP):
            self.assertContains(self.attempt("wrong"), "Please enter a correct username")
        response = self.attempt(PASSWORD)
        self.assertContains(response, "Too many failed sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_success_resets_counter(self):
        for _ in range(MAX_FAILURES_PER_IP - 1):
            self.attempt("wrong")
        self.assertEqual(self.attempt(PASSWORD).status_code, 302)
        self.client.logout()
        for _ in range(MAX_FAILURES_PER_IP - 1):
            self.attempt("wrong")
        self.assertEqual(self.attempt(PASSWORD).status_code, 302)  # counter was reset, so not locked

    def test_inactive_user_cannot_log_in(self):
        User.objects.filter(username="victim").update(is_active=False)
        self.assertNotEqual(self.attempt(PASSWORD).status_code, 302)
