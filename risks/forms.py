from django import forms

from accounts.models import User
from core.forms import BootstrapFormMixin
from facilities.models import Facility
from scheduling.forms import DateInput

from .models import MitigationAction, Risk

NOTE_REQUIRED = (Risk.Status.CLOSED, Risk.Status.ACCEPTED)


def staff_users():
    return User.objects.filter(is_active=True).exclude(role=User.Role.VIEWER).order_by("first_name", "username")


class PersonChoiceField(forms.ModelChoiceField):
    """Shows 'Full Name (Role)' instead of the login name."""

    def label_from_instance(self, user):
        return f"{user.get_full_name() or user.username} ({user.get_role_display()})"


def _use_person_labels(form, *names):
    for name in names:
        old = form.fields[name]
        form.fields[name] = PersonChoiceField(
            queryset=staff_users(), required=old.required, label=old.label,
            empty_label="Unassigned", widget=forms.Select(attrs={"class": "form-select"}),
        )


class RiskUpdateForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=Risk.Status.choices)
    owner = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label="Unassigned")
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=1000,
                           help_text="Required when closing or accepting a risk.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _use_person_labels(self, "owner")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") in NOTE_REQUIRED and not cleaned.get("note", "").strip():
            self.add_error("note", "Explain why the risk is being closed or accepted.")
        return cleaned


class ManualRiskForm(BootstrapFormMixin, forms.ModelForm):
    """Risks raised by people (e.g. from an inspection), scored on the same 1-5 scales."""

    class Meta:
        model = Risk
        fields = ["facility", "category", "likelihood", "impact", "reason", "owner"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}
        labels = {"reason": "Description and evidence"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facility"].queryset = Facility.objects.filter(is_active=True)
        _use_person_labels(self, "owner")
        for name in ("likelihood", "impact"):
            self.fields[name] = forms.TypedChoiceField(
                choices=[(i, str(i)) for i in range(1, 6)], coerce=int, label=name.title(),
                widget=forms.Select(attrs={"class": "form-select"}),
            )


class MitigationActionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = MitigationAction
        fields = ["action", "assigned_to", "due_date"]
        widgets = {"due_date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _use_person_labels(self, "assigned_to")
