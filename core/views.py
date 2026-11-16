"""Role-aware home dashboard: each person sees what needs their attention first."""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html

from analytics.models import MetricDefinition
from analytics.queries import (
    base_queryset, by_group, by_hour_of_week, by_period, by_period_and_group, last_calculated, thresholds, totals,
)
from analytics.views import ANALYTICS_VIEWERS, STALE_AFTER
from facilities.models import Facility
from risks.models import MitigationAction, Risk
from risks.views import RISK_VIEWERS
from scheduling.models import Reservation
from scheduling.views import RESERVATION_APPROVERS
from usage.models import UsageRecord
from usage.sessions import records_by_session, sessions_between
from usage.views import USAGE_STAFF

KPI_DAYS = 28


@login_required
def dashboard(request):
    user = request.user
    now = timezone.now()
    today = timezone.localdate()
    ctx = {
        "my_reservations": Reservation.objects.filter(
            requested_by=user, end_at__gte=now,
            status__in=[Reservation.Status.PENDING, Reservation.Status.APPROVED],
        ).select_related("facility").order_by("start_at")[:5],
        "is_approver": user.has_role(*RESERVATION_APPROVERS),
        "can_see_risks": user.has_role(*RISK_VIEWERS),
        "can_see_analytics": user.has_role(*ANALYTICS_VIEWERS),
        "is_usage_staff": user.has_role(*USAGE_STAFF),
    }

    if ctx["is_approver"]:
        pending = Reservation.objects.filter(status=Reservation.Status.PENDING, end_at__gte=now)
        ctx["pending_count"] = pending.count()
        ctx["pending"] = pending.select_related("facility", "requested_by").order_by("start_at")[:5]

    # Actions and risks that belong to this person, whatever their role
    ctx["my_actions"] = (
        MitigationAction.objects.exclude(status=MitigationAction.Status.DONE)
        .filter(Q(assigned_to=user) | Q(assigned_to__isnull=True, risk__owner=user), risk__status__in=Risk.ACTIVE_STATUSES)
        .select_related("risk__facility").order_by("due_date")[:6]
    )
    ctx["my_risk_count"] = Risk.objects.filter(owner=user, status__in=Risk.ACTIVE_STATUSES).count()

    if ctx["can_see_risks"]:
        active = Risk.objects.filter(status__in=Risk.ACTIVE_STATUSES)
        ctx["top_risks"] = active.select_related("facility", "owner").order_by("-score", "-detected_at")[:5]
        by_priority = dict(active.values_list("priority").annotate(n=Count("id")))
        ctx["high_risks"] = by_priority.get("HIGH", 0)
        ctx["open_risks"] = sum(by_priority.values())
        ctx["risk_note"] = (f"{by_priority.get('MEDIUM', 0)} medium and {by_priority.get('LOW', 0)} low; "
                            f"{ctx['open_risks']} active in total")

    if ctx["can_see_analytics"]:
        definition = MetricDefinition.active()
        if definition:
            yesterday = today - datetime.timedelta(days=1)
            start = today - datetime.timedelta(days=KPI_DAYS)
            ctx["kpi"] = totals(base_queryset(definition, start, yesterday))
            ctx["kpi_days"] = KPI_DAYS
            ctx["limits"] = thresholds()
            ctx["below_target"] = sum(
                1 for r in by_group(base_queryset(definition, start, yesterday), "facility")
                if r.rate is not None and r.rate < ctx["limits"]["low"]
            )
            ctx["below_target_note"] = f"under {ctx['limits']['low']:.0f}% utilization in the last {KPI_DAYS} days"
            ctx["busiest_hour"] = _busiest_hour(start, yesterday)
            ctx.update(_trend_chart(request, definition, today))
        ctx["freshness"] = _freshness(definition, now)

    if ctx["is_usage_staff"]:
        sessions_today = [s for s in sessions_between(today, today) if s.start_at <= now]
        recorded = records_by_session(today, today)
        ctx["unconfirmed_today"] = sum(1 for s in sessions_today if s.key not in recorded)
        ctx["sessions_today"] = len(sessions_today)
        ctx["unavailable"] = Facility.objects.filter(is_active=True).exclude(status=Facility.Status.AVAILABLE)

    return render(request, "core/dashboard.html", ctx)


TREND_GROUPS = {"building": "building", "type": "facility type", "department": "department"}
TREND_PERIODS = {"week": ("Weekly", 12 * 7), "month": ("Monthly", 365)}


def _trend_chart(request, definition, today):
    """Utilization over time per group, plus a one-sentence insight (like the chart's caption)."""
    group = request.GET.get("group") if request.GET.get("group") in TREND_GROUPS else "building"
    period = request.GET.get("period") if request.GET.get("period") in TREND_PERIODS else "week"
    days = TREND_PERIODS[period][1]
    start, end = today - datetime.timedelta(days=days), today - datetime.timedelta(days=1)
    qs = base_queryset(definition, start, end)
    chart = by_period_and_group(qs, period, group)
    overall = by_period(qs, period)

    insight = ""
    groups = [r for r in by_group(qs, group) if r.rate is not None]
    if groups and overall:
        top = max(groups, key=lambda r: r.rate)
        peak = max((r for r in overall if r.rate is not None), key=lambda r: r.rate, default=None)
        word = TREND_GROUPS[group]
        insight = format_html(
            "<strong>{}</strong> was the busiest {} at <strong>{}%</strong> utilization. "
            "Campus-wide use peaked in <strong>{}</strong> at <strong>{}%</strong>; "
            "the average for the period was <strong>{}%</strong> against a target of at least {}%.",
            top.label, word, f"{top.rate:.0f}",
            peak.label.replace("Wk of ", "the week of ") if peak else "—", f"{peak.rate:.0f}" if peak else "—",
            f"{totals(qs).rate or 0:.0f}", f"{thresholds()['low']:.0f}",
        )
    return {
        "trend": chart, "trend_group": group, "trend_period": period, "trend_insight": insight,
        "trend_groups": TREND_GROUPS, "trend_periods": {k: v[0] for k, v in TREND_PERIODS.items()},
        "trend_range": f"{start:%b %d} – {end:%b %d, %Y}", "trend_limits": thresholds(),
    }


def _busiest_hour(start, end):
    """Hour of day with the most facility-hours of use (the 'peak window')."""
    grid = by_hour_of_week(start, end, Facility.objects.filter(is_active=True))
    per_hour = [sum(day[h] for day in grid) for h in range(24)]
    if not any(per_hour):
        return None
    hour = max(range(24), key=lambda h: per_hour[h])
    return datetime.time(hour).strftime("%I %p").lstrip("0")


def _freshness(definition, now):
    """When each data feed last updated (Requirement R10). 'stale' means older than expected."""
    calc = last_calculated(definition) if definition else None
    detection = Risk.objects.filter(auto_detected=True).aggregate(m=Max("last_detected_at"))["m"]
    usage = UsageRecord.objects.aggregate(m=Max("created_at"))["m"]
    return [
        {"label": "Utilization calculated", "at": calc, "stale": calc is None or now - calc > STALE_AFTER},
        {"label": "Risk detection run", "at": detection, "stale": detection is None or now - detection > STALE_AFTER},
        {"label": "Latest usage record", "at": usage, "stale": usage is None or now - usage > datetime.timedelta(days=3)},
    ]
