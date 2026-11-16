import datetime

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import role_required
from facilities.models import Building, Facility

from .forms import CheckInForm, DateRangeForm, HeadcountForm, UsageImportForm, UsageRecordForm
from .importer import OPTIONAL_COLUMNS, REQUIRED_COLUMNS, import_usage, template_csv
from .models import UsageRecord
from .reports import compare
from .sessions import (
    find_session, link_to_session, records_by_session, records_for_session, session_from_key, sessions_between,
)

# Facilities staff record usage; planners/registrar may view usage evidence.
USAGE_STAFF = (User.Role.ADMIN, User.Role.FACILITIES)
USAGE_VIEWERS = (User.Role.ADMIN, User.Role.FACILITIES, User.Role.PLANNER, User.Role.REGISTRAR)


def _can_manage(user, record):
    return user.has_role(*USAGE_STAFF) or record.recorded_by_id == user.pk


def _open_record(facility, now):
    return (
        UsageRecord.objects.filter(facility=facility, status=UsageRecord.Status.USED, start_at__lte=now, end_at__gt=now)
        .order_by("-start_at").first()
    )


# ================================================================ QR check-in

@login_required
def checkin(request, code):
    facility = get_object_or_404(Facility, facility_code__iexact=code, is_active=True)
    now = timezone.now()
    open_record = _open_record(facility, now)
    session = find_session(facility, now)
    form = CheckInForm(request.POST or None)

    if request.method == "POST":
        if open_record:
            messages.info(request, "This room is already checked in. You can update the headcount below.")
            return redirect("usage:checkin", code=facility.facility_code)
        if form.is_valid():
            end_at = session.end_at if session and session.end_at > now else now + datetime.timedelta(minutes=form.cleaned_data["duration"])
            UsageRecord.objects.create(
                facility=facility, start_at=now, end_at=end_at,
                schedule=session.schedule if session else None,
                reservation=session.reservation if session else None,
                headcount=form.cleaned_data.get("headcount"),
                source=UsageRecord.Source.QR, recorded_by=request.user,
            )
            messages.success(request, f"Checked in to {facility.facility_code} until {timezone.localtime(end_at):%H:%M}.")
            return redirect("usage:checkin", code=facility.facility_code)

    return render(request, "usage/checkin.html", {
        "facility": facility, "session": session, "open_record": open_record, "form": form,
        "headcount_form": HeadcountForm(initial={"headcount": open_record.headcount} if open_record else None),
        "can_manage": open_record is not None and _can_manage(request.user, open_record),
    })


@require_POST
@login_required
def record_end_now(request, pk):
    record = get_object_or_404(UsageRecord, pk=pk)
    if not _can_manage(request.user, record):
        raise PermissionDenied
    now = timezone.now()
    if record.start_at <= now < record.end_at:
        code = record.facility.facility_code
        if now - record.start_at < datetime.timedelta(minutes=1):
            record.delete()  # checked out right away: treat as an accidental check-in
            messages.success(request, f"Check-in to {code} cancelled.")
            return redirect("usage:checkin", code=code)
        record.end_at = now
        record.save()
        messages.success(request, f"{code} checked out.")
    else:
        messages.error(request, "This session is not in progress.")
    return redirect("usage:checkin", code=record.facility.facility_code)


@require_POST
@login_required
def record_update_headcount(request, pk):
    record = get_object_or_404(UsageRecord, pk=pk)
    if not _can_manage(request.user, record):
        raise PermissionDenied
    form = HeadcountForm(request.POST)
    if form.is_valid():
        record.headcount = form.cleaned_data["headcount"]
        record.save()
        messages.success(request, "Headcount updated.")
    else:
        messages.error(request, "Please enter a valid headcount.")
    return redirect("usage:checkin", code=record.facility.facility_code)


def qr_svg(url):
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return mark_safe(img.to_string(encoding="unicode"))  # generated locally from our own URL


