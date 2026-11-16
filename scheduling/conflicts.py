"""Conflict detection for schedules and reservations (Requirement R4, business rules 7.4).

Two bookings overlap when:   new.start < existing.end  AND  new.end > existing.start

Severity policy:
  ERROR   - blocks saving/approval (double booking, maintenance, closed, outside hours)
  WARNING - allowed, but the user must acknowledge it (over capacity, overlapping pending request)
"""
import datetime
from dataclasses import dataclass

from django.utils import timezone

from facilities.models import Facility, MaintenanceRecord

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Conflict:
    kind: str
    severity: str
    message: str

    @property
    def is_error(self):
        return self.severity == ERROR


def has_errors(conflicts):
    return any(c.is_error for c in conflicts)


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and end_a > start_b


def _fmt(t):
    return t.strftime("%H:%M")


def _facility_state_conflicts(facility):
    if not facility.is_active:
        return [Conflict("INACTIVE", ERROR, f"{facility.facility_code} is deactivated.")]
    if facility.status == Facility.Status.CLOSED:
        return [Conflict("CLOSED", ERROR, f"{facility.facility_code} is closed.")]
    return []


def _hours_conflicts(facility, start_time, end_time):
    if start_time < facility.open_time or end_time > facility.close_time:
        return [Conflict(
            "OUTSIDE_HOURS", ERROR,
            f"Outside operating hours ({_fmt(facility.open_time)}–{_fmt(facility.close_time)}).",
        )]
    return []


def _capacity_conflicts(facility, attendees):
    if attendees and attendees > facility.capacity:
        return [Conflict(
            "OVER_CAPACITY", WARNING,
            f"{attendees} expected attendees exceed the capacity of {facility.capacity}.",
        )]
    return []


def _open_maintenance(facility, start_at, end_at):
    """Maintenance records (not completed) that overlap [start_at, end_at)."""
    qs = facility.maintenance_records.exclude(status=MaintenanceRecord.Status.COMPLETED).filter(start_at__lt=end_at)
    return [m for m in qs if m.end_at is None or m.end_at > start_at]


# ---------------------------------------------------------------- reservations

def check_reservation(facility, start_at, end_at, attendees=0, exclude_pk=None):
    """Conflicts for a one-day booking of `facility` from start_at to end_at (aware datetimes)."""
    from .models import Reservation, Schedule

    conflicts = _facility_state_conflicts(facility)
    local_start, local_end = timezone.localtime(start_at), timezone.localtime(end_at)
    day = local_start.date()

    conflicts += _hours_conflicts(facility, local_start.time(), local_end.time())
    conflicts += _capacity_conflicts(facility, attendees)

    # Status flag without any open maintenance record: the end date is unknown, so block.
    # With dated records, only bookings that overlap a record are blocked.
    open_records = facility.maintenance_records.exclude(status=MaintenanceRecord.Status.COMPLETED)
    if facility.status == Facility.Status.MAINTENANCE and not open_records.exists():
        conflicts.append(Conflict("MAINTENANCE", ERROR, f"{facility.facility_code} is under maintenance (no end date recorded)."))
    for m in _open_maintenance(facility, start_at, end_at):
        conflicts.append(Conflict("MAINTENANCE", ERROR, f"Maintenance scheduled: {m.reason}."))

    classes = Schedule.objects.filter(
        facility=facility, day_of_week=day.weekday(), term_start__lte=day, term_end__gte=day,
        start_time__lt=local_end.time(), end_time__gt=local_start.time(),
    ).select_related("activity")
    for s in classes:
        conflicts.append(Conflict(
            "SCHEDULE_OVERLAP", ERROR,
            f"Overlaps class {s.activity} ({_fmt(s.start_time)}–{_fmt(s.end_time)}).",
        ))

    others = Reservation.objects.filter(
        facility=facility, start_at__lt=end_at, end_at__gt=start_at,
        status__in=[Reservation.Status.APPROVED, Reservation.Status.PENDING],
    ).exclude(pk=exclude_pk)
    for r in others:
        span = f"{_fmt(timezone.localtime(r.start_at))}–{_fmt(timezone.localtime(r.end_at))}"
        if r.status == Reservation.Status.APPROVED:
            conflicts.append(Conflict("RESERVATION_OVERLAP", ERROR, f"Overlaps approved reservation “{r.purpose}” ({span})."))
        else:
            conflicts.append(Conflict("PENDING_OVERLAP", WARNING, f"Overlaps pending request “{r.purpose}” ({span})."))
    return conflicts


# ---------------------------------------------------------------- recurring schedules

def check_schedule(facility, day_of_week, start_time, end_time, term_start, term_end, attendees=0, exclude_pk=None):
    """Conflicts for a weekly class slot between term_start and term_end."""
    from .models import Reservation, Schedule

    conflicts = _facility_state_conflicts(facility)
    conflicts += _hours_conflicts(facility, start_time, end_time)
    conflicts += _capacity_conflicts(facility, attendees)

    clashes = Schedule.objects.filter(
        facility=facility, day_of_week=day_of_week,
        term_start__lte=term_end, term_end__gte=term_start,
        start_time__lt=end_time, end_time__gt=start_time,
    ).exclude(pk=exclude_pk).select_related("activity")
    for s in clashes:
        conflicts.append(Conflict(
            "SCHEDULE_OVERLAP", ERROR,
            f"Overlaps class {s.activity} ({s.get_day_of_week_display()} {_fmt(s.start_time)}–{_fmt(s.end_time)}).",
        ))

    tz = timezone.get_current_timezone()
    term_start_dt = timezone.make_aware(datetime.datetime.combine(term_start, datetime.time.min), tz)
    term_end_dt = timezone.make_aware(datetime.datetime.combine(term_end, datetime.time.max), tz)
    approved = Reservation.objects.filter(
        facility=facility, status=Reservation.Status.APPROVED,
        start_at__lt=term_end_dt, end_at__gt=term_start_dt,
    )
    for r in approved:
        rs, re_ = timezone.localtime(r.start_at), timezone.localtime(r.end_at)
        if rs.weekday() == day_of_week and overlaps(start_time, end_time, rs.time(), re_.time()):
            conflicts.append(Conflict(
                "RESERVATION_OVERLAP", WARNING,
                f"Overlaps approved reservation “{r.purpose}” on {rs:%b %d} ({_fmt(rs)}–{_fmt(re_)}).",
            ))

    for m in _open_maintenance(facility, term_start_dt, term_end_dt):
        end = f"{timezone.localtime(m.end_at):%b %d}" if m.end_at else "open-ended"
        conflicts.append(Conflict(
            "MAINTENANCE", WARNING,
            f"Maintenance during the term: {m.reason} ({timezone.localtime(m.start_at):%b %d} – {end}).",
        ))
    return conflicts
