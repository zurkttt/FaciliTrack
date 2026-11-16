import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from audit.models import AuditEvent

from .models import Building, Facility, FacilityType, MaintenanceRecord


class FacilityRuleTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="B1", name="Building 1")
        self.ftype = FacilityType.objects.create(name="Classroom")

    def make(self, code="B1-101", **kwargs):
        data = dict(facility_code=code, name="Room", building=self.building, facility_type=self.ftype, capacity=40)
        data.update(kwargs)
        return Facility(**data)

    def test_facility_code_must_be_unique(self):
        self.make().save()
        with self.assertRaises(IntegrityError):
            self.make().save()

    def test_close_time_must_be_after_open_time(self):
        facility = self.make(open_time=datetime.time(18, 0), close_time=datetime.time(8, 0))
        with self.assertRaises(ValidationError):
            facility.full_clean()

    def test_operating_hours(self):
        self.assertEqual(self.make().operating_hours_per_day, 14)


class FacilityViewTests(TestCase):
    PASSWORD = "Pass@12345"

    def setUp(self):
        self.building = Building.objects.create(code="B1", name="Building 1", floors=3)
        self.ftype = FacilityType.objects.create(name="Classroom")
        self.facility = Facility.objects.create(
            facility_code="B1-101", name="Room 101", building=self.building, facility_type=self.ftype, capacity=40
        )
        self.staff = User.objects.create_user("fac", password=self.PASSWORD, role=User.Role.FACILITIES)
        self.viewer = User.objects.create_user("view", password=self.PASSWORD, role=User.Role.VIEWER)

    def login(self, user):
        self.client.login(username=user.username, password=self.PASSWORD)

    def form_data(self, **overrides):
        data = {
            "facility_code": "b1-102", "name": "Room 102", "building": self.building.pk,
            "facility_type": self.ftype.pk, "floor": 1, "capacity": 30,
            "open_time": "07:00", "close_time": "21:00", "status": Facility.Status.AVAILABLE, "notes": "",
        }
        data.update(overrides)
        return data

    def test_viewer_can_list_and_view(self):
        self.login(self.viewer)
        self.assertContains(self.client.get(reverse("facilities:list")), "B1-101")
        self.assertContains(self.client.get(reverse("facilities:detail", args=[self.facility.pk])), "Room 101")

    def test_viewer_cannot_create_or_edit(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("facilities:create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("facilities:create"), self.form_data()).status_code, 403)
        self.assertEqual(self.client.get(reverse("facilities:update", args=[self.facility.pk])).status_code, 403)
        url = reverse("facilities:change_status", args=[self.facility.pk])
        self.assertEqual(self.client.post(url, {"status": "CLOSED"}).status_code, 403)
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.status, Facility.Status.AVAILABLE)

    def test_staff_creates_facility_with_normalized_code(self):
        self.login(self.staff)
        response = self.client.post(reverse("facilities:create"), self.form_data())
        created = Facility.objects.get(facility_code="B1-102")
        self.assertRedirects(response, reverse("facilities:detail", args=[created.pk]))

    def test_duplicate_code_rejected_case_insensitive(self):
        self.login(self.staff)
        response = self.client.post(reverse("facilities:create"), self.form_data(facility_code="b1-101"))
        self.assertContains(response, "already exists")
        self.assertEqual(Facility.objects.count(), 1)

    def test_floor_cannot_exceed_building_floors(self):
        self.login(self.staff)
        response = self.client.post(reverse("facilities:create"), self.form_data(floor=5))
        self.assertContains(response, "only has 3 floor")

    def test_capacity_edit_is_audited_with_user(self):
        self.login(self.staff)
        self.client.post(
            reverse("facilities:update", args=[self.facility.pk]),
            self.form_data(facility_code="B1-101", name="Room 101", capacity=55),
        )
        event = AuditEvent.objects.get(entity="facilities.Facility", action=AuditEvent.Action.UPDATE)
        self.assertEqual(event.username, "fac")
        self.assertEqual(event.changes["capacity"], {"old": 40, "new": 55})

    def test_status_change(self):
        self.login(self.staff)
        self.client.post(reverse("facilities:change_status", args=[self.facility.pk]), {"status": "CLOSED"})
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.status, Facility.Status.CLOSED)

    def test_status_change_requires_post(self):
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse("facilities:change_status", args=[self.facility.pk])).status_code, 405)

    def test_deactivated_facility_hidden_from_default_list(self):
        self.login(self.staff)
        # follow=True so the "deactivated" flash message is consumed by the detail page
        self.client.post(reverse("facilities:toggle_active", args=[self.facility.pk]), follow=True)
        self.assertNotContains(self.client.get(reverse("facilities:list")), "B1-101")
        self.assertContains(self.client.get(reverse("facilities:list") + "?show=all"), "B1-101")

    def test_filters(self):
        lab = FacilityType.objects.create(name="Lab")
        Facility.objects.create(facility_code="B1-LAB", name="Lab", building=self.building, facility_type=lab, capacity=20)
        self.login(self.viewer)
        response = self.client.get(reverse("facilities:list"), {"type": lab.pk})
        self.assertContains(response, "B1-LAB")
        self.assertNotContains(response, "B1-101")
        response = self.client.get(reverse("facilities:list"), {"min_capacity": 30})
        self.assertContains(response, "B1-101")
        self.assertNotContains(response, "B1-LAB")

    def test_log_maintenance_marks_facility(self):
        self.login(self.staff)
        self.client.post(
            reverse("facilities:maintenance_create", args=[self.facility.pk]),
            {"reason": "Aircon repair", "start_at": "2026-11-20T08:00", "end_at": "2026-11-21T17:00",
             "status": "SCHEDULED", "mark_facility": "on"},
        )
        self.assertEqual(MaintenanceRecord.objects.get().created_by, self.staff)
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.status, Facility.Status.MAINTENANCE)

    def test_maintenance_end_before_start_rejected(self):
        self.login(self.staff)
        response = self.client.post(
            reverse("facilities:maintenance_create", args=[self.facility.pk]),
            {"reason": "x", "start_at": "2026-11-21T08:00", "end_at": "2026-11-20T08:00", "status": "SCHEDULED"},
        )
        self.assertContains(response, "End must be later than start")
        self.assertFalse(MaintenanceRecord.objects.exists())

    def test_building_pages_restricted(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("facilities:building_list")).status_code, 403)
        self.login(self.staff)
        self.assertContains(self.client.get(reverse("facilities:building_list")), "Building 1")
