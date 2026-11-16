from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class UsageRecord(models.Model):
    """Recorded or imported evidence of actual facility use (vs. scheduled use)."""

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual entry"
        QR = "QR", "QR check-in"
        IMPORT = "IMPORT", "File import"

    class Status(models.TextChoices):
        USED = "USED", "Used"
        NO_SHOW = "NO_SHOW", "No-show"  # a booked session that was confirmed NOT to happen

    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE, related_name="usage_records")
    schedule = models.ForeignKey(
        "scheduling.Schedule", null=True, blank=True, on_delete=models.SET_NULL, related_name="usage_records"
    )
    reservation = models.ForeignKey(
        "scheduling.Reservation", null=True, blank=True, on_delete=models.SET_NULL, related_name="usage_records"
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    headcount = models.PositiveIntegerField(null=True, blank=True, help_text="Observed number of occupants, if counted.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.USED)
    source =models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_at"]

    def __str__(self):
        verb = "used" if self.status == self.Status.USED else "no-show"
        return f"{self.facility.facility_code} {verb} {self.start_at:%Y-%m-%d %H:%M}"

    @property
    def over_capacity(self):
        return self.headcount is not None and self.headcount > self.facility.capacity

    def clean(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "End must be later than start."})

    @property
    def duration_hours(self):
        return (self.end_at - self.start_at).total_seconds() / 3600
