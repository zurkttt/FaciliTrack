"""Risk analytics engine: detects, scores and tracks facility risks.

Each rule converts measurements into a LIKELIHOOD (1-5) and IMPACT (1-5) using fixed,
documented scales (see RATING SCALES below), and writes a plain-language reason plus
the evidence used, so every score can be explained and reproduced.

    score = likelihood x impact  (1-25)   ->  Low 1-7, Medium 8-14, High 15-25

Risk scores are decision-support indicators. They do not certify a facility as safe and
do not replace inspections or decisions by qualified personnel (Section 1.5).

Sync rules (one tracked risk per facility + category):
  * condition found, no active risk          -> create OPEN risk with a default owner
  * condition found, risk active or accepted -> refresh ratings, reason and evidence
  * accepted risk whose priority increases   -> reopen (status OPEN)
  * condition no longer found                -> close automatically, with a note
"""
import datetime
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from analytics.models import MetricDefinition, UtilizationMetric
from core.models import SystemSetting
from facilities.models import Facility, MaintenanceRecord
from notifications.services import Level, notify
from scheduling.conflicts import check_reservation, check_schedule
from scheduling.models import Reservation, Schedule
from usage.models import UsageRecord
from usage.reports import compare
from usage.sessions import sessions_between

from .models import MitigationAction, Risk

C = Risk.Category

DEFAULT_OWNER_ROLE = {
    C.OVERCROWDING: User.Role.FACILITIES,
    C.UNAVAILABLE: User.Role.FACILITIES,
    C.DATA_QUALITY: User.Role.FACILITIES,
    C.UNDERUTILIZATION: User.Role.PLANNER,
    C.OVER_DEMAND: User.Role.PLANNER,
    C.CONFLICT: User.Role.REGISTRAR,
}

# ---------------------------------------------------------------- RATING SCALES
# Each scale: list of (minimum value, rating), checked top to bottom.
OVERCROWD_SHARE = [(50, 5), (30, 4), (15, 3), (5, 2), (0, 1)]        # % of counted sessions over capacity
OVERCROWD_EXCESS = [(1.30, 5), (1.15, 4), (0, 3)]                   # peak people / capacity (safety: never below 3)
NO_SHOW_RATE = [(50, 5), (35, 4), (25, 3)]                           # % of recorded sessions
DEMAND_PEAK = [(95, 5), (90, 4), (0, 3)]                             # max(actual, scheduled) %
COVERAGE_GAP = [(70, 5), (50, 4), (30, 3)]                           # % of sessions WITHOUT usage data
AFFECTED_SESSIONS = [(10, 5), (3, 4), (1, 3), (0, 2)]                # sessions hit in the next 14 days
CAPACITY_IMPACT = [(40, 3), (0, 2)]                                  # efficiency risk: capped below safety risks

LOOKAHEAD_DAYS = 14
MIN_SESSIONS_FOR_RATES = 4


def rate(value, scale, default=None):
    for minimum, rating in scale:
        if value >= minimum:
            return rating
    return default


def settings_snapshot():
    return {
        "window_days": SystemSetting.get("risk.window_days", 28, int),
        "low": SystemSetting.get("risk.underutilization_threshold", 30.0, float),
        "high": SystemSetting.get("risk.overdemand_threshold", 85.0, float),
        "crowd_ratio": SystemSetting.get("risk.overcrowding_ratio", 1.0, float),
        "maintenance_days": SystemSetting.get("risk.maintenance_days", 14, int),
        "stale_days": SystemSetting.get("risk.stale_data_days", 7, int),
    }


@dataclass
class Finding:
    facility: Facility
    category: str
    likelihood: int
    impact: int
    reason: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Context:
    as_of: datetime.date
    start: datetime.date
    now: datetime.datetime
    cfg: dict
    facilities: list
    metrics: dict          # facility_id -> {"available", "used", "scheduled"}
    summaries: dict        # facility_id -> usage.reports.FacilitySummary


def _pct(part, whole):
    return part / whole * 100 if whole else None


# ================================================================ rules

