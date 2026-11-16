"""In-app administration console (Section 9.1 'Administration Console')."""
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts.models import Department, User
from accounts.permissions import role_required
from analytics.models import MetricDefinition, UtilizationMetric
from analytics.views import STALE_AFTER
from audit.middleware import get_client_ip
from audit.models import AuditEvent
from core.models import SystemSetting
from facilities.models import Facility
from notifications.models import Notification
from risks.models import Risk
from scheduling.models import Reservation, Schedule
from usage.models import UsageRecord

from .forms import SETTING_DEFAULTS, SETTING_SPECS, DepartmentForm, SettingsForm, UserForm, temporary_password

ADMINS = (User.Role.ADMIN,)
SETTINGS_EDITORS = (User.Role.ADMIN, User.Role.PLANNER)  # Planning owns the analytic definitions (RACI 12.4)


def _admin_count(exclude_pk=None):
    qs = User.objects.filter(is_active=True).filter(Q(role=User.Role.ADMIN) | Q(is_superuser=True))
    return qs.exclude(pk=exclude_pk).count()


def _check_editable(request, target):
    if target.is_superuser and not request.user.is_superuser:
        raise PermissionDenied("Only a superuser can change a superuser account.")


def _credentials_page(request, target, password, created):
    response = render(request, "console/credentials.html", {"target": target, "password": password, "created": created})
    response["Cache-Control"] = "no-store"  # never cache a page showing a password
    return response


# ================================================================ users

@role_required(*ADMINS)
def user_list(request):
    p = request.GET
    qs = User.objects.select_related("department").order_by("username")
    if q := p.get("q", "").strip():
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    if p.get("role") in User.Role.values:
        qs = qs.filter(role=p["role"])
    if p.get("status") == "inactive":
        qs = qs.filter(is_active=False)
    elif p.get("status") != "all":
        qs = qs.filter(is_active=True)
    page = Paginator(qs, 25).get_page(p.get("page"))
    params = p.copy()
    params.pop("page", None)
    return render(request, "console/user_list.html", {
        "page_obj": page, "users": page.object_list, "filters": p, "roles": User.Role.choices, "querystring": params.urlencode(),
    })


@never_cache
@role_required(*ADMINS)
def user_create(request):
    form = UserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        password = temporary_password()
        user.set_password(password)
        user.must_change_password = True
        user.save()
        return _credentials_page(request, user, password, created=True)
    return render(request, "console/form.html", {"form": form, "title": "Add user"})


@role_required(*ADMINS)
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    _check_editable(request, target)
    original_role = target.role  # form validation copies the new values onto `target`, so read it first
    form = UserForm(request.POST or None, instance=target)
    if request.method == "POST" and form.is_valid():
        losing_admin = original_role == User.Role.ADMIN and form.cleaned_data["role"] != User.Role.ADMIN
        if losing_admin and not target.is_superuser and _admin_count(exclude_pk=target.pk) == 0:
            form.add_error("role", "This is the last active administrator. Make someone else an administrator first.")
        else:
            form.save()
            messages.success(request, f"Saved {target.username}.")
            return redirect("console:user_list")
    return render(request, "console/form.html", {"form": form, "title": f"Edit {target.username}", "target": target})


@never_cache
@require_POST
@role_required(*ADMINS)
def user_reset_password(request, pk):
    target = get_object_or_404(User, pk=pk)
    _check_editable(request, target)
    password = temporary_password()
    target.set_password(password)
    target.must_change_password = True
    target.save()
    AuditEvent.objects.create(
        user=request.user, username=request.user.get_username(), action=AuditEvent.Action.UPDATE,
        entity="accounts.User", entity_id=str(target.pk), entity_repr=str(target),
        changes={"password": "temporary password issued by administrator"}, ip_address=get_client_ip(request),
    )
    return _credentials_page(request, target, password, created=False)


@require_POST
@role_required(*ADMINS)
def user_toggle_active(request, pk):
    target = get_object_or_404(User, pk=pk)
    _check_editable(request, target)
    if target == request.user:
        messages.error(request, "You cannot deactivate your own account.")
    elif target.is_active and target.has_role(User.Role.ADMIN) and _admin_count(exclude_pk=target.pk) == 0:
        messages.error(request, "This is the last active administrator and cannot be deactivated.")
    else:
        target.is_active = not target.is_active
        target.save()
        messages.success(request, f"{target.username} {'reactivated' if target.is_active else 'deactivated'}.")
    return redirect("console:user_list")


# ================================================================ departments

