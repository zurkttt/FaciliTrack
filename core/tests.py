from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

PASSWORD = "Pass@12345"


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for u in User.objects.all():
            u.set_password(PASSWORD)
            u.save()

    def get(self, username):
        self.client.login(username=username, password=PASSWORD)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_viewer_sees_only_personal_sections(self):
        response = self.get("viewer")
        self.assertContains(response, "My upcoming reservations")
        self.assertContains(response, "Thesis defense panel")  # their seeded booking
        self.assertNotContains(response, "Top risks")
        self.assertNotContains(response, "Awaiting your approval")
        self.assertNotContains(response, "Data freshness")

    def test_facilities_sees_approvals_risks_and_freshness(self):
        response = self.get("facilities")
        for text in ("Top risks", "Awaiting your approval", "CCS freshman orientation", "Data freshness", "Rooms not available"):
            self.assertContains(response, text)

    def test_planner_sees_kpis_and_owned_risks(self):
        response = self.get("planner")
        self.assertContains(response, "Utilization, last 28 days")
        self.assertContains(response, "risks I own")
        self.assertNotContains(response, "Awaiting your approval")

    def test_benchmark_runs_and_leaves_data_untouched(self):
        from facilities.models import Facility
        from usage.models import UsageRecord

        before = (Facility.objects.count(), UsageRecord.objects.count(), User.objects.count())
        out = StringIO()
        call_command("benchmark", "--facilities", "3", "--days", "7", "--runs", "1", stdout=out)
        self.assertIn("rolled back", out.getvalue())
        self.assertIn("Home dashboard", out.getvalue())
        self.assertEqual((Facility.objects.count(), UsageRecord.objects.count(), User.objects.count()), before)

    def test_navigation_matches_role(self):
        self.assertNotContains(self.get("viewer"), 'href="/reports/"')
        self.assertContains(self.get("planner"), 'href="/reports/"')
