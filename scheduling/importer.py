"""CSV import of class schedules (simulates the Academic Scheduling System interface, Section 9.2).

All-or-nothing: every row is validated and conflict-checked; if any row has an error,
nothing is saved and the full error report is returned.
"""
import csv
import datetime
import io
from dataclasses import dataclass, field

from django.db import transaction

from accounts.models import Department
from facilities.models import Facility

from .conflicts import check_schedule
from .models import Activity, Schedule

REQUIRED_COLUMNS = [
    "facility_code", "activity_code", "activity_title", "day",
    "start_time", "end_time", "term_start", "term_end",
]
OPTIONAL_COLUMNS = ["expected_attendees", "activity_type", "department_code"]
TEMPLATE_ROWS = [
    ["ENG-301", "EE210-A", "Electronics 1", "Tue", "08:00", "10:00", "2026-08-10", "2026-12-12", "30", "CLASS", "COE"],
    ["MAIN-102", "ENG102-A", "Readings in Philippine History", "Thursday", "08:00", "09:30", "2026-08-10", "2026-12-12", "38", "CLASS", "CAS"],
]
MAX_ROWS = 5000

DAY_ALIASES = {}
for value, label in Schedule.Day.choices:
    DAY_ALIASES[str(value)] = value
    DAY_ALIASES[label.lower()] = value
    DAY_ALIASES[label[:3].lower()] = value
DAY_ALIASES.update({"tues": 1, "thu": 3, "thur": 3, "thurs": 3})

TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


@dataclass
class ImportResult:
    rows: int = 0
    created: int = 0
    errors: list = field(default_factory=list)     # [(row_number, [messages])]
    warnings: list = field(default_factory=list)   # [(row_number, [messages])]
    file_error: str = ""

    @property
    def committed(self):
        return not self.file_error and not self.errors and self.created > 0


def template_csv():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def parse_value(value, formats, kind):
    for fmt in formats:
        try:
            parsed = datetime.datetime.strptime(value.strip(), fmt)
            return parsed.time() if kind == "time" else parsed.date()
        except ValueError:
            continue
    raise ValueError(f"invalid {kind} '{value}'")


def decode_upload(uploaded_file):
    raw = uploaded_file.read()
    for encoding in ("utf-8-sig", "cp1252"):  # Excel on Windows often saves cp1252
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("File is not valid UTF-8 text.")


def import_schedules(uploaded_file):
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
    departments = {d.code.upper(): d for d in Department.objects.all()}
    activity_types = {v for v, _ in Activity.Type.choices}

    with transaction.atomic():
        for row_number, row in enumerate(reader, start=2):  # row 1 is the header
            if result.rows >= MAX_ROWS:
                result.file_error = f"Too many rows (maximum {MAX_ROWS})."
                break
            row = {k: (v or "").strip() for k, v in row.items() if k}
            if not any(row.values()):
                continue
            result.rows += 1
            errors, warnings = [], []

            for col in REQUIRED_COLUMNS:
                if not row.get(col):
                    errors.append(f"{col} is required")
            if errors:
                result.errors.append((row_number, errors))
                continue

            facility = facilities.get(row["facility_code"].upper())
            if facility is None:
                errors.append(f"unknown facility_code '{row['facility_code']}'")
            day = DAY_ALIASES.get(row["day"].lower())
            if day is None:
                errors.append(f"invalid day '{row['day']}' (use Mon..Sun, Monday..Sunday, or 0..6)")
            try:
                start_time = parse_value(row["start_time"], TIME_FORMATS, "time")
                end_time = parse_value(row["end_time"], TIME_FORMATS, "time")
                if end_time <= start_time:
                    errors.append("end_time must be later than start_time")
            except ValueError as exc:
                errors.append(str(exc))
            try:
                term_start = parse_value(row["term_start"], DATE_FORMATS, "date")
                term_end = parse_value(row["term_end"], DATE_FORMATS, "date")
                if term_end < term_start:
                    errors.append("term_end must not be before term_start")
            except ValueError as exc:
                errors.append(str(exc))
            attendees = 0
            if row.get("expected_attendees"):
                if row["expected_attendees"].isdigit():
                    attendees = int(row["expected_attendees"])
                else:
                    errors.append(f"expected_attendees must be a whole number, got '{row['expected_attendees']}'")
            activity_type = (row.get("activity_type") or Activity.Type.CLASS).upper()
            if activity_type not in activity_types:
                errors.append(f"invalid activity_type '{row['activity_type']}'")
            department = None
            if row.get("department_code"):
                department = departments.get(row["department_code"].upper())
                if department is None:
                    errors.append(f"unknown department_code '{row['department_code']}'")
            if errors:
                result.errors.append((row_number, errors))
                continue

            # Rows saved earlier in this file are visible here, so clashes inside the file are caught too.
            conflicts = check_schedule(facility, day, start_time, end_time, term_start, term_end, attendees)
            errors = [c.message for c in conflicts if c.is_error]
            warnings = [c.message for c in conflicts if not c.is_error]
            if errors:
                result.errors.append((row_number, errors))
                continue
            if warnings:
                result.warnings.append((row_number, warnings))

            activity, _ = Activity.objects.get_or_create(
                code=row["activity_code"].upper(),
                defaults={"title": row["activity_title"], "activity_type": activity_type, "department": department},
            )
            Schedule.objects.create(
                facility=facility, activity=activity, day_of_week=day,
                start_time=start_time, end_time=end_time, term_start=term_start, term_end=term_end,
                expected_attendees=attendees, source=Schedule.Source.IMPORT,
            )
            result.created += 1

        if result.errors or result.file_error:
            transaction.set_rollback(True)
            result.created = 0
    return result
