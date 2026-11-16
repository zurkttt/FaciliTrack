"""Automatic audit logging for tracked models and authentication events."""
from django.apps import apps
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_delete, post_save, pre_save

from .middleware import get_client_ip, get_current_ip, get_current_user
from .models import AuditEvent

# "app_label.ModelName" of every model whose changes must be audited.
TRACKED_MODELS = [
    "accounts.User",
    "accounts.Department",
    "facilities.Building",
    "facilities.FacilityType",
    "facilities.Facility",
    "facilities.MaintenanceRecord",
    "scheduling.Activity",
    "scheduling.Schedule",
    "scheduling.Reservation",
    "usage.UsageRecord",
    "analytics.MetricDefinition",
    "risks.Risk",
    "risks.MitigationAction",
    "core.SystemSetting",
]

# Never store these values in the audit log.
EXCLUDED_FIELDS = {
    "password", "last_login", "updated_at", "created_at", "calculated_at",
    "last_detected_at", "evidence",  # refreshed by every risk-detection run
}


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in EXCLUDED_FIELDS:
            continue
        value = field.value_from_object(instance)
        data[field.name] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return data


def _log(action, instance=None, changes=None, user=None, ip=None, username=""):
    user = user or get_current_user()
    AuditEvent.objects.create(
        user=user,
        username=username or (user.get_username() if user else ""),
        action=action,
        entity=instance._meta.label if instance is not None else "",
        entity_id=str(instance.pk) if instance is not None else "",
        entity_repr=str(instance)[:255] if instance is not None else "",
        changes=changes or {},
        ip_address=ip or get_current_ip(),
    )


def _pre_save(sender, instance, **kwargs):
    instance._audit_old = None
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_old = _snapshot(old)


def _post_save(sender, instance, created, **kwargs):
    new = _snapshot(instance)
    if created:
        _log(AuditEvent.Action.CREATE, instance, {"new": new})
        return
    old = getattr(instance, "_audit_old", None) or {}
    diff = {k: {"old": old.get(k), "new": v} for k, v in new.items() if old.get(k) != v}
    if diff:
        _log(AuditEvent.Action.UPDATE, instance, diff)


def _post_delete(sender, instance, **kwargs):
    _log(AuditEvent.Action.DELETE, instance, {"old": _snapshot(instance)})


def _on_login(sender, request, user, **kwargs):
    _log(AuditEvent.Action.LOGIN, user=user, ip=get_client_ip(request))


def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        _log(AuditEvent.Action.LOGOUT, user=user, ip=get_client_ip(request))


def _on_login_failed(sender, credentials, request=None, **kwargs):
    _log(
        AuditEvent.Action.LOGIN_FAILED,
        username=str(credentials.get("username", ""))[:150],
        ip=get_client_ip(request) if request else None,
    )


def connect():
    for label in TRACKED_MODELS:
        model = apps.get_model(label)
        uid = f"audit_{label}"
        pre_save.connect(_pre_save, sender=model, dispatch_uid=f"{uid}_pre")
        post_save.connect(_post_save, sender=model, dispatch_uid=f"{uid}_post")
        post_delete.connect(_post_delete, sender=model, dispatch_uid=f"{uid}_del")
    user_logged_in.connect(_on_login, dispatch_uid="audit_login")
    user_logged_out.connect(_on_logout, dispatch_uid="audit_logout")
    user_login_failed.connect(_on_login_failed, dispatch_uid="audit_login_failed")
