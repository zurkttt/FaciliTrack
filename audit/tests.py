from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from facilities.models import Building, Facility, FacilityType

from .models import AuditEvent


class AuditLogTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="B1", name="Building 1")
        self.ftype = FacilityType.objects.create(name="Classroom")

    def _facility(self):
        return Facility.objects.create(
            facility_code="B1-101", name="Room 101", building=self.building, facility_type=self.ftype, capacity=40
        )

    def test_create_is_audited(self):
        facility = self._facility()
        event = AuditEvent.objects.get(entity="facilities.Facility", action=AuditEvent.Action.CREATE)
        self.assertEqual(event.entity_id, str(facility.pk))

    def test_capacity_change_records_old_and_new(self):
        facility = self._facility()
        facility.capacity = 55
        facility.save()
        event = AuditEvent.objects.get(entity="facilities.Facility", action=AuditEvent.Action.UPDATE)
        self.assertEqual(event.changes["capacity"], {"old": 40, "new": 55})

    def test_save_without_changes_is_not_audited(self):
        facility = self._facility()
        facility.save()
        self.assertFalse(AuditEvent.objects.filter(action=AuditEvent.Action.UPDATE).exists())

    def test_delete_is_audited(self):
        facility = self._facility()
        facility.delete()
        self.assertTrue(AuditEvent.objects.filter(entity="facilities.Facility", action=AuditEvent.Action.DELETE).exists())

    def test_password_never_logged(self):
        User.objects.create_user("u1", password="Pass@12345")
        event = AuditEvent.objects.get(entity="accounts.User", action=AuditEvent.Action.CREATE)
        self.assertNotIn("password", event.changes["new"])

    def test_login_is_audited(self):
        User.objects.create_user("admin1", password="Pass@12345", role=User.Role.ADMIN)
        self.client.login(username="admin1", password="Pass@12345")
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN, username="admin1").exists())

    def test_failed_login_is_audited(self):
        self.client.post(reverse("accounts:login"), {"username": "nobody", "password": "wrong"})
        self.assertTrue(AuditEvent.objects.filter(action=AuditEvent.Action.LOGIN_FAILED, username="nobody").exists())
