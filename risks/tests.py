import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from analytics.engine import calculate
from analytics.models import MetricDefinition
from facilities.models import Building, Facility, FacilityType, MaintenanceRecord
from scheduling.models import Activity, Reservation, Schedule
from usage.models import UsageRecord

from . import engine
from .models import MitigationAction, Risk

PASSWORD = "Pass@12345"
TZ = timezone.get_current_timezone()
TODAY = datetime.date(2026, 11, 18)  # a Wednesday
NOW = timezone.make_aware(datetime.datetime.combine(TODAY, datetime.time(12)), TZ)


def at(day, hour):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), TZ)


class RiskScoringTests(TestCase):
    def setUp(self):
        building = Building.objects.create(code="B1", name="Building 1")
        ftype = FacilityType.objects.create(name="Classroom")
        self.facility = Facility.objects.create(
            facility_code="B1-101", name="Room 101", building=building, facility_type=ftype, capacity=40
        )

    def risk(self, likelihood, impact):
        return Risk.objects.create(
            facility=self.facility, category=Risk.Category.OVERCROWDING,
            likelihood=likelihood, impact=impact, reason="test",
        )

    def test_score_is_likelihood_times_impact(self):
        self.assertEqual(self.risk(3, 4).score, 12)

    def test_priority_bands(self):
        self.assertEqual(self.risk(2, 3).priority, Risk.Priority.LOW)  # 6
        self.assertEqual(self.risk(2, 4).priority, Risk.Priority.MEDIUM)  # 8
        self.assertEqual(self.risk(3, 4).priority, Risk.Priority.MEDIUM)  # 12
        self.assertEqual(self.risk(3, 5).priority, Risk.Priority.HIGH)  # 15
        self.assertEqual(self.risk(5, 5).priority, Risk.Priority.HIGH)  # 25

    def test_score_recalculated_on_update(self):
        risk = self.risk(1, 1)
        risk.likelihood = 5
        risk.impact = 4
        risk.save()
        self.assertEqual((risk.score, risk.priority), (20, Risk.Priority.HIGH))


