"""Load demo data for development and the capstone defense.

    python manage.py seed_demo                  # create anything missing
    python manage.py seed_demo --reset          # regenerate schedules, usage, maintenance and metrics
    python manage.py seed_demo --password "MyDemoPass1"

Safe to run more than once: existing records are reused, not duplicated.
The timetable is designed to show a realistic mix for the analytics and risk modules:
an over-demanded computer lab, moderately used classrooms, underused rooms, a room with
frequent no-shows, a room with poor usage-data coverage, an overcrowded study area and
an over-capacity class, and a room under maintenance.
"""
import datetime
import itertools
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Department, User
from analytics.engine import calculate
from analytics.models import MetricDefinition, UtilizationMetric
from core.models import SystemSetting
from facilities.models import Building, Facility, FacilityType, MaintenanceRecord
from risks import engine as risk_engine
from risks.models import Risk
from scheduling.models import Activity, Reservation, Schedule
from usage.models import UsageRecord
from usage.sessions import sessions_between

TERM_WEEKS_BEFORE_TODAY = 9
TERM_WEEKS_AFTER_TODAY = 9
USAGE_HISTORY_DAYS = 56

DEPARTMENTS = [
    ("CCS", "College of Computer Studies"),
    ("COE", "College of Engineering"),
    ("CAS", "College of Arts and Sciences"),
    ("ADMIN", "Office of Administration"),
    ("REG", "Office of the Registrar"),
]

USERS = [
    ("admin", "System", "Administrator", User.Role.ADMIN, "ADMIN", True),
    ("facilities", "Fiona", "Reyes", User.Role.FACILITIES, "ADMIN", False),
    ("registrar", "Ramon", "Santos", User.Role.REGISTRAR, "REG", False),
    ("planner", "Paula", "Cruz", User.Role.PLANNER, "ADMIN", False),
    ("depthead", "Dante", "Garcia", User.Role.DEPT_HEAD, "CCS", False),
    ("viewer", "Vina", "Lopez", User.Role.VIEWER, "CAS", False),
]

BUILDINGS = [
    ("MAIN", "Main Building", 4),
    ("ENG", "Engineering Building", 3),
    ("LIB", "University Library", 2),
]

FACILITY_TYPES = ["Classroom", "Computer Laboratory", "Science Laboratory", "Lecture Hall", "Library Study Area", "Meeting Room"]

# code, name, building, type, floor, capacity, owner dept
FACILITIES = [
    ("MAIN-101", "Room 101", "MAIN", "Classroom", 1, 40, "CAS"),
    ("MAIN-102", "Room 102", "MAIN", "Classroom", 1, 40, "CAS"),
    ("MAIN-201", "Room 201", "MAIN", "Classroom", 2, 45, "CAS"),
    ("MAIN-AVR", "Audio-Visual Room", "MAIN", "Lecture Hall", 3, 150, "ADMIN"),
    ("MAIN-CONF", "Conference Room", "MAIN", "Meeting Room", 4, 20, "ADMIN"),
    ("ENG-CL1", "Computer Lab 1", "ENG", "Computer Laboratory", 1, 35, "CCS"),
    ("ENG-CL2", "Computer Lab 2", "ENG", "Computer Laboratory", 1, 35, "CCS"),
    ("ENG-SL1", "Physics Laboratory", "ENG", "Science Laboratory", 2, 30, "COE"),
    ("ENG-301", "Room 301", "ENG", "Classroom", 3, 50, "COE"),
    ("LIB-SA1", "Study Area A", "LIB", "Library Study Area", 1, 60, "ADMIN"),
    ("LIB-DR1", "Discussion Room 1", "LIB", "Meeting Room", 2, 10, "ADMIN"),
]

WEEKDAYS, MWF, TTH, SAT, FRI = [0, 1, 2, 3, 4], [0, 2, 4], [1, 3], [5], [4]

