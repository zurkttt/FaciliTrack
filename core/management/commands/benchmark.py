"""Performance benchmark against KPI 14.1 ("common dashboard views load within 3 seconds").

    python manage.py benchmark                       # 150 facilities, 90 days of history
    python manage.py benchmark --facilities 300 --days 120

Builds a campus-sized dataset INSIDE A TRANSACTION, times each page (server-side, median of
3 runs) and counts database queries, then ROLLS BACK: your real data is never changed.
Times exclude network and browser rendering, so add a margin when comparing to the KPI.
"""
import datetime
import random
import statistics
import time

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from analytics.engine import calculate
from analytics.models import MetricDefinition
from facilities.models import Building, Facility, FacilityType
from risks import engine as risk_engine
from scheduling.models import Activity, Schedule
from usage.models import UsageRecord
from usage.sessions import sessions_between

KPI_SECONDS = 3.0
BLOCKS = [("07:00", "09:00"), ("09:00", "11:00"), ("11:00", "13:00"), ("13:00", "15:00"), ("15:00", "17:00"), ("17:00", "19:00")]


class Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Time key pages on a large generated dataset, then roll everything back."

    def add_arguments(self, parser):
        parser.add_argument("--facilities", type=int, default=150)
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--runs", type=int, default=3)

    def handle(self, *args, **opts):
        try:
            with transaction.atomic():
                self._run(opts)
                raise Rollback
        except Rollback:
            self.stdout.write(self.style.SUCCESS("\nAll benchmark data rolled back. Your database is unchanged."))

    # ------------------------------------------------------------------ data
    def _generate(self, n_facilities, days):
        rng = random.Random(7)
        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        definition = MetricDefinition.active() or MetricDefinition.objects.create(version="bench", is_active=True)
        buildings = [Building.objects.create(code=f"BX{i:02d}", name=f"Bench Building {i}", floors=5) for i in range(10)]
        ftype = FacilityType.objects.get_or_create(name="Bench Room")[0]
        facilities = Facility.objects.bulk_create([
            Facility(facility_code=f"BX-{i:04d}", name=f"Bench Room {i}", building=buildings[i % 10],
                     facility_type=ftype, capacity=rng.choice([30, 40, 45, 60, 120]))
            for i in range(n_facilities)
        ])
        activity = Activity.objects.create(code="BENCH-101", title="Benchmark Course")
        schedules = []
        for f in facilities:
            for day in range(6):
                for start, end in rng.sample(BLOCKS, rng.randint(1, 4)):
                    schedules.append(Schedule(
                        facility=f, activity=activity, day_of_week=day,
                        start_time=datetime.time.fromisoformat(start), end_time=datetime.time.fromisoformat(end),
                        term_start=today - datetime.timedelta(days=days), term_end=today + datetime.timedelta(days=60),
                        expected_attendees=int(f.capacity * rng.uniform(0.5, 1.05)),
                    ))
        Schedule.objects.bulk_create(schedules, batch_size=2000)
        start = today - datetime.timedelta(days=days)
        records = []
        now = timezone.now()
        for s in sessions_between(start, today, facilities=facilities):
            if s.end_at > now or rng.random() < 0.12:
                continue
            records.append(UsageRecord(
                facility=s.facility, schedule=s.schedule, start_at=s.start_at, end_at=s.end_at,
                headcount=int(s.expected * rng.uniform(0.6, 1.1)),
            ))
        UsageRecord.objects.bulk_create(records, batch_size=5000)
        return definition, facilities, len(schedules), len(records), tz

    # ------------------------------------------------------------------ timing
    def _time(self, label, fn, runs):
        # An execute hook counts every query. (Django's query log is reset at the start of each
        # request, so CaptureQueriesContext would report 0 for pages fetched through the test client.)
        times, counter = [], {"n": 0}

        def count(execute, sql, params, many, context):
            counter["n"] += 1
            return execute(sql, params, many, context)

        for _ in range(runs):
            counter["n"] = 0
            with connection.execute_wrapper(count):
                t0 = time.perf_counter()
                status = fn()
                times.append(time.perf_counter() - t0)
        queries = counter["n"]
        median = statistics.median(times)
        ok = median <= KPI_SECONDS
        self.rows.append((label, median, queries, status, ok))
        mark = self.style.SUCCESS("PASS") if ok else self.style.ERROR("FAIL")
        self.stdout.write(f"  {label:<46} {median * 1000:>8.0f} ms {queries:>6} queries  {status}  {mark}")

    def _run(self, opts):
        self.rows = []
        n, days, runs = opts["facilities"], opts["days"], opts["runs"]
        self.stdout.write(f"Generating {n} facilities with {days} days of history...")
        t0 = time.perf_counter()
        definition, facilities, n_sched, n_usage, _tz = self._generate(n, days)
        self.stdout.write(f"  {n_sched:,} weekly class slots, {n_usage:,} usage records ({time.perf_counter() - t0:.1f} s)")

        self.stdout.write("\nNightly jobs (not interactive; no 3 s target):")
        today = timezone.localdate()
        t0 = time.perf_counter()
        rows = calculate(today - datetime.timedelta(days=days), today, definition)
        self.stdout.write(f"  utilization calculation: {rows:,} facility-days in {time.perf_counter() - t0:.1f} s")
        t0 = time.perf_counter()
        result = risk_engine.run()
        self.stdout.write(f"  risk detection: {result.findings} findings in {time.perf_counter() - t0:.1f} s")

        users = {role: User.objects.create_user(f"bench-{role.lower()}", password=None, role=role)
                 for role in (User.Role.FACILITIES, User.Role.PLANNER, User.Role.VIEWER)}
        clients = {}
        for role, user in users.items():
            c = Client(HTTP_HOST="localhost")
            c.force_login(user)
            clients[role] = c

        def get(role, name, params=None, **kwargs):
            return lambda: clients[role].get(reverse(name, kwargs=kwargs or None), params or {}).status_code

        f = facilities[0]
        start90 = (today - datetime.timedelta(days=days)).isoformat()
        start28 = (today - datetime.timedelta(days=28)).isoformat()
        monday = today + datetime.timedelta(days=7 - today.weekday())
        P, F, V = User.Role.PLANNER, User.Role.FACILITIES, User.Role.VIEWER

        self.stdout.write(f"\nPages (median of {runs} runs, server time, target <= {KPI_SECONDS:.0f} s):")
        checks = [
            ("Home dashboard (facilities staff)", get(F, "core:dashboard")),
            ("Home dashboard (planner)", get(P, "core:dashboard")),
            ("Home dashboard (viewer)", get(V, "core:dashboard")),
            ("Facility list", get(V, "facilities:list")),
            ("Facility detail", get(P, "facilities:detail", pk=f.pk)),
            ("Facility weekly calendar", get(V, "scheduling:facility_calendar", pk=f.pk)),
            ("Find a room (all facilities)", get(V, "scheduling:availability",
                                                 {"date": monday.isoformat(), "start_time": "13:00", "end_time": "14:00"})),
            ("Utilization dashboard, 28 days", get(P, "analytics:dashboard")),
            (f"Utilization dashboard, {days} days, by building", get(P, "analytics:dashboard",
                                                                    {"start": start90, "end": today.isoformat(), "group": "building"})),
            ("Risk register", get(P, "risks:register")),
            ("Scheduled vs actual, 28 days", get(P, "usage:comparison", {"start": start28, "end": today.isoformat()})),
            ("Attendance sheet (today)", get(F, "usage:attendance")),
            ("Utilization report preview", get(P, "reports:report", slug="utilization")),
            ("Utilization report PDF", get(P, "reports:report", {"format": "pdf"}, slug="utilization")),
            ("Risk register Excel", get(P, "reports:report", {"format": "xlsx"}, slug="risks")),
        ]
        for label, fn in checks:
            self._time(label, fn, runs)

        failed = [r for r in self.rows if not r[4]]
        worst = max(self.rows, key=lambda r: r[1])
        self.stdout.write(f"\nSlowest page: {worst[0]} at {worst[1] * 1000:.0f} ms.")
        if failed:
            self.stdout.write(self.style.ERROR(f"{len(failed)} page(s) exceed the {KPI_SECONDS:.0f} s target."))
        else:
            self.stdout.write(self.style.SUCCESS(f"All {len(self.rows)} pages meet the {KPI_SECONDS:.0f} s target."))
