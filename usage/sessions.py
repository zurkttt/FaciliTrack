"""Expands schedules and approved reservations into dated *sessions* and matches usage records to them.

A Schedule is a weekly rule ("every Monday 08:00-10:00 during the term").
A Session is one concrete occurrence ("Monday Nov 23, 08:00-10:00").
"""
import datetime
from collections import defaultdict
from dataclasses import dataclass

from django.utils import timezone

from scheduling.models import Reservation, Schedule

# A QR check-in up to this many minutes before a session starts still counts for that session.
EARLY_CHECKIN_MINUTES = 15


@dataclass
class Session:
    facility: object
    start_at: datetime.datetime
    end_at: datetime.datetime
    label: str
    expected: int
    schedule: Schedule = None
    reservation: Reservation = None

    @property
    def kind(self):
        return "class" if self.schedule else "reservation"

    @property
    def key(self):
        if self.schedule:
            return ("S", self.schedule.pk, timezone.localtime(self.start_at).date())
        return ("R", self.reservation.pk)

    @property
    def hours(self):
        return (self.end_at - self.start_at).total_seconds() / 3600


def _aware(day, t):
    return timezone.make_aware(datetime.datetime.combine(day, t), timezone.get_current_timezone())


def _day_bounds(start_date, end_date):
    return _aware(start_date, datetime.time.min), _aware(end_date, datetime.time.max)


def sessions_between(start_date, end_date, facilities=None):
    """All class and approved-reservation sessions from start_date to end_date (inclusive)."""
    schedules = Schedule.objects.filter(term_start__lte=end_date, term_end__gte=start_date).select_related("activity", "facility")
    range_start, range_end = _day_bounds(start_date, end_date)
    reservations = Reservation.objects.filter(
        status=Reservation.Status.APPROVED, start_at__lt=range_end, end_at__gt=range_start,
    ).select_related("facility")
    if facilities is not None:
        schedules = schedules.filter(facility__in=facilities)
        reservations = reservations.filter(facility__in=facilities)

    by_weekday = defaultdict(list)
    for s in schedules:
        by_weekday[s.day_of_week].append(s)

    sessions = []
    day = start_date
    while day <= end_date:
        for s in by_weekday[day.weekday()]:
            if s.term_start <= day <= s.term_end:
                sessions.append(Session(
                    facility=s.facility, start_at=_aware(day, s.start_time), end_at=_aware(day, s.end_time),
                    label=str(s.activity), expected=s.expected_attendees, schedule=s,
                ))
        day += datetime.timedelta(days=1)
    for r in reservations:
        sessions.append(Session(
            facility=r.facility, start_at=r.start_at, end_at=r.end_at,
            label=r.purpose, expected=r.expected_attendees, reservation=r,
        ))
    sessions.sort(key=lambda x: (x.start_at, x.facility.facility_code))
    return sessions


def find_session(facility, start_at, end_at=None):
    """The session in `facility` that best matches a usage period (largest overlap)."""
    end_at = end_at or start_at + datetime.timedelta(minutes=1)
    day = timezone.localtime(start_at).date()
    early = datetime.timedelta(minutes=EARLY_CHECKIN_MINUTES)
    best, best_overlap = None, datetime.timedelta(0)
    for s in sessions_between(day, day, facilities=[facility]):
        overlap = min(end_at, s.end_at) - max(start_at, s.start_at - early)
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    return best


def session_from_key(kind, obj_id, day=None):
    """Rebuild one session from a key posted by a form. Returns None if it doesn't exist."""
    if kind == "S":
        s = Schedule.objects.select_related("activity", "facility").filter(pk=obj_id).first()
        if not s or day is None or day.weekday() != s.day_of_week or not (s.term_start <= day <= s.term_end):
            return None
        return Session(s.facility, _aware(day, s.start_time), _aware(day, s.end_time), str(s.activity), s.expected_attendees, schedule=s)
    r = Reservation.objects.select_related("facility").filter(pk=obj_id, status=Reservation.Status.APPROVED).first()
    if not r:
        return None
    return Session(r.facility, r.start_at, r.end_at, r.purpose, r.expected_attendees, reservation=r)


def link_to_session(record):
    """Attach a usage record to its matching schedule/reservation (if any). Does not save."""
    session = find_session(record.facility, record.start_at, record.end_at)
    record.schedule = session.schedule if session else None
    record.reservation = session.reservation if session else None
    return session


def record_key(record):
    if record.schedule_id:
        return ("S", record.schedule_id, timezone.localtime(record.start_at).date())
    if record.reservation_id:
        return ("R", record.reservation_id)
    return None


def records_by_session(start_date, end_date, facilities=None):
    """All usage records in a date range, grouped by session key, in ONE query.

    Use this instead of calling records_for_session() in a loop.
    """
    from .models import UsageRecord

    start, end = _day_bounds(start_date, end_date)
    qs = UsageRecord.objects.filter(start_at__gte=start, start_at__lte=end).exclude(schedule=None, reservation=None)
    if facilities is not None:
        qs = qs.filter(facility__in=facilities)
    grouped = defaultdict(list)
    for r in qs.order_by("start_at"):
        grouped[record_key(r)].append(r)
    return grouped


def records_for_session(session):
    from .models import UsageRecord

    qs = UsageRecord.objects.filter(facility=session.facility)
    if session.schedule:
        day = timezone.localtime(session.start_at).date()
        start, end = _day_bounds(day, day)
        return qs.filter(schedule=session.schedule, start_at__range=(start, end))
    return qs.filter(reservation=session.reservation)
