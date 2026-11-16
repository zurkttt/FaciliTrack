from django.test import TestCase
from django.urls import reverse

from .models import User


class RoleAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin1", password="Pass@12345", role=User.Role.ADMIN)
        self.viewer = User.objects.create_user("viewer1", password="Pass@12345", role=User.Role.VIEWER)

    def test_has_role(self):
        self.assertTrue(self.admin.has_role(User.Role.ADMIN))
        self.assertFalse(self.viewer.has_role(User.Role.ADMIN, User.Role.FACILITIES))

    def test_superuser_passes_all_role_checks(self):
        su = User.objects.create_superuser("root", password="Pass@12345", role=User.Role.VIEWER)
        self.assertTrue(su.has_role(User.Role.ADMIN))

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/")

    def test_login_and_dashboard(self):
        self.client.login(username="viewer1", password="Pass@12345")
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_audit_log_admin_only(self):
        self.client.login(username="viewer1", password="Pass@12345")
        self.assertEqual(self.client.get(reverse("audit:list")).status_code, 403)
        self.client.login(username="admin1", password="Pass@12345")
        self.assertEqual(self.client.get(reverse("audit:list")).status_code, 200)
