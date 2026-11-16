import datetime

from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin, TimeInput
from facilities.models import Building, Facility, FacilityType

from .conflicts import check_reservation, check_schedule
from .models import Activity, Reservation, Schedule

ACK_LABEL = "I have reviewed the warnings above and want to save anyway"


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class ConflictCheckMixin:
    """Runs a conflict check in clean(): errors block; warnings need the acknowledge box ticked."""

    def _apply_conflicts(self, conflicts):
        self.conflicts = conflicts
        errors = [c for c in conflicts if c.is_error]
        warnings = [c for c in conflicts if not c.is_error]
        for c in errors:
            self.add_error(None, c.message)
        if warnings and not errors and not self.cleaned_data.get("acknowledge_warnings"):
            self.add_error("acknowledge_warnings", "Please review the warnings and tick this box to continue.")

    @property
    def warnings(self):
        return [c for c in getattr(self, "conflicts", []) if not c.is_error]


class ActivityForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["code", "title", "activity_type", "department"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class ScheduleForm(ConflictCheckMixin, BootstrapFormMixin, forms.ModelForm):
    acknowledge_warnings = forms.BooleanField(required=False, label=ACK_LABEL)

    class Meta:
        model = Schedule
        fields = ["facility", "activity", "day_of_week", "start_time", "end_time", "term_start", "term_end", "expected_attendees"]
        widgets = {
            "start_time": TimeInput(), "end_time": TimeInput(),
            "term_start": DateInput(), "term_end": DateInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facility"].queryset = Facility.objects.filter(is_active=True).select_related("building")

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if cleaned["end_time"] <= cleaned["start_time"]:
            self.add_error("end_time", "End time must be later than start time.")
            return cleaned
        if cleaned["term_end"] < cleaned["term_start"]:
            self.add_error("term_end", "Term end must not be before term start.")
            return cleaned
        self._apply_conflicts(check_schedule(
            cleaned["facility"], cleaned["day_of_week"], cleaned["start_time"], cleaned["end_time"],
            cleaned["term_start"], cleaned["term_end"], cleaned.get("expected_attendees") or 0,
            exclude_pk=self.instance.pk,
        ))
        return cleaned


class ReservationForm(ConflictCheckMixin, BootstrapFormMixin, forms.ModelForm):
    """One-day booking. Multi-day events are entered as one reservation per day."""

    date = forms.DateField(widget=DateInput())
    start_time = forms.TimeField(widget=TimeInput())
    end_time = forms.TimeField(widget=TimeInput())
    acknowledge_warnings = forms.BooleanField(required=False, label=ACK_LABEL)

    field_order = ["facility", "purpose", "date", "start_time", "end_time", "expected_attendees"]

    class Meta:
        model = Reservation
        fields = ["facility", "purpose", "expected_attendees"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facility"].queryset = Facility.objects.filter(is_active=True).select_related("building")

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(datetime.datetime.combine(cleaned["date"], cleaned["start_time"]), tz)
        end_at = timezone.make_aware(datetime.datetime.combine(cleaned["date"], cleaned["end_time"]), tz)
        if end_at <= start_at:
            self.add_error("end_time", "End time must be later than start time.")
            return cleaned
        if start_at < timezone.now():
            self.add_error("date", "Reservations cannot start in the past.")
            return cleaned
        self.instance.start_at, self.instance.end_at = start_at, end_at
        self._apply_conflicts(check_reservation(
            cleaned["facility"], start_at, end_at, cleaned.get("expected_attendees") or 0, exclude_pk=self.instance.pk,
        ))
        return cleaned


class ReviewForm(BootstrapFormMixin, forms.Form):
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=1000)


class ScheduleImportForm(BootstrapFormMixin, forms.Form):
    MAX_BYTES = 2 * 1024 * 1024

    file = forms.FileField(label="CSV file", help_text="UTF-8 CSV, max 2 MB. Download the template for the expected columns.")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        if f.size > self.MAX_BYTES:
            raise forms.ValidationError("File is larger than 2 MB.")
        return f


class AvailabilityForm(BootstrapFormMixin, forms.Form):
    date = forms.DateField(widget=DateInput())
    start_time = forms.TimeField(widget=TimeInput())
    end_time = forms.TimeField(widget=TimeInput())
    attendees = forms.IntegerField(min_value=1, required=False, label="Attendees")
    building = forms.ModelChoiceField(Building.objects.filter(is_active=True), required=False, empty_label="Any building")
    facility_type = forms.ModelChoiceField(FacilityType.objects.all(), required=False, empty_label="Any type")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_time") and cleaned.get("end_time") and cleaned["end_time"] <= cleaned["start_time"]:
            self.add_error("end_time", "End time must be later than start time.")
        return cleaned
