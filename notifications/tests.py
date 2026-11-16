import datetime
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from facilities.models import Facility
from risks import engine
from risks.models import MitigationAction, Risk
from scheduling.models import Reservation

from .models import Notification
from .services import Level, notify, notify_roles

PASSWORD = "Pass@12345"


class ServiceTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password=PASSWORD, email="a@example.com", role=User.Role.FACILITIES)
        self.b = User.objects.create_user("b", password=PASSWORD, role=User.Role.FACILITIES)
        self.inactive = User.objects.create_user("c", password=PASSWORD, role=User.Role.FACILITIES, is_active=False)

    def test_notify_roles_skips_inactive_and_excluded(self):
        self.assertEqual(notify_roles([User.Role.FACILITIES], "Hello", exclude=self.b), 1)
        self.assertEqual(list(Notification.objects.values_list("user__username", flat=True)), ["a"])

    def test_dedupe_key(self):
        notify(self.a, "Reminder", dedupe_key="k1")
        notify(self.a, "Reminder again", dedupe_key="k1")
        notify(self.b, "Reminder", dedupe_key="k1")  # other users are independent
        self.assertEqual(Notification.objects.count(), 2)

    def test_no_email_by_default(self):
        notify(self.a, "Hi")
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(NOTIFY_BY_EMAIL=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_when_enabled(self):
        notify(self.a, "Hi", "Body", url="/risks/1/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/risks/1/", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].subject, "[FaciliTrack] Hi")


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password=PASSWORD)
        self.other = User.objects.create_user("o", password=PASSWORD)
        self.client.login(username="u", password=PASSWORD)

    def test_open_marks_read_and_follows_local_link(self):
        n = Notification.objects.create(user=self.user, title="x", url="/facilities/")
        response = self.client.post(reverse("notifications:open", args=[n.pk]))
        self.assertRedirects(response, "/facilities/", fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_external_link_not_followed(self):
        n = Notification.objects.create(user=self.user, title="x", url="//evil.example/")
        response = self.client.post(reverse("notifications:open", args=[n.pk]))
        self.assertRedirects(response, reverse("notifications:list"))

    def test_cannot_open_someone_elses(self):
        n = Notification.objects.create(user=self.other, title="x")
        self.assertEqual(self.client.post(reverse("notifications:open", args=[n.pk])).status_code, 404)

    def test_bell_count_and_mark_all(self):
        for i in range(3):
            Notification.objects.create(user=self.user, title=f"n{i}")
        self.assertEqual(self.client.get(reverse("core:dashboard")).context["nav_unread_count"], 3)
        self.client.post(reverse("notifications:mark_all_read"))
        self.assertFalse(self.user.notifications.filter(read_at__isnull=True).exists())


class TriggerTests(TestCase):
    """Alerts raised by reservation and risk workflows."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        for u in User.objects.all():
            u.set_password(PASSWORD)
            u.save()

    def login(self, username):
        self.client.login(username=username, password=PASSWORD)

    def test_seed_alerts_owner_and_admins_about_high_risks(self):
        high = Risk.objects.filter(priority=Risk.Priority.HIGH).count()
        admin = User.objects.get(username="admin")
        self.assertEqual(admin.notifications.filter(level=Level.CRITICAL).count(), high)
        planner = User.objects.get(username="planner")
        self.assertTrue(planner.notifications.filter(title__startswith="High risk").exists())

    def test_rerun_does_not_repeat_high_alerts(self):
        before = Notification.objects.count()
        engine.run()
        self.assertEqual(Notification.objects.count(), before)

    def test_request_notifies_approvers_and_decision_notifies_requester(self):
        self.login("viewer")
        facility = Facility.objects.get(facility_code="MAIN-CONF")
        day = timezone.localdate() + datetime.timedelta(days=9)
        while day.weekday() == 6:
            day += datetime.timedelta(days=1)
        self.client.post(reverse("scheduling:reservation_create"), {
            "facility": facility.pk, "purpose": "Club meeting", "date": day.isoformat(),
            "start_time": "17:00", "end_time": "18:00", "expected_attendees": 10,
        })
        reservation = Reservation.objects.get(purpose="Club meeting")
        fac = User.objects.get(username="facilities")
        self.assertTrue(fac.notifications.filter(title__contains="New reservation request").exists())
        self.login("facilities")
        self.client.post(reverse("scheduling:reservation_approve", args=[reservation.pk]), {"remarks": "Enjoy"})
        viewer = User.objects.get(username="viewer")
        n = viewer.notifications.get(title__startswith="Reservation approved")
        self.assertIn("Enjoy", n.message)
        self.assertEqual(n.level, Level.SUCCESS)

    def test_assigning_action_notifies_assignee(self):
        risk = Risk.objects.get(category=Risk.Category.OVER_DEMAND)
        self.login("planner")
        registrar = User.objects.get(username="registrar")
        self.client.post(reverse("risks:add_action", args=[risk.pk]), {"action": "Add lab section", "assigned_to": registrar.pk})
        self.assertTrue(registrar.notifications.filter(title__startswith="New action assigned").exists())

    def test_overdue_reminder_once_per_day(self):
        risk = Risk.objects.get(category=Risk.Category.OVER_DEMAND)
        registrar = User.objects.get(username="registrar")
        MitigationAction.objects.create(risk=risk, action="Late task", assigned_to=registrar,
                                        due_date=timezone.localdate() - datetime.timedelta(days=2))
        self.assertEqual(engine.remind_overdue_actions(), 1)
        self.assertEqual(engine.remind_overdue_actions(), 0)
        self.assertIn("2 days ago", registrar.notifications.get(title__startswith="Overdue").message)
