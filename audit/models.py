from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """Record of important system and data actions (who, what, when)."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
        EXPORT = "EXPORT", "Report export"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    username = models.CharField(max_length=150, blank=True, help_text="Kept even if the user is deleted.")
    action = models.CharField(max_length=20, choices=Action.choices)
    entity = models.CharField(max_length=100, blank=True)
    entity_id = models.CharField(max_length=50, blank=True)
    entity_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.username or 'system'} {self.action} {self.entity} {self.entity_id}"
