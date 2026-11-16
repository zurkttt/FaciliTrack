import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from facilities.models import Building, Facility, FacilityType, MaintenanceRecord
from scheduling.models import Activity, Reservation, Schedule
from usage.models import UsageRecord

from .engine import calculate, calculate_facility, hours, intersect, subtract, union
from .models import MetricDefinition, UtilizationMetric
from .queries import base_queryset, by_group, classify, totals

PASSWORD = "Pass@12345"
TZ = timezone.get_current_timezone()
MONDAY = datetime.date(2026, 11, 9)
SUNDAY = MONDAY + datetime.timedelta(days=6)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)), TZ)


class IntervalTests(TestCase):
    def test_union_merges_overlaps_and_touching(self):
        a = [(at(MONDAY, 8), at(MONDAY, 10)), (at(MONDAY, 9), at(MONDAY, 11)), (at(MONDAY, 11), at(MONDAY, 12))]
        self.assertEqual(union(a), [(at(MONDAY, 8), at(MONDAY, 12))])

    def test_subtract_splits(self):
        result = subtract([(at(MONDAY, 7), at(MONDAY, 21))], [(at(MONDAY, 10), at(MONDAY, 12))])
        self.assertEqual(hours(result), 12)
        self.assertEqual(len(result), 2)

    def test_intersect(self):
        a = [(at(MONDAY, 6), at(MONDAY, 9))]
        b = [(at(MONDAY, 7), at(MONDAY, 21))]
        self.assertEqual(hours(intersect(a, b)), 2)


class EngineBase(TestCase):
    """Room open 07:00-21:00 (14 h/day), operating Mon-Sat by default."""

    def setUp(self):
        patcher = mock.patch("django.utils.timezone.now", return_value=at(MONDAY + datetime.timedelta(days=8), 12))
        patcher.start()
        self.addCleanup(patcher.stop)
        building = Building.objects.create(code="B1", name="B1")
        ftype = FacilityType.objects.create(name="Room")
        self.room = Facility.objects.create(facility_code="R1", name="R1", building=building, facility_type=ftype, capacity=40)
        self.definition = MetricDefinition.objects.create(version="1", is_active=True)
        self.user = User.objects.create_user("u", password=PASSWORD)

    def use(self, day, start, end, headcount=None, status=UsageRecord.Status.USED):
        return UsageRecord.objects.create(facility=self.room, start_at=at(day, *start), end_at=at(day, *end),
                                          headcount=headcount, status=status)

    def day_row(self, day, definition=None):
        return calculate_facility(self.room, day, day, definition or self.definition)[0]


class EngineTests(EngineBase):
    def test_basic_rate(self):
        self.use(MONDAY, (8, 0), (15, 0))  # 7 of 14 hours
        row = self.day_row(MONDAY)
        self.assertEqual((row["available_hours"], row["used_hours"], row["utilization_rate"]),
                         (Decimal("14.00"), Decimal("7.00"), Decimal("50.00")))

    def test_overlapping_records_not_double_counted(self):
        self.use(MONDAY, (8, 0), (10, 0))
        self.use(MONDAY, (9, 0), (11, 0))
        self.assertEqual(self.day_row(MONDAY)["used_hours"], Decimal("3.00"))

    def test_use_outside_operating_hours_is_clipped(self):
        self.use(MONDAY, (6, 0), (8, 0))
        self.assertEqual(self.day_row(MONDAY)["used_hours"], Decimal("1.00"))

    def test_no_show_does_not_count(self):
        self.use(MONDAY, (8, 0), (10, 0), status=UsageRecord.Status.NO_SHOW)
        self.assertEqual(self.day_row(MONDAY)["used_hours"], Decimal("0.00"))

    def test_sunday_has_no_available_time_by_default(self):
        self.use(SUNDAY, (9, 0), (10, 0))
        row = self.day_row(SUNDAY)
        self.assertEqual((row["available_hours"], row["used_hours"], row["utilization_rate"]),
                         (Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))

    def test_maintenance_reduces_available_and_used(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="x", start_at=at(MONDAY, 7), end_at=at(MONDAY, 14),
                                         status=MaintenanceRecord.Status.COMPLETED)
        self.use(MONDAY, (13, 0), (15, 0))
        row = self.day_row(MONDAY)
        self.assertEqual((row["available_hours"], row["used_hours"]), (Decimal("7.00"), Decimal("1.00")))

    def test_maintenance_can_be_included_by_definition(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="x", start_at=at(MONDAY, 7), end_at=at(MONDAY, 14))
        v2 = MetricDefinition.objects.create(version="2", exclude_maintenance=False)
        self.assertEqual(self.day_row(MONDAY, v2)["available_hours"], Decimal("14.00"))

    def test_open_ended_maintenance_blocks_following_days(self):
        MaintenanceRecord.objects.create(facility=self.room, reason="x", start_at=at(MONDAY, 0),
                                         status=MaintenanceRecord.Status.ONGOING)
        self.assertEqual(self.day_row(MONDAY + datetime.timedelta(days=2))["available_hours"], Decimal("0.00"))

    def test_reservations_count_as_use_only_when_defined(self):
        Reservation.objects.create(facility=self.room, requested_by=self.user, purpose="x", status=Reservation.Status.APPROVED,
                                   start_at=at(MONDAY, 13), end_at=at(MONDAY, 15))
        self.assertEqual(self.day_row(MONDAY)["used_hours"], Decimal("0.00"))
        v2 = MetricDefinition.objects.create(version="2", count_reservations_as_use=True)
        self.assertEqual(self.day_row(MONDAY, v2)["used_hours"], Decimal("2.00"))
        self.assertEqual(self.day_row(MONDAY)["scheduled_hours"], Decimal("2.00"))

    def test_scheduled_hours_from_classes(self):
        activity = Activity.objects.create(code="A", title="A")
        Schedule.objects.create(facility=self.room, activity=activity, day_of_week=0, start_time=datetime.time(8),
                                end_time=datetime.time(11), term_start=MONDAY, term_end=SUNDAY)
        self.assertEqual(self.day_row(MONDAY)["scheduled_hours"], Decimal("3.00"))

    def test_occupancy(self):
        self.use(MONDAY, (8, 0), (9, 0), headcount=20)   # 0.5
        self.use(MONDAY, (10, 0), (11, 0), headcount=40)  # 1.0
        self.use(MONDAY, (12, 0), (13, 0))                # no count: ignored
        row = self.day_row(MONDAY)
        self.assertEqual((row["occupancy_sum"], row["occupancy_count"]), (Decimal("1.5000"), 2))

    def test_calculate_stores_rows_and_is_repeatable(self):
        self.use(MONDAY, (8, 0), (15, 0))
        self.assertEqual(calculate(MONDAY, SUNDAY), 7)
        self.assertEqual(calculate(MONDAY, SUNDAY), 7)
        self.assertEqual(UtilizationMetric.objects.count(), 7)

    def test_future_days_skipped(self):
        today = timezone.localdate()
        self.assertEqual(calculate(today, today + datetime.timedelta(days=5)), 1)

    def test_no_active_definition(self):
        self.definition.is_active = False
        self.definition.save()
        with self.assertRaises(ValueError):
            calculate(MONDAY, MONDAY)


