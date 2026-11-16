"""Scheduled-vs-actual comparison (Section 7.1 Usage Monitoring, success criterion 5.3)."""
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

from django.utils import timezone

from .models import UsageRecord
from .sessions import _day_bounds, record_key, sessions_between

USED, NO_SHOW, NO_DATA = "used", "no_show", "no_data"


@dataclass
class SessionRow:
    session: object
    status: str
    headcount: int = None
    records: list = field(default_factory=list)

    @property
    def over_capacity(self):
        return self.headcount is not None and self.headcount > self.session.facility.capacity


@dataclass
class FacilitySummary:
    facility: object
    sessions: int = 0
    used: int = 0
    no_show: int = 0
    no_data: int = 0
    unscheduled: int = 0
    over_capacity: int = 0
    scheduled_hours: float = 0.0
    used_hours: float = 0.0

    @property
    def recorded(self):
        return self.used + self.no_show

    @property
    def coverage_rate(self):
        """Share of finished sessions that have any usage evidence (data-quality indicator)."""
        return self.recorded / self.sessions * 100 if self.sessions else None

    @property
    def no_show_rate(self):
        return self.no_show / self.recorded * 100 if self.recorded else None


def compare(start_date, end_date, facilities):
    now = timezone.now()
    facilities = list(facilities)
    sessions = [s for s in sessions_between(start_date, end_date, facilities=facilities) if s.end_at <= now]
    range_start, range_end = _day_bounds(start_date, end_date)
    by_id = {f.pk: f for f in facilities}
    # Only the columns used below; the facility objects are reused from memory instead of re-loaded
    # per record (this matters with tens of thousands of records).
    records = UsageRecord.objects.filter(
        facility__in=facilities, start_at__gte=range_start, start_at__lte=range_end,
    ).only("facility_id", "schedule_id", "reservation_id", "start_at", "end_at", "headcount", "status", "source")

    by_session = defaultdict(list)
    unscheduled = []
    summaries = OrderedDict((f.pk, FacilitySummary(f)) for f in sorted(facilities, key=lambda f: f.facility_code))

    for r in records:
        r.facility = by_id[r.facility_id]
        if r.status == UsageRecord.Status.USED:
            summaries[r.facility_id].used_hours += r.duration_hours
        key = record_key(r)
        if key:
            by_session[key].append(r)
        elif r.status == UsageRecord.Status.USED:
            unscheduled.append(r)
            summaries[r.facility_id].unscheduled += 1
            if r.over_capacity:
                summaries[r.facility_id].over_capacity += 1

    rows = []
    for s in sessions:
        recs = by_session.get(s.key, [])
        used = [r for r in recs if r.status == UsageRecord.Status.USED]
        status = USED if used else NO_SHOW if recs else NO_DATA
        counts = [r.headcount for r in used if r.headcount is not None]
        row = SessionRow(s, status, max(counts) if counts else None, recs)
        rows.append(row)
        summary = summaries[s.facility.pk]
        summary.sessions += 1
        summary.scheduled_hours += s.hours
        setattr(summary, status, getattr(summary, status) + 1)
        if row.over_capacity:
            summary.over_capacity += 1

    totals = FacilitySummary(facility=None)
    for summary in summaries.values():
        for name in ("sessions", "used", "no_show", "no_data", "unscheduled", "over_capacity", "scheduled_hours", "used_hours"):
            setattr(totals, name, getattr(totals, name) + getattr(summary, name))

    return {
        "rows": rows,
        "unscheduled": sorted(unscheduled, key=lambda r: r.start_at),
        "summaries": [s for s in summaries.values() if s.sessions or s.unscheduled],
        "totals": totals,
    }
