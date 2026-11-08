from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(DjangoUserManager):
    """Users log in with `phone_number`; the inherited `username` column is
    still unique, so it defaults to the phone number when not given."""

    def _create_user(self, phone_number, email, password, **extra_fields):
        if not phone_number:
            raise ValueError("The phone number must be set")
        extra_fields.setdefault("username", phone_number)
        email = self.normalize_email(email)
        user = self.model(phone_number=phone_number, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone_number=None, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone_number, email, password, **extra_fields)

    def create_superuser(
        self, phone_number=None, email=None, password=None, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(phone_number, email, password, **extra_fields)


class User(AbstractUser):
    objects = UserManager()

    def upload_to(self, filename):
        return filename

    profile_pic = models.ImageField(
        upload_to=upload_to,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(
                [
                    "jpg",
                    "png",
                    "jpeg",
                    "heic",
                ]
            )
        ],
    )
    phone_number = models.CharField(
        max_length=255,
        unique=True,
        validators=[
            RegexValidator(regex=r"^(7|9)\d{8}$"),
        ],
    )
    USERNAME_FIELD = "phone_number"

    REQUIRED_FIELDS = ["first_name"]