@role_required(*USAGE_STAFF)
def qr_codes(request):
    facilities = Facility.objects.filter(is_active=True).select_related("building")
    building_id = request.GET.get("building", "")
    if building_id.isdigit():
        facilities = facilities.filter(building_id=building_id)
    if request.GET.get("facility", "").isdigit():
        facilities = facilities.filter(pk=request.GET["facility"])
    cards = []
    for f in facilities:
        url = request.build_absolute_uri(reverse("usage:checkin", args=[f.facility_code]))
        cards.append({"facility": f, "url": url, "svg": qr_svg(url)})
    host = request.get_host().split(":")[0]
    return render(request, "usage/qr_codes.html", {
        "cards": cards, "buildings": Building.objects.filter(is_active=True), "building_id": building_id,
        "is_localhost": host in ("127.0.0.1", "localhost"),
    })


# ================================================================ attendance sheet (manual)

def _parse_date(value, default):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


@role_required(*USAGE_STAFF)
def attendance(request):
    day = _parse_date(request.GET.get("date"), timezone.localdate())
    facilities = Facility.objects.filter(is_active=True)
    building_id = request.GET.get("building", "")
    if building_id.isdigit():
        facilities = facilities.filter(building_id=building_id)
    now = timezone.now()
    rows = []
    recorded = records_by_session(day, day, facilities=facilities)  # one query for the whole sheet
    for s in sessions_between(day, day, facilities=facilities):
        records = recorded.get(s.key, [])
        used = [r for r in records if r.status == UsageRecord.Status.USED]
        rows.append({
            "session": s,
            "status": "used" if used else "no_show" if records else "no_data",
            "headcount": next((r.headcount for r in used if r.headcount is not None), None),
            "can_mark": s.start_at <= now,
            "key_id": s.schedule.pk if s.schedule else s.reservation.pk,
            "key_kind": "S" if s.schedule else "R",
        })
    return render(request, "usage/attendance.html", {
        "day": day, "rows": rows, "prev_day": day - datetime.timedelta(days=1), "next_day": day + datetime.timedelta(days=1),
        "buildings": Building.objects.filter(is_active=True), "building_id": building_id,
        "back": request.get_full_path(),
    })


@require_POST
@role_required(*USAGE_STAFF)
def attendance_mark(request):
    p = request.POST
    back = p.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = reverse("usage:attendance")
    status = p.get("status")
    if status not in (UsageRecord.Status.USED, UsageRecord.Status.NO_SHOW) or p.get("kind") not in ("S", "R") or not p.get("id", "").isdigit():
        messages.error(request, "Invalid attendance entry.")
        return redirect(back)
    session = session_from_key(p["kind"], int(p["id"]), _parse_date(p.get("date"), None))
    if session is None:
        messages.error(request, "That session no longer exists.")
        return redirect(back)
    if session.start_at > timezone.now():
        messages.error(request, "You can't record a session that hasn't started yet.")
        return redirect(back)
    headcount = None
    if status == UsageRecord.Status.USED and p.get("headcount", "").strip():
        if not p["headcount"].strip().isdigit():
            messages.error(request, "Headcount must be a whole number.")
            return redirect(back)
        headcount = int(p["headcount"])

    record = records_for_session(session).order_by("start_at").first()
    if record is None:
        record = UsageRecord(
            facility=session.facility, start_at=session.start_at, end_at=session.end_at,
            schedule=session.schedule, reservation=session.reservation,
            source=UsageRecord.Source.MANUAL, recorded_by=request.user,
        )
    record.status = status
    record.headcount = headcount if status == UsageRecord.Status.USED else None
    record.save()
    label = "used" if status == UsageRecord.Status.USED else "a no-show"
    messages.success(request, f"{session.label} ({session.facility.facility_code}) marked as {label}.")
    return redirect(back)


# ================================================================ records, manual entry, import