def rule_overcrowding(f, ctx):
    ratio = ctx.cfg["crowd_ratio"]
    limit = f.capacity * ratio
    counted = list(UsageRecord.objects.filter(
        facility=f, status=UsageRecord.Status.USED, headcount__isnull=False,
        start_at__date__gte=ctx.start, start_at__date__lte=ctx.as_of,
    ).values_list("headcount", flat=True))
    over = [h for h in counted if h > limit]
    planned = [
        (str(s.activity), s.expected_attendees)
        for s in Schedule.objects.filter(facility=f, term_end__gte=ctx.as_of, expected_attendees__gt=limit).select_related("activity")
    ] + [
        (r.purpose, r.expected_attendees)
        for r in Reservation.objects.filter(
            facility=f, status=Reservation.Status.APPROVED, end_at__gte=ctx.now,
            start_at__lte=ctx.now + datetime.timedelta(days=30), expected_attendees__gt=limit,
        )
    ]
    planned = list(dict.fromkeys(planned))  # a class meeting twice a week is listed once
    if not over and not planned:
        return None
    likelihood = rate(_pct(len(over), len(counted)), OVERCROWD_SHARE) if over else 1
    if planned:
        likelihood = max(likelihood, 4)  # it is already scheduled to happen
    peak = max(over + [n for _, n in planned])
    impact = rate(peak / f.capacity, OVERCROWD_EXCESS)
    parts = []
    if over:
        parts.append(f"{len(over)} of {len(counted)} counted sessions ({_pct(len(over), len(counted)):.0f}%) in the last "
                     f"{ctx.cfg['window_days']} days exceeded the capacity of {f.capacity} (peak {max(over)} people).")
    if planned:
        parts.append("Planned bookings over capacity: " + "; ".join(f"{name} expects {n}" for name, n in planned[:5]) + ".")
    return Finding(f, C.OVERCROWDING, likelihood, impact, " ".join(parts), {
        "capacity": f.capacity, "counted_sessions": len(counted), "over_capacity_sessions": len(over),
        "peak_people": peak, "peak_ratio": round(peak / f.capacity, 2), "planned_over_capacity": len(planned),
    })


def rule_underutilization(f, ctx):
    m, s = ctx.metrics.get(f.pk), ctx.summaries.get(f.pk)
    if f.status != Facility.Status.AVAILABLE or not m or not m["available"]:
        return None
    low, high = ctx.cfg["low"], ctx.cfg["high"]
    util = _pct(m["used"], m["available"])
    sched = _pct(m["scheduled"], m["available"])
    if max(util, sched) > high:
        return None  # in demand: handled by the over-demand rule
    ratings, parts = [], []
    if util < low:
        ratings.append(5 if util < low / 4 else 4 if util < low / 2 else 3 if util < low * 0.75 else 2)
        parts.append(f"Utilization was {util:.1f}% over the last {ctx.cfg['window_days']} days (target at least {low:.0f}%); "
                     f"{m['used']:.0f} of {m['available']:.0f} available hours were used.")
    no_show_rate = s.no_show_rate if s and s.recorded >= MIN_SESSIONS_FOR_RATES else None
    if no_show_rate is not None and no_show_rate >= NO_SHOW_RATE[-1][0]:
        ratings.append(rate(no_show_rate, NO_SHOW_RATE))
        parts.append(f"{s.no_show} of {s.recorded} recorded bookings ({no_show_rate:.0f}%) were no-shows, "
                     "blocking the room for others.")
    if not ratings:
        return None
    likelihood = max(ratings)
    coverage = s.coverage_rate if s else None
    if coverage is not None and coverage < 70 and util < low:
        likelihood = max(1, likelihood - 1)
        parts.append(f"Only {coverage:.0f}% of sessions have usage records, so actual use may be higher.")
    impact = rate(f.capacity, CAPACITY_IMPACT)
    if no_show_rate is not None and no_show_rate >= NO_SHOW_RATE[-1][0]:
        impact = max(impact, 3)
    return Finding(f, C.UNDERUTILIZATION, likelihood, impact, " ".join(parts), {
        "utilization_pct": round(util, 1), "scheduled_pct": round(sched, 1), "target_min_pct": low,
        "no_show_pct": None if no_show_rate is None else round(no_show_rate, 1),
        "data_coverage_pct": None if coverage is None else round(coverage, 1), "capacity": f.capacity,
    })


