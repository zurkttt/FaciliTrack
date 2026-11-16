import secrets

from django import forms

from accounts.models import Department, User
from core.forms import BootstrapFormMixin

TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes (0/O, 1/l)


def temporary_password(length=12):
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length))


class UserForm(BootstrapFormMixin, forms.ModelForm):
    """Role and department only: superuser/staff flags are never exposed here."""

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "role", "department"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name"):
            self.fields[name].required = True
        self.fields["department"].queryset = Department.objects.order_by("name")

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another user already has this email address.")
        return email


class DepartmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ["code", "name"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


# key, label, type, min, max, help
SETTING_SPECS = [
    ("risk.underutilization_threshold", "Underutilization threshold (%)", float, 1, 99,
     "Facilities used less than this are flagged as underutilized."),
    ("risk.overdemand_threshold", "High-demand threshold (%)", float, 1, 100,
     "Facilities used or booked more than this are flagged as over-demanded."),
    ("risk.overcrowding_ratio", "Overcrowding ratio", float, 0.5, 2.0,
     "Headcount ÷ capacity above which overcrowding is flagged (1.0 = any attendee over capacity)."),
    ("risk.maintenance_days", "Maintenance days before flagging", int, 1, 365,
     "A facility unavailable this long is flagged as an unavailable-facility risk."),
    ("risk.stale_data_days", "Days without usage data", int, 1, 90,
     "No usage records for this many days (despite sessions) is flagged as unreliable data."),
    ("risk.window_days", "Risk analysis window (days)", int, 7, 180,
     "How many recent days the risk rules analyse."),
]
SETTING_DEFAULTS = {
    "risk.underutilization_threshold": 30, "risk.overdemand_threshold": 85, "risk.overcrowding_ratio": 1.0,
    "risk.maintenance_days": 14, "risk.stale_data_days": 7, "risk.window_days": 28,
}


class SettingsForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        max_length=300, label="Reason for change",
        help_text="Recorded in the audit log, e.g. “Approved by Data Governance Council, 12 Nov 2026”.",
    )

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        current = current or {}
        fields = {}
        for key, label, kind, low, high, help_text in SETTING_SPECS:
            field_cls = forms.FloatField if kind is float else forms.IntegerField
            fields[key] = field_cls(label=label, min_value=low, max_value=high, help_text=help_text,
                                    initial=current.get(key, SETTING_DEFAULTS[key]))
        fields.update(self.fields)  # keep "reason" last
        self.fields = fields
        for field in fields.values():  # the mixin styled only "reason"; style the added fields too
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("risk.underutilization_threshold"), cleaned.get("risk.overdemand_threshold")
        if low is not None and high is not None and low >= high:
            self.add_error("risk.overdemand_threshold", "Must be higher than the underutilization threshold.")
        return cleaned
