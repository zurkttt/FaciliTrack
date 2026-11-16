import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from facilities.models import Building, Facility, FacilityType, MaintenanceRecord

from .conflicts import check_reservation, check_schedule, has_errors
from .importer import import_schedules, template_csv
from .models import Activity, Reservation, Schedule

PASSWORD = "Pass@12345"


def next_monday():
    today = timezone.localdate()
    return today + datetime.timedelta(days=7 - today.weekday())


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)))


class BaseSchedulingTest(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="B1", name="Building 1", floors=3)
        self.ftype = FacilityType.objects.create(name="Classroom")
        self.room = Facility.objects.create(
            facility_code="B1-101", name="Room 101", building=self.building, facility_type=self.ftype, capacity=40,
        )
        self.big_room = Facility.objects.create(
            facility_code="B1-HALL", name="Hall", building=self.building, facility_type=self.ftype, capacity=200,
        )
        self.monday = next_monday()
        self.activity = Activity.objects.create(code="IT101-A", title="Intro to Computing")
        # Class every Monday 08:00-10:00 in B1-101 for a term around next Monday
        self.schedule = Schedule.objects.create(
            facility=self.room, activity=self.activity, day_of_week=0,
            start_time=datetime.time(8), end_time=datetime.time(10),
            term_start=self.monday - datetime.timedelta(days=30), term_end=self.monday + datetime.timedelta(days=60),
            expected_attendees=35,
        )
        self.viewer = User.objects.create_user("viewer", password=PASSWORD, role=User.Role.VIEWER)
        self.other = User.objects.create_user("other", password=PASSWORD, role=User.Role.VIEWER)
        self.approver = User.objects.create_user("fac", password=PASSWORD, role=User.Role.FACILITIES)
        self.registrar = User.objects.create_user("reg", password=PASSWORD, role=User.Role.REGISTRAR)

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)

    def kinds(self, conflicts):
        return {c.kind for c in conflicts}


class ReservationConflictTests(BaseSchedulingTest):
    def test_overlap_with_class_is_error(self):
        conflicts = check_reservation(self.room, at(self.monday, 9), at(self.monday, 11))
        self.assertIn("SCHEDULE_OVERLAP", self.kinds(conflicts))
        self.assertTrue(has_errors(conflicts))

    def test_back_to_back_is_not_a_conflict(self):
        self.assertEqual(check_reservation(self.room, at(self.monday, 10), at(self.monday, 12)), [])

    def test_class_outside_its_term_is_ignored(self):
        later = self.monday + datetime.timedelta(days=70)  # a Monday after term_end
        self.assertEqual(check_reservation(self.room, at(later, 8), at(later, 10)), [])

    def test_approved_overlap_is_error_pending_is_warning(self):
        r = Reservation.objects.create(
            facility=self.room, requested_by=self.other, purpose="Seminar",
            start_at=at(self.monday, 13), end_at=at(self.monday, 15), status=Reservation.Status.PENDING,
        )
        conflicts = check_reservation(self.room, at(self.monday, 14), at(self.monday, 16))
        self.assertEqual(self.kinds(conflicts), {"PENDING_OVERLAP"})
        self.assertFalse(has_errors(conflicts))
        r.status = Reservation.Status.APPROVED
        r.save()
        self.assertTrue(has_errors(check_reservation(self.room, at(self.monday, 14), at(self.monday, 16))))

    def test_rejected_and_cancelled_are_ignored(self):
        for status in (Reservation.Status.REJECTED, Reservation.Status.CANCELLED):
            Reservation.objects.create(
                facility=self.room, requested_by=self.other, purpose="x",
                start_at=at(self.monday, 13), end_at=at(self.monday, 15), status=status,
            )
        self.assertEqual(check_reservation(self.room, at(self.monday, 13), at(self.monday, 15)), [])

    def test_outside_operating_hours(self):
        conflicts = check_reservation(self.room, at(self.monday, 20), at(self.monday, 22))
        self.assertIn("OUTSIDE_HOURS", self.kinds(conflicts))

    def test_over_capacity_is_warning(self):
        conflicts = check_reservation(self.room, at(self.monday, 13), at(self.monday, 15), attendees=41)
        self.assertEqual(self.kinds(conflicts), {"OVER_CAPACITY"})
        self.assertFalse(has_errors(conflicts))

    def test_maintenance_record_blocks_only_its_period(self):
        MaintenanceRecord.objects.create(
            facility=self.room, reason="Aircon", start_at=at(self.monday, 12), end_at=at(self.monday, 17),
        )
        self.room.status = Facility.Status.MAINTENANCE
        self.room.save()
        self.assertIn("MAINTENANCE", self.kinds(check_reservation(self.room, at(self.monday, 13), at(self.monday, 14))))
        tuesday = self.monday + datetime.timedelta(days=1)
        self.assertEqual(check_reservation(self.room, at(tuesday, 13), at(tuesday, 14)), [])

    def test_maintenance_status_without_record_blocks(self):
        self.room.status = Facility.Status.MAINTENANCE
        self.room.save()
        self.assertIn("MAINTENANCE", self.kinds(check_reservation(self.room, at(self.monday, 13), at(self.monday, 14))))

    def test_closed_and_inactive_block(self):
        self.room.status = Facility.Status.CLOSED
        self.room.save()
        self.assertIn("CLOSED", self.kinds(check_reservation(self.room, at(self.monday, 13), at(self.monday, 14))))
        self.big_room.is_active = False
        self.big_room.save()
        self.assertIn("INACTIVE", self.kinds(check_reservation(self.big_room, at(self.monday, 13), at(self.monday, 14))))


