import datetime
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from facilities.models import Building, Facility, FacilityType
from scheduling.models import Activity, Reservation, Schedule

from .importer import import_usage
from .models import UsageRecord
from .reports import NO_DATA, NO_SHOW, USED, compare
from .sessions import find_session, sessions_between

PASSWORD = "Pass@12345"
TZ = timezone.get_current_timezone()


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)), TZ)


class UsageTestBase(TestCase):
    """A fixed 'now' of Wednesday 2026-11-18 10:30 keeps every test deterministic."""

    MONDAY = datetime.date(2026, 11, 16)
    WEDNESDAY = datetime.date(2026, 11, 18)

    def setUp(self):
        self.now = at(self.WEDNESDAY, 10, 30)
        patcher = mock.patch("django.utils.timezone.now", return_value=self.now)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)

        building = Building.objects.create(code="B1", name="Building 1", floors=3)
        ftype = FacilityType.objects.create(name="Lab")
        self.lab = Facility.objects.create(facility_code="B1-LAB", name="Lab", building=building, facility_type=ftype, capacity=30)
        self.room = Facility.objects.create(facility_code="B1-201", name="Room", building=building, facility_type=ftype, capacity=40)
        activity = Activity.objects.create(code="IT101-A", title="Intro")
        # Mon & Wed 09:00-11:00 in the lab; Wednesday's is in progress at "now".
        self.mon = Schedule.objects.create(
            facility=self.lab, activity=activity, day_of_week=0, start_time=datetime.time(9), end_time=datetime.time(11),
            term_start=datetime.date(2026, 8, 1), term_end=datetime.date(2026, 12, 15), expected_attendees=28,
        )
        self.wed = Schedule.objects.create(
            facility=self.lab, activity=activity, day_of_week=2, start_time=datetime.time(9), end_time=datetime.time(11),
            term_start=datetime.date(2026, 8, 1), term_end=datetime.date(2026, 12, 15), expected_attendees=28,
        )
        self.viewer = User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER)
        self.staff = User.objects.create_user("fac", password=PASSWORD, role=User.Role.FACILITIES)
        self.planner = User.objects.create_user("plan", password=PASSWORD, role=User.Role.PLANNER)

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)


class SessionTests(UsageTestBase):
    def test_sessions_expand_weekly_schedule(self):
        sessions = sessions_between(self.MONDAY, self.MONDAY + datetime.timedelta(days=6))
        self.assertEqual([s.start_at for s in sessions], [at(self.MONDAY, 9), at(self.WEDNESDAY, 9)])

    def test_approved_reservations_are_sessions_pending_are_not(self):
        for status in (Reservation.Status.APPROVED, Reservation.Status.PENDING):
            Reservation.objects.create(
                facility=self.room, requested_by=self.viewer, purpose=status, status=status,
                start_at=at(self.MONDAY, 13), end_at=at(self.MONDAY, 15),
            )
        labels = [s.label for s in sessions_between(self.MONDAY, self.MONDAY)]
        self.assertIn("APPROVED", labels)
        self.assertNotIn("PENDING", labels)

    def test_find_session_allows_early_checkin(self):
        self.assertEqual(find_session(self.lab, at(self.MONDAY, 8, 50)).schedule, self.mon)
        self.assertIsNone(find_session(self.lab, at(self.MONDAY, 8, 30)))
        self.assertIsNone(find_session(self.lab, at(self.MONDAY, 11, 0)))


