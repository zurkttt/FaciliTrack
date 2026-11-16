import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView, UpdateView

from accounts.models import User
from accounts.permissions import RoleRequiredMixin, role_required
from facilities.models import Building, Facility, MaintenanceRecord
from notifications.services import Level, notify, notify_roles

from .conflicts import check_reservation, has_errors
from .forms import (
    ActivityForm, AvailabilityForm, ReservationForm, ReviewForm, ScheduleForm, ScheduleImportForm,
)
from .importer import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, import_schedules, template_csv
from .models import Reservation, Schedule

# Registrar owns class schedules; the Facility Office approves reservations (Section 8.1).
SCHEDULE_EDITORS = (User.Role.ADMIN, User.Role.REGISTRAR)
RESERVATION_APPROVERS = (User.Role.ADMIN, User.Role.FACILITIES)


def is_approver(user):
    return user.has_role(*RESERVATION_APPROVERS)


# ================================================================ schedules

class ScheduleListView(LoginRequiredMixin, ListView):
    template_name = "scheduling/schedule_list.html"
    context_object_name = "schedules"
    paginate_by = 25

    def get_queryset(self):
        qs = Schedule.objects.select_related("facility__building", "activity")
        p = self.request.GET
        if p.get("term") != "all":
            today = timezone.localdate()
            qs = qs.filter(term_start__lte=today, term_end__gte=today)
        if q := p.get("q", "").strip():
            qs = qs.filter(Q(activity__code__icontains=q) | Q(activity__title__icontains=q))
        if p.get("facility"):
            qs = qs.filter(facility_id=p["facility"])
        if p.get("building"):
            qs = qs.filter(facility__building_id=p["building"])
        if p.get("day", "").isdigit():
            qs = qs.filter(day_of_week=int(p["day"]))
        return qs.order_by("day_of_week", "start_time", "facility__facility_code")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            filters=self.request.GET,
            querystring=params.urlencode(),
            facilities=Facility.objects.filter(is_active=True),
            buildings=Building.objects.filter(is_active=True),
            days=Schedule.Day.choices,
            can_edit=self.request.user.has_role(*SCHEDULE_EDITORS),
        )
        return ctx


class ScheduleEditorMixin(RoleRequiredMixin):
    allowed_roles = SCHEDULE_EDITORS
    model = Schedule
    form_class = ScheduleForm
    template_name = "scheduling/conflict_form.html"
    success_url = reverse_lazy("scheduling:schedule_list")


class ScheduleCreateView(ScheduleEditorMixin, CreateView):
    extra_context = {
        "title": "Add class schedule",
        "cancel_url": reverse_lazy("scheduling:schedule_list"),
        "extra_link": {"url": reverse_lazy("scheduling:activity_create"), "text": "Course/activity not listed? Add it first."},
    }

    def get_initial(self):
        return {k: self.request.GET[k] for k in ("facility", "activity") if k in self.request.GET}

    def form_valid(self, form):
        form.instance.source = Schedule.Source.MANUAL
        messages.success(self.request, "Schedule added.")
        return super().form_valid(form)


class ScheduleUpdateView(ScheduleEditorMixin, UpdateView):
    extra_context = {"title": "Edit class schedule", "cancel_url": reverse_lazy("scheduling:schedule_list")}

    def form_valid(self, form):
        messages.success(self.request, "Schedule updated.")
        return super().form_valid(form)


@require_POST
@role_required(*SCHEDULE_EDITORS)
def schedule_delete(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk)
    schedule.delete()
    messages.success(request, f"Deleted schedule {schedule.activity} ({schedule.get_day_of_week_display()}).")
    return redirect("scheduling:schedule_list")


class ActivityCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = SCHEDULE_EDITORS
    form_class = ActivityForm
    template_name = "facilities/form.html"
    extra_context = {"title": "Add activity / course section", "cancel_url": reverse_lazy("scheduling:schedule_create")}

    def get_success_url(self):
        messages.success(self.request, f"Activity {self.object} added.")
        return f"{reverse('scheduling:schedule_create')}?activity={self.object.pk}"


@role_required(*SCHEDULE_EDITORS)
def schedule_import(request):
    result = None
    form = ScheduleImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        result = import_schedules(form.cleaned_data["file"])
    return render(request, "scheduling/schedule_import.html", {
        "form": form, "result": result, "required_columns": REQUIRED_COLUMNS, "optional_columns": OPTIONAL_COLUMNS,
    })


