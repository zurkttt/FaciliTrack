import csv
import datetime

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import role_required
from facilities.models import Facility

from .engine import calculate
from .forms import AnalyticsFilterForm, MetricDefinitionForm, RecalculateForm
from .models import WEEKDAY_NAMES, MetricDefinition
from .queries import GROUPINGS, base_queryset, by_group, by_hour_of_week, by_period, classify, last_calculated, thresholds, totals

ANALYTICS_VIEWERS = (User.Role.ADMIN, User.Role.FACILITIES, User.Role.PLANNER, User.Role.REGISTRAR, User.Role.DEPT_HEAD)
# Planning owns utilization definitions (RACI, Section 12.4).
ANALYTICS_ADMINS = (User.Role.ADMIN, User.Role.PLANNER)

STALE_AFTER = datetime.timedelta(hours=26)  # nightly job + margin
RECALC_DAYS_ON_ACTIVATE = 120
HEAT_STEPS = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]  # blue 100..700


def _r(value, digits=1):
    return None if value is None else round(value, digits)


def _heatmap(start, end, facilities):
    grid = by_hour_of_week(start, end, facilities)
    weekday_counts = [0] * 7
    day = start
    while day <= end:
        weekday_counts[day.weekday()] += 1
        day += datetime.timedelta(days=1)
    if facilities:
        first = min(f.open_time.hour for f in facilities)
        last = max(f.close_time.hour + (1 if f.close_time.minute else 0) for f in facilities)
    else:
        first, last = 7, 21
    hours = list(range(first, last))
    # Average number of rooms in use during that weekday-hour
    values = [[grid[d][h] / weekday_counts[d] if weekday_counts[d] else 0 for h in hours] for d in range(7)]
    peak = max((v for row in values for v in row), default=0)
    rows = []
    for d in range(7):
        if not any(values[d]) and d >= 5:
            continue  # hide empty weekend rows
        cells = []
        for h, v in zip(hours, values[d]):
            step = None if v <= 0 or not peak else min(len(HEAT_STEPS) - 1, int(v / peak * len(HEAT_STEPS)))
            cells.append({"hour": h, "value": round(v, 1), "color": HEAT_STEPS[step] if step is not None else ""})
        rows.append({"day": WEEKDAY_NAMES[d], "cells": cells})
    return {"hours": hours, "rows": rows, "peak": round(peak, 1), "steps": HEAT_STEPS}