class CheckInTests(UsageTestBase):
    def checkin_url(self, facility):
        return reverse("usage:checkin", args=[facility.facility_code])

    def test_checkin_links_to_running_class_and_ends_with_it(self):
        self.login(self.viewer)
        self.client.post(self.checkin_url(self.lab), {"headcount": 25, "duration": 60})
        r = UsageRecord.objects.get()
        self.assertEqual((r.schedule, r.source, r.headcount, r.recorded_by), (self.wed, UsageRecord.Source.QR, 25, self.viewer))
        self.assertEqual((r.start_at, r.end_at), (self.now, at(self.WEDNESDAY, 11)))

    def test_unscheduled_checkin_uses_duration(self):
        self.login(self.viewer)
        self.client.post(self.checkin_url(self.room), {"duration": 90})
        r = UsageRecord.objects.get()
        self.assertIsNone(r.schedule)
        self.assertEqual(r.end_at, self.now + datetime.timedelta(minutes=90))

    def test_second_checkin_does_not_duplicate(self):
        self.login(self.viewer)
        self.client.post(self.checkin_url(self.lab), {"duration": 60})
        self.client.post(self.checkin_url(self.lab), {"duration": 60})
        self.assertEqual(UsageRecord.objects.count(), 1)

    def test_checkin_url_is_case_insensitive_and_requires_login(self):
        url = reverse("usage:checkin", args=["b1-lab"])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.viewer)
        self.assertContains(self.client.get(url), "IT101-A")

    def test_end_now_and_headcount_by_owner_only(self):
        self.login(self.viewer)
        self.client.post(self.checkin_url(self.lab), {"duration": 60})
        r = UsageRecord.objects.get()
        other = User.objects.create_user("other", password=PASSWORD)
        self.login(other)
        self.assertEqual(self.client.post(reverse("usage:record_end_now", args=[r.pk])).status_code, 403)
        self.login(self.viewer)
        self.client.post(reverse("usage:record_update_headcount", args=[r.pk]), {"headcount": 31})
        later = self.now + datetime.timedelta(minutes=20)
        self.clock.return_value = later
        self.client.post(reverse("usage:record_end_now", args=[r.pk]))
        r.refresh_from_db()
        self.assertEqual((r.headcount, r.end_at), (31, later))
        self.assertTrue(r.over_capacity)

    def test_immediate_checkout_cancels_checkin(self):
        self.login(self.viewer)
        self.client.post(self.checkin_url(self.lab), {"duration": 60})
        self.client.post(reverse("usage:record_end_now", args=[UsageRecord.objects.get().pk]))
        self.assertFalse(UsageRecord.objects.exists())

    def test_qr_page_staff_only(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("usage:qr_codes")).status_code, 403)
        self.login(self.staff)
        response = self.client.get(reverse("usage:qr_codes"))
        self.assertContains(response, "<svg")
        self.assertContains(response, "/usage/checkin/B1-LAB/")


class AttendanceTests(UsageTestBase):
    def mark(self, schedule, day, status, headcount=""):
        return self.client.post(reverse("usage:attendance_mark"), {
            "kind": "S", "id": schedule.pk, "date": day.isoformat(), "status": status, "headcount": headcount,
            "next": "/usage/attendance/",
        })

    def test_mark_used_then_change_to_no_show_updates_same_record(self):
        self.login(self.staff)
        self.mark(self.mon, self.MONDAY, "USED", 20)
        self.mark(self.mon, self.MONDAY, "NO_SHOW")
        r = UsageRecord.objects.get()
        self.assertEqual((r.status, r.headcount, r.start_at), (UsageRecord.Status.NO_SHOW, None, at(self.MONDAY, 9)))

    def test_cannot_mark_future_session(self):
        self.login(self.staff)
        next_monday = self.MONDAY + datetime.timedelta(days=7)
        self.mark(self.mon, next_monday, "USED")
        self.assertFalse(UsageRecord.objects.exists())

    def test_wrong_weekday_rejected(self):
        self.login(self.staff)
        self.mark(self.mon, self.MONDAY + datetime.timedelta(days=1), "USED")
        self.assertFalse(UsageRecord.objects.exists())

    def test_viewer_cannot_mark(self):
        self.login(self.viewer)
        self.assertEqual(self.mark(self.mon, self.MONDAY, "USED").status_code, 403)

    def test_external_next_url_is_ignored(self):
        self.login(self.staff)
        response = self.client.post(reverse("usage:attendance_mark"), {
            "kind": "S", "id": self.mon.pk, "date": self.MONDAY.isoformat(), "status": "USED", "next": "https://evil.example/",
        })
        self.assertEqual(response["Location"], reverse("usage:attendance"))

    def test_sheet_lists_sessions(self):
        self.login(self.staff)
        response = self.client.get(reverse("usage:attendance"), {"date": self.MONDAY.isoformat()})
        self.assertContains(response, "IT101-A")