@role_required(*SCHEDULE_EDITORS)
def schedule_import_template(request):
    response = HttpResponse(template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="schedule_import_template.csv"'
    return response


# ================================================================ reservations

class ReservationListView(LoginRequiredMixin, ListView):
    template_name = "scheduling/reservation_list.html"
    context_object_name = "reservations"
    paginate_by = 25

    def get_queryset(self):
        qs = Reservation.objects.select_related("facility", "requested_by")
        user, p = self.request.user, self.request.GET
        if not is_approver(user) or p.get("scope") == "mine":
            qs = qs.filter(requested_by=user)
        status = p.get("status", "PENDING" if is_approver(user) and p.get("scope") != "mine" else "")
        if status:
            qs = qs.filter(status=status)
        if p.get("when") != "past":
            qs = qs.filter(end_at__gte=timezone.now()).order_by("start_at")
        else:
            qs = qs.filter(end_at__lt=timezone.now()).order_by("-start_at")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user, p = self.request.user, self.request.GET
        params = p.copy()
        params.pop("page", None)
        ctx.update(
            is_approver=is_approver(user),
            scope=p.get("scope", "all" if is_approver(user) else "mine"),
            selected_status=p.get("status", "PENDING" if is_approver(user) and p.get("scope") != "mine" else ""),
            when=p.get("when", "upcoming"),
            statuses=Reservation.Status.choices,
            querystring=params.urlencode(),
        )
        return ctx


@login_required
def reservation_create(request):
    initial = {}
    for key in ("facility", "date", "start_time", "end_time", "expected_attendees"):
        if request.GET.get(key):
            initial[key] = request.GET[key]
    form = ReservationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        reservation = form.save(commit=False)
        reservation.requested_by = request.user
        reservation.status = Reservation.Status.PENDING
        reservation.save()
        when = f"{timezone.localtime(reservation.start_at):%b %d, %H:%M}"
        notify_roles(
            RESERVATION_APPROVERS, f"New reservation request: {reservation.facility.facility_code}",
            f"{request.user.get_full_name() or request.user.username} requested {reservation.facility.facility_code} "
            f"on {when} for “{reservation.purpose}” ({reservation.expected_attendees} people).",
            url=reverse("scheduling:reservation_detail", args=[reservation.pk]), exclude=request.user,
        )
        messages.success(request, "Reservation request submitted for approval.")
        return redirect("scheduling:reservation_detail", pk=reservation.pk)
    return render(request, "scheduling/conflict_form.html", {
        "form": form, "title": "Request a reservation", "cancel_url": reverse("scheduling:reservation_list"),
    })


def _get_visible_reservation(request, pk):
    reservation = get_object_or_404(Reservation.objects.select_related("facility", "requested_by", "reviewed_by"), pk=pk)
    if reservation.requested_by_id != request.user.pk and not is_approver(request.user):
        raise PermissionDenied
    return reservation


@login_required
def reservation_detail(request, pk):
    reservation = _get_visible_reservation(request, pk)
    conflicts = []
    if reservation.status == Reservation.Status.PENDING:
        conflicts = check_reservation(
            reservation.facility, reservation.start_at, reservation.end_at,
            reservation.expected_attendees, exclude_pk=reservation.pk,
        )
    can_cancel = reservation.status in (Reservation.Status.PENDING, Reservation.Status.APPROVED) and (
        reservation.requested_by_id == request.user.pk or is_approver(request.user)
    ) and reservation.end_at > timezone.now()
    return render(request, "scheduling/reservation_detail.html", {
        "reservation": reservation,
        "conflicts": conflicts,
        "blocking": has_errors(conflicts),
        "can_review": is_approver(request.user) and reservation.status == Reservation.Status.PENDING,
        "can_cancel": can_cancel,
        "review_form": ReviewForm(),
    })


def _review(request, pk, new_status):
    reservation = get_object_or_404(Reservation, pk=pk)
    if reservation.status != Reservation.Status.PENDING:
        messages.error(request, "Only pending reservations can be reviewed.")
        return redirect("scheduling:reservation_detail", pk=pk)
    form = ReviewForm(request.POST)
    form.is_valid()
    if new_status == Reservation.Status.APPROVED:
        # Re-check at approval time: the situation may have changed since the request was made.
        conflicts = check_reservation(
            reservation.facility, reservation.start_at, reservation.end_at,
            reservation.expected_attendees, exclude_pk=reservation.pk,
        )
        if has_errors(conflicts):
            messages.error(request, "Cannot approve: " + " ".join(c.message for c in conflicts if c.is_error))
            return redirect("scheduling:reservation_detail", pk=pk)
    reservation.status = new_status
    reservation.reviewed_by = request.user
    reservation.reviewed_at = timezone.now()
    reservation.remarks = form.cleaned_data.get("remarks", "")
    reservation.save()
    if reservation.requested_by_id != request.user.pk:
        approved = new_status == Reservation.Status.APPROVED
        notify(
            reservation.requested_by,
            f"Reservation {'approved' if approved else 'rejected'}: {reservation.facility.facility_code}",
            f"“{reservation.purpose}” on {timezone.localtime(reservation.start_at):%b %d, %H:%M}"
            + (f". Remarks: {reservation.remarks}" if reservation.remarks else "."),
            url=reverse("scheduling:reservation_detail", args=[reservation.pk]),
            level=Level.SUCCESS if approved else Level.WARNING,
        )
    messages.success(request, f"Reservation {reservation.get_status_display().lower()}.")
    return redirect("scheduling:reservation_detail", pk=pk)


@require_POST
@role_required(*RESERVATION_APPROVERS)
def reservation_approve(request, pk):
    return _review(request, pk, Reservation.Status.APPROVED)


@require_POST
@role_required(*RESERVATION_APPROVERS)
def reservation_reject(request, pk):
    return _review(request, pk, Reservation.Status.REJECTED)


@require_POST
@login_required
def reservation_cancel(request, pk):
    reservation = _get_visible_reservation(request, pk)
    if reservation.status not in (Reservation.Status.PENDING, Reservation.Status.APPROVED) or reservation.end_at <= timezone.now():
        messages.error(request, "This reservation can no longer be cancelled.")
    else:
        reservation.status = Reservation.Status.CANCELLED
        reservation.save()
        if reservation.requested_by_id != request.user.pk:
            notify(reservation.requested_by, f"Reservation cancelled: {reservation.facility.facility_code}",
                   f"“{reservation.purpose}” on {timezone.localtime(reservation.start_at):%b %d, %H:%M} was cancelled "
                   f"by {request.user.get_full_name() or request.user.username}.",
                   url=reverse("scheduling:reservation_detail", args=[reservation.pk]), level=Level.WARNING)
        messages.success(request, "Reservation cancelled.")
    return redirect("scheduling:reservation_detail", pk=pk)


# ================================================================ availability & calendar

@login_required
def availability(request):
    form = AvailabilityForm(request.GET or None)
    results = None
    if request.GET and form.is_valid():
        d = form.cleaned_data
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(datetime.datetime.combine(d["date"], d["start_time"]), tz)
        end_at = timezone.make_aware(datetime.datetime.combine(d["date"], d["end_time"]), tz)
        facilities = Facility.objects.filter(is_active=True).select_related("building", "facility_type")
        if d.get("building"):
            facilities = facilities.filter(building=d["building"])
        if d.get("facility_type"):
            facilities = facilities.filter(facility_type=d["facility_type"])
        if d.get("attendees"):
            facilities = facilities.filter(capacity__gte=d["attendees"])
        results = []
        for facility in facilities.order_by("capacity", "facility_code"):  # best fit first
            conflicts = check_reservation(facility, start_at, end_at, d.get("attendees") or 0)
            if not has_errors(conflicts):
                results.append({"facility": facility, "warnings": conflicts})
    return render(request, "scheduling/availability.html", {"form": form, "results": results})


@login_required
def facility_calendar(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    try:
        anchor = datetime.date.fromisoformat(request.GET.get("week", ""))
    except ValueError:
        anchor = timezone.localdate()
    monday = anchor - datetime.timedelta(days=anchor.weekday())
    sunday = monday + datetime.timedelta(days=6)
    tz = timezone.get_current_timezone()
    week_start = timezone.make_aware(datetime.datetime.combine(monday, datetime.time.min), tz)
    week_end = timezone.make_aware(datetime.datetime.combine(sunday, datetime.time.max), tz)

    schedules = list(Schedule.objects.filter(facility=facility, term_start__lte=sunday, term_end__gte=monday).select_related("activity"))
    reservations = list(Reservation.objects.filter(
        facility=facility, start_at__lt=week_end, end_at__gt=week_start,
        status__in=[Reservation.Status.APPROVED, Reservation.Status.PENDING],
    ))
    maintenance = [
        m for m in facility.maintenance_records.exclude(status=MaintenanceRecord.Status.COMPLETED).filter(start_at__lt=week_end)
        if m.end_at is None or m.end_at > week_start
    ]

    days = []
    for offset in range(7):
        day = monday + datetime.timedelta(days=offset)
        events = []
        for s in schedules:
            if s.day_of_week == day.weekday() and s.term_start <= day <= s.term_end:
                events.append({"start": s.start_time, "end": s.end_time, "label": str(s.activity), "kind": "class",
                               "over": s.expected_attendees > facility.capacity})
        for r in reservations:
            rs, re_ = timezone.localtime(r.start_at), timezone.localtime(r.end_at)
            if rs.date() == day:
                events.append({"start": rs.time(), "end": re_.time(), "label": r.purpose,
                               "kind": "approved" if r.status == Reservation.Status.APPROVED else "pending",
                               "url": reverse("scheduling:reservation_detail", args=[r.pk])
                               if r.requested_by_id == request.user.pk or is_approver(request.user) else ""})
        for m in maintenance:
            ms = timezone.localtime(m.start_at).date()
            me = timezone.localtime(m.end_at).date() if m.end_at else day
            if ms <= day <= me:
                events.append({"start": datetime.time.min, "end": None, "label": f"Maintenance: {m.reason}", "kind": "maintenance"})
        events.sort(key=lambda e: e["start"])
        days.append({"date": day, "events": events, "is_today": day == timezone.localdate()})

    return render(request, "scheduling/calendar.html", {
        "facility": facility, "days": days, "monday": monday, "sunday": sunday,
        "prev_week": monday - datetime.timedelta(days=7), "next_week": monday + datetime.timedelta(days=7),
    })
