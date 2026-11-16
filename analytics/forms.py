import datetime

from django import forms
from django.utils import timezone

from accounts.models import Department
from core.forms import BootstrapFormMixin
from facilities.models import Building, Facility, FacilityType
from scheduling.forms import DateInput

from .models import WEEKDAY_NAMES, MetricDefinition
from .queries import GROUPINGS

MAX_RANGE_DAYS = 366


def default_range():
    end = timezone.localdate() - datetime.timedelta(days=1)  # last complete day
    return end - datetime.timedelta(days=27), end


class AnalyticsFilterForm(BootstrapFormMixin, forms.Form):
    start = forms.DateField(widget=DateInput(), label="From")
    end = forms.DateField(widget=DateInput(), label="To")
    group = forms.ChoiceField(choices=[(k, v[2]) for k, v in GROUPINGS.items()], initial="facility", label="Group by")
    period = forms.ChoiceField(choices=[("day", "Daily"), ("week", "Weekly"), ("month", "Monthly")], initial="week", label="Trend")
    building = forms.ModelChoiceField(Building.objects.filter(is_active=True), required=False, empty_label="All buildings")
    facility_type = forms.ModelChoiceField(FacilityType.objects.all(), required=False, empty_label="All types", label="Type")
    department = forms.ModelChoiceField(Department.objects.all(), required=False, empty_label="All departments")
    facility = forms.ModelChoiceField(Facility.objects.filter(is_active=True), required=False, empty_label="All facilities")

    def __init__(self, data=None, **kwargs):
        start, end = default_range()
        defaults = {"start": start.isoformat(), "end": end.isoformat(), "group": "facility", "period": "week"}
        merged = {**defaults, **{k: v for k, v in (data or {}).items() if v not in ("", None)}}
        super().__init__(merged, **kwargs)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end:
            if end < start:
                self.add_error("end", "'To' must not be before 'From'.")
            elif (end - start).days >= MAX_RANGE_DAYS:
                self.add_error("end", f"Please choose at most {MAX_RANGE_DAYS} days.")
        return cleaned


class MetricDefinitionForm(BootstrapFormMixin, forms.ModelForm):
    operating_days = forms.TypedMultipleChoiceField(
        choices=list(enumerate(WEEKDAY_NAMES)), coerce=int, widget=forms.CheckboxSelectMultiple,
        initial=[0, 1, 2, 3, 4, 5], help_text="Days counted as available time.",
    )

    class Meta:
        model = MetricDefinition
        fields = ["version", "name", "formula", "operating_days", "count_reservations_as_use", "exclude_maintenance", "approved_by", "notes"]
        widgets = {"formula": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # CheckboxSelectMultiple renders many inputs; Bootstrap class goes on each one.
        self.fields["operating_days"].widget.attrs["class"] = "form-check-input"

    def clean_operating_days(self):
        days = sorted(set(self.cleaned_data["operating_days"]))
        if not days:
            raise forms.ValidationError("Choose at least one day.")
        return ",".join(str(d) for d in days)


class RecalculateForm(forms.Form):
    start = forms.DateField()
    end = forms.DateField()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and (end < start or (end - start).days >= MAX_RANGE_DAYS):
            raise forms.ValidationError(f"Choose a valid range of at most {MAX_RANGE_DAYS} days.")
        return cleaned
