"""Utilization calculation (Requirement R6, Section 7.3).

For each facility and day:

    available = operating window (open..close on operating days)
                minus maintenance periods (if the definition excludes maintenance)
    used      = union of actual-use intervals (USED usage records, plus approved
                reservations if the definition says so), clipped to `available`
    scheduled = union of class and approved-reservation intervals, clipped to `available`

    utilization rate = used / available x 100

Working with interval unions means overlapping records (e.g. a QR check-in and a
manual entry for the same class) are never double-counted, and the rate can't exceed 100%.
"""
import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from facilities.models import Facility, MaintenanceRecord
from scheduling.models import Reservation
from usage.models import UsageRecord
from usage.sessions import sessions_between

from .models import MetricDefinition, UtilizationMetric

# ---------------------------------------------------------------- interval helpers (datetimes)


def union(intervals):
    merged = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract(intervals, holes):
    result = []
    for start, end in intervals:
        pieces = [(start, end)]
        for h_start, h_end in holes:
            next_pieces = []
            for p_start, p_end in pieces:
                if h_end <= p_start or h_start >= p_end:
                    next_pieces.append((p_start, p_end))
                    continue
                if h_start > p_start:
                    next_pieces.append((p_start, h_start))
                if h_end < p_end:
                    next_pieces.append((h_end, p_end))
            pieces = next_pieces
        result.extend(pieces)
    return result


def intersect(a, b):
    result = []
    for a_start, a_end in a:
        for b_start, b_end in b:
            start, end = max(a_start, b_start), min(a_end, b_end)
            if end > start:
                result.append((start, end))
    return union(result)


def hours(intervals):
    return sum((end - start).total_seconds() for start, end in intervals) / 3600


def _dec(value, places="0.01"):
    return Decimal(str(value)).quantize(Decimal(places))


# ---------------------------------------------------------------- calculation


def _aware(day, t):
    return timezone.make_aware(datetime.datetime.combine(day, t), timezone.get_current_timezone())


def calculate_facility(facility, start_date, end_date, definition):
    """Daily metric values for one facility. Returns a list of dicts (not saved)."""
    range_start, range_end = _aware(start_date, datetime.time.min), _aware(end_date, datetime.time.max)

    usage = list(UsageRecord.objects.filter(
        facility=facility, status=UsageRecord.Status.USED, start_at__lt=range_end, end_at__gt=range_start,
    ))
    maintenance = []
    if definition.exclude_maintenance:
        for m in MaintenanceRecord.objects.filter(facility=facility, start_at__lt=range_end):
            end = m.end_at or (range_end if m.status != MaintenanceRecord.Status.COMPLETED else m.start_at)
            if end > range_start:
                maintenance.append((m.start_at, end))
    reservations = []
    if definition.count_reservations_as_use:
        reservations = [(r.start_at, r.end_at) for r in Reservation.objects.filter(
            facility=facility, status=Reservation.Status.APPROVED, start_at__lt=range_end, end_at__gt=range_start,
        )]
    sessions = sessions_between(start_date, end_date, facilities=[facility])
    operating_days = definition.operating_weekdays

    rows = []
    day = start_date
    while day <= end_date:
        if day.weekday() in operating_days:
            window = [(_aware(day, facility.open_time), _aware(day, facility.close_time))]
            available = subtract(window, maintenance) if maintenance else window
        else:
            available = []

        used_raw = [(u.start_at, u.end_at) for u in usage] + reservations
        used = intersect(union(used_raw), available) if available else []
        scheduled = intersect(union([(s.start_at, s.end_at) for s in sessions]), available) if available else []

        day_start, day_end = _aware(day, datetime.time.min), _aware(day, datetime.time.max)
        ratios = [
            u.headcount / facility.capacity for u in usage
            if u.headcount is not None and day_start <= u.start_at <= day_end and facility.capacity
        ]

        available_h, used_h = hours(available), hours(used)
        rows.append({
            "day": day,
            "available_hours": _dec(available_h),
            "used_hours": _dec(used_h),
            "scheduled_hours": _dec(hours(scheduled)),
            "utilization_rate": _dec(used_h / available_h * 100 if available_h else 0),
            "occupancy_sum": _dec(sum(ratios), "0.0001"),
            "occupancy_count": len(ratios),
        })
        day += datetime.timedelta(days=1)
    return rows


@transaction.atomic
def calculate(start_date, end_date, definition=None, facilities=None):
    """Recalculate and store daily metrics. Returns the number of rows written.

    Future days are skipped; today is included as a partial day.
    """
    definition = definition or MetricDefinition.active()
    if definition is None:
        raise ValueError("No active metric definition. Create and activate one first.")
    end_date = min(end_date, timezone.localdate())
    if end_date < start_date:
        return 0
    facilities = facilities if facilities is not None else Facility.objects.filter(is_active=True)

    written = 0
    for facility in facilities:
        UtilizationMetric.objects.filter(
            facility=facility, definition=definition, period_start__gte=start_date, period_start__lte=end_date,
        ).delete()
        UtilizationMetric.objects.bulk_create([
            UtilizationMetric(
                facility=facility, definition=definition, period_start=r["day"], period_end=r["day"],
                **{k: v for k, v in r.items() if k != "day"},
            )
            for r in calculate_facility(facility, start_date, end_date, definition)
        ])
        written += (end_date - start_date).days + 1
    return written
