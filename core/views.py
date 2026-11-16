"""Role-aware home dashboard: each person sees what needs their attention first."""
import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.shortcuts import render
from django.utils import timezone

from analytics.models import MetricDefinition
from analytics.queries import base_queryset, last_calculated, totals
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

    if ctx["can_see_analytics"]:
        definition = MetricDefinition.active()
        if definition:
            start = today - datetime.timedelta(days=KPI_DAYS)
            ctx["kpi"] = totals(base_queryset(definition, start, today - datetime.timedelta(days=1)))
            ctx["kpi_days"] = KPI_DAYS
        ctx["freshness"] = _freshness(definition, now)

    if ctx["is_usage_staff"]:
        sessions_today = [s for s in sessions_between(today, today) if s.start_at <= now]
        recorded = records_by_session(today, today)
        ctx["unconfirmed_today"] = sum(1 for s in sessions_today if s.key not in recorded)
        ctx["sessions_today"] = len(sessions_today)
        ctx["unavailable"] = Facility.objects.filter(is_active=True).exclude(status=Facility.Status.AVAILABLE)

    return render(request, "core/dashboard.html", ctx)


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
