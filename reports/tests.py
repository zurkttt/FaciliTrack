import csv
import io
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import User
from audit.models import AuditEvent
from facilities.models import Building

from .registry import REPORTS, default_period
from .renderers import fmt

PASSWORD = "Pass@12345"


class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for u in User.objects.filter(username__in=["planner", "viewer"]):
            u.set_password(PASSWORD)
            u.save()

    def setUp(self):
        self.client.login(username="planner", password=PASSWORD)

    def test_viewer_has_no_access(self):
        self.client.login(username="viewer", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("reports:index")).status_code, 403)
        self.assertEqual(self.client.get(reverse("reports:report", args=["risks"])).status_code, 403)

    def test_every_report_previews_and_exports_in_every_format(self):
        for slug in REPORTS:
            url = reverse("reports:report", args=[slug])
            self.assertEqual(self.client.get(url).status_code, 200, slug)
            for fmt_key, signature in (("pdf", b"%PDF"), ("xlsx", b"PK"), ("csv", "﻿".encode())):
                response = self.client.get(url, {"format": fmt_key})
                self.assertEqual(response.status_code, 200, (slug, fmt_key))
                self.assertTrue(response.content.startswith(signature), (slug, fmt_key))
                self.assertIn("attachment;", response["Content-Disposition"])

    def test_formats_contain_the_same_numbers(self):
        start, end = default_period()
        builder = REPORTS["utilization"].builder
        data = builder(start, end)
        eng_cl1 = next(r for r in data.rows if r[0] == "ENG-CL1")
        url = reverse("reports:report", args=["utilization"])

        rows = list(csv.reader(io.StringIO(self.client.get(url, {"format": "csv"}).content.decode("utf-8-sig"))))
        csv_row = next(r for r in rows if r and r[0] == "ENG-CL1")
        self.assertAlmostEqual(float(csv_row[8]), eng_cl1[8], places=2)

        ws = load_workbook(io.BytesIO(self.client.get(url, {"format": "xlsx"}).content)).active
        xlsx_row = next(r for r in ws.iter_rows(values_only=True) if r[0] == "ENG-CL1")
        self.assertAlmostEqual(xlsx_row[8], eng_cl1[8], places=6)  # stored as a real number

        self.assertContains(self.client.get(url), fmt(eng_cl1[8], "pct"))

    def test_reports_state_parameters_and_definition(self):
        response = self.client.get(reverse("reports:report", args=["utilization"]))
        self.assertContains(response, "Metric definition")
        self.assertContains(response, "v1.0")
        self.assertContains(response, "summed hours")

    def test_building_filter(self):
        eng = Building.objects.get(code="ENG")
        response = self.client.get(reverse("reports:report", args=["inventory"]), {"building": eng.pk})
        self.assertContains(response, "ENG-CL1")
        self.assertNotContains(response, "MAIN-101")

    def test_invalid_range_shows_error(self):
        response = self.client.get(reverse("reports:report", args=["usage"]), {"start": "2026-05-02", "end": "2026-05-01"})
        self.assertContains(response, "must not be before")

    def test_unknown_report_404(self):
        self.assertEqual(self.client.get(reverse("reports:report", args=["nope"])).status_code, 404)

    def test_exports_are_audited(self):
        self.client.get(reverse("reports:report", args=["risks"]), {"format": "pdf"})
        event = AuditEvent.objects.get(action=AuditEvent.Action.EXPORT)
        self.assertEqual((event.username, event.entity_id, event.changes["format"]), ("planner", "risks", "pdf"))
        self.assertGreater(event.changes["rows"], 0)

    def test_risk_report_includes_disclaimer(self):
        self.assertContains(self.client.get(reverse("reports:report", args=["risks"])), "do not certify a facility")
