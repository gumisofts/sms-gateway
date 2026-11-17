"""Contacts: private to their owner in the admin and the API."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth import roles
from nodes.factories import make_node, make_user

from . import contacts
from .models import ApiKey, Contact, ContactGroup, SmsBatch, SmsMessage
from .tests_bulk import staff


def make_contact(owner, name, phone, *groups):
    contact = Contact.objects.create(owner=owner, name=name, phone=phone)
    contact.groups.set(groups)
    return contact


class ContactApiTests(TestCase):
    def setUp(self):
        make_node()
        self.user, self.other = make_user(), make_user()
        _, self.key = ApiKey.create_for(self.user, "k")
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.key)
        self.group = ContactGroup.objects.create(owner=self.user, name="VIP")
        self.abebe = make_contact(self.user, "Abebe", "+251912345678", self.group)
        self.sara = make_contact(self.user, "Sara", "+251911111111")
        self.their_group = ContactGroup.objects.create(owner=self.other, name="Theirs")
        self.theirs = make_contact(
            self.other, "Other", "+251922222222", self.their_group
        )

    def test_crud_is_scoped_to_the_owner(self):
        listing = self.client.get("/api/contacts/").json()
        self.assertEqual(
            {c["id"] for c in listing["results"]}, {self.abebe.pk, self.sara.pk}
        )
        self.assertEqual(
            self.client.get(f"/api/contacts/{self.theirs.pk}/").status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"/api/contacts/{self.theirs.pk}/").status_code, 404
        )
        groups = self.client.get("/api/contact-groups/").json()["results"]
        self.assertEqual([(g["name"], g["contacts"]) for g in groups], [("VIP", 1)])

        created = self.client.post(
            "/api/contacts/",
            {"name": "Kebede", "phone": "0933333333", "groups": [self.group.pk]},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["phone"], "+251933333333")
        self.assertEqual(Contact.objects.get(name="Kebede").owner, self.user)

    def test_staff_do_not_see_other_users_contacts(self):
        admin = make_user(staff=True, is_superuser=True)
        client = APIClient()
        client.force_authenticate(admin)
        self.assertEqual(client.get("/api/contacts/").json()["results"], [])
        response = client.post(
            "/api/contacts/send/",
            {"body": "Hi", "contacts": [self.abebe.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_validation(self):
        duplicate = self.client.post(
            "/api/contacts/", {"name": "Again", "phone": "0912345678"}, format="json"
        )
        self.assertEqual(duplicate.status_code, 400)
        # The same number is fine for another user.
        self.assertEqual(
            self.client.post(
                "/api/contacts/",
                {"name": "Same", "phone": "+251922222222"},
                format="json",
            ).status_code,
            201,
        )
        foreign_group = self.client.post(
            "/api/contacts/",
            {"name": "X", "phone": "0944444444", "groups": [self.their_group.pk]},
            format="json",
        )
        self.assertEqual(foreign_group.status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/contact-groups/", {"name": "VIP"}, format="json"
            ).status_code,
            400,
        )
        # Renaming to its own name is not a duplicate.
        self.assertEqual(
            self.client.patch(
                f"/api/contact-groups/{self.group.pk}/", {"name": "VIP"}, format="json"
            ).status_code,
            200,
        )

    def test_send_to_contacts_and_groups(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/contacts/send/",
                {
                    "body": "Hello {name}",
                    "contacts": [self.abebe.pk, self.sara.pk],
                    "groups": [self.group.pk],
                    "name": "Promo",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual((result["total"], result["skipped"]), (2, 0))
        batch = SmsBatch.objects.get(pk=result["batch"])
        self.assertEqual((batch.created_by, batch.name), (self.user, "Promo"))
        self.assertEqual(
            sorted(batch.messages.values_list("to", "body")),
            [("+251911111111", "Hello Sara"), ("+251912345678", "Hello Abebe")],
        )
        self.assertTrue(all(m.owner == self.user for m in batch.messages.all()))
        listed = self.client.get(f"/api/messages/?batch={batch.pk}").json()
        self.assertEqual(listed["count"], 2)

    def test_cannot_send_to_someone_elses_contacts(self):
        for payload in (
            {"contacts": [self.abebe.pk, self.theirs.pk]},
            {"groups": [self.their_group.pk]},
            {},
        ):
            response = self.client.post(
                "/api/contacts/send/", {"body": "Hi", **payload}, format="json"
            )
            self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(SmsMessage.objects.count(), 0)

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get("/api/contacts/").status_code, 401)


class ContactImportTests(TestCase):
    def setUp(self):
        self.user, self.other = make_user(), make_user()
        self.group = ContactGroup.objects.create(owner=self.user, name="VIP")
        self.abebe = make_contact(self.user, "Abebe", "+251912345678")
        make_contact(self.other, "Other", "+251911111111")

    def test_header_csv(self):
        data = (
            "Name;Mobile\nAbebe K;0912345678\nSara;0911111111\n"
            "Sara again;911111111\nBad;12\n;0933333333\n"
        ).encode("utf-8-sig")
        result = contacts.import_contacts(
            self.user, contacts.rows_from_bytes(data), [self.group]
        )
        self.assertEqual(
            (result.created, result.updated, result.existing, result.duplicates),
            (2, 0, 1, 1),
        )
        self.assertEqual(result.invalid, ["12"])
        mine = dict(
            Contact.objects.filter(owner=self.user).values_list("phone", "name")
        )
        self.assertEqual(
            mine,
            {
                "+251912345678": "Abebe",  # kept its name
                "+251911111111": "Sara",  # same number as another user's contact
                "+251933333333": "+251933333333",  # no name given
            },
        )
        self.assertEqual(self.group.contacts.count(), 3)
        self.assertEqual(Contact.objects.filter(owner=self.other).count(), 1)

    def test_headerless_either_order_and_rename(self):
        data = "0912345678,Abebe Kebede\nSara,0911111111\n0922222222\n".encode()
        rows = contacts.rows_from_bytes(data)
        self.assertEqual(
            [(r["phone"], r["name"]) for r in rows][:1],
            [("0912345678", "Abebe Kebede")],
        )
        result = contacts.import_contacts(self.user, rows, update_existing=True)
        self.assertEqual((result.created, result.updated), (2, 1))
        self.abebe.refresh_from_db()
        self.assertEqual(self.abebe.name, "Abebe Kebede")
        # Importing the same file again changes nothing.
        again = contacts.import_contacts(self.user, rows, update_existing=True)
        self.assertEqual((again.created, again.updated, again.existing), (0, 0, 3))

    def test_first_and_last_name_columns(self):
        data = b"first_name,last_name,phone\nSara,Tesfaye,0911111111\n"
        contacts.import_contacts(self.user, contacts.rows_from_bytes(data))
        self.assertEqual(
            Contact.objects.get(owner=self.user, phone="+251911111111").name,
            "Sara Tesfaye",
        )

    def test_api_json_and_csv(self):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=ApiKey.create_for(self.user, "k")[1])
        response = client.post(
            "/api/contacts/import/",
            {
                "contacts": [
                    {"name": "Sara", "phone": "0911111111"},
                    {"phone": "nope"},
                    {"name": "New name", "phone": "0912345678"},
                ],
                "groups": [self.group.pk],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {
                "created": 1,
                "updated": 0,
                "existing": 1,
                "duplicates": 0,
                "invalid": ["nope"],
            },
        )
        self.assertEqual(self.group.contacts.count(), 2)

        upload = SimpleUploadedFile("c.csv", b"name,phone\nKebede,0933333333\n")
        response = client.post(
            "/api/contacts/import/",
            {"file": upload, "update_existing": "true"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["created"], 1)

        their_group = ContactGroup.objects.create(owner=self.other, name="Theirs")
        for payload in (
            {},
            {"contacts": [{"phone": "0944444444"}], "groups": [their_group.pk]},
        ):
            self.assertEqual(
                client.post(
                    "/api/contacts/import/", payload, format="json"
                ).status_code,
                400,
            )
        self.assertFalse(their_group.contacts.exists())


class ContactAdminTests(TestCase):
    def setUp(self):
        roles.sync_roles()
        make_node()
        self.operator = staff("911000001", roles.OPERATOR)
        self.other = staff("911000002", roles.OPERATOR)
        self.client.force_login(self.operator)
        self.group = ContactGroup.objects.create(owner=self.operator, name="VIP")
        self.abebe = make_contact(self.operator, "Abebe", "+251912345678", self.group)
        self.sara = make_contact(self.operator, "Sara", "+251911111111")
        self.their_group = ContactGroup.objects.create(owner=self.other, name="Theirs")
        self.theirs = make_contact(
            self.other, "Other", "+251922222222", self.their_group
        )

    def post_bulk(self, **data):
        payload = {
            "name": "Promo",
            "body": "Hello {name}",
            "recipients": "",
            "valid_hours": 24,
            "callback_url": "",
            "sim_slot": "",
            "requested_node": "",
            "confirm": "on",
        }
        payload.update(data)
        return self.client.post(reverse("admin:messaging_smsbatch_add"), payload)

    def test_lists_show_only_own_rows(self):
        response = self.client.get(reverse("admin:messaging_contact_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.context["cl"].result_list), {self.abebe, self.sara}
        )
        groups = self.client.get(reverse("admin:messaging_contactgroup_changelist"))
        self.assertEqual(list(groups.context["cl"].result_list), [self.group])
        self.assertEqual(
            self.client.get(
                reverse("admin:messaging_contact_change", args=[self.theirs.pk])
            ).status_code,
            302,
        )

    def test_superuser_sees_only_own_contacts(self):
        root = make_user(staff=True, is_superuser=True)
        self.client.force_login(root)
        response = self.client.get(reverse("admin:messaging_contact_changelist"))
        self.assertEqual(list(response.context["cl"].result_list), [])

    def test_add_contact(self):
        url = reverse("admin:messaging_contact_add")
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Theirs")
        response = self.client.post(
            url, {"name": "Kebede", "phone": "0933333333", "groups": [self.group.pk]}
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        contact = Contact.objects.get(name="Kebede")
        self.assertEqual(
            (contact.owner, contact.phone), (self.operator, "+251933333333")
        )
        duplicate = self.client.post(url, {"name": "Again", "phone": "912345678"})
        self.assertContains(duplicate, "already have a contact")
        foreign = self.client.post(
            url, {"name": "X", "phone": "0944444444", "groups": [self.their_group.pk]}
        )
        self.assertEqual(foreign.status_code, 200)
        self.assertFalse(Contact.objects.filter(name="X").exists())

    def test_send_action_preselects_contacts(self):
        response = self.client.post(
            reverse("admin:messaging_contact_changelist"),
            {"action": "send_sms", "_selected_action": [self.abebe.pk, self.sara.pk]},
        )
        self.assertEqual(response.status_code, 302)
        form_page = self.client.get(response["Location"])
        self.assertEqual(form_page.status_code, 200)
        initial = form_page.context["adminform"].form.initial["contacts"]
        self.assertEqual(set(initial), {self.abebe.pk, self.sara.pk})

    def test_group_action_preselects_groups(self):
        response = self.client.post(
            reverse("admin:messaging_contactgroup_changelist"),
            {"action": "send_sms", "_selected_action": [self.group.pk]},
        )
        self.assertEqual(response.status_code, 302)
        form_page = self.client.get(response["Location"])
        self.assertEqual(
            form_page.context["adminform"].form.initial["contact_groups"],
            [str(self.group.pk)],
        )

    def test_bulk_send_to_contacts_groups_and_numbers(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post_bulk(
                contacts=[self.sara.pk],
                contact_groups=[self.group.pk],
                recipients="0912345678\n0955555555",
            )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        batch = SmsBatch.objects.get()
        self.assertEqual((batch.total, batch.skipped), (3, 1))
        self.assertEqual(
            sorted(batch.messages.values_list("to", "body")),
            [
                ("+251911111111", "Hello Sara"),
                ("+251912345678", "Hello Abebe"),
                ("+251955555555", "Hello {name}"),
            ],
        )

    def test_bulk_send_rejects_other_users_contacts(self):
        for data in (
            {"contacts": [self.theirs.pk]},
            {"contact_groups": [self.their_group.pk]},
        ):
            response = self.post_bulk(**data)
            self.assertEqual(response.status_code, 200, data)
        self.assertEqual(SmsBatch.objects.count(), 0)

    def test_import_page(self):
        url = reverse("admin:messaging_contact_import_contacts")
        listing = self.client.get(reverse("admin:messaging_contact_changelist"))
        self.assertContains(listing, url)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Import contacts")
        self.assertNotContains(page, "Theirs")

        empty = self.client.post(url, {})
        self.assertContains(empty, "Choose a CSV file or paste")

        response = self.client.post(
            url,
            {
                "csv_file": SimpleUploadedFile(
                    "c.csv", b"name,phone\nKebede,0933333333\nAbebe,0912345678\n"
                ),
                "pasted": "Hana, 0944444444\n0955555555",
                "groups": [self.group.pk],
            },
            follow=True,
        )
        self.assertContains(response, "3 contact(s) added")
        self.assertContains(response, "1 already in your contacts")
        self.assertEqual(Contact.objects.filter(owner=self.operator).count(), 5)
        self.assertEqual(self.group.contacts.count(), 4)
        self.assertEqual(Contact.objects.filter(owner=self.other).count(), 1)

        foreign = self.client.post(
            url, {"pasted": "0966666666", "groups": [self.their_group.pk]}
        )
        self.assertEqual(foreign.status_code, 200)
        self.assertFalse(Contact.objects.filter(phone="+251966666666").exists())

    def test_viewer_has_no_contacts(self):
        self.client.force_login(staff("911000003", roles.VIEWER))
        self.assertEqual(
            self.client.get(reverse("admin:messaging_contact_changelist")).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("admin:messaging_contact_import_contacts")
            ).status_code,
            403,
        )
