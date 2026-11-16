from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Activity(models.Model):
    """Academic or institutional activity that uses a facility (course section, event, etc.)."""

    class Type(models.TextChoices):
        CLASS = "CLASS", "Class"
        EXAM = "EXAM", "Examination"
        EVENT = "EVENT", "Event"
        MEETING = "MEETING", "Meeting"
        OTHER = "OTHER", "Other"

    code = models.CharField(max_length=40, blank=True, help_text="e.g. course code and section, IT101-A")
    title = models.CharField(max_length=200)
    activity_type = models.CharField(max_length=20, choices=Type.choices, default=Type.CLASS)
    department = models.ForeignKey("accounts.Department", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["code", "title"]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.code} {self.title}".strip()


class Schedule(models.Model):
    """Recurring planned use of a facility (usually from the registrar's class schedule)."""

    class Day(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual entry"
        IMPORT = "IMPORT", "File import"

    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE, related_name="schedules")
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="schedules")
    day_of_week = models.IntegerField(choices=Day.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    term_start = models.DateField()
    term_end = models.DateField()
    expected_attendees = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["facility", "day_of_week", "start_time"]

    def __str__(self):
        return f"{self.activity} @ {self.facility.facility_code} {self.get_day_of_week_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "End time must be later than start time."})
        if self.term_start and self.term_end and self.term_end < self.term_start:
            raise ValidationError({"term_end": "Term end must not be before term start."})


class Reservation(models.Model):
    """Non-class booking or approved facility request."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE, related_name="reservations")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reservations")
    purpose = models.CharField(max_length=255)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    expected_attendees = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_reservations"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_at"]

    def __str__(self):
        return f"{self.facility.facility_code}: {self.purpose} ({self.get_status_display()})"

    def clean(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "End must be later than start."})