@role_required(*USAGE_VIEWERS)
def record_list(request):
    today = timezone.localdate()
    start = _parse_date(request.GET.get("start"), today - datetime.timedelta(days=6))
    end = _parse_date(request.GET.get("end"), today)
    tz = timezone.get_current_timezone()
    qs = UsageRecord.objects.filter(
        start_at__gte=timezone.make_aware(datetime.datetime.combine(start, datetime.time.min), tz),
        start_at__lte=timezone.make_aware(datetime.datetime.combine(end, datetime.time.max), tz),
    ).select_related("facility", "schedule__activity", "reservation", "recorded_by").order_by("-start_at")
    p = request.GET
    if p.get("facility", "").isdigit():
        qs = qs.filter(facility_id=p["facility"])
    if p.get("source") in UsageRecord.Source.values:
        qs = qs.filter(source=p["source"])
    if p.get("status") in UsageRecord.Status.values:
        qs = qs.filter(status=p["status"])
    page = Paginator(qs, 25).get_page(p.get("page"))
    params = p.copy()
    params.pop("page", None)
    return render(request, "usage/record_list.html", {
        "page_obj": page, "records": page.object_list, "start": start, "end": end, "filters": p,
        "facilities": Facility.objects.filter(is_active=True), "sources": UsageRecord.Source.choices,
        "statuses": UsageRecord.Status.choices, "querystring": params.urlencode(),
        "can_edit": request.user.has_role(*USAGE_STAFF),
    })


@role_required(*USAGE_STAFF)
def record_create(request):
    form = UsageRecordForm(request.POST or None, initial={"facility": request.GET.get("facility")})
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.source = UsageRecord.Source.MANUAL
        record.recorded_by = request.user
        session = link_to_session(record)
        record.save()
        where = f"linked to {session.label}" if session else "recorded as unscheduled use"
        messages.success(request, f"Usage {where}.")
        return redirect("usage:record_list")
    return render(request, "facilities/form.html", {
        "form": form, "title": "Record facility use", "cancel_url": reverse("usage:record_list"),
    })


@require_POST
@role_required(*USAGE_STAFF)
def record_delete(request, pk):
    record = get_object_or_404(UsageRecord, pk=pk)
    record.delete()
    messages.success(request, "Usage record deleted.")
    return redirect("usage:record_list")


@role_required(*USAGE_STAFF)
def usage_import(request):
    result = None
    form = UsageImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        result = import_usage(form.cleaned_data["file"], request.user)
    return render(request, "usage/import.html", {
        "form": form, "result": result, "required_columns": REQUIRED_COLUMNS, "optional_columns": OPTIONAL_COLUMNS,
    })


@role_required(*USAGE_STAFF)
def usage_import_template(request):
    response = HttpResponse(template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="usage_import_template.csv"'
    return response


# ================================================================ scheduled vs actual

@role_required(*USAGE_VIEWERS)
def comparison(request):
    today = timezone.localdate()
    data = request.GET if request.GET.get("start") else {"start": today - datetime.timedelta(days=6), "end": today}
    form = DateRangeForm(data)
    report = None
    if form.is_valid():
        d = form.cleaned_data
        facilities = Facility.objects.filter(is_active=True)
        if d.get("building"):
            facilities = facilities.filter(building=d["building"])
        if d.get("facility"):
            facilities = facilities.filter(pk=d["facility"].pk)
        report = compare(d["start"], d["end"], facilities)
        rows = report["rows"]
        if not d.get("facility"):
            # Campus-wide: list only the sessions that need attention, so the page stays fast and readable.
            rows = [r for r in rows if r.status != "used" or r.over_capacity]
        report["detail_total"] = len(rows)
        report["detail_rows"] = rows[:DETAIL_ROW_LIMIT]
        report["detail_exceptions_only"] = not d.get("facility")
        report["unscheduled_total"] = len(report["unscheduled"])
        report["unscheduled"] = report["unscheduled"][:DETAIL_ROW_LIMIT]
    return render(request, "usage/comparison.html", {"form": form, "report": report, "limit": DETAIL_ROW_LIMIT})


DETAIL_ROW_LIMIT = 300
