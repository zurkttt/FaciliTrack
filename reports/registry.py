"""Standard reports (Requirement R8). Each builder returns plain data that every renderer
(HTML preview, CSV, Excel, PDF) uses, so all formats show exactly the same numbers.

Every report states its parameters, data sources, metric-definition version and the time it was
generated, so it can be reproduced from governed data (success criterion 5.3, KPI 14.1).
"""
import datetime
from dataclasses import dataclass, field

from django.db.models import Count, Q
from django.utils import timezone

from analytics.models import MetricDefinition
from analytics.queries import base_queryset, by_group, classify, last_calculated, thresholds, totals
from facilities.models import Facility
from risks.models import MitigationAction, Risk
from usage.reports import compare

TEXT, INT, NUM, PCT = "text", "int", "num", "pct"
DISCLAIMER = ("Risk scores are decision-support indicators based on approved rules. They do not certify a facility "
              "as safe and do not replace inspections or decisions by qualified campus personnel.")


@dataclass
class Column:
    header: str
    kind: str = TEXT
    width: int = 12  # relative width (Excel characters; PDF scales proportionally)


@dataclass
class ReportData:
    title: str
    columns: list
    rows: list
    params: list = field(default_factory=list)   # [(label, value)]
    notes: list = field(default_factory=list)
    totals: list = None


@dataclass
class ReportSpec:
    slug: str
    title: str
    description: str
    builder: object
    uses_dates: bool = True
    icon: str = "file-earmark-text"


def _fmt_date(d):
    return d.strftime("%b %d, %Y")


def _facilities(building):
    qs = Facility.objects.filter(is_active=True).select_related("building", "facility_type", "owner_department")
    return qs.filter(building=building) if building else qs


def _common_params(start, end, building):
    params = []
    if start and end:
        params.append(("Period", f"{_fmt_date(start)} to {_fmt_date(end)} ({(end - start).days + 1} days)"))
    params.append(("Building", str(building) if building else "All buildings"))
    return params


# ---------------------------------------------------------------- builders

def utilization_report(start, end, building=None):
    definition = MetricDefinition.active()
    limits = thresholds()
    cols = [Column("Facility", width=11), Column("Name", width=18), Column("Building", width=9), Column("Type", width=18),
            Column("Capacity", INT, 9), Column("Available h", NUM, 11), Column("Used h", NUM, 9),
            Column("Scheduled h", NUM, 11), Column("Utilization %", PCT, 12), Column("Scheduled %", PCT, 12),
            Column("Seat occupancy %", PCT, 14), Column("Status", width=16)]
    if definition is None:
        return ReportData("Facility Utilization Report", cols, [], _common_params(start, end, building),
                          ["No active metric definition: nothing to report."])
    qs = base_queryset(definition, start, end, building=building)
    facilities = {f.pk: f for f in _facilities(building)}
    labels = {"low": "Underutilized", "high": "High demand", "ok": "In range", "unavailable": "Unavailable", "none": "No available time"}
    rows = []
    for r in by_group(qs, "facility"):
        f = facilities.get(r.key)
        if f is None:
            continue
        rows.append([f.facility_code, f.name, f.building.code, f.facility_type.name, f.capacity, r.available, r.used,
                     r.scheduled, r.rate, r.scheduled_rate, r.occupancy, labels[classify(r, limits)]])
    t = totals(qs)
    calculated = last_calculated(definition)
    params = _common_params(start, end, building) + [
        ("Metric definition", f"{definition.name} v{definition.version} (approved by {definition.approved_by or 'n/a'})"),
        ("Data as of", timezone.localtime(calculated).strftime("%b %d, %Y %H:%M") if calculated else "never calculated"),
    ]
    notes = [
        f"Utilization = used hours ÷ available hours. Available time: {definition.operating_days_display}, within each "
        f"facility's operating hours{', excluding maintenance' if definition.exclude_maintenance else ''}.",
        f"Target range {limits['low']:.0f}–{limits['high']:.0f}%. 'High demand' means actual or scheduled utilization is above "
        f"{limits['high']:.0f}%.",
        "Totals are computed from summed hours, not by averaging percentages.",
    ]
    total_row = ["Total", "", "", "", sum(f.capacity for f in facilities.values()), t.available, t.used, t.scheduled,
                 t.rate, t.scheduled_rate, t.occupancy, ""]
    return ReportData("Facility Utilization Report", cols, rows, params, notes, total_row)


