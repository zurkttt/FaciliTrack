from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import role_required
from facilities.models import Facility
from notifications.services import Level, notify

from .engine import run
from .forms import ManualRiskForm, MitigationActionForm, RiskUpdateForm
from .models import MitigationAction, Risk, priority_for_score

RISK_VIEWERS = (User.Role.ADMIN, User.Role.FACILITIES, User.Role.PLANNER, User.Role.REGISTRAR, User.Role.DEPT_HEAD)
RISK_MANAGERS = (User.Role.ADMIN, User.Role.FACILITIES, User.Role.PLANNER)


def can_manage(user, risk):
    return user.has_role(*RISK_MANAGERS) or risk.owner_id == user.pk


def _matrix(risks):
    """5x5 likelihood x impact grid of active-risk counts (impact 5 on top)."""
    counts = {(r["likelihood"], r["impact"]): r["n"] for r in risks.values("likelihood", "impact").annotate(n=Count("id"))}
    return [
        {"impact": i, "cells": [
            {"likelihood": li, "count": counts.get((li, i), 0), "band": priority_for_score(li * i).lower(), "score": li * i}
            for li in range(1, 6)
        ]}
        for i in range(5, 0, -1)
    ]


@role_required(*RISK_VIEWERS)
def register(request):
    p = request.GET
    qs = Risk.objects.select_related("facility", "owner").annotate(
        open_actions=Count("actions", filter=~Q(actions__status=MitigationAction.Status.DONE)),
    )
    status = p.get("status", "active")
    if status == "active":
        qs = qs.filter(status__in=Risk.ACTIVE_STATUSES)
    elif status in Risk.Status.values:
        qs = qs.filter(status=status)
    if p.get("priority") in Risk.Priority.values:
        qs = qs.filter(priority=p["priority"])
    if p.get("category") in Risk.Category.values:
        qs = qs.filter(category=p["category"])
    if p.get("facility", "").isdigit():
        qs = qs.filter(facility_id=p["facility"])
    if p.get("mine"):
        qs = qs.filter(owner=request.user)
    if p.get("likelihood", "").isdigit() and p.get("impact", "").isdigit():
        qs = qs.filter(likelihood=int(p["likelihood"]), impact=int(p["impact"]))
    page = Paginator(qs.order_by("-score", "-detected_at"), 25).get_page(p.get("page"))

    active = Risk.objects.filter(status__in=Risk.ACTIVE_STATUSES)
    by_priority = dict(active.values_list("priority").annotate(n=Count("id")))
    params = p.copy()
    params.pop("page", None)
    last_run = Risk.objects.filter(auto_detected=True).order_by("-last_detected_at").values_list("last_detected_at", flat=True).first()
    return render(request, "risks/register.html", {
        "page_obj": page, "risks": page.object_list, "filters": p, "status": status,
        "statuses": Risk.Status.choices, "priorities": Risk.Priority.choices, "categories": Risk.Category.choices,
        "facilities": Facility.objects.filter(is_active=True), "querystring": params.urlencode(),
        "matrix": _matrix(active), "high": by_priority.get("HIGH", 0), "medium": by_priority.get("MEDIUM", 0),
        "low": by_priority.get("LOW", 0),
        "overdue_actions": MitigationAction.objects.exclude(status=MitigationAction.Status.DONE)
                           .filter(due_date__lt=timezone.localdate(), risk__status__in=Risk.ACTIVE_STATUSES).count(),
        "accepted": Risk.objects.filter(status=Risk.Status.ACCEPTED).count(),
        "can_manage": request.user.has_role(*RISK_MANAGERS), "last_run": last_run,
    })


@role_required(*RISK_VIEWERS)
def detail(request, pk):
    risk = get_object_or_404(Risk.objects.select_related("facility__building", "owner"), pk=pk)
    manage = can_manage(request.user, risk)
    return render(request, "risks/detail.html", {
        "risk": risk, "actions": risk.actions.select_related("assigned_to"), "can_manage": manage,
        "update_form": RiskUpdateForm(initial={"status": risk.status, "owner": risk.owner_id}),
        "action_form": MitigationActionForm(), "action_statuses": MitigationAction.Status.choices,
        "notes": [line for line in risk.resolution.splitlines() if line.strip()],
        "evidence": [(_label(k), "—" if v is None else v) for k, v in risk.evidence.items()],
    })


