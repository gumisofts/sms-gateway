from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .utils import normalize_phone_number

User = get_user_model()


class PhoneBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user)

    def authenticate(
        self, request, username=None, phone_number=None, password=None, **kwargs
    ):

        if username is not None:
            phone_number = username
        if phone_number is None:
            return None

        user = User.objects.filter(
            phone_number=normalize_phone_number(phone_number)
        ).first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user


class EmailBackend(ModelBackend):
    def user_can_authenticate(self, user):
        return super().user_can_authenticate(user)

    def authenticate(self, request, username=None, email=None, password=None, **kwargs):
        if username is not None:
            email = username
        if email is None:
            return None

        user = User.objects.filter(email=email).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