class ScheduleConflictTests(BaseSchedulingTest):
    def check(self, **kwargs):
        args = dict(
            facility=self.room, day_of_week=0, start_time=datetime.time(9), end_time=datetime.time(11),
            term_start=self.schedule.term_start, term_end=self.schedule.term_end,
        )
        args.update(kwargs)
        return check_schedule(**args)

    def test_same_day_overlapping_time_is_error(self):
        self.assertIn("SCHEDULE_OVERLAP", self.kinds(self.check()))

    def test_different_day_is_fine(self):
        self.assertEqual(self.check(day_of_week=1), [])

    def test_non_overlapping_terms_are_fine(self):
        start = self.schedule.term_end + datetime.timedelta(days=1)
        self.assertEqual(self.check(term_start=start, term_end=start + datetime.timedelta(days=90)), [])

    def test_editing_a_schedule_does_not_conflict_with_itself(self):
        s = self.schedule
        self.assertEqual(
            check_schedule(self.room, 0, s.start_time, s.end_time, s.term_start, s.term_end, exclude_pk=s.pk), []
        )

    def test_approved_reservation_in_term_is_warning(self):
        Reservation.objects.create(
            facility=self.room, requested_by=self.other, purpose="Seminar", status=Reservation.Status.APPROVED,
            start_at=at(self.monday, 13), end_at=at(self.monday, 15),
        )
        conflicts = self.check(start_time=datetime.time(14), end_time=datetime.time(16))
        self.assertEqual(self.kinds(conflicts), {"RESERVATION_OVERLAP"})
        self.assertFalse(has_errors(conflicts))