# facility, dept, days, [(start, end), ...], expected attendees
TIMETABLE = [
    ("ENG-CL1", "CCS", WEEKDAYS, [("07:00", "10:00"), ("10:00", "13:00"), ("13:00", "16:00"), ("16:00", "19:00"), ("19:00", "21:00")], 34),
    ("ENG-CL1", "CCS", SAT, [("08:00", "11:00"), ("11:00", "14:00")], 30),
    ("ENG-CL2", "CCS", MWF, [("08:00", "11:00"), ("13:00", "16:00")], 32),
    ("ENG-CL2", "CCS", TTH, [("10:00", "13:00")], 38),  # over capacity on purpose (35 seats)
    ("ENG-CL2", "CCS", TTH, [("14:00", "17:00")], 30),
    ("MAIN-101", "CAS", WEEKDAYS, [("07:30", "09:00"), ("09:00", "10:30"), ("10:30", "12:00"), ("13:00", "14:30")], 38),
    ("MAIN-201", "CAS", WEEKDAYS, [("09:00", "12:00"), ("13:00", "17:00")], 42),
    ("MAIN-102", "CAS", MWF, [("08:00", "09:30"), ("10:00", "11:30")], 25),
    ("ENG-SL1", "COE", TTH, [("08:00", "11:00"), ("13:00", "16:00")], 26),
    ("ENG-301", "COE", WEEKDAYS, [("13:00", "15:00")], 30),
    ("MAIN-AVR", "ADMIN", FRI, [("13:00", "16:00")], 90),
]

COURSE_TITLES = {
    "CCS": ("IT", ["Introduction to Computing", "Computer Programming 1", "Computer Programming 2", "Data Structures",
                   "Database Systems", "Web Development", "Networking 1", "Operating Systems", "Software Engineering",
                   "Information Management", "Systems Analysis and Design", "Human-Computer Interaction",
                   "Mobile Application Development", "Capstone Project 1", "Information Assurance", "Data Analytics"]),
    "COE": ("EE", ["Circuit Analysis", "Physics for Engineers", "Engineering Drawing", "Statics of Rigid Bodies"]),
    "CAS": ("GE", ["Purposive Communication", "Mathematics in the Modern World", "Readings in Philippine History",
                   "Ethics", "Understanding the Self", "Science, Technology and Society", "Art Appreciation",
                   "The Contemporary World", "Rizal's Life and Works", "Statistics"]),
    "ADMIN": ("EVT", ["University Seminar Series"]),
}

# Per-facility behaviour of the generated usage history
NO_SHOW_RATE = {"MAIN-102": 0.35}       # "ghost bookings": booked but often unused
UNRECORDED_RATE = {"ENG-301": 0.45}     # poor data coverage
DEFAULT_NO_SHOW, DEFAULT_UNRECORDED = 0.07, 0.08

SETTINGS = [
    ("risk.underutilization_threshold", "30", "Utilization % below which a facility is flagged as underutilized"),
    ("risk.overdemand_threshold", "85", "Utilization % above which a facility is flagged as over-demanded"),
    ("risk.overcrowding_ratio", "1.0", "Attendees/capacity ratio above which overcrowding is flagged"),
    ("risk.maintenance_days", "14", "Days under maintenance before a facility is flagged as unavailable"),
    ("risk.stale_data_days", "7", "Days without usage data before data quality is flagged"),
]


def _t(text):
    return datetime.time.fromisoformat(text)


