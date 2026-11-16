import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.models import User
from accounts.permissions import RoleRequiredMixin, role_required
from risks.models import Risk
from risks.views import RISK_VIEWERS
from scheduling.models import Reservation, Schedule

from .forms import BuildingForm, FacilityForm, FacilityStatusForm, FacilityTypeForm, MaintenanceRecordForm
from .models import Building, Facility, FacilityType

# Roles allowed to change facility master data (Section 8.1: Facilities is the data owner).
EDIT_ROLES = (User.Role.ADMIN, User.Role.FACILITIES)


def can_edit(user):
    return user.is_authenticated and user.has_role(*EDIT_ROLES)


class EditRoleMixin(RoleRequiredMixin):
    allowed_roles = EDIT_ROLES


# ---------------------------------------------------------------- Facilities

class FacilityListView(LoginRequiredMixin, ListView):
    model = Facility
    template_name = "facilities/facility_list.html"
    context_object_name = "facilities"
    paginate_by = 20

    def get_queryset(self):
        qs = Facility.objects.select_related("building", "facility_type", "owner_department")
        p = self.request.GET
        if p.get("show") != "all":
            qs = qs.filter(is_active=True)
        if q := p.get("q", "").strip():
            qs = qs.filter(Q(facility_code__icontains=q) | Q(name__icontains=q))
        if p.get("building"):
            qs = qs.filter(building_id=p["building"])
        if p.get("type"):
            qs = qs.filter(facility_type_id=p["type"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("min_capacity", "").isdigit():
            qs = qs.filter(capacity__gte=int(p["min_capacity"]))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            buildings=Building.objects.filter(is_active=True),
            types=FacilityType.objects.all(),
            statuses=Facility.Status.choices,
            filters=self.request.GET,
            querystring=params.urlencode(),
            can_edit=can_edit(self.request.user),
        )
        return ctx


class FacilityDetailView(LoginRequiredMixin, DetailView):
    model = Facility
    template_name = "facilities/facility_detail.html"
    context_object_name = "facility"

    def get_queryset(self):
        return Facility.objects.select_related("building", "facility_type", "owner_department")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        facility = self.object
        today = timezone.localdate()

        schedules = (
            Schedule.objects.filter(facility=facility, term_start__lte=today, term_end__gte=today)
            .select_related("activity")
            .order_by("day_of_week", "start_time")
        )
        week = {value: {"label": label, "items": []} for value, label in Schedule.Day.choices}
        scheduled_hours = 0.0
        for s in schedules:
            week[s.day_of_week]["items"].append(s)
            start = datetime.datetime.combine(today, s.start_time)
            end = datetime.datetime.combine(today, s.end_time)
            scheduled_hours += (end - start).total_seconds() / 3600

        weekly_operating_hours = facility.operating_hours_per_day * 6  # Mon-Sat; refined in Step 7
        ctx.update(
            week=[day for value, day in week.items() if value < 6 or day["items"]],
            scheduled_hours=scheduled_hours,
            weekly_operating_hours=weekly_operating_hours,
            scheduled_load=(scheduled_hours / weekly_operating_hours * 100) if weekly_operating_hours else 0,
            over_capacity=[s for s in schedules if s.expected_attendees > facility.capacity],
            reservations=Reservation.objects.filter(
                facility=facility,
                end_at__gte=timezone.now(),
                status__in=[Reservation.Status.PENDING, Reservation.Status.APPROVED],
            ).select_related("requested_by")[:10],
            maintenance_records=facility.maintenance_records.all()[:10],
            open_risks=facility.risks.filter(status__in=Risk.ACTIVE_STATUSES).order_by("-score")
            if self.request.user.has_role(*RISK_VIEWERS) else None,
            can_edit=can_edit(self.request.user),
            status_form=FacilityStatusForm(instance=facility),
        )
        return ctx


class FacilityCreateView(EditRoleMixin, CreateView):
    model = Facility
    form_class = FacilityForm
    template_name = "facilities/form.html"
    extra_context = {"title": "Add facility"}

    def form_valid(self, form):
        messages.success(self.request, f"Facility {form.instance.facility_code} created.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("facilities:detail", args=[self.object.pk])


class FacilityUpdateView(EditRoleMixin, UpdateView):
    model = Facility
    form_class = FacilityForm
    template_name = "facilities/form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit {self.object.facility_code}"
        ctx["cancel_url"] = reverse("facilities:detail", args=[self.object.pk])
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"Facility {form.instance.facility_code} updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("facilities:detail", args=[self.object.pk])


@require_POST
@role_required(*EDIT_ROLES)
def facility_change_status(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    form = FacilityStatusForm(request.POST, instance=facility)
    if form.is_valid():
        form.save()
        messages.success(request, f"{facility.facility_code} is now {facility.get_status_display()}.")
    else:
        messages.error(request, "Invalid status.")
    return redirect("facilities:detail", pk=pk)


@require_POST
@role_required(*EDIT_ROLES)
def facility_toggle_active(request, pk):
    """Deactivate instead of delete, so history (schedules, usage, audit) is preserved."""
    facility = get_object_or_404(Facility, pk=pk)
    facility.is_active = not facility.is_active
    facility.save()
    state = "reactivated" if facility.is_active else "deactivated"
    messages.success(request, f"{facility.facility_code} {state}.")
    return redirect("facilities:detail", pk=pk)


@role_required(*EDIT_ROLES)
def maintenance_create(request, pk):
    facility = get_object_or_404(Facility, pk=pk)
    form = MaintenanceRecordForm(request.POST or None)
    form.instance.facility = facility
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.created_by = request.user
        record.save()
        if form.cleaned_data["mark_facility"] and facility.status != Facility.Status.MAINTENANCE:
            facility.status = Facility.Status.MAINTENANCE
            facility.save()
        messages.success(request, "Maintenance record added.")
        return redirect("facilities:detail", pk=pk)
    return render(
        request,
        "facilities/form.html",
        {
            "form": form,
            "title": f"Log maintenance: {facility.facility_code}",
            "cancel_url": reverse("facilities:detail", args=[pk]),
        },
    )


# ---------------------------------------------------------------- Buildings & types

class BuildingListView(EditRoleMixin, ListView):
    template_name = "facilities/building_list.html"
    context_object_name = "buildings"

    def get_queryset(self):
        return Building.objects.annotate(facility_count=Count("facilities", filter=Q(facilities__is_active=True)))


class BuildingCreateView(EditRoleMixin, CreateView):
    model = Building
    form_class = BuildingForm
    template_name = "facilities/form.html"
    success_url = reverse_lazy("facilities:building_list")
    extra_context = {"title": "Add building", "cancel_url": reverse_lazy("facilities:building_list")}


class BuildingUpdateView(EditRoleMixin, UpdateView):
    model = Building
    form_class = BuildingForm
    template_name = "facilities/form.html"
    success_url = reverse_lazy("facilities:building_list")
    extra_context = {"title": "Edit building", "cancel_url": reverse_lazy("facilities:building_list")}


class FacilityTypeListView(EditRoleMixin, ListView):
    template_name = "facilities/type_list.html"
    context_object_name = "types"

    def get_queryset(self):
        return FacilityType.objects.annotate(facility_count=Count("facilities", filter=Q(facilities__is_active=True)))


class FacilityTypeCreateView(EditRoleMixin, CreateView):
    model = FacilityType
    form_class = FacilityTypeForm
    template_name = "facilities/form.html"
    success_url = reverse_lazy("facilities:type_list")
    extra_context = {"title": "Add facility type", "cancel_url": reverse_lazy("facilities:type_list")}


class FacilityTypeUpdateView(EditRoleMixin, UpdateView):
    model = FacilityType
    form_class = FacilityTypeForm
    template_name = "facilities/form.html"
    success_url = reverse_lazy("facilities:type_list")
    extra_context = {"title": "Edit facility type", "cancel_url": reverse_lazy("facilities:type_list")}