class ScheduleImportTests(BaseSchedulingTest):
    HEADER = "facility_code,activity_code,activity_title,day,start_time,end_time,term_start,term_end,expected_attendees\n"

    def upload(self, body, header=None):
        content = (self.HEADER if header is None else header) + body
        return import_schedules(SimpleUploadedFile("s.csv", content.encode("utf-8")))

    def term(self):
        return f"{self.schedule.term_start},{self.schedule.term_end}"

    def test_valid_rows_are_imported(self):
        result = self.upload(
            f"B1-101,IT202-A,Databases,Tue,08:00,10:00,{self.term()},30\n"
            f"b1-hall,ENG101-A,English,Wednesday,1:00 PM,3:00 PM,{self.term()},\n"
        )
        self.assertTrue(result.committed, result.errors)
        self.assertEqual(result.created, 2)
        self.assertEqual(Schedule.objects.filter(source=Schedule.Source.IMPORT).count(), 2)
        self.assertTrue(Activity.objects.filter(code="ENG101-A").exists())

    def test_one_bad_row_imports_nothing(self):
        result = self.upload(
            f"B1-101,IT202-A,Databases,Tue,08:00,10:00,{self.term()},30\n"
            f"NOPE-1,X,Y,Funday,25:00,10:00,{self.term()},abc\n"
        )
        self.assertFalse(result.committed)
        self.assertEqual(result.created, 0)
        self.assertEqual(result.errors[0][0], 3)  # row 3 of the file
        self.assertEqual(len(result.errors[0][1]), 4)  # facility, day, time, attendees
        self.assertFalse(Schedule.objects.filter(source=Schedule.Source.IMPORT).exists())
        self.assertFalse(Activity.objects.filter(code="IT202-A").exists())

    def test_conflict_with_existing_class_rejected(self):
        result = self.upload(f"B1-101,IT202-A,Databases,Mon,09:00,11:00,{self.term()},30\n")
        self.assertIn("Overlaps class", result.errors[0][1][0])

    def test_clash_between_rows_in_same_file_rejected(self):
        result = self.upload(
            f"B1-HALL,A1,One,Fri,08:00,10:00,{self.term()},30\n"
            f"B1-HALL,A2,Two,Fri,09:00,11:00,{self.term()},30\n"
        )
        self.assertEqual([row for row, _ in result.errors], [3])
        self.assertFalse(Schedule.objects.filter(facility=self.big_room).exists())

    def test_over_capacity_is_warning_only(self):
        result = self.upload(f"B1-101,IT202-A,Databases,Tue,08:00,10:00,{self.term()},50\n")
        self.assertTrue(result.committed)
        self.assertEqual(len(result.warnings), 1)

    def test_missing_columns(self):
        result = self.upload("x\n", header="facility_code,day\n")
        self.assertIn("Missing column", result.file_error)

    def test_template_has_header_and_examples(self):
        lines = template_csv().strip().splitlines()
        self.assertTrue(lines[0].startswith("facility_code,activity_code"))
        self.assertEqual(len(lines), 3)

    def test_import_page_registrar_only(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("scheduling:schedule_import")).status_code, 403)
        self.login(self.registrar)
        f = SimpleUploadedFile("s.csv", (self.HEADER + f"B1-101,IT202-A,Databases,Tue,08:00,10:00,{self.term()},30\n").encode())
        response = self.client.post(reverse("scheduling:schedule_import"), {"file": f})
        self.assertContains(response, "Imported 1 schedule")