class Command(BaseCommand):
    help = "Load demo departments, users, buildings, facilities, timetable, usage history and metrics."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo@12345", help="Password for new demo users.")
        parser.add_argument("--reset", action="store_true",
                            help="Delete and regenerate schedules, reservations, usage, maintenance and metrics (DEBUG only).")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            if not settings.DEBUG:
                raise CommandError("--reset is only allowed when DEBUG=True.")
            self._reset()

        depts = {}
        for code, name in DEPARTMENTS:
            depts[code], _ = Department.objects.get_or_create(code=code, defaults={"name": name})

        for username, first, last, role, dept, is_super in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first, "last_name": last, "email": f"{username}@facilitrack.local",
                    "role": role, "department": depts[dept], "is_staff": is_super, "is_superuser": is_super,
                },
            )
            if created:
                user.set_password(options["password"])
                user.save()
        recorder = User.objects.get(username="facilities")

        buildings = {}
        for code, name, floors in BUILDINGS:
            buildings[code], _ = Building.objects.get_or_create(code=code, defaults={"name": name, "floors": floors})
        types = {name: FacilityType.objects.get_or_create(name=name)[0] for name in FACILITY_TYPES}
        facilities = {}
        for code, name, bldg, ftype, floor, cap, dept in FACILITIES:
            facilities[code], _ = Facility.objects.get_or_create(
                facility_code=code,
                defaults={"name": name, "building": buildings[bldg], "facility_type": types[ftype],
                          "floor": floor, "capacity": cap, "owner_department": depts[dept]},
            )

        definition, _ = MetricDefinition.objects.get_or_create(
            version="1.0", defaults={"is_active": True, "approved_by": "Data Governance Council (demo)"},
        )
        for key, value, desc in SETTINGS:
            SystemSetting.objects.get_or_create(key=key, defaults={"value": value, "description": desc})

        if not Schedule.objects.exists():
            self.stdout.write(f"Created {self._seed_timetable(facilities, depts)} weekly class slots.")
        if not MaintenanceRecord.objects.exists():
            self._seed_maintenance(facilities, recorder)
        if not UsageRecord.objects.exists():
            self.stdout.write(f"Generated {self._seed_usage(facilities, recorder)} usage records ({USAGE_HISTORY_DAYS} days).")
        if not Reservation.objects.exists():
            self.stdout.write(f"Created {self._seed_reservations(facilities)} reservations.")
        if not UtilizationMetric.objects.filter(definition=definition).exists():
            today = timezone.localdate()
            rows = calculate(today - datetime.timedelta(days=USAGE_HISTORY_DAYS), today, definition)
            self.stdout.write(f"Calculated {rows} facility-days of utilization.")
        if not Risk.objects.exists():
            result = risk_engine.run()
            self.stdout.write(f"Risk detection: {result.created} risks opened.")

        self.stdout.write(self.style.SUCCESS("Demo data loaded."))
        self.stdout.write(f"Demo users: {', '.join(u[0] for u in USERS)}  (password for new users: {options['password']})")

    def _reset(self):
        counts = []
        for model in (Risk, UtilizationMetric, UsageRecord, Reservation, Schedule, Activity, MaintenanceRecord):
            counts.append(f"{model._meta.verbose_name_plural}: {model.objects.all().delete()[0]}")
        Facility.objects.filter(facility_code="LIB-DR1").update(status=Facility.Status.AVAILABLE)
        self.stdout.write("Reset " + ", ".join(counts))

    def _seed_timetable(self, facilities, depts):
        today = timezone.localdate()
        term_start = today - datetime.timedelta(weeks=TERM_WEEKS_BEFORE_TODAY)
        term_end = today + datetime.timedelta(weeks=TERM_WEEKS_AFTER_TODAY)
        titles = {dept: itertools.cycle(enumerate(names, start=101)) for dept, (_, names) in COURSE_TITLES.items()}
        sections = {}
        created = 0
        for fac_code, dept, days, blocks, attendees in TIMETABLE:
            prefix = COURSE_TITLES[dept][0]
            for start, end in blocks:
                number, title = next(titles[dept])
                sections[(prefix, number)] = sections.get((prefix, number), 0) + 1
                code = f"{prefix}{number}-{'ABCDEFGH'[sections[(prefix, number)] - 1]}"
                if attendees == 38 and fac_code == "ENG-CL2":
                    code, title = "IT202-B", "Database Systems"
                activity, _ = Activity.objects.get_or_create(code=code, defaults={
                    "title": title, "department": depts[dept],
                    "activity_type": Activity.Type.EVENT if dept == "ADMIN" else Activity.Type.CLASS,
                })
                for day in days:
                    Schedule.objects.create(
                        facility=facilities[fac_code], activity=activity, day_of_week=day,
                        start_time=_t(start), end_time=_t(end), term_start=term_start, term_end=term_end,
                        expected_attendees=attendees, source=Schedule.Source.IMPORT,
                    )
                    created += 1
        return created

    def _seed_reservations(self, facilities):
        """Rejected requests (demand evidence), a conflicting approved booking, and pending requests."""
        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        viewer, dept_head = User.objects.get(username="viewer"), User.objects.get(username="depthead")
        reviewer = User.objects.get(username="facilities")

        def at(day, hhmm):
            return timezone.make_aware(datetime.datetime.combine(day, _t(hhmm)), tz)

        def next_weekday(days_ahead, weekday=None):
            day = today + datetime.timedelta(days=days_ahead)
            while day.weekday() == 6 or (weekday is not None and day.weekday() != weekday):
                day += datetime.timedelta(days=1)
            return day

        rows = []
        for i, days_ago in enumerate((3, 8, 12, 19)):
            day = today - datetime.timedelta(days=days_ago)
            if day.weekday() == 6:
                day -= datetime.timedelta(days=1)
            rows.append(Reservation(
                facility=facilities["ENG-CL1"], requested_by=dept_head, purpose=f"Programming workshop {i + 1}",
                start_at=at(day, "10:00"), end_at=at(day, "12:00"), expected_attendees=30,
                status=Reservation.Status.REJECTED, reviewed_by=reviewer, reviewed_at=timezone.now(),
                remarks="No free slot: the lab is fully booked.",
            ))
        defense_day = next_weekday(3)
        rows.append(Reservation(  # approved before the leak: now conflicts with maintenance
            facility=facilities["LIB-DR1"], requested_by=viewer, purpose="Thesis defense panel",
            start_at=at(defense_day, "10:00"), end_at=at(defense_day, "12:00"), expected_attendees=8,
            status=Reservation.Status.APPROVED, reviewed_by=reviewer, reviewed_at=timezone.now() - datetime.timedelta(days=25),
        ))
        orientation_day = next_weekday(2, weekday=2)
        rows.append(Reservation(
            facility=facilities["MAIN-AVR"], requested_by=dept_head, purpose="CCS freshman orientation",
            start_at=at(orientation_day, "09:00"), end_at=at(orientation_day, "12:00"), expected_attendees=120,
        ))
        meeting_day = next_weekday(1)
        rows.append(Reservation(
            facility=facilities["MAIN-CONF"], requested_by=viewer, purpose="Faculty meeting",
            start_at=at(meeting_day, "10:00"), end_at=at(meeting_day, "11:00"), expected_attendees=15,
        ))
        Reservation.objects.bulk_create(rows)
        return len(rows)

    def _seed_maintenance(self, facilities, recorder):
        now = timezone.now()
        MaintenanceRecord.objects.create(
            facility=facilities["LIB-DR1"], reason="Water leak and ceiling repair",
            start_at=now - datetime.timedelta(days=18), status=MaintenanceRecord.Status.ONGOING, created_by=recorder,
        )
        Facility.objects.filter(pk=facilities["LIB-DR1"].pk).update(status=Facility.Status.MAINTENANCE)
        MaintenanceRecord.objects.create(
            facility=facilities["ENG-SL1"], reason="Electrical rewiring",
            start_at=now - datetime.timedelta(days=15), end_at=now - datetime.timedelta(days=12),
            status=MaintenanceRecord.Status.COMPLETED, created_by=recorder,
        )

    def _seed_usage(self, facilities, recorder):
        rng = random.Random(42)  # fixed seed: every team member gets the same demo numbers
        today = timezone.localdate()
        start = today - datetime.timedelta(days=USAGE_HISTORY_DAYS)
        tz = timezone.get_current_timezone()
        now = timezone.now()
        records = []

        for s in sessions_between(start, today):
            if s.end_at > now:
                continue
            code = s.facility.facility_code
            roll = rng.random()
            if roll < UNRECORDED_RATE.get(code, DEFAULT_UNRECORDED):
                continue
            if rng.random() < NO_SHOW_RATE.get(code, DEFAULT_NO_SHOW):
                records.append(UsageRecord(
                    facility=s.facility, schedule=s.schedule, start_at=s.start_at, end_at=s.end_at,
                    status=UsageRecord.Status.NO_SHOW, source=UsageRecord.Source.MANUAL, recorded_by=recorder,
                ))
                continue
            low = 0.8 if code == "ENG-CL1" else 0.6
            records.append(UsageRecord(
                facility=s.facility, schedule=s.schedule,
                start_at=s.start_at + datetime.timedelta(minutes=rng.randint(0, 10)),
                end_at=s.end_at - datetime.timedelta(minutes=rng.randint(0, 10)),
                headcount=round(s.expected * rng.uniform(low, 1.06)),
                source=rng.choice([UsageRecord.Source.QR, UsageRecord.Source.MANUAL]), recorded_by=recorder,
            ))

        # Walk-in use: a busy library study area (sometimes overcrowded) and occasional meetings
        walk_ins = [("LIB-SA1", "09:00", "12:00", 0.85, (35, 72)), ("LIB-SA1", "13:00", "17:00", 0.85, (35, 72)),
                    ("MAIN-CONF", "14:00", "15:30", 0.5, (6, 16))]
        for offset in range(USAGE_HISTORY_DAYS + 1):
            day = start + datetime.timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            for code, begin, end, chance, (low, high) in walk_ins:
                begin_at = timezone.make_aware(datetime.datetime.combine(day, _t(begin)), tz)
                end_at = timezone.make_aware(datetime.datetime.combine(day, _t(end)), tz)
                if end_at <= now and rng.random() < chance:
                    records.append(UsageRecord(
                        facility=facilities[code], start_at=begin_at, end_at=end_at, headcount=rng.randint(low, high),
                        source=UsageRecord.Source.QR, recorded_by=recorder,
                    ))
        UsageRecord.objects.bulk_create(records)  # bulk insert: demo data is not written to the audit log
        return len(records)
