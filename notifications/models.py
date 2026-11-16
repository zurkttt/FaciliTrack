from django.conf import settings
from django.db import models


class Notification(models.Model):
    """An in-app alert for one user (optionally also emailed)."""

    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        SUCCESS = "SUCCESS", "Success"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True, help_text="Site-relative link, e.g. /risks/12/")
    dedupe_key = models.CharField(
        max_length=120, blank=True, help_text="If set, the same user never receives two alerts with this key."
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"], condition=~models.Q(dedupe_key=""), name="unique_notification_key",
            )
        ]

    def __str__(self):
        return f"{self.user}: {self.title}"

    @property
    def is_read(self):
        return self.read_at is not None