def rule_over_demand(f, ctx):
    m = ctx.metrics.get(f.pk)
    rejected = Reservation.objects.filter(
        facility=f, status=Reservation.Status.REJECTED, start_at__date__gte=ctx.start, start_at__date__lte=ctx.as_of + datetime.timedelta(days=30),
    ).count()
    util = sched = None
    if m and m["available"]:
        util, sched = _pct(m["used"], m["available"]), _pct(m["scheduled"], m["available"])
    peak = max(util or 0, sched or 0)
    if peak <= ctx.cfg["high"] and rejected < 5:
        return None
    parts = []
    if peak > ctx.cfg["high"]:
        likelihood = rate(peak, DEMAND_PEAK)
        parts.append(f"{sched:.1f}% of available time is booked and {util:.1f}% is actually used "
                     f"(high-demand threshold {ctx.cfg['high']:.0f}%). Little room is left for new classes or requests.")
    else:
        likelihood = 3
    if rejected:
        parts.append(f"{rejected} reservation request{'s were' if rejected != 1 else ' was'} rejected in this period.")
        if rejected >= 3:
            likelihood = min(5, likelihood + 1)
    impact = 4 if (sched or 0) > 90 else 3
    return Finding(f, C.OVER_DEMAND, likelihood, impact, " ".join(parts), {
        "utilization_pct": None if util is None else round(util, 1),
        "scheduled_pct": None if sched is None else round(sched, 1),
        "threshold_pct": ctx.cfg["high"], "rejected_requests": rejected,
    })


def rule_conflict(f, ctx):
    issues, soonest = [], None
    for s in Schedule.objects.filter(facility=f, term_end__gte=ctx.as_of).select_related("activity"):
        for c in check_schedule(f, s.day_of_week, s.start_time, s.end_time, max(s.term_start, ctx.as_of), s.term_end, exclude_pk=s.pk):
            if c.kind == "SCHEDULE_OVERLAP":
                issues.append(f"{s.activity} ({s.get_day_of_week_display()}): {c.message}")
                soonest = ctx.now if soonest is None else soonest
    for r in Reservation.objects.filter(
        facility=f, status=Reservation.Status.APPROVED, end_at__gt=ctx.now, start_at__lte=ctx.now + datetime.timedelta(days=60),
    ):
        errors = [c for c in check_reservation(f, r.start_at, r.end_at, exclude_pk=r.pk) if c.is_error]
        if errors:
            issues.append(f"Approved reservation “{r.purpose}” on {timezone.localtime(r.start_at):%b %d}: "
                          + " ".join(c.message for c in errors))
            soonest = r.start_at if soonest is None else min(soonest, r.start_at)
    issues = list(dict.fromkeys(issues))  # each overlapping pair is found from both sides
    if not issues:
        return None
    likelihood = 5 if soonest and soonest <= ctx.now + datetime.timedelta(days=7) else 4
    return Finding(f, C.CONFLICT, likelihood, 4,
                   f"{len(issues)} conflicting allocation(s) exist: " + " | ".join(issues[:5]),
                   {"conflicts": len(issues)})


def rule_unavailable(f, ctx):
    open_maint = list(f.maintenance_records.exclude(status=MaintenanceRecord.Status.COMPLETED).filter(start_at__lte=ctx.now))
    open_maint = [m for m in open_maint if m.end_at is None or m.end_at > ctx.now]
    if f.status == Facility.Status.AVAILABLE and not open_maint:
        return None
    started = min((m.start_at for m in open_maint), default=None)
    ends = [m.end_at for m in open_maint]
    until = None if not ends or None in ends else max(ends)
    days_down = (ctx.now - started).days if started else None
    window_end = ctx.as_of + datetime.timedelta(days=LOOKAHEAD_DAYS)
    affected = [
        s for s in sessions_between(ctx.as_of, window_end, facilities=[f])
        if s.start_at >= ctx.now and (until is None or s.start_at < until)
    ]
    threshold = ctx.cfg["maintenance_days"]
    if (days_down is None or days_down < threshold) and not affected:
        return None
    if days_down is None:
        likelihood = 4
    else:
        likelihood = 5 if days_down >= threshold else 4 if days_down >= threshold / 2 else 3
    impact = rate(len(affected), AFFECTED_SESSIONS)
    state = f.get_status_display().lower() if f.status != Facility.Status.AVAILABLE else "under maintenance"
    parts = [f"{f.facility_code} has been {state}"
             + (f" for {days_down} days" if days_down is not None else " with no maintenance record (end date unknown)")
             + (f" ({open_maint[0].reason})" if open_maint else "") + "."]
    parts.append(f"{len(affected)} scheduled session(s) in the next {LOOKAHEAD_DAYS} days are affected and need relocation."
                 if affected else "No sessions are scheduled in the next 14 days.")
    return Finding(f, C.UNAVAILABLE, likelihood, impact, " ".join(parts), {
        "status": f.status, "days_unavailable": days_down, "threshold_days": threshold,
        "affected_sessions_next_14_days": len(affected), "expected_end": until.isoformat() if until else None,
    })


