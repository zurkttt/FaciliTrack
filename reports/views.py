from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import role_required
from analytics.views import ANALYTICS_VIEWERS
from audit.middleware import get_client_ip
from audit.models import AuditEvent
from core.forms import BootstrapFormMixin
from facilities.models import Building
from scheduling.forms import DateInput

from .registry import REPORTS, TEXT, default_period
from .renderers import fmt, to_csv, to_pdf, to_xlsx

REPORT_VIEWERS = ANALYTICS_VIEWERS
MAX_DAYS = 366
FORMATS = {
    "csv": ("text/csv; charset=utf-8", "csv", to_csv),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", to_xlsx),
    "pdf": ("application/pdf", "pdf", to_pdf),
}


class ReportParamsForm(BootstrapFormMixin, forms.Form):
    start = forms.DateField(widget=DateInput(), label="From", required=False)
    end = forms.DateField(widget=DateInput(), label="To", required=False)
    building = forms.ModelChoiceField(Building.objects.filter(is_active=True), required=False, empty_label="All buildings")

    def __init__(self, data, uses_dates, **kwargs):
        start, end = default_period()
        merged = {"start": start.isoformat(), "end": end.isoformat(), **{k: v for k, v in data.items() if v}}
        super().__init__(merged, **kwargs)
        self.uses_dates = uses_dates
        if not uses_dates:
            del self.fields["start"], self.fields["end"]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if self.uses_dates and start and end:
            if end < start:
                self.add_error("end", "'To' must not be before 'From'.")
            elif (end - start).days >= MAX_DAYS:
                self.add_error("end", f"Please choose at most {MAX_DAYS} days.")
        return cleaned


@role_required(*REPORT_VIEWERS)
def index(request):
    return render(request, "reports/index.html", {"reports": REPORTS.values()})


@role_required(*REPORT_VIEWERS)
def report(request, slug):
    spec = REPORTS.get(slug)
    if spec is None:
        raise Http404
    form = ReportParamsForm(request.GET, spec.uses_dates)
    ctx = {"spec": spec, "form": form}
    if not form.is_valid():
        return render(request, "reports/report.html", ctx)
    d = form.cleaned_data
    data = spec.builder(d.get("start"), d.get("end"), d.get("building"))

    fmt_key = request.GET.get("format")
    if fmt_key in FORMATS:
        content_type, ext, render_fn = FORMATS[fmt_key]
        _audit_export(request, spec, d, fmt_key, len(data.rows))
        response = HttpResponse(render_fn(data, request.user), content_type=content_type)
        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        response["Content-Disposition"] = f'attachment; filename="facilitrack-{slug}-{stamp}.{ext}"'
        return response

    params = request.GET.copy()
    params.pop("format", None)
    ctx.update(
        data=data, querystring=params.urlencode(),
        headers=[(c.header, c.kind != TEXT) for c in data.columns],
        rows=[[(fmt(v, c.kind), c.kind != TEXT) for v, c in zip(row, data.columns)] for row in data.rows],
        totals=[(fmt(v, c.kind), c.kind != TEXT) for v, c in zip(data.totals, data.columns)] if data.totals else None,
    )
    return render(request, "reports/report.html", ctx)


def _audit_export(request, spec, params, fmt_key, row_count):
    """Exports leave the system, so each one is recorded (who exported what, when)."""
    AuditEvent.objects.create(
        user=request.user, username=request.user.get_username(), action=AuditEvent.Action.EXPORT,
        entity="report", entity_id=spec.slug, entity_repr=f"{spec.title} ({fmt_key.upper()})",
        changes={"format": fmt_key, "rows": row_count,
                 **{k: str(v) for k, v in params.items() if v}},
        ip_address=get_client_ip(request),
    )
