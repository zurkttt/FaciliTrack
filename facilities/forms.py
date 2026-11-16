from django import forms

from core.forms import BootstrapFormMixin, DateTimeLocalInput, TimeInput

from .models import Building, Facility, FacilityType, MaintenanceRecord


class FacilityForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Facility
        fields = [
            "facility_code", "name", "building", "facility_type", "floor", "capacity",
            "owner_department", "open_time", "close_time", "status", "notes",
        ]
        widgets = {
            "open_time": TimeInput(),
            "close_time": TimeInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        buildings = Building.objects.filter(is_active=True)
        if self.instance.pk:  # keep the current building selectable even if it was deactivated
            buildings = buildings | Building.objects.filter(pk=self.instance.building_id)
        self.fields["building"].queryset = buildings

    def clean_facility_code(self):
        code = self.cleaned_data["facility_code"].strip().upper()
        clash = Facility.objects.filter(facility_code__iexact=code).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A facility with this code already exists.")
        return code

    def clean(self):
        cleaned = super().clean()
        building, floor = cleaned.get("building"), cleaned.get("floor")
        if building and floor and floor > building.floors:
            self.add_error("floor", f"{building.code} only has {building.floors} floor(s).")
        return cleaned


class FacilityStatusForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Facility
        fields = ["status"]


class BuildingForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Building
        fields = ["code", "name", "floors", "is_active"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class FacilityTypeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = FacilityType
        fields = ["name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class MaintenanceRecordForm(BootstrapFormMixin, forms.ModelForm):
    mark_facility = forms.BooleanField(
        required=False,
        initial=True,
        label="Also set the facility status to Under Maintenance",
    )

    class Meta:
        model = MaintenanceRecord
        fields = ["reason", "start_at", "end_at", "status"]
        widgets = {"start_at": DateTimeLocalInput(), "end_at": DateTimeLocalInput()}
