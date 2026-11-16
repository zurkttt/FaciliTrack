import datetime

from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin, TimeInput
from facilities.models import Building, Facility
from scheduling.forms import DateInput, ScheduleImportForm

from .importer import overlapping_usage
from .models import UsageRecord


class CheckInForm(BootstrapFormMixin, forms.Form):
    DURATIONS = [(30, "30 minutes"), (60, "1 hour"), (90, "1.5 hours"), (120, "2 hours"), (180, "3 hours"), (240, "4 hours")]

    headcount = forms.IntegerField(min_value=0, max_value=5000, required=False, label="People in the room (optional)")
    duration = forms.TypedChoiceField(choices=DURATIONS, coerce=int, initial=60, label="Expected duration",
                                      help_text="Ignored when a matching class or reservation is found; its end time is used instead.")


class HeadcountForm(BootstrapFormMixin, forms.Form):
    headcount = forms.IntegerField(min_value=0, max_value=5000, label="Updated headcount")


class UsageRecordForm(BootstrapFormMixin, forms.ModelForm):
    """Manual entry of unscheduled (walk-in) use, or use not captured by QR check-in."""

    date = forms.DateField(widget=DateInput())
    start_time = forms.TimeField(widget=TimeInput())
    end_time = forms.TimeField(widget=TimeInput())

    field_order = ["facility", "date", "start_time", "end_time", "headcount"]

    class Meta:
        model = UsageRecord
        fields = ["facility", "headcount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facility"].queryset = Facility.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(datetime.datetime.combine(cleaned["date"], cleaned["start_time"]), tz)
        end_at = timezone.make_aware(datetime.datetime.combine(cleaned["date"], cleaned["end_time"]), tz)
        if end_at <= start_at:
            self.add_error("end_time", "End time must be later than start time.")
        elif end_at > timezone.now():
            self.add_error("end_time", "Usage cannot end in the future. Use QR check-in for sessions in progress.")
        elif overlapping_usage(cleaned["facility"], start_at, end_at).exists():
            self.add_error(None, "Usage is already recorded for this facility during that time.")
        else:
            self.instance.start_at, self.instance.end_at = start_at, end_at
        return cleaned


class UsageImportForm(ScheduleImportForm):
    pass


class DateRangeForm(BootstrapFormMixin, forms.Form):
    MAX_DAYS = 92

    start = forms.DateField(widget=DateInput(), label="From")
    end = forms.DateField(widget=DateInput(), label="To")
    building = forms.ModelChoiceField(Building.objects.filter(is_active=True), required=False, empty_label="All buildings")
    facility = forms.ModelChoiceField(Facility.objects.filter(is_active=True), required=False, empty_label="All facilities")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end:
            if end < start:
                self.add_error("end", "'To' must not be before 'From'.")
            elif (end - start).days >= self.MAX_DAYS:
                self.add_error("end", f"Please choose at most {self.MAX_DAYS} days.")
        return cleaned
