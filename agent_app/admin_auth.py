"""
Admin paneli için özel authentication backend.

Kullanıcı iki şekilde giriş yapabilir:
  1) Django `auth.User` kaydı ile (kullanıcı adı VEYA e-posta)
  2) Uygulamanın kendi `users` tablosundaki şifresiyle
     (parola aynı kalır, hash'i `auth.User` ile eşitlenir)

Sadece yetkili hesap (settings.SUPERUSER_EMAIL / ADMIN_ALLOWED_USERNAMES) kabul edilir.
"""
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

logger = logging.getLogger(__name__)


class EmogAdminAuthBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not password:
            return None

        User = get_user_model()
        identifier = (username or kwargs.get("email") or "").strip()

        user = self._find_auth_user(identifier)

        if user is not None and user.check_password(password):
            if self.user_can_authenticate(user):
                return user
            return None

        return self._authenticate_with_app_user(identifier, password)

    def _find_auth_user(self, identifier):
        """Kullanıcı adı veya e-posta ile Django auth.User bulur."""
        User = get_user_model()

        user = User.objects.filter(username__iexact=identifier).first()
        if user is not None:
            return user

        if "@" in identifier:
            return User.objects.filter(email__iexact=identifier).first()

        return None

    def _authenticate_with_app_user(self, identifier, password):
        """Uygulamanın `users` tablosundaki parolayı kabul eder."""
        from agent_app.auth_views import verify_password
        from agent_app.models import User as AppUser
        from agent_app.security import is_authorized_identity

        User = get_user_model()

        if "@" in identifier:
            app_user = AppUser.objects.filter(email__iexact=identifier).first()
        else:
            app_user = AppUser.objects.filter(username__iexact=identifier).first()

        if app_user is None:
            return None

        if not is_authorized_identity(app_user.email, app_user.username):
            logger.warning("APP-LOGIN REJECTED (not authorized): %s", identifier)
            return None

        if not app_user.password_hash or not verify_password(password, app_user.password_hash):
            return None

        auth_user, created = User.objects.get_or_create(
            username=app_user.username,
            defaults={"email": app_user.email},
        )
        auth_user.email = app_user.email
        auth_user.set_password(password)
        auth_user.is_active = True
        auth_user.save()

        logger.info(
            "ADMIN LOGIN VIA APP USER - %s (created=%s)", auth_user.username, created
        )
        return auth_user if self.user_can_authenticate(auth_user) else None