class AggregationTests(EngineBase):
    def test_rate_from_summed_hours_not_average_of_rates(self):
        # Monday 14h available, 14h used (100%); Saturday available 14h, used 0 -> 50% over the two days
        self.use(MONDAY, (7, 0), (21, 0))
        calculate(MONDAY, SUNDAY)
        qs = base_queryset(self.definition, MONDAY, MONDAY + datetime.timedelta(days=5))
        self.assertAlmostEqual(totals(qs).rate, 14 / (14 * 6) * 100)
        rows = by_group(qs, "building")
        self.assertEqual(rows[0].label, "B1")

    def test_classify(self):
        from .queries import Row

        limits = {"low": 30, "high": 85}

        def row(used, scheduled, available=100, status=None):
            return Row(None, "x", available, used, scheduled, 0, 0, status=status)

        self.assertEqual(classify(row(0, 0, available=0), limits), "none")
        self.assertEqual(classify(row(10, 10), limits), "low")
        self.assertEqual(classify(row(50, 60), limits), "ok")
        self.assertEqual(classify(row(90, 90), limits), "high")
        self.assertEqual(classify(row(60, 95), limits), "high")  # fully booked = high demand
        self.assertEqual(classify(row(0, 0, status="MAINTENANCE"), limits), "unavailable")


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for u in User.objects.filter(username__in=["planner", "viewer", "depthead"]):
            u.set_password(PASSWORD)
            u.save()

    def setUp(self):
        self.planner = User.objects.get(username="planner")
        self.viewer = User.objects.get(username="viewer")
        self.depthead = User.objects.get(username="depthead")

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)

    def test_access(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("analytics:dashboard")).status_code, 403)
        self.login(self.depthead)
        self.assertContains(self.client.get(reverse("analytics:dashboard")), "Facility Utilization")
        self.assertEqual(self.client.get(reverse("analytics:definitions")).status_code, 403)

    def test_dashboard_shows_seeded_data_and_groupings(self):
        self.login(self.planner)
        for group in ("facility", "building", "type", "department"):
            response = self.client.get(reverse("analytics:dashboard"), {"group": group})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Peak periods")
        self.assertContains(self.client.get(reverse("analytics:dashboard")), "ENG-CL1")

    def test_invalid_range(self):
        self.login(self.planner)
        response = self.client.get(reverse("analytics:dashboard"), {"start": "2026-05-01", "end": "2026-04-01"})
        self.assertContains(response, "must not be before")

    def test_csv_export(self):
        self.login(self.planner)
        response = self.client.get(reverse("analytics:dashboard"), {"group": "building", "export": "csv"})
        self.assertEqual(response["Content-Type"], "text/csv")
        lines = response.content.decode().strip().splitlines()
        self.assertTrue(lines[1].startswith("Building,Available hours"))
        self.assertEqual(len(lines), 2 + 3)  # title, header, 3 buildings

    def test_new_definition_and_activation_recalculates(self):
        self.login(self.planner)
        self.client.post(reverse("analytics:definitions"), {
            "version": "2.0", "name": "Utilization Rate", "formula": "Used / Available",
            "operating_days": ["0", "1", "2", "3", "4"], "exclude_maintenance": "on", "approved_by": "DGC",
        })
        v2 = MetricDefinition.objects.get(version="2.0")
        self.assertEqual((v2.operating_days, v2.is_active), ("0,1,2,3,4", False))
        self.client.post(reverse("analytics:activate_definition", args=[v2.pk]))
        self.assertEqual(MetricDefinition.active(), v2)
        self.assertEqual(MetricDefinition.objects.filter(is_active=True).count(), 1)
        self.assertTrue(UtilizationMetric.objects.filter(definition=v2).exists())
        saturday = UtilizationMetric.objects.filter(definition=v2, period_start__week_day=7).first()
        self.assertEqual(saturday.available_hours, 0)  # Saturdays no longer count

    def test_recalculate_requires_admin_role(self):
        self.login(self.depthead)
        response = self.client.post(reverse("analytics:recalculate"), {"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(response.status_code, 403)

    def test_management_command(self):
        out = StringIO()
        call_command("calculate_utilization", "--days", "3", stdout=out)
        self.assertIn("Calculated", out.getvalue())
