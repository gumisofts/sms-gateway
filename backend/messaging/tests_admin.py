"""Admin (django-unfold) smoke tests: dashboard, changelists, forms, actions."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from nodes.factories import make_node, make_user
from nodes.models import NodeSim

from .models import ApiKey, SmsMessage, Status
from .tests import make_message


class AdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            phone_number="900000000", first_name="Admin", password="pass12345"
        )
        self.client.force_login(self.admin)
        self.node = make_node(sims=((0, 3), (1, 4)), sms_limit_raised=False)
        self.offline = make_node(connected=False, device_name="Old phone")
        self.sim0, self.sim1 = self.node.sims.order_by("slot")
        self.sim1.paused_until = timezone.now() + timedelta(minutes=30)
        self.sim1.pause_reason = "5 failed messages in a row"
        self.sim1.save()
        owner = make_user()
        now = timezone.now()
        for status in (Status.DELIVERED, Status.SENT, Status.FAILED, Status.PENDING):
            make_message(
                owner, node=self.node, sim=self.sim0, status=status, dispatched_at=now
            )

    def get(self, name, *args):
        response = self.client.get(reverse(name, args=args))
        self.assertEqual(response.status_code, 200, name)
        return response

    def test_dashboard(self):
        response = self.get("admin:index")
        html = response.content.decode()
        for text in (
            "Sent today",
            "Android SMS limit not raised",
            "Paused SIMs",
            "Offline phones",
            "Old phone",
            "SIM lanes today",
            "Last 24 hours",
        ):
            self.assertIn(text, html)
        self.assertEqual(response.context["stats"]["sent"], 2)
        self.assertEqual(response.context["stats"]["failed"], 1)

    def test_dashboard_empty(self):
        SmsMessage.objects.all().delete()
        NodeSim.objects.all().delete()
        self.assertIn("No SIMs yet", self.get("admin:index").content.decode())

    def test_changelists_and_change_pages(self):
        message = SmsMessage.objects.first()
        _, _key = ApiKey.create_for(self.admin, "k")
        for name in (
            "admin:messaging_smsmessage_changelist",
            "admin:nodes_node_changelist",
            "admin:nodes_nodesim_changelist",
            "admin:messaging_apikey_changelist",
            "admin:accounts_user_changelist",
            "admin:auth_group_changelist",
            "admin:nodes_nodetoken_changelist",
            "admin:accounts_user_add",
            "admin:messaging_apikey_add",
            "admin:auth_group_add",
        ):
            self.get(name)
        self.get("admin:messaging_smsmessage_change", message.pk)
        self.get("admin:nodes_node_change", self.node.pk)
        self.get("admin:nodes_nodesim_change", self.sim0.pk)
        self.get("admin:accounts_user_change", self.admin.pk)
        self.get("admin:messaging_apikey_change", ApiKey.objects.first().pk)

    def test_sim_list_shows_usage(self):
        html = self.get("admin:nodes_nodesim_changelist").content.decode()
        self.assertIn("4 / 1000", html)  # 4 messages x 1 part, server default cap

    def test_create_api_key_shows_it_once(self):
        response = self.client.post(
            reverse("admin:messaging_apikey_add"),
            {"user": self.admin.pk, "name": "main"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        key = ApiKey.objects.get(name="main")
        shown = [str(m) for m in response.context["messages"]]
        self.assertTrue(any(key.prefix in m and "sgk_" in m for m in shown), shown)
        self.assertEqual(len(key.key_hash), 64)

    def test_create_user_in_admin(self):
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "911223344",
                "first_name": "Op",
                "password1": "a-Strong-pass-123",
                "password2": "a-Strong-pass-123",
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        user = get_user_model().objects.get(phone_number="911223344")
        self.assertEqual(user.username, "911223344")
        self.assertTrue(user.check_password("a-Strong-pass-123"))

    def test_resume_sim_action(self):
        response = self.client.post(
            reverse("admin:nodes_nodesim_changelist"),
            {"action": "resume_sims", "_selected_action": [self.sim1.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.sim1.refresh_from_db()
        self.assertFalse(self.sim1.is_paused())

    def test_retry_failed_message_action(self):
        failed = SmsMessage.objects.get(status=Status.FAILED)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("admin:messaging_smsmessage_changelist"),
                {"action": "retry_messages", "_selected_action": [failed.pk]},
            )
        failed.refresh_from_db()
        # Re-queued and dispatched again on a free SIM.
        self.assertIn(failed.status, (Status.PENDING, Status.DISPATCHED))
        self.assertEqual(failed.error_message, "")