def _label(key):
    label = key.replace("_pct", " (%)").replace("_", " ")
    return label[0].upper() + label[1:]


@require_POST
@role_required(*RISK_VIEWERS)
def update(request, pk):
    risk = get_object_or_404(Risk, pk=pk)
    if not can_manage(request.user, risk):
        raise PermissionDenied
    form = RiskUpdateForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for e in errors:
                messages.error(request, e)
        return redirect("risks:detail", pk=pk)
    d = form.cleaned_data
    changes = []
    if d["status"] != risk.status:
        changes.append(f"Status {risk.get_status_display()} → {Risk.Status(d['status']).label}")
        risk.status = d["status"]
        risk.closed_at = timezone.now() if risk.status == Risk.Status.CLOSED else None
    if d["owner"] != risk.owner:
        changes.append(f"Owner → {d['owner'].get_full_name() or d['owner'].username if d['owner'] else 'unassigned'}")
        risk.owner = d["owner"]
        if risk.owner and risk.owner != request.user:
            notify(risk.owner, f"You now own a risk: {risk.get_category_display()} at {risk.facility.facility_code}",
                   f"{risk.get_priority_display()} priority (score {risk.score}). Assigned by "
                   f"{request.user.get_full_name() or request.user.username}.",
                   url=reverse("risks:detail", args=[risk.pk]),
                   level=Level.CRITICAL if risk.priority == Risk.Priority.HIGH else Level.INFO)
    note = d.get("note", "").strip()
    if changes or note:
        who = request.user.get_full_name() or request.user.username
        risk.add_note(f"{who}: " + "; ".join(changes + ([note] if note else [])))
        risk.save()
        messages.success(request, "Risk updated.")
    return redirect("risks:detail", pk=pk)


@role_required(*RISK_MANAGERS)
def create(request):
    form = ManualRiskForm(request.POST or None, initial={"facility": request.GET.get("facility")})
    if request.method == "POST" and form.is_valid():
        risk = form.save(commit=False)
        risk.auto_detected = False
        risk.add_note(f"Raised manually by {request.user.get_full_name() or request.user.username}.")
        risk.save()
        messages.success(request, f"Risk recorded with score {risk.score} ({risk.get_priority_display()}).")
        return redirect("risks:detail", pk=risk.pk)
    return render(request, "risks/create.html", {"form": form})


@require_POST
@role_required(*RISK_VIEWERS)
def add_action(request, pk):
    risk = get_object_or_404(Risk, pk=pk)
    if not can_manage(request.user, risk):
        raise PermissionDenied
    form = MitigationActionForm(request.POST)
    if form.is_valid():
        action = form.save(commit=False)
        action.risk = risk
        action.save()
        if action.assigned_to and action.assigned_to != request.user:
            due = f" Due {action.due_date:%b %d}." if action.due_date else ""
            notify(action.assigned_to, f"New action assigned: {action.action[:80]}",
                   f"For {risk.get_category_display()} at {risk.facility.facility_code}.{due}",
                   url=reverse("risks:detail", args=[risk.pk]))
        if risk.status in (Risk.Status.OPEN, Risk.Status.IN_REVIEW):
            risk.status = Risk.Status.MITIGATING
            risk.add_note(f"Mitigation started: {action.action}")
            risk.save()
        messages.success(request, "Mitigation action added.")
    else:
        messages.error(request, "Please describe the action.")
    return redirect("risks:detail", pk=pk)


@require_POST
@role_required(*RISK_VIEWERS)
def action_status(request, pk):
    action = get_object_or_404(MitigationAction.objects.select_related("risk"), pk=pk)
    if not (can_manage(request.user, action.risk) or action.assigned_to_id == request.user.pk):
        raise PermissionDenied
    new = request.POST.get("status")
    if new in MitigationAction.Status.values:
        action.status = new
        action.completed_at = timezone.now() if new == MitigationAction.Status.DONE else None
        action.save()
        messages.success(request, f"Action marked {action.get_status_display().lower()}.")
    return redirect("risks:detail", pk=action.risk_id)


@require_POST
@role_required(*RISK_MANAGERS)
def run_detection(request):
    r = run()
    messages.success(request, f"Detection complete: {r.created} new, {r.updated} updated, "
                              f"{r.reopened} reopened, {r.closed} closed automatically, {r.reminders} reminders sent.")
    return redirect("risks:register")