@role_required(*ADMINS)
def departments(request, pk=None):
    instance = get_object_or_404(Department, pk=pk) if pk else None
    form = DepartmentForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        dept = form.save()
        messages.success(request, f"Department {dept.code} saved.")
        return redirect("console:departments")
    return render(request, "console/departments.html", {
        "form": form, "editing": instance,
        "departments": Department.objects.annotate(users_count=Count("users", distinct=True),
                                                   facility_count=Count("facilities", distinct=True)).order_by("code"),
    })


# ================================================================ settings

@role_required(*SETTINGS_EDITORS)
def system_settings(request):
    current = {key: SystemSetting.get(key, SETTING_DEFAULTS[key], kind) for key, _, kind, *_ in SETTING_SPECS}
    form = SettingsForm(request.POST or None, current=current)
    if request.method == "POST" and form.is_valid():
        changed = {}
        for key, label, kind, *_ in SETTING_SPECS:
            new = form.cleaned_data[key]
            if float(new) != float(current[key]):
                value = str(int(new)) if kind is int else f"{new:g}"
                SystemSetting.objects.update_or_create(key=key, defaults={"value": value, "description": label})
                changed[key] = {"old": current[key], "new": new}
        if changed:
            AuditEvent.objects.create(
                user=request.user, username=request.user.get_username(), action=AuditEvent.Action.UPDATE,
                entity="core.SystemSetting", entity_repr="Risk and utilization thresholds",
                changes={"reason": form.cleaned_data["reason"], **changed}, ip_address=get_client_ip(request),
            )
            messages.success(request, f"Updated {len(changed)} setting(s). Run risk detection to apply them to the register.")
        else:
            messages.info(request, "No values changed.")
        return redirect("console:settings")
    return render(request, "console/settings.html", {
        "form": form,
        "history": AuditEvent.objects.filter(entity="core.SystemSetting", changes__has_key="reason")[:10],
    })


# ================================================================ system status

@role_required(*ADMINS)
def status(request):
    now = timezone.now()
    definition = MetricDefinition.active()
    calc = UtilizationMetric.objects.filter(definition=definition).aggregate(m=Max("calculated_at"))["m"] if definition else None
    detection = Risk.objects.filter(auto_detected=True).aggregate(m=Max("last_detected_at"))["m"]
    executor = MigrationExecutor(connection)
    pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    default_key = settings.SECRET_KEY.startswith("dev-only")

    checks = [
        ("Database migrations", not pending, f"{len(pending)} pending: run python manage.py migrate" if pending else "Up to date"),
        ("Active metric definition", definition is not None, f"v{definition.version}" if definition else "None: analytics cannot run"),
        ("Utilization calculation", calc is not None and now - calc <= STALE_AFTER,
         f"Last run {timezone.localtime(calc):%b %d %H:%M}" if calc else "Never run"),
        ("Risk detection", detection is not None and now - detection <= STALE_AFTER,
         f"Last run {timezone.localtime(detection):%b %d %H:%M}" if detection else "Never run"),
        ("Active administrators", _admin_count() >= 2, f"{_admin_count()} (at least 2 recommended so one can't lock everyone out)"),
        ("Secret key", not default_key, "Set from .env" if not default_key else "Using the development default: set SECRET_KEY in .env"),
        ("Debug mode", not settings.DEBUG, "Off" if not settings.DEBUG else "On: fine for development, must be off in production"),
    ]
    info = [
        ("Database", connection.vendor),
        ("Email", "printed to console (development)" if "console" in settings.EMAIL_BACKEND else settings.EMAIL_BACKEND.rsplit(".", 1)[-1]),
        ("Email alerts", "on" if settings.NOTIFY_BY_EMAIL else "off (in-app only)"),
        ("Time zone", settings.TIME_ZONE),
    ]
    counts = [
        ("Users (active)", User.objects.filter(is_active=True).count()), ("Facilities (active)", Facility.objects.filter(is_active=True).count()),
        ("Class schedules", Schedule.objects.count()), ("Reservations", Reservation.objects.count()),
        ("Usage records", UsageRecord.objects.count()), ("Utilization rows", UtilizationMetric.objects.count()),
        ("Active risks", Risk.objects.filter(status__in=Risk.ACTIVE_STATUSES).count()),
        ("Unread alerts", Notification.objects.filter(read_at__isnull=True).count()),
        ("Audit events", AuditEvent.objects.count()),
    ]
    return render(request, "console/status.html", {"checks": checks, "info": info, "counts": counts})