class EngineRuleTests(TestCase):
    def setUp(self):
        patcher = mock.patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        b = Building.objects.create(code="B", name="B", floors=3)
        t = FacilityType.objects.create(name="Room")
        self.room = Facility.objects.create(facility_code="R1", name="R1", building=b, facility_type=t, capacity=30)
        MetricDefinition.objects.create(version="1", is_active=True)
        self.planner = User.objects.create_user("planner", password=PASSWORD, role=User.Role.PLANNER)
        self.activity = Activity.objects.create(code="A1", title="A1")

    def findings(self, category):
        return [f for f in engine.detect(TODAY) if f.category == category and f.facility == self.room]

    def schedule(self, day, start, end, expected=20):
        return Schedule.objects.create(
            facility=self.room, activity=self.activity, day_of_week=day,
            start_time=datetime.time(start), end_time=datetime.time(end),
            term_start=TODAY - datetime.timedelta(days=60), term_end=TODAY + datetime.timedelta(days=60),
            expected_attendees=expected,
        )

    def test_overcrowding_from_observed_headcounts(self):
        for days_ago in range(1, 11):
            day = TODAY - datetime.timedelta(days=days_ago)
            UsageRecord.objects.create(facility=self.room, start_at=at(day, 9), end_at=at(day, 10),
                                       headcount=40 if days_ago <= 6 else 20)  # 6 of 10 over
        (f,) = self.findings(Risk.Category.OVERCROWDING)
        self.assertEqual((f.likelihood, f.impact), (5, 5))  # 60% of sessions; 40/30 = 1.33x
        self.assertIn("6 of 10 counted sessions", f.reason)

    def test_planned_overcrowding_is_likely(self):
        self.schedule(0, 8, 10, expected=33)
        (f,) = self.findings(Risk.Category.OVERCROWDING)
        self.assertEqual((f.likelihood, f.impact), (4, 3))
        self.assertIn("A1", f.reason)

    def test_underutilization_and_no_overdemand(self):
        day = TODAY - datetime.timedelta(days=2)
        UsageRecord.objects.create(facility=self.room, start_at=at(day, 9), end_at=at(day, 10))
        calculate(TODAY - datetime.timedelta(days=27), TODAY)
        (f,) = self.findings(Risk.Category.UNDERUTILIZATION)
        self.assertEqual(f.likelihood, 5)
        self.assertEqual(self.findings(Risk.Category.OVER_DEMAND), [])

    def test_over_demand_when_fully_booked(self):
        for day in range(6):
            self.schedule(day, 7, 21)
        calculate(TODAY - datetime.timedelta(days=27), TODAY)
        (f,) = self.findings(Risk.Category.OVER_DEMAND)
        self.assertEqual((f.likelihood, f.impact), (5, 4))
        self.assertEqual(self.findings(Risk.Category.UNDERUTILIZATION), [])

    def test_approved_booking_during_maintenance_is_a_conflict(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="Leak", start_at=NOW - datetime.timedelta(days=2),
                                         status=MaintenanceRecord.Status.ONGOING)
        day = TODAY + datetime.timedelta(days=2)
        Reservation.objects.create(facility=self.room, requested_by=self.planner, purpose="Defense",
                                   status=Reservation.Status.APPROVED, start_at=at(day, 10), end_at=at(day, 12))
        (f,) = self.findings(Risk.Category.CONFLICT)
        self.assertEqual(f.likelihood, 5)
        self.assertIn("Defense", f.reason)

    def test_long_maintenance_is_unavailable_risk(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="Leak", start_at=NOW - datetime.timedelta(days=20),
                                         status=MaintenanceRecord.Status.ONGOING)
        self.room.status = Facility.Status.MAINTENANCE
        self.room.save()
        self.schedule(3, 8, 10)  # Thursdays: 2 sessions in the next 14 days
        (f,) = self.findings(Risk.Category.UNAVAILABLE)
        self.assertEqual((f.likelihood, f.impact), (5, 3))
        self.assertEqual(f.evidence["affected_sessions_next_14_days"], 2)

    def test_short_maintenance_without_affected_sessions_is_not_flagged(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="Paint", start_at=NOW - datetime.timedelta(days=2),
                                         status=MaintenanceRecord.Status.ONGOING)
        self.assertEqual(self.findings(Risk.Category.UNAVAILABLE), [])

    def test_missing_usage_data(self):
        for day in (0, 1, 2):
            self.schedule(day, 8, 10)
        (f,) = self.findings(Risk.Category.DATA_QUALITY)
        self.assertGreaterEqual(f.likelihood, 4)
        self.assertIn("No usage has been recorded", f.reason)


class SyncTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())  # also runs detection once

    def test_seed_produces_risks_in_every_band(self):
        self.assertEqual(set(Risk.objects.values_list("priority", flat=True)), {"LOW", "MEDIUM", "HIGH"})
        self.assertFalse(Risk.objects.filter(owner__isnull=True).exists())

    def test_second_run_does_not_duplicate(self):
        before = Risk.objects.count()
        result = engine.run()
        self.assertEqual((result.created, Risk.objects.count()), (0, before))

    def test_cleared_condition_closes_risk(self):
        risk = Risk.objects.get(category=Risk.Category.UNAVAILABLE, facility__facility_code="LIB-DR1")
        MaintenanceRecord.objects.filter(facility=risk.facility).update(
            status=MaintenanceRecord.Status.COMPLETED, end_at=timezone.now())
        Facility.objects.filter(pk=risk.facility_id).update(status=Facility.Status.AVAILABLE)
        engine.run()
        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.CLOSED)
        self.assertIn("no longer detected", risk.resolution)

    def test_accepted_risk_stays_accepted_unless_priority_rises(self):
        risk = Risk.objects.get(category=Risk.Category.UNDERUTILIZATION, facility__facility_code="MAIN-AVR")
        risk.status = Risk.Status.ACCEPTED
        risk.save()
        engine.run()
        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.ACCEPTED)
        risk.likelihood, risk.impact = 1, 1  # pretend it used to be Low
        risk.save()
        engine.run()
        risk.refresh_from_db()
        self.assertEqual(risk.status, Risk.Status.OPEN)
        self.assertIn("Reopened", risk.resolution)

    def test_manual_risks_are_left_alone(self):
        manual = Risk.objects.create(facility=Facility.objects.first(), category=Risk.Category.OVERCROWDING,
                                     likelihood=2, impact=2, reason="Inspection note")
        engine.run()
        manual.refresh_from_db()
        self.assertEqual(manual.status, Risk.Status.OPEN)

    def test_command(self):
        out = StringIO()
        call_command("detect_risks", stdout=out)
        self.assertIn("0 new", out.getvalue())


class RiskViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for u in User.objects.all():
            u.set_password(PASSWORD)
            u.save()

    def setUp(self):
        self.risk = Risk.objects.get(category=Risk.Category.OVER_DEMAND)  # owner: planner

    def login(self, username):
        self.client.login(username=username, password=PASSWORD)

    def test_register_access(self):
        self.login("viewer")
        self.assertEqual(self.client.get(reverse("risks:register")).status_code, 403)
        self.login("depthead")
        response = self.client.get(reverse("risks:register"))
        self.assertContains(response, "Risk register")
        self.assertContains(response, "ENG-CL1")
        self.assertNotContains(response, "Run detection")

    def test_matrix_cell_filter(self):
        self.login("planner")
        response = self.client.get(reverse("risks:register"), {"likelihood": self.risk.likelihood, "impact": self.risk.impact})
        self.assertContains(response, "Over-demand")

    def test_dept_head_cannot_update(self):
        self.login("depthead")
        response = self.client.post(reverse("risks:update", args=[self.risk.pk]), {"status": "IN_REVIEW"})
        self.assertEqual(response.status_code, 403)

    def test_closing_or_accepting_requires_note(self):
        self.login("planner")
        self.client.post(reverse("risks:update", args=[self.risk.pk]), {"status": "CLOSED", "owner": self.risk.owner_id})
        self.risk.refresh_from_db()
        self.assertNotEqual(self.risk.status, Risk.Status.CLOSED)
        self.client.post(reverse("risks:update", args=[self.risk.pk]),
                         {"status": "ACCEPTED", "owner": self.risk.owner_id, "note": "Expected during enrolment."})
        self.risk.refresh_from_db()
        self.assertEqual(self.risk.status, Risk.Status.ACCEPTED)
        self.assertIn("Expected during enrolment.", self.risk.resolution)

    def test_owner_who_is_not_a_manager_can_update(self):
        registrar = User.objects.get(username="registrar")
        self.risk.owner = registrar
        self.risk.save()
        self.login("registrar")
        self.client.post(reverse("risks:update", args=[self.risk.pk]), {"status": "IN_REVIEW", "owner": registrar.pk})
        self.risk.refresh_from_db()
        self.assertEqual(self.risk.status, Risk.Status.IN_REVIEW)

    def test_adding_action_starts_mitigation_and_assignee_completes_it(self):
        self.login("planner")
        registrar = User.objects.get(username="registrar")
        self.client.post(reverse("risks:add_action", args=[self.risk.pk]),
                         {"action": "Open another lab section", "assigned_to": registrar.pk, "due_date": "2026-12-01"})
        self.risk.refresh_from_db()
        self.assertEqual(self.risk.status, Risk.Status.MITIGATING)
        action = MitigationAction.objects.get()
        self.login("registrar")
        self.client.post(reverse("risks:action_status", args=[action.pk]), {"status": "DONE"})
        action.refresh_from_db()
        self.assertEqual(action.status, MitigationAction.Status.DONE)
        self.assertIsNotNone(action.completed_at)

    def test_manual_risk(self):
        self.login("facilities")
        facility = Facility.objects.get(facility_code="MAIN-101")
        response = self.client.post(reverse("risks:create"), {
            "facility": facility.pk, "category": "OVERCROWDING", "likelihood": 3, "impact": 5, "reason": "Blocked fire exit",
        })
        risk = Risk.objects.get(auto_detected=False)
        self.assertRedirects(response, reverse("risks:detail", args=[risk.pk]))
        self.assertEqual((risk.score, risk.priority), (15, Risk.Priority.HIGH))

    def test_run_detection_button(self):
        self.login("depthead")
        self.assertEqual(self.client.post(reverse("risks:run_detection")).status_code, 403)
        self.login("facilities")
        self.assertRedirects(self.client.post(reverse("risks:run_detection")), reverse("risks:register"))

    def test_detail_and_facility_pages(self):
        self.login("planner")
        self.assertContains(self.client.get(reverse("risks:detail", args=[self.risk.pk])), "Why this was flagged")
        self.assertContains(self.client.get(reverse("facilities:detail", args=[self.risk.facility_id])), "Open risks")
