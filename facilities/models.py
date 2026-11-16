import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Building(models.Model):
    """Physical structure containing facilities."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    floors = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class FacilityType(models.Model):
    """Classification: classroom, laboratory, library, lecture hall, etc."""

    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Facility(models.Model):
    """Canonical record for a usable campus space (business rule: unique facility identifier)."""

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        MAINTENANCE = "MAINTENANCE", "Under Maintenance"
        CLOSED = "CLOSED", "Closed"

    facility_code = models.CharField(max_length=30, unique=True, help_text="Unique institutional facility ID, e.g. ENG-101")
    name = models.CharField(max_length=150)
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="facilities")
    facility_type = models.ForeignKey(FacilityType, on_delete=models.PROTECT, related_name="facilities")
    floor = models.PositiveSmallIntegerField(default=1)
    capacity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    owner_department = models.ForeignKey(
        "accounts.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="facilities"
    )
    open_time = models.TimeField(default=datetime.time(7, 0))
    close_time = models.TimeField(default=datetime.time(21, 0))
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["facility_code"]
        verbose_name_plural = "facilities"

    def __str__(self):
        return f"{self.facility_code} - {self.name}"

    def clean(self):
        if self.open_time and self.close_time and self.close_time <= self.open_time:
            raise ValidationError({"close_time": "Closing time must be later than opening time."})
        if self.capacity is not None and self.capacity < 1:
            raise ValidationError({"capacity": "Capacity must be at least 1."})

    @property
    def operating_hours_per_day(self):
        open_dt = datetime.datetime.combine(datetime.date.min, self.open_time)
        close_dt = datetime.datetime.combine(datetime.date.min, self.close_time)
        return (close_dt - open_dt).total_seconds() / 3600


class MaintenanceRecord(models.Model):
    """Maintenance or closure affecting facility availability."""

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        ONGOING = "ONGOING", "Ongoing"
        COMPLETED = "COMPLETED", "Completed"

    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="maintenance_records")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True, help_text="Leave blank if the end date is unknown.")
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_at"]

    def __str__(self):
        return f"{self.facility.facility_code}: {self.reason}"

    def clean(self):
        if self.end_at and self.start_at and self.end_at <= self.start_at:
            raise ValidationError({"end_at": "End must be later than start."})