class ManualAndImportTests(UsageTestBase):
    def test_manual_unscheduled_use(self):
        self.login(self.staff)
        self.client.post(reverse("usage:record_create"), {
            "facility": self.room.pk, "date": self.MONDAY.isoformat(), "start_time": "13:00", "end_time": "14:00", "headcount": 5,
        })
        r = UsageRecord.objects.get()
        self.assertEqual((r.source, r.schedule, r.recorded_by), (UsageRecord.Source.MANUAL, None, self.staff))

    def test_manual_entry_links_to_class_and_rejects_overlap_and_future(self):
        self.login(self.staff)
        data = {"facility": self.lab.pk, "date": self.MONDAY.isoformat(), "start_time": "09:05", "end_time": "10:55"}
        self.client.post(reverse("usage:record_create"), data)
        self.assertEqual(UsageRecord.objects.get().schedule, self.mon)
        self.assertContains(self.client.post(reverse("usage:record_create"), data), "already recorded")
        data.update(date=self.WEDNESDAY.isoformat(), start_time="11:00", end_time="12:00")
        self.assertContains(self.client.post(reverse("usage:record_create"), data), "cannot end in the future")

    def upload(self, body):
        header = "facility_code,date,start_time,end_time,headcount,status\n"
        return import_usage(SimpleUploadedFile("u.csv", (header + body).encode()), self.staff)

    def test_import_links_rows(self):
        result = self.upload(f"B1-LAB,{self.MONDAY},09:02,10:58,26,USED\nB1-201,{self.MONDAY},15:00,16:00,,\n")
        self.assertTrue(result.committed, result.errors)
        self.assertEqual(UsageRecord.objects.get(facility=self.lab).schedule, self.mon)
        self.assertEqual(len(result.warnings), 1)  # the unscheduled row

    def test_import_all_or_nothing(self):
        result = self.upload(
            f"B1-LAB,{self.MONDAY},09:02,10:58,26,USED\n"
            f"B1-201,{self.WEDNESDAY},13:00,14:00,,USED\n"  # future
            f"B1-201,{self.MONDAY},13:00,14:00,,NO_SHOW\n"  # no-show with nothing scheduled
        )
        self.assertFalse(result.committed)
        self.assertEqual([row for row, _ in result.errors], [3, 4])
        self.assertFalse(UsageRecord.objects.exists())

    def test_import_rejects_duplicates_within_file(self):
        result = self.upload(f"B1-201,{self.MONDAY},13:00,14:00,,\nB1-201,{self.MONDAY},13:30,14:30,,\n")
        self.assertEqual([row for row, _ in result.errors], [3])


class ComparisonTests(UsageTestBase):
    def test_compare_counts_used_no_show_no_data_and_unscheduled(self):
        # Previous week's Monday & Wednesday + this Monday are finished; this Wednesday is still running.
        start = self.MONDAY - datetime.timedelta(days=7)
        UsageRecord.objects.create(facility=self.lab, schedule=self.mon, start_at=at(start, 9), end_at=at(start, 11), headcount=35)
        UsageRecord.objects.create(facility=self.lab, schedule=self.mon, start_at=at(self.MONDAY, 9), end_at=at(self.MONDAY, 11),
                                   status=UsageRecord.Status.NO_SHOW)
        UsageRecord.objects.create(facility=self.room, start_at=at(self.MONDAY, 13), end_at=at(self.MONDAY, 14), headcount=10)

        report = compare(start, self.WEDNESDAY, Facility.objects.all())
        self.assertEqual([r.status for r in report["rows"]], [USED, NO_DATA, NO_SHOW])
        lab = next(s for s in report["summaries"] if s.facility == self.lab)
        self.assertEqual((lab.sessions, lab.used, lab.no_show, lab.no_data, lab.over_capacity), (3, 1, 1, 1, 1))
        self.assertAlmostEqual(lab.coverage_rate, 200 / 3)
        self.assertEqual(report["totals"].unscheduled, 1)
        self.assertAlmostEqual(report["totals"].used_hours, 3.0)

    def test_comparison_page_access(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("usage:comparison")).status_code, 403)
        self.login(self.planner)
        self.assertContains(self.client.get(reverse("usage:comparison")), "By facility")
        self.assertEqual(self.client.get(reverse("usage:attendance")).status_code, 403)  # planners view, not record
