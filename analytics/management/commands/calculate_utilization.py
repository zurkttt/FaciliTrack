"""Recalculate daily utilization metrics.

    python manage.py calculate_utilization                 # last 7 days (default nightly run)
    python manage.py calculate_utilization --days 120      # a longer window
    python manage.py calculate_utilization --start 2026-08-01 --end 2026-12-15
    python manage.py calculate_utilization --definition 1.0

Recalculating the last few days every night picks up late usage entries and corrections.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analytics.engine import calculate
from analytics.models import MetricDefinition


class Command(BaseCommand):
    help = "Recalculate daily facility utilization metrics."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Recalculate this many days up to today (default 7).")
        parser.add_argument("--start", type=datetime.date.fromisoformat, help="Start date YYYY-MM-DD.")
        parser.add_argument("--end", type=datetime.date.fromisoformat, help="End date YYYY-MM-DD (default today).")
        parser.add_argument("--definition", help="Metric definition version (default: the active one).")

    def handle(self, *args, **options):
        if options["definition"]:
            definition = MetricDefinition.objects.filter(version=options["definition"]).first()
            if definition is None:
                raise CommandError(f"No metric definition with version {options['definition']!r}.")
        else:
            definition = MetricDefinition.active()
            if definition is None:
                raise CommandError("No active metric definition.")

        end = options["end"] or timezone.localdate()
        start = options["start"] or end - datetime.timedelta(days=options["days"] - 1)
        if start > end:
            raise CommandError("--start must not be after --end.")

        rows = calculate(start, end, definition)
        self.stdout.write(self.style.SUCCESS(f"Calculated {rows} facility-days ({start} to {end}) using {definition}."))