def rule_data_quality(f, ctx):
    s = ctx.summaries.get(f.pk)
    parts, ratings = [], []
    coverage = s.coverage_rate if s and s.sessions >= 5 else None
    if coverage is not None and coverage < 70:
        ratings.append(rate(100 - coverage, COVERAGE_GAP))
        parts.append(f"Only {s.recorded} of {s.sessions} finished sessions ({coverage:.0f}%) have usage records; "
                     f"{s.no_data} have no data, so utilization for this room is unreliable.")
    stale_start = ctx.as_of - datetime.timedelta(days=ctx.cfg["stale_days"])
    recent_sessions = [x for x in sessions_between(stale_start, ctx.as_of, facilities=[f]) if x.end_at <= ctx.now]
    has_recent_data = UsageRecord.objects.filter(facility=f, start_at__date__gte=stale_start).exists()
    if len(recent_sessions) >= 3 and not has_recent_data:
        ratings.append(4)
        parts.append(f"No usage has been recorded in the last {ctx.cfg['stale_days']} days despite "
                     f"{len(recent_sessions)} finished sessions.")
    if not ratings:
        return None
    return Finding(f, C.DATA_QUALITY, max(ratings), 3, " ".join(parts), {
        "data_coverage_pct": None if coverage is None else round(coverage, 1),
        "finished_sessions": s.sessions if s else 0, "sessions_without_data": s.no_data if s else 0,
        "sessions_last_n_days": len(recent_sessions), "stale_days": ctx.cfg["stale_days"],
    })


RULES = [rule_overcrowding, rule_underutilization, rule_over_demand, rule_conflict, rule_unavailable, rule_data_quality]


# ================================================================ detection and sync

def build_context(as_of=None):
    cfg = settings_snapshot()
    as_of = as_of or timezone.localdate()
    start = as_of - datetime.timedelta(days=cfg["window_days"] - 1)
    facilities = list(Facility.objects.filter(is_active=True))
    metrics = {}
    definition = MetricDefinition.active()
    if definition:
        rows = UtilizationMetric.objects.filter(
            definition=definition, period_start__gte=start, period_start__lte=as_of, facility__in=facilities,
        ).values("facility_id").annotate(a=Sum("available_hours"), u=Sum("used_hours"), s=Sum("scheduled_hours"))
        metrics = {r["facility_id"]: {"available": float(r["a"] or 0), "used": float(r["u"] or 0), "scheduled": float(r["s"] or 0)}
                   for r in rows}
    summaries = {s.facility.pk: s for s in compare(start, as_of, facilities)["summaries"]}
    return Context(as_of, start, timezone.now(), cfg, facilities, metrics, summaries)


def detect(as_of=None):
    ctx = build_context(as_of)
    findings = []
    for f in ctx.facilities:
        for rule in RULES:
            finding = rule(f, ctx)
            if finding:
                finding.evidence.update(window_start=ctx.start.isoformat(), window_end=ctx.as_of.isoformat())
                findings.append(finding)
    return findings


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    reopened: int = 0
    closed: int = 0
    findings: int = 0
    reminders: int = 0