def risk_report(start=None, end=None, building=None):
    cols = [Column("Priority", width=9), Column("Score", INT, 8), Column("L", INT, 4), Column("I", INT, 4),
            Column("Facility", width=13), Column("Category", width=19), Column("Status", width=12),
            Column("Owner", width=15), Column("Open actions", INT, 9), Column("Overdue", INT, 10),
            Column("Detected", width=12), Column("Reason", width=54)]
    today = timezone.localdate()
    qs = (Risk.objects.exclude(status=Risk.Status.CLOSED).select_related("facility", "owner")
          .annotate(open_actions=Count("actions", filter=~Q(actions__status=MitigationAction.Status.DONE)),
                    overdue=Count("actions", filter=~Q(actions__status=MitigationAction.Status.DONE) & Q(actions__due_date__lt=today)))
          .order_by("-score", "facility__facility_code"))
    if building:
        qs = qs.filter(facility__building=building)
    rows = [[r.get_priority_display(), r.score, r.likelihood, r.impact, r.facility.facility_code, r.get_category_display(),
             r.get_status_display(), (r.owner.get_full_name() or r.owner.username) if r.owner else "Unassigned",
             r.open_actions, r.overdue, timezone.localtime(r.detected_at).strftime("%Y-%m-%d"), r.reason] for r in qs]
    counts = {p: sum(1 for row in rows if row[0] == label) for p, label in Risk.Priority.choices}
    params = [("Snapshot", timezone.localtime().strftime("%b %d, %Y %H:%M")),
              ("Building", str(building) if building else "All buildings"),
              ("Included", "All risks that are not closed (open, in review, mitigating, accepted)"),
              ("Summary", f"{counts['HIGH']} high, {counts['MEDIUM']} medium, {counts['LOW']} low")]
    notes = ["Score = likelihood (L, 1–5) × impact (I, 1–5). Low 1–7, Medium 8–14, High 15–25.", DISCLAIMER]
    return ReportData("Risk Register Report", cols, rows, params, notes)


def usage_report(start, end, building=None):
    cols = [Column("Facility", width=11), Column("Sessions", INT, 9), Column("Used", INT, 7), Column("No-show", INT, 8),
            Column("Not recorded", INT, 11), Column("Data coverage %", PCT, 14), Column("No-show %", PCT, 10),
            Column("Unscheduled uses", INT, 14), Column("Over-capacity", INT, 12), Column("Scheduled h", NUM, 11),
            Column("Actual h", NUM, 9)]
    data = compare(start, end, _facilities(building))
    rows = [[s.facility.facility_code, s.sessions, s.used, s.no_show, s.no_data, s.coverage_rate, s.no_show_rate,
             s.unscheduled, s.over_capacity, s.scheduled_hours, s.used_hours] for s in data["summaries"]]
    t = data["totals"]
    total_row = ["Total", t.sessions, t.used, t.no_show, t.no_data, t.coverage_rate, t.no_show_rate, t.unscheduled,
                 t.over_capacity, t.scheduled_hours, t.used_hours]
    notes = ["Only sessions that had ended when the report was generated are counted.",
             "Data coverage = sessions with any usage evidence (used or no-show) ÷ finished sessions. "
             "Low coverage means utilization figures for that room are less reliable."]
    return ReportData("Scheduled vs Actual Use Report", cols, rows, _common_params(start, end, building), notes, total_row)


def inventory_report(start=None, end=None, building=None):
    cols = [Column("Code", width=11), Column("Name", width=20), Column("Building", width=9), Column("Floor", INT, 6),
            Column("Type", width=18), Column("Capacity", INT, 9), Column("Status", width=16), Column("Owner", width=10),
            Column("Hours", width=12), Column("Active risks", INT, 11)]
    qs = _facilities(building).annotate(
        active_risks=Count("risks", filter=Q(risks__status__in=Risk.ACTIVE_STATUSES))).order_by("facility_code")
    rows = [[f.facility_code, f.name, f.building.code, f.floor, f.facility_type.name, f.capacity, f.get_status_display(),
             f.owner_department.code if f.owner_department else "", f"{f.open_time:%H:%M}–{f.close_time:%H:%M}",
             f.active_risks] for f in qs]
    params = [("Snapshot", timezone.localtime().strftime("%b %d, %Y %H:%M")),
              ("Building", str(building) if building else "All buildings")]
    total_row = ["Total", f"{len(rows)} facilities", "", "", "", sum(r[5] for r in rows), "", "", "", sum(r[9] for r in rows)]
    return ReportData("Facility Inventory Report", cols, rows, params, ["Active facilities only."], total_row)


REPORTS = {
    spec.slug: spec for spec in [
        ReportSpec("utilization", "Facility utilization", "Used, scheduled and available hours, utilization and seat occupancy per facility.",
                   utilization_report, icon="graph-up"),
        ReportSpec("usage", "Scheduled vs actual use", "Sessions used, no-shows, missing data and walk-in use per facility.",
                   usage_report, icon="clipboard-check"),
        ReportSpec("risks", "Risk register", "All risks that are not closed, with scores, owners and mitigation status.",
                   risk_report, uses_dates=False, icon="shield-exclamation"),
        ReportSpec("inventory", "Facility inventory", "The facility registry: capacity, status, ownership and active risks.",
                   inventory_report, uses_dates=False, icon="buildings"),
    ]
}


def default_period():
    end = timezone.localdate() - datetime.timedelta(days=1)
    return end - datetime.timedelta(days=27), end
