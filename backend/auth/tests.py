import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


class UserManagerTests(TestCase):
    def test_create_user_defaults_username_to_phone_number(self):
        user = get_user_model().objects.create_user(
            phone_number="911223344", password="pass12345", first_name="A"
        )
        self.assertEqual(user.username, "911223344")
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password("pass12345"))

    def test_createsuperuser_noinput(self):
        with mock.patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": "S3cret-pass!"}):
            call_command(
                "createsuperuser",
                interactive=False,
                phone_number="900000000",
                first_name="Murad",
                verbosity=0,
            )
        user = get_user_model().objects.get(phone_number="900000000")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.username, "900000000")
        self.assertTrue(user.check_password("S3cret-pass!"))