def _owner_lookup():
    """Default owner per category: the first active user holding the responsible role."""
    cache = {}

    def owner(category):
        role = DEFAULT_OWNER_ROLE[category]
        if role not in cache:
            cache[role] = User.objects.filter(role=role, is_active=True).order_by("pk").first()
        return cache[role]

    return owner


@transaction.atomic
def run(as_of=None):
    """Detect risks and synchronize the register. Returns a SyncResult."""
    default_owner = _owner_lookup()
    findings = detect(as_of)
    result = SyncResult(findings=len(findings))
    now = timezone.now()
    seen = set()

    for fd in findings:
        seen.add((fd.facility.pk, fd.category))
        risk = (Risk.objects.filter(facility=fd.facility, category=fd.category, auto_detected=True)
                .exclude(status=Risk.Status.CLOSED).order_by("-detected_at").first())
        if risk is None:
            risk = Risk.objects.create(
                facility=fd.facility, category=fd.category, likelihood=fd.likelihood, impact=fd.impact,
                reason=fd.reason, evidence=fd.evidence, auto_detected=True, last_detected_at=now,
                owner=default_owner(fd.category),
            )
            result.created += 1
            if risk.priority == Risk.Priority.HIGH:
                _alert_high(risk)
            continue
        old_rank = Risk.PRIORITY_RANK[risk.priority]
        changed = (risk.likelihood, risk.impact, risk.reason) != (fd.likelihood, fd.impact, fd.reason)
        risk.likelihood, risk.impact, risk.reason, risk.evidence, risk.last_detected_at = (
            fd.likelihood, fd.impact, fd.reason, fd.evidence, now)
        risk.save()  # recomputes score and priority
        rose = Risk.PRIORITY_RANK[risk.priority] > old_rank
        if risk.status == Risk.Status.ACCEPTED and rose:
            risk.status = Risk.Status.OPEN
            risk.add_note(f"Reopened automatically: priority rose to {risk.get_priority_display()}.")
            risk.save()
            result.reopened += 1
            notify(risk.owner, f"Accepted risk reopened: {risk.facility.facility_code}",
                   f"{risk.get_category_display()} rose to {risk.get_priority_display()} priority.",
                   url=_risk_url(risk), level=Level.WARNING)
        elif changed:
            result.updated += 1
        if rose and risk.priority == Risk.Priority.HIGH:
            _alert_high(risk)

    for risk in Risk.objects.filter(auto_detected=True).exclude(status=Risk.Status.CLOSED):
        if (risk.facility_id, risk.category) not in seen:
            risk.status = Risk.Status.CLOSED
            risk.closed_at = now
            risk.add_note("Closed automatically: the condition is no longer detected.")
            risk.save()
            result.closed += 1

    result.reminders = remind_overdue_actions()
    return result


def _risk_url(risk):
    return reverse("risks:detail", args=[risk.pk])


def _alert_high(risk):
    """Once per risk: tell the owner and administrators about a High-priority risk."""
    admins = User.objects.filter(role=User.Role.ADMIN, is_active=True)
    notify([risk.owner, *admins], f"High risk: {risk.get_category_display()} at {risk.facility.facility_code}",
           f"Score {risk.score}/25. {risk.reason[:300]}", url=_risk_url(risk), level=Level.CRITICAL,
           dedupe_key=f"risk-{risk.pk}-high")


def remind_overdue_actions():
    """One reminder per overdue action per day, to the person it is assigned to (or the risk owner)."""
    today = timezone.localdate()
    sent = 0
    overdue = (MitigationAction.objects.exclude(status=MitigationAction.Status.DONE)
               .filter(due_date__lt=today, risk__status__in=Risk.ACTIVE_STATUSES)
               .select_related("risk__facility", "assigned_to", "risk__owner"))
    for action in overdue:
        days = (today - action.due_date).days
        sent += notify(action.assigned_to or action.risk.owner, f"Overdue action: {action.action[:80]}",
                       f"Due {action.due_date:%b %d} ({days} day{'s' if days != 1 else ''} ago) for "
                       f"{action.risk.get_category_display()} at {action.risk.facility.facility_code}.",
                       url=_risk_url(action.risk), level=Level.WARNING,
                       dedupe_key=f"action-{action.pk}-overdue-{today.isoformat()}")
    return sent
