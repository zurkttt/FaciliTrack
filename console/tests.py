import re

from django.test import TestCase
from django.urls import reverse

from accounts.models import Department, User
from audit.models import AuditEvent
from core.models import SystemSetting

from .forms import SETTING_DEFAULTS, temporary_password

PASSWORD = "Pass@12345"


class ConsoleTestBase(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="CCS", name="Computer Studies")
        self.admin = User.objects.create_user("admin1", password=PASSWORD, role=User.Role.ADMIN, first_name="Ada", last_name="Min")
        self.planner = User.objects.create_user("planner", password=PASSWORD, role=User.Role.PLANNER)
        self.viewer = User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER, first_name="Vi", last_name="Ewer")

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)


class AccessTests(ConsoleTestBase):
    def test_only_admins_manage_users(self):
        for user, code in ((self.viewer, 403), (self.planner, 403), (self.admin, 200)):
            self.login(user)
            self.assertEqual(self.client.get(reverse("console:user_list")).status_code, code, user.username)
            self.assertEqual(self.client.get(reverse("console:status")).status_code, code, user.username)

    def test_planner_can_edit_thresholds(self):
        self.login(self.planner)
        self.assertEqual(self.client.get(reverse("console:settings")).status_code, 200)
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("console:settings")).status_code, 403)