@role_required(*ANALYTICS_VIEWERS)
def dashboard(request):
    definition = MetricDefinition.active()
    form = AnalyticsFilterForm(request.GET)
    ctx = {"form": form, "definition": definition, "limits": thresholds(),
           "can_admin": request.user.has_role(*ANALYTICS_ADMINS)}
    if definition is None:
        messages.warning(request, "No active metric definition. A planner or administrator must activate one.")
        return render(request, "analytics/dashboard.html", ctx)
    if not form.is_valid():
        return render(request, "analytics/dashboard.html", ctx)

    d = form.cleaned_data
    filters = {k: d.get(k) for k in ("building", "facility_type", "department", "facility")}
    qs = base_queryset(definition, d["start"], d["end"], **filters)
    group_rows = by_group(qs, d["group"])
    limits = ctx["limits"]

    if request.GET.get("export") == "csv":
        return _export_csv(d, group_rows, definition)

    facilities = Facility.objects.filter(is_active=True)
    for key, field in (("building", "building"), ("facility_type", "facility_type"), ("department", "owner_department")):
        if filters[key]:
            facilities = facilities.filter(**{field: filters[key]})
    if filters["facility"]:
        facilities = facilities.filter(pk=filters["facility"].pk)

    trend = by_period(qs, d["period"])
    ranked = sorted((r for r in group_rows if r.rate is not None), key=lambda r: r.rate, reverse=True)
    calculated = last_calculated(definition)
    insight = ""
    if ranked:
        word = GROUPINGS[d["group"]][2].lower()
        plural = {"facility": "facilities", "building": "buildings", "facility type": "facility types",
                  "department": "departments"}.get(word, word + "s")
        low_n = sum(1 for r in group_rows if classify(r, limits) == "low")
        high_n = sum(1 for r in group_rows if classify(r, limits) == "high")
        insight = format_html(
            "<strong>{}</strong> was the most used {} at <strong>{}%</strong>"
            "{}. <strong>{}</strong> of {} {} were below the {}% target and <strong>{}</strong> {} in high demand.",
            ranked[0].label, word, f"{ranked[0].rate:.1f}",
            format_html("; <strong>{}</strong> the least at <strong>{}%</strong>", ranked[-1].label, f"{ranked[-1].rate:.1f}")
            if len(ranked) > 1 else "",
            low_n, len(group_rows), plural, f"{limits['low']:.0f}", high_n, "was" if high_n == 1 else "were",
        )
    ctx.update(
        total=totals(qs),
        rows=[{"row": r, "band": classify(r, limits)} for r in group_rows],
        group_label=GROUPINGS[d["group"]][2],
        group=d["group"],
        low_count=sum(1 for r in group_rows if classify(r, limits) == "low"),
        high_count=sum(1 for r in group_rows if classify(r, limits) == "high"),
        has_data=qs.exists(),
        heat=_heatmap(d["start"], d["end"], list(facilities)),
        last_calculated=calculated,
        is_stale=calculated is None or timezone.now() - calculated > STALE_AFTER,
        chart_data={
            "trend": {"labels": [r.label for r in trend], "actual": [_r(r.rate) for r in trend],
                      "scheduled": [_r(r.scheduled_rate) for r in trend]},
            "ranked": {"labels": [r.label for r in ranked], "values": [_r(r.rate) for r in ranked],
                       "scheduled": [_r(r.scheduled_rate) for r in ranked]},
            "limits": limits,
        },
        insight=insight,
        used_note=f"{totals(qs).used:,.0f} of {totals(qs).available:,.0f} available hours used",
        gap_note=f"{(totals(qs).gap or 0):.1f} pts booked but not used",
        range_note=(f"{sum(1 for r in group_rows if classify(r, limits) == 'low')} under {limits['low']:.0f}% and "
                    f"{sum(1 for r in group_rows if classify(r, limits) == 'high')} high demand"),
        groupings=[(k, v[2]) for k, v in GROUPINGS.items()],
        periods=[("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")],
        export_query=request.GET.urlencode(),
        recalc_form=RecalculateForm(initial={"start": d["start"], "end": d["end"]}),
    )
    return render(request, "analytics/dashboard.html", ctx)


def _export_csv(d, rows, definition):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="utilization_{d["start"]}_{d["end"]}.csv"'
    writer = csv.writer(response)
    writer.writerow([f"Utilization by {GROUPINGS[d['group']][2].lower()}, {d['start']} to {d['end']}, definition v{definition.version}"])
    writer.writerow([GROUPINGS[d["group"]][2], "Available hours", "Used hours", "Scheduled hours",
                     "Utilization %", "Scheduled %", "Avg seat occupancy %"])
    for r in rows:
        writer.writerow([r.label, f"{r.available:.2f}", f"{r.used:.2f}", f"{r.scheduled:.2f}",
                         "" if r.rate is None else f"{r.rate:.1f}",
                         "" if r.scheduled_rate is None else f"{r.scheduled_rate:.1f}",
                         "" if r.occupancy is None else f"{r.occupancy:.1f}"])
    return response


@require_POST
@role_required(*ANALYTICS_ADMINS)
def recalculate(request):
    form = RecalculateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid date range for recalculation.")
    else:
        try:
            rows = calculate(form.cleaned_data["start"], form.cleaned_data["end"])
            messages.success(request, f"Recalculated {rows} facility-days.")
        except ValueError as exc:
            messages.error(request, str(exc))
    back = request.POST.get("next", "")
    return redirect(f"/analytics/?{back}" if back and "//" not in back else "analytics:dashboard")


@role_required(*ANALYTICS_ADMINS)
def definitions(request):
    form = MetricDefinitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        definition = form.save()
        messages.success(request, f"Definition v{definition.version} created. Activate it to start using it.")
        return redirect("analytics:definitions")
    return render(request, "analytics/definitions.html", {
        "definitions": MetricDefinition.objects.all(), "form": form, "recalc_days": RECALC_DAYS_ON_ACTIVATE,
    })


@require_POST
@role_required(*ANALYTICS_ADMINS)
def activate_definition(request, pk):
    definition = get_object_or_404(MetricDefinition, pk=pk)
    definition.activate()
    end = timezone.localdate()
    rows = calculate(end - datetime.timedelta(days=RECALC_DAYS_ON_ACTIVATE - 1), end, definition)
    messages.success(request, f"v{definition.version} is now active. Recalculated {rows} facility-days.")
    return redirect("analytics:definitions")