class ReservationWorkflowTests(BaseSchedulingTest):
    def request_data(self, **overrides):
        data = {
            "facility": self.room.pk, "purpose": "Org meeting", "date": self.monday.isoformat(),
            "start_time": "13:00", "end_time": "15:00", "expected_attendees": 20,
        }
        data.update(overrides)
        return data

    def make_pending(self, user=None, **kwargs):
        defaults = dict(
            facility=self.room, requested_by=user or self.viewer, purpose="Org meeting",
            start_at=at(self.monday, 13), end_at=at(self.monday, 15), expected_attendees=20,
        )
        defaults.update(kwargs)
        return Reservation.objects.create(**defaults)

    def test_user_requests_reservation(self):
        self.login(self.viewer)
        response = self.client.post(reverse("scheduling:reservation_create"), self.request_data())
        r = Reservation.objects.get()
        self.assertRedirects(response, reverse("scheduling:reservation_detail", args=[r.pk]))
        self.assertEqual((r.status, r.requested_by), (Reservation.Status.PENDING, self.viewer))

    def test_request_overlapping_class_is_blocked(self):
        self.login(self.viewer)
        response = self.client.post(reverse("scheduling:reservation_create"), self.request_data(start_time="09:00"))
        self.assertContains(response, "Overlaps class")
        self.assertFalse(Reservation.objects.exists())

    def test_warning_requires_acknowledgement(self):
        self.login(self.viewer)
        response = self.client.post(reverse("scheduling:reservation_create"), self.request_data(expected_attendees=60))
        self.assertContains(response, "exceed the capacity")
        self.assertFalse(Reservation.objects.exists())
        self.client.post(reverse("scheduling:reservation_create"), self.request_data(expected_attendees=60, acknowledge_warnings="on"))
        self.assertTrue(Reservation.objects.exists())

    def test_past_reservation_rejected(self):
        self.login(self.viewer)
        yesterday = timezone.localdate() - datetime.timedelta(days=1)
        response = self.client.post(reverse("scheduling:reservation_create"), self.request_data(date=yesterday.isoformat()))
        self.assertContains(response, "cannot start in the past")

    def test_approver_approves(self):
        r = self.make_pending()
        self.login(self.approver)
        self.client.post(reverse("scheduling:reservation_approve", args=[r.pk]), {"remarks": "OK"})
        r.refresh_from_db()
        self.assertEqual((r.status, r.reviewed_by, r.remarks), (Reservation.Status.APPROVED, self.approver, "OK"))

    def test_viewer_cannot_approve(self):
        r = self.make_pending()
        self.login(self.viewer)
        self.assertEqual(self.client.post(reverse("scheduling:reservation_approve", args=[r.pk])).status_code, 403)

    def test_approval_rechecks_conflicts(self):
        first = self.make_pending()
        second = self.make_pending(user=self.other, purpose="Clash")
        self.login(self.approver)
        self.client.post(reverse("scheduling:reservation_approve", args=[first.pk]))
        self.client.post(reverse("scheduling:reservation_approve", args=[second.pk]))
        second.refresh_from_db()
        self.assertEqual(second.status, Reservation.Status.PENDING)

    def test_users_only_see_their_own_reservations(self):
        r = self.make_pending(user=self.other)
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("scheduling:reservation_detail", args=[r.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("scheduling:reservation_list")), "Org meeting")
        self.login(self.approver)
        self.assertContains(self.client.get(reverse("scheduling:reservation_list")), "Org meeting")

    def test_owner_cancels(self):
        r = self.make_pending()
        self.login(self.viewer)
        self.client.post(reverse("scheduling:reservation_cancel", args=[r.pk]))
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.Status.CANCELLED)

    def test_other_user_cannot_cancel(self):
        r = self.make_pending(user=self.other)
        self.login(self.viewer)
        self.assertEqual(self.client.post(reverse("scheduling:reservation_cancel", args=[r.pk])).status_code, 403)


class AvailabilityAndCalendarTests(BaseSchedulingTest):
    def test_busy_room_excluded_and_small_rooms_filtered(self):
        self.login(self.viewer)
        params = {"date": self.monday.isoformat(), "start_time": "08:30", "end_time": "09:30"}
        response = self.client.get(reverse("scheduling:availability"), params)
        self.assertNotContains(response, "B1-101")  # class at that time
        self.assertContains(response, "B1-HALL")
        params.update(start_time="13:00", end_time="14:00", attendees=100)
        response = self.client.get(reverse("scheduling:availability"), params)
        self.assertNotContains(response, "B1-101")  # too small
        self.assertContains(response, "B1-HALL")

    def test_calendar_shows_class_and_reservation(self):
        Reservation.objects.create(
            facility=self.room, requested_by=self.viewer, purpose="Org meeting", status=Reservation.Status.APPROVED,
            start_at=at(self.monday, 13), end_at=at(self.monday, 15),
        )
        self.login(self.viewer)
        response = self.client.get(reverse("scheduling:facility_calendar", args=[self.room.pk]), {"week": self.monday.isoformat()})
        self.assertContains(response, "IT101-A")
        self.assertContains(response, "Org meeting")

    def test_schedule_edit_by_registrar_only(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse("scheduling:schedule_create")).status_code, 403)
        self.login(self.registrar)
        self.assertEqual(self.client.get(reverse("scheduling:schedule_update", args=[self.schedule.pk])).status_code, 200)
