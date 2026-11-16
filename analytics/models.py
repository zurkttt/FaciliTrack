from django.core.exceptions import ValidationError
from django.db import models, transaction

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class MetricDefinition(models.Model):
    """Governed definition of the utilization metric (Section 7.3).

    Each calculated metric points to the definition version it used,
    so historical reports stay reproducible when the definition changes.
    Definitions are never edited after creation: create a new version instead.
    """

    version = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150, default="Utilization Rate")
    formula = models.TextField(default="Used Facility Hours / Available Facility Hours x 100")
    operating_days = models.CharField(
        max_length=20, default="0,1,2,3,4,5",
        help_text="Weekdays that count as available time (0=Mon ... 6=Sun). Default: Monday to Saturday.",
    )
    count_reservations_as_use = models.BooleanField(
        default=False, help_text="If true, approved reservations count as use even without a usage record."
    )
    exclude_maintenance = models.BooleanField(
        default=True, help_text="Subtract maintenance/closure periods from available hours."
    )
    is_active = models.BooleanField(default=False)
    approved_by = models.CharField(max_length=150, blank=True, help_text="e.g. Data Governance Council, resolution no.")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} v{self.version}{' (active)' if self.is_active else ''}"

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).first()

    @property
    def operating_weekdays(self):
        return {int(d) for d in self.operating_days.split(",") if d.strip().isdigit()}

    @property
    def operating_days_display(self):
        return ", ".join(WEEKDAY_NAMES[d] for d in sorted(self.operating_weekdays))

    def clean(self):
        days = [d.strip() for d in self.operating_days.split(",") if d.strip()]
        if not days or any(not d.isdigit() or int(d) > 6 for d in days):
            raise ValidationError({"operating_days": "Use weekday numbers 0-6 separated by commas."})

    @transaction.atomic
    def activate(self):
        MetricDefinition.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)
        self.is_active = True
        self.save()


class UtilizationMetric(models.Model):
    """Calculated utilization for one facility on one day. Derived, never entered manually.

    Longer periods and groupings are computed by SUMMING hours across daily rows,
    then dividing (never by averaging percentages).
    """

    facility = models.ForeignKey("facilities.Facility", on_delete=models.CASCADE, related_name="utilization_metrics")
    definition = models.ForeignKey(MetricDefinition, on_delete=models.PROTECT, related_name="metrics")
    period_start = models.DateField(db_index=True)
    period_end = models.DateField()
    available_hours = models.DecimalField(max_digits=10, decimal_places=2)
    used_hours = models.DecimalField(max_digits=10, decimal_places=2)
    scheduled_hours = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Hours booked by classes and approved reservations."
    )
    utilization_rate = models.DecimalField(max_digits=6, decimal_places=2, help_text="Percent, 0-100")
    occupancy_sum = models.DecimalField(
        max_digits=12, decimal_places=4, default=0, help_text="Sum of headcount/capacity over records with a headcount."
    )
    occupancy_count = models.PositiveIntegerField(default=0)
    calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "facility"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "definition", "period_start", "period_end"], name="unique_metric_per_period"
            )
        ]

    def __str__(self):
        return f"{self.facility.facility_code} {self.period_start}..{self.period_end}: {self.utilization_rate}%"
