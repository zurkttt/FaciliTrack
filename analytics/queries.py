"""Aggregate daily UtilizationMetric rows into groups and periods for dashboards and reports."""
import datetime
from dataclasses import dataclass

from django.db.models import F, Max, Sum
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

from core.models import SystemSetting
from usage.models import UsageRecord

from .models import UtilizationMetric

GROUPINGS = {
    "facility": ("facility_id", "facility__facility_code", "Facility"),
    "building": ("facility__building_id", "facility__building__code", "Building"),
    "type": ("facility__facility_type_id", "facility__facility_type__name", "Facility type"),
    "department": ("facility__owner_department_id", "facility__owner_department__code", "Department"),
}
PERIODS = {"day": None, "week": TruncWeek, "month": TruncMonth}


def thresholds():
    return {
        "low": SystemSetting.get("risk.underutilization_threshold", 30.0, float),
        "high": SystemSetting.get("risk.overdemand_threshold", 85.0, float),
    }


@dataclass
class Row:
    key: object
    label: str
    available: float
    used: float
    scheduled: float
    occupancy_sum: float
    occupancy_count: int
    capacity: int = None
    status: str = None  # facility status, only when grouped by facility

    @property
    def rate(self):
        return self.used / self.available * 100 if self.available else None

    @property
    def scheduled_rate(self):
        return self.scheduled / self.available * 100 if self.available else None

    @property
    def occupancy(self):
        """Average share of seats filled when the room was in use and counted."""
        return self.occupancy_sum / self.occupancy_count * 100 if self.occupancy_count else None

    @property
    def gap(self):
        """Scheduled minus actual (percentage points): booked but not used."""
        if self.rate is None:
            return None
        return self.scheduled_rate - self.rate


def classify(row, limits):
    """Band for a Row: unavailable / none / high / low / ok.

    High demand looks at BOTH actual and scheduled utilization: a room that is fully
    booked is in high demand even if some bookings go unused.
    """
    if row.status and row.status != "AVAILABLE":
        return "unavailable"
    if row.rate is None:
        return "none"
    if max(row.rate, row.scheduled_rate or 0) > limits["high"]:
        return "high"
    if row.rate < limits["low"]:
        return "low"
    return "ok"


def base_queryset(definition, start, end, building=None, facility_type=None, department=None, facility=None):
    qs = UtilizationMetric.objects.filter(
        definition=definition, period_start__gte=start, period_start__lte=end, facility__is_active=True,
    )
    if building:
        qs = qs.filter(facility__building=building)
    if facility_type:
        qs = qs.filter(facility__facility_type=facility_type)
    if department:
        qs = qs.filter(facility__owner_department=department)
    if facility:
        qs = qs.filter(facility=facility)
    return qs


def _sums():
    return {
        "available": Sum("available_hours"), "used": Sum("used_hours"), "scheduled": Sum("scheduled_hours"),
        "occ_sum": Sum("occupancy_sum"), "occ_count": Sum("occupancy_count"),
    }


def _row(key, label, agg, capacity=None, status=None):
    return Row(
        key=key, label=label or "(none)",
        available=float(agg["available"] or 0), used=float(agg["used"] or 0), scheduled=float(agg["scheduled"] or 0),
        occupancy_sum=float(agg["occ_sum"] or 0), occupancy_count=int(agg["occ_count"] or 0),
        capacity=capacity, status=status,
    )


def totals(qs):
    return _row(None, "All", qs.aggregate(**_sums()))


def by_group(qs, group):
    key_field, label_field, _ = GROUPINGS[group]
    extra = {"capacity": Max("facility__capacity"), "status": Max("facility__status")} if group == "facility" else {}
    rows = qs.values(key_field, label_field).annotate(**_sums(), **extra).order_by(label_field)
    return [_row(r[key_field], r[label_field], r, r.get("capacity"), r.get("status")) for r in rows]


def by_period(qs, period):
    trunc = PERIODS[period]
    if trunc is None:
        rows = qs.values("period_start").annotate(**_sums()).order_by("period_start")
        return [_row(r["period_start"], r["period_start"].strftime("%b %d"), r) for r in rows]
    rows = qs.annotate(bucket=trunc("period_start")).values("bucket").annotate(**_sums()).order_by("bucket")
    fmt = "Wk of %b %d" if period == "week" else "%b %Y"
    return [_row(r["bucket"], r["bucket"].strftime(fmt), r) for r in rows]


def by_period_and_group(qs, period, group, limit=6):
    """Series per group over time: ({'labels': [...], 'series': [{'label', 'values', 'rate'}...]}).

    Groups are ordered by label (stable, so colors follow the entity, not its rank); only the
    `limit` groups with the most used hours are kept so the chart stays readable.
    """
    key_field, label_field, _ = GROUPINGS[group]
    trunc = PERIODS[period]
    base = qs.annotate(bucket=trunc("period_start")) if trunc else qs.annotate(bucket=F("period_start"))
    rows = base.values("bucket", label_field).annotate(**_sums()).order_by("bucket")
    buckets = sorted({r["bucket"] for r in rows})
    fmt = {"day": "%b %d", "week": "Wk %b %d", "month": "%b %Y"}[period]
    totals_by_group, cells = {}, {}
    for r in rows:
        name = r[label_field] or "(none)"
        totals_by_group[name] = totals_by_group.get(name, 0) + float(r["used"] or 0)
        avail = float(r["available"] or 0)
        cells[(name, r["bucket"])] = round(float(r["used"] or 0) / avail * 100, 1) if avail else None
    keep = sorted(sorted(totals_by_group, key=totals_by_group.get, reverse=True)[:limit])
    return {
        "labels": [b.strftime(fmt) for b in buckets],
        "series": [{"label": name, "values": [cells.get((name, b)) for b in buckets]} for name in keep],
    }


def by_hour_of_week(start, end, facilities):
    """Heat-map data: used facility-hours per weekday x hour (peak period analysis)."""
    tz = timezone.get_current_timezone()
    records = UsageRecord.objects.filter(
        facility__in=facilities, status=UsageRecord.Status.USED,
        start_at__gte=timezone.make_aware(datetime.datetime.combine(start, datetime.time.min), tz),
        start_at__lte=timezone.make_aware(datetime.datetime.combine(end, datetime.time.max), tz),
    ).only("start_at", "end_at")
    grid = [[0.0] * 24 for _ in range(7)]
    for r in records:
        t, stop = timezone.localtime(r.start_at), timezone.localtime(r.end_at)
        while t < stop:
            hour_end = min(stop, t.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1))
            grid[t.weekday()][t.hour] += (hour_end - t).total_seconds() / 3600
            t = hour_end
    return grid


def last_calculated(definition):
    return UtilizationMetric.objects.filter(definition=definition).aggregate(m=Max("calculated_at"))["m"]
