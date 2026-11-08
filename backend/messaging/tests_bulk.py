"""Admin bulk send and staff roles."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auth import roles
from nodes.factories import make_node, make_user

from . import bulk, services
from .models import SmsBatch, SmsMessage, Status
from .tests import make_message

User = get_user_model()


class NormalizeTests(TestCase):
    def test_local_and_international_forms(self):
        for raw in (
            "0912345678",
            "912345678",
            "251912345678",
            "+251912345678",
            "00251912345678",
            "+251 91 234-5678",
        ):
            self.assertEqual(bulk.normalize_number(raw), "+251912345678", raw)
        self.assertEqual(bulk.normalize_number("+15551234567"), "+15551234567")

    def test_invalid(self):
        for raw in ("", "abc", "12", "+0123456789", "09-12-ab"):
            self.assertIsNone(bulk.normalize_number(raw), raw)


class PlanTests(TestCase):
    def test_pasted_list_dedupes_and_reports_invalid(self):
        plan = bulk.build_plan("Hi", "0912345678\n912345678, 0911111111;bad\n\n")
        self.assertEqual(
            [r.to for r in plan.recipients], ["+251912345678", "+251911111111"]
        )
        self.assertEqual(plan.duplicates, 1)
        self.assertEqual(plan.invalid, ["bad"])
        self.assertEqual(plan.skipped, 2)

    def test_csv_with_header_placeholders_and_message_column(self):
        data = (
            "name,phone,message\n"
            "Abebe,0912345678,\n"
            "Sara,0911111111,Custom text\n"
            ",,\n"
        ).encode()
        plan = bulk.build_plan("Hello {name}, code {code}", csv_data=data)
        self.assertEqual(
            [(r.to, r.body) for r in plan.recipients],
            [
                ("+251912345678", "Hello Abebe, code {code}"),
                ("+251911111111", "Custom text"),
            ],
        )
        self.assertEqual(plan.missing_placeholders, {"code"})

    def test_csv_without_header_semicolon_and_bom(self):
        data = "﻿0912345678;x\n0911111111;y\n".encode("utf-8")
        plan = bulk.build_plan("Hi", csv_data=data)
        self.assertEqual(len(plan.recipients), 2)

    def test_amharic_parts(self):
        plan = bulk.build_plan("ሰ" * 100, "0912345678")
        self.assertEqual(plan.recipients[0].parts, 2)
        self.assertEqual(plan.total_parts, 2)

    def test_create_batch(self):
        user = make_user()
        make_node()  # online: the first ones dispatch right away
        plan = bulk.build_plan(
            "Hi {name}", csv_data=b"phone,name\n0912345678,A\n0911111111,B\n"
        )
        batch = SmsBatch(name="t", body="Hi {name}", created_by=user, valid_hours=2)
        with self.captureOnCommitCallbacks(execute=True):
            bulk.create_batch(batch, plan)
        self.assertEqual(batch.total, 2)
        messages = SmsMessage.objects.filter(batch=batch)
        self.assertEqual(sorted(m.body for m in messages), ["Hi A", "Hi B"])
        self.assertTrue(all(m.owner == user for m in messages))
        self.assertTrue(
            all(
                abs((m.expires_at - batch.created_at) - timedelta(hours=2))
                < timedelta(seconds=1)
                for m in messages
            )
        )
        self.assertTrue(messages.filter(status=Status.DISPATCHED).exists())


class ExpiryTests(TestCase):
    def test_expires_at_overrides_global_ttl(self):
        owner = make_user()
        long_lived = make_message(owner, expires_at=timezone.now() + timedelta(hours=5))
        SmsMessage.objects.filter(pk=long_lived.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        due = make_message(owner, expires_at=timezone.now() - timedelta(seconds=1))
        old_default = make_message(owner)
        SmsMessage.objects.filter(pk=old_default.pk).update(
            created_at=timezone.now() - timedelta(hours=3)
        )
        self.assertEqual(services.expire_pending(), 2)
        statuses = {
            m.pk: m.status
            for m in SmsMessage.objects.filter(
                pk__in=[long_lived.pk, due.pk, old_default.pk]
            )
        }
        self.assertEqual(statuses[long_lived.pk], Status.PENDING)
        self.assertEqual(statuses[due.pk], Status.EXPIRED)
        self.assertEqual(statuses[old_default.pk], Status.EXPIRED)


def staff(phone, role):
    user = User.objects.create_user(phone_number=phone, first_name="x", password="p")
    roles.set_role(user, role)
    return User.objects.get(pk=user.pk)  # fresh permission cache


class BulkSendAdminTests(TestCase):
    url = reverse("admin:messaging_smsbatch_add")

    def setUp(self):
        roles.sync_roles()
        self.operator = staff("911000001", roles.OPERATOR)
        self.client.force_login(self.operator)
        make_node()

    def post(self, **data):
        payload = {
            "name": "Promo",
            "body": "Hello {name}",
            "recipients": "",
            "valid_hours": 24,
            "callback_url": "",
            "sim_slot": "",
            "requested_node": "",
        }
        payload.update(data)
        return self.client.post(self.url, payload)

    def test_form_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm and send")

    def test_preview_then_confirm_with_csv(self):
        csv_file = SimpleUploadedFile(
            "list.csv", b"phone,name\n0912345678,Abebe\n0911111111,Sara\nbad,X\n"
        )
        response = self.post(csv_file=csv_file)
        self.assertEqual(response.status_code, 200)  # preview, not saved
        self.assertContains(response, "2 recipients")
        self.assertContains(response, "1 invalid number")
        self.assertEqual(SmsBatch.objects.count(), 0)
        cache = response.context["adminform"].form.data["csv_cache"]
        self.assertTrue(cache)

        # Second submit: the browser does not resend the file, the cache does.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(csv_cache=cache, confirm="on")
        self.assertEqual(response.status_code, 302)
        batch = SmsBatch.objects.get()
        self.assertEqual((batch.total, batch.skipped), (2, 1))
        self.assertEqual(batch.created_by, self.operator)
        self.assertEqual(
            sorted(batch.messages.values_list("body", flat=True)),
            ["Hello Abebe", "Hello Sara"],
        )

        detail = self.client.get(
            reverse("admin:messaging_smsbatch_change", args=[batch.pk])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "View messages")

    def test_no_valid_recipients(self):
        response = self.post(recipients="nope", confirm="on")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No valid recipients")
        self.assertEqual(SmsBatch.objects.count(), 0)

    def test_cancel_waiting(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(recipients="0912345678\n0911111111\n0922222222", confirm="on")
        batch = SmsBatch.objects.get()
        self.client.post(
            reverse("admin:messaging_smsbatch_changelist"),
            {"action": "cancel_waiting", "_selected_action": [batch.pk]},
        )
        self.assertFalse(
            batch.messages.filter(
                status__in=[Status.PENDING, Status.DISPATCHED]
            ).exists()
        )
        self.assertEqual(batch.messages.filter(status=Status.CANCELLED).count(), 3)


class RoleTests(TestCase):
    def setUp(self):
        roles.sync_roles()

    def test_groups_are_created_with_permissions(self):
        self.assertEqual(
            set(
                Group.objects.filter(name__in=roles.ROLES).values_list(
                    "name", flat=True
                )
            ),
            set(roles.ROLES),
        )
        operator = staff("911000001", roles.OPERATOR)
        self.assertTrue(operator.is_staff)
        self.assertTrue(operator.has_perm("messaging.add_smsbatch"))
        self.assertFalse(operator.has_perm("messaging.view_all_messages"))
        self.assertFalse(operator.has_perm("nodes.change_nodesim"))
        viewer = staff("911000002", roles.VIEWER)
        self.assertFalse(viewer.has_perm("messaging.add_smsbatch"))
        self.assertTrue(viewer.has_perm("messaging.view_all_messages"))
        admin = staff("911000003", roles.GATEWAY_ADMIN)
        self.assertTrue(admin.has_perm("nodes.change_nodesim"))
        self.assertTrue(admin.has_perm("accounts.change_user"))

    def test_sync_is_idempotent(self):
        roles.sync_roles()
        roles.sync_roles()
        self.assertEqual(Group.objects.filter(name__in=roles.ROLES).count(), 3)

    def test_removing_role_removes_admin_access(self):
        user = staff("911000001", roles.OPERATOR)
        roles.set_role(user, None)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertEqual(roles.role_of(user), "None")

    def test_operator_sees_only_own_messages(self):
        operator = staff("911000001", roles.OPERATOR)
        mine = make_message(operator)
        theirs = make_message(make_user())
        self.client.force_login(operator)
        response = self.client.get(reverse("admin:messaging_smsmessage_changelist"))
        self.assertEqual(response.status_code, 200)
        ids = {m.pk for m in response.context["cl"].result_list}
        self.assertEqual(ids, {mine.pk})
        self.assertEqual(
            self.client.get(
                reverse("admin:messaging_smsmessage_change", args=[theirs.pk])
            ).status_code,
            302,  # not in its queryset: redirected to the list
        )
        # Dashboard is scoped too.
        dash = self.client.get(reverse("admin:index"))
        self.assertTrue(dash.context["scoped"])

    def test_operator_cannot_change_sims_or_users(self):
        operator = staff("911000001", roles.OPERATOR)
        node = make_node()
        self.client.force_login(operator)
        sims = self.client.get(reverse("admin:nodes_nodesim_changelist"))
        self.assertEqual(sims.status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:accounts_user_changelist")).status_code, 403
        )
        self.client.post(
            reverse("admin:nodes_nodesim_changelist"),
            {"action": "disable_sims", "_selected_action": [node.sims.get().pk]},
        )
        self.assertTrue(node.sims.get().is_enabled)

    def test_viewer_cannot_bulk_send(self):
        viewer = staff("911000002", roles.VIEWER)
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(reverse("admin:messaging_smsbatch_add")).status_code, 403
        )
        dash = self.client.get(reverse("admin:index"))
        self.assertEqual(dash.status_code, 200)
        self.assertFalse(dash.context["scoped"])
        self.assertNotContains(dash, reverse("admin:messaging_smsbatch_add"))

    def test_gateway_admin_cannot_touch_superusers(self):
        root = User.objects.create_superuser(
            phone_number="900000000", first_name="r", password="p"
        )
        admin = staff("911000003", roles.GATEWAY_ADMIN)
        self.client.force_login(admin)
        listing = self.client.get(reverse("admin:accounts_user_changelist"))
        self.assertNotIn(root, listing.context["cl"].result_list)
        password_url = reverse("admin:auth_user_password_change", args=[root.pk])
        self.assertIn(self.client.get(password_url).status_code, (302, 403, 404))
        self.assertTrue(root.check_password("p"))

    def test_admin_creates_user_with_role(self):
        admin = staff("911000003", roles.GATEWAY_ADMIN)
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "phone_number": "922334455",
                "first_name": "New",
                "role": roles.OPERATOR,
                "password1": "a-Strong-pass-123",
                "password2": "a-Strong-pass-123",
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:1500])
        user = User.objects.get(phone_number="922334455")
        self.assertTrue(user.is_staff)
        self.assertEqual(roles.role_of(user), roles.OPERATOR)

    def test_change_role_on_user_form(self):
        target = staff("922334455", roles.VIEWER)
        root = User.objects.create_superuser(
            phone_number="900000000", first_name="r", password="p"
        )
        self.client.force_login(root)
        page = self.client.get(reverse("admin:accounts_user_change", args=[target.pk]))
        self.assertEqual(page.status_code, 200)
        form = page.context["adminform"].form
        self.assertEqual(form.fields["role"].initial, roles.VIEWER)
        response = self.client.post(
            reverse("admin:accounts_user_change", args=[target.pk]),
            {
                "phone_number": target.phone_number,
                "first_name": "x",
                "last_name": "",
                "email": "",
                "role": roles.GATEWAY_ADMIN,
                "is_active": "on",
                "last_login_0": "",
                "last_login_1": "",
                "date_joined_0": "2026-01-01",
                "date_joined_1": "00:00:00",
            },
        )
        self.assertIn(response.status_code, (302,), response.content[:3000])
        self.assertEqual(
            roles.role_of(User.objects.get(pk=target.pk)), roles.GATEWAY_ADMIN
        )
