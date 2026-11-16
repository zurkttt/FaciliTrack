"""Run risk detection and update the risk register.

    python manage.py detect_risks

Run nightly AFTER calculate_utilization so the underutilization and over-demand rules see fresh metrics.
"""
from django.core.management.base import BaseCommand

from risks.engine import run


class Command(BaseCommand):
    help = "Detect facility risks and synchronize the risk register."

    def handle(self, *args, **options):
        r = run()
        self.stdout.write(self.style.SUCCESS(
            f"{r.findings} conditions found: {r.created} new, {r.updated} updated, "
            f"{r.reopened} reopened, {r.closed} closed automatically; {r.reminders} overdue-action reminders sent."
        ))