class UserManagementTests(ConsoleTestBase):
    def create(self, **overrides):
        data = {"username": "newbie", "first_name": "New", "last_name": "Bie", "email": "New@Example.com",
                "role": User.Role.REGISTRAR, "department": self.dept.pk}
        data.update(overrides)
        return self.client.post(reverse("console:user_create"), data)

    def test_create_shows_temporary_password_once_and_forces_change(self):
        self.login(self.admin)
        response = self.create()
        self.assertIn("no-store", response["Cache-Control"])
        password = re.search(r'id="temp-password">([^<]+)<', response.content.decode()).group(1)
        user = User.objects.get(username="newbie")
        self.assertTrue(user.check_password(password))
        self.assertTrue(user.must_change_password)
        self.assertEqual(user.email, "new@example.com")
        self.assertFalse(user.is_staff or user.is_superuser)

        self.client.logout()
        self.client.login(username="newbie", password=password)
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("accounts:password_change"))
        self.client.post(reverse("accounts:password_change"), {
            "old_password": password, "new_password1": "Br4nd-new-pass!", "new_password2": "Br4nd-new-pass!",
        })
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_duplicate_username_and_email_rejected(self):
        self.login(self.admin)
        self.assertContains(self.create(username="VIEWER"), "already taken")
        self.viewer.email = "v@example.com"
        self.viewer.save()
        self.assertContains(self.create(email="V@example.com"), "already has this email")

    def test_reset_password(self):
        self.login(self.admin)
        response = self.client.post(reverse("console:user_reset_password", args=[self.viewer.pk]))
        password = re.search(r'id="temp-password">([^<]+)<', response.content.decode()).group(1)
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.check_password(password))
        self.assertFalse(self.viewer.check_password(PASSWORD))
        self.assertTrue(self.viewer.must_change_password)
        self.assertTrue(AuditEvent.objects.filter(entity_id=str(self.viewer.pk), changes__has_key="password").exists())

    def test_cannot_deactivate_self(self):
        self.login(self.admin)
        self.client.post(reverse("console:user_toggle_active", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_last_admin_cannot_be_demoted_or_deactivated(self):
        other = User.objects.create_user("admin2", password=PASSWORD, role=User.Role.ADMIN)
        self.login(other)
        self.client.post(reverse("console:user_toggle_active", args=[self.admin.pk]))  # allowed: other admin remains
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)
        response = self.client.post(reverse("console:user_edit", args=[other.pk]), {
            "username": "admin2", "first_name": "A", "last_name": "B", "email": "", "role": User.Role.VIEWER,
        })
        self.assertContains(response, "last active administrator")
        other.refresh_from_db()
        self.assertEqual(other.role, User.Role.ADMIN)

    def test_admin_cannot_touch_superuser(self):
        root = User.objects.create_superuser("root", password=PASSWORD)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("console:user_edit", args=[root.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("console:user_reset_password", args=[root.pk])).status_code, 403)

    def test_deactivated_user_cannot_log_in(self):
        self.login(self.admin)
        self.client.post(reverse("console:user_toggle_active", args=[self.viewer.pk]))
        self.client.logout()
        self.assertFalse(self.client.login(username="viewer", password=PASSWORD))

    def test_temporary_password_has_no_lookalike_characters(self):
        for _ in range(50):
            pw = temporary_password()
            self.assertEqual(len(pw), 12)
            self.assertFalse(set(pw) & set("0O1lI"))


class DepartmentTests(ConsoleTestBase):
    def test_create_and_edit(self):
        self.login(self.admin)
        self.client.post(reverse("console:departments"), {"code": "coe", "name": "Engineering"})
        dept = Department.objects.get(code="COE")
        self.client.post(reverse("console:department_edit", args=[dept.pk]), {"code": "COE", "name": "College of Engineering"})
        dept.refresh_from_db()
        self.assertEqual(dept.name, "College of Engineering")


class SettingsTests(ConsoleTestBase):
    def data(self, **overrides):
        data = {**{k: v for k, v in SETTING_DEFAULTS.items()}, "reason": "DGC resolution 2026-04"}
        data.update(overrides)
        return data

    def test_change_is_saved_and_audited_with_reason(self):
        self.login(self.planner)
        self.client.post(reverse("console:settings"), self.data(**{"risk.underutilization_threshold": 25}))
        self.assertEqual(SystemSetting.get("risk.underutilization_threshold", cast=float), 25.0)
        event = AuditEvent.objects.get(entity="core.SystemSetting", changes__has_key="reason")
        self.assertEqual(event.changes["reason"], "DGC resolution 2026-04")
        self.assertEqual(event.changes["risk.underutilization_threshold"], {"old": 30, "new": 25.0})

    def test_low_must_be_below_high(self):
        self.login(self.planner)
        response = self.client.post(reverse("console:settings"), self.data(**{"risk.underutilization_threshold": 90}))
        self.assertContains(response, "Must be higher than the underutilization threshold")
        self.assertIsNone(SystemSetting.get("risk.underutilization_threshold"))

    def test_out_of_range_and_missing_reason(self):
        self.login(self.planner)
        response = self.client.post(reverse("console:settings"), self.data(**{"risk.overcrowding_ratio": 5, "reason": ""}))
        self.assertContains(response, "less than or equal to 2.0")
        self.assertContains(response, "This field is required")

    def test_engine_uses_new_threshold(self):
        from risks.engine import settings_snapshot

        self.login(self.planner)
        self.client.post(reverse("console:settings"), self.data(**{"risk.window_days": 14}))
        self.assertEqual(settings_snapshot()["window_days"], 14)


class StatusTests(ConsoleTestBase):
    def test_status_page_reports_health(self):
        self.login(self.admin)
        response = self.client.get(reverse("console:status"))
        self.assertContains(response, "Database migrations")
        self.assertContains(response, "Up to date")
        self.assertContains(response, "Active metric definition")
        self.assertContains(response, "None: analytics cannot run")  # no definition in this test DB


class NavigationTests(ConsoleTestBase):
    def test_admin_menu_by_role(self):
        self.login(self.viewer)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), reverse("console:settings"))
        self.login(self.planner)
        page = self.client.get(reverse("core:dashboard"))
        self.assertContains(page, reverse("console:settings"))
        self.assertNotContains(page, reverse("console:user_list"))
        self.login(self.admin)
        self.assertContains(self.client.get(reverse("core:dashboard")), reverse("console:user_list"))
