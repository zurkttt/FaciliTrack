from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

RATING_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]


def priority_for_score(score):
    """Risk score (1-25) -> priority band."""
    if score >= 15:
        return Risk.Priority.HIGH
    if score >= 8:
        return Risk.Priority.MEDIUM
    return Risk.Priority.LOW


class Risk(models.Model):
    """A facility-related operational risk. Scores are decision support, not safety certification."""

    class Category(models.TextChoices):
        OVERCROWDING = "OVERCROWDING", "Overcrowding"
        UNDERUTILIZATION = "UNDERUTILIZATION", "Underutilization"
        OVER_DEMAND = "OVER_DEMAND", "Over-demand / capacity gap"
        CONFLICT = "CONFLICT", "Scheduling conflict"
        UNAVAILABLE = "UNAVAILABLE", "Unavailable facility"
        DATA_QUALITY = "DATA_QUALITY", "Unreliable usage data"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_REVIEW = "IN_REVIEW", "In Review"
        MITIGATING = "MITIGATING", "Mitigating"
        ACCEPTED = "ACCEPTED", "Accepted (monitoring)"
        CLOSED = "CLOSED", "Closed"

    ACTIVE_STATUSES = (Status.OPEN, Status.IN_REVIEW, Status.MITIGATING)
    PRIORITY_RANK = {Priority.LOW: 1, Priority.MEDIUM: 2, Priority.HIGH: 3}

    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE, related_name="risks")
    category = models.CharField(max_length=20, choices=Category.choices)
    likelihood = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    impact = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    score = models.PositiveSmallIntegerField(editable=False)
    priority = models.CharField(max_length=10, choices=Priority.choices, editable=False)
    reason = models.TextField(help_text="Why this risk was flagged (must be explainable).")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_risks"
    )
    auto_detected = models.BooleanField(default=False)
    evidence = models.JSONField(default=dict, blank=True, help_text="Measurements behind the rating.")
    detected_at = models.DateTimeField(auto_now_add=True)
    last_detected_at = models.DateTimeField(null=True, blank=True, help_text="Last time the engine saw this condition.")
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True, help_text="How the risk was resolved, accepted or reopened.")

    class Meta:
        ordering = ["-score", "-detected_at"]

    def __str__(self):
        return f"[{self.priority}] {self.get_category_display()} - {self.facility.facility_code}"

    def save(self, *args, **kwargs):
        self.score = self.likelihood * self.impact
        self.priority = priority_for_score(self.score)
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    def add_note(self, text):
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        self.resolution = f"{self.resolution}\n[{stamp}] {text}".strip()


class MitigationAction(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        DONE = "DONE", "Done"

    risk = models.ForeignKey(Risk, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=255)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["status", "due_date"]

    def __str__(self):
        return self.action

    @property
    def is_overdue(self):
        return self.status != self.Status.DONE and self.due_date is not None and self.due_date < timezone.localdate()
