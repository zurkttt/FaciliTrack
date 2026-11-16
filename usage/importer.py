"""CSV import of actual usage logs (e.g. exported from door sensors or paper logbooks).

All-or-nothing, like the schedule import. Each row is linked to its matching
class/reservation automatically.
"""
import csv
import datetime
import io

from django.db import transaction
from django.utils import timezone

from facilities.models import Facility
from scheduling.importer import DATE_FORMATS, MAX_ROWS, TIME_FORMATS, ImportResult, decode_upload, parse_value

from .models import UsageRecord
from .sessions import link_to_session

REQUIRED_COLUMNS = ["facility_code", "date", "start_time", "end_time"]
OPTIONAL_COLUMNS = ["headcount", "status"]
TEMPLATE_ROWS = [
    ["ENG-CL1", "2026-11-16", "08:05", "09:55", "33", "USED"],
    ["MAIN-101", "2026-11-16", "07:30", "09:00", "", "NO_SHOW"],
]
STATUS_ALIASES = {"used": UsageRecord.Status.USED, "no_show": UsageRecord.Status.NO_SHOW, "no-show": UsageRecord.Status.NO_SHOW, "noshow": UsageRecord.Status.NO_SHOW}


def template_csv():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def overlapping_usage(facility, start_at, end_at, exclude_pk=None):
    return UsageRecord.objects.filter(
        facility=facility, status=UsageRecord.Status.USED, start_at__lt=end_at, end_at__gt=start_at,
    ).exclude(pk=exclude_pk)


def import_usage(uploaded_file, user):
    result = ImportResult()
    try:
        text = decode_upload(uploaded_file)
    except ValueError as exc:
        result.file_error = str(exc)
        return result

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.file_error = "The file is empty."
        return result
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        result.file_error = f"Missing column(s): {', '.join(missing)}."
        return result

    facilities = {f.facility_code.upper(): f for f in Facility.objects.all()}
    tz = timezone.get_current_timezone()
    now = timezone.now()

    with transaction.atomic():
        for row_number, row in enumerate(reader, start=2):
            if result.rows >= MAX_ROWS:
                result.file_error = f"Too many rows (maximum {MAX_ROWS})."
                break
            row = {k: (v or "").strip() for k, v in row.items() if k}
            if not any(row.values()):
                continue
            result.rows += 1
            errors = [f"{c} is required" for c in REQUIRED_COLUMNS if not row.get(c)]
            if errors:
                result.errors.append((row_number, errors))
                continue

            facility = facilities.get(row["facility_code"].upper())
            if facility is None:
                errors.append(f"unknown facility_code '{row['facility_code']}'")
            start_at = end_at = None
            try:
                day = parse_value(row["date"], DATE_FORMATS, "date")
                start_at = timezone.make_aware(datetime.datetime.combine(day, parse_value(row["start_time"], TIME_FORMATS, "time")), tz)
                end_at = timezone.make_aware(datetime.datetime.combine(day, parse_value(row["end_time"], TIME_FORMATS, "time")), tz)
                if end_at <= start_at:
                    errors.append("end_time must be later than start_time")
                elif end_at > now:
                    errors.append("usage cannot be in the future")
            except ValueError as exc:
                errors.append(str(exc))
            headcount = None
            if row.get("headcount"):
                if row["headcount"].isdigit():
                    headcount = int(row["headcount"])
                else:
                    errors.append(f"headcount must be a whole number, got '{row['headcount']}'")
            status = STATUS_ALIASES.get((row.get("status") or "used").lower())
            if status is None:
                errors.append(f"invalid status '{row['status']}' (use USED or NO_SHOW)")
            if errors:
                result.errors.append((row_number, errors))
                continue

            if status == UsageRecord.Status.USED and overlapping_usage(facility, start_at, end_at).exists():
                result.errors.append((row_number, ["overlaps usage already recorded for this facility (duplicate?)"]))
                continue

            record = UsageRecord(
                facility=facility, start_at=start_at, end_at=end_at, headcount=headcount,
                status=status, source=UsageRecord.Source.IMPORT, recorded_by=user,
            )
            session = link_to_session(record)
            warnings = []
            if session is None:
                if status == UsageRecord.Status.NO_SHOW:
                    result.errors.append((row_number, ["NO_SHOW rows must match a scheduled class or approved reservation"]))
                    continue
                warnings.append("no matching class or reservation (recorded as unscheduled use)")
            if record.over_capacity:
                warnings.append(f"headcount {headcount} exceeds capacity {facility.capacity}")
            if warnings:
                result.warnings.append((row_number, warnings))
            record.save()
            result.created += 1

        if result.errors or result.file_error:
            transaction.set_rollback(True)
            result.created = 0
    return result
