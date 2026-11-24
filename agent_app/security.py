"""
EMOG güvenlik katmanı.

- Tek yetkili admin hesabı: settings.SUPERUSER_EMAIL / settings.ADMIN_ALLOWED_USERNAMES
- Gizli admin URL: settings.ADMIN_URL
- Zorunlu TOTP (2FA) doğrulaması admin girişinde
- Sadece yetkili hesap admin paneline erişebilir; diğer herkes 403 alır
"""
import logging

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

logger = logging.getLogger(__name__)


def authorized_admin_email():
    return (getattr(settings, "SUPERUSER_EMAIL", "sefaspro@gmail.com") or "").strip().lower()


def authorized_admin_usernames():
    """Admin paneline girebilecek kullanıcı adları (küçük harf)."""
    raw = getattr(settings, "ADMIN_ALLOWED_USERNAMES", None)
    if raw is None:
        raw = "whoches"
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def admin_url_prefix():
    return "/" + getattr(settings, "ADMIN_URL", "management-portal-v1/").strip("/") + "/"


def is_authorized_identity(email=None, username=None):
    """E-posta veya kullanıcı adı yetkili admin hesabına ait mi?

    Uygulamanın kendi `users` tablosu ile Django admin'in `auth.User`
    tablosu farklı olduğu için frontend de bu kontrolü kullanır.
    """
    email_ok = (email or "").strip().lower() == authorized_admin_email()
    username_ok = (username or "").strip().lower() in authorized_admin_usernames()
    return bool(email_ok or username_ok)


def is_authorized_admin(user):
    """Sadece yetkili hesap admin paneline girebilir."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    if not user.is_superuser or not user.is_staff:
        return False

    return is_authorized_identity(
        getattr(user, "email", None),
        user.get_username() if hasattr(user, "get_username") else None,
    )




def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def deny(request):
    return HttpResponseForbidden(
        "<!doctype html><html><head><meta charset='utf-8'><title>403</title></head>"
        "<body style=\"font-family:system-ui,-apple-system,sans-serif;background:#0a0f1a;"
        "color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0\">"
        "<div style='text-align:center'>"
        "<h1 style='color:#ef4444;font-size:48px;margin:0 0 8px'>403</h1>"
        "<p style='color:#94a3b8'>Bu alana erişim yetkiniz yok.</p>"
        "<p style='color:#64748b;font-size:13px'>Erişim denemeleri kayıt altına alınmıştır.</p>"
        "</div></body></html>",
        content_type="text/html",
    )


# ============================================================
# ADMIN LOGIN FORM (TOTP zorunlu)
# ============================================================

def otp_enabled():
    """2FA zorunlu mu? (settings.ADMIN_REQUIRE_OTP)"""
    return bool(getattr(settings, "ADMIN_REQUIRE_OTP", False))


class OTPAdminAuthenticationForm(AdminAuthenticationForm):
    """Admin giriş formu; 2FA açıksa doğrulama kodunu zorunlu kılar."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not otp_enabled():
            return

        from django import forms

        self.fields["otp_token"] = forms.CharField(
            label=_("Doğrulama kodu (2FA)"),
            max_length=16,
            required=False,
            widget=forms.TextInput(
                attrs={
                    "autofocus": True,
                    "autocomplete": "one-time-code",
                    "inputmode": "numeric",
                    "size": 12,
                }
            ),
        )

    def clean(self):
        super().clean()

        if not otp_enabled():
            return self.cleaned_data

        # AdminAuthenticationForm.clean() doğrulanmış kullanıcıyı user_cache'e koyar
        user = getattr(self, "user_cache", None)
        if user is None:
            return self.cleaned_data

        user_devices = list(devices_for_user(user, confirmed=True))
        token = (self.cleaned_data.get("otp_token") or "").strip()

        if not user_devices:
            raise ValidationError(
                "Bu hesap için 2FA cihazı kayıtlı değil. "
                "Yönetici ile: python manage.py admin_totp enroll"
            )

        if not token:
            raise ValidationError("Doğrulama kodu zorunludur.")

        if not any(device.verify_token(token) for device in user_devices):
            logger.warning("INVALID OTP - User: %s", getattr(user, "email", "?"))
            raise ValidationError("Doğrulama kodu hatalı veya süresi dolmuş.")

        self.otp_verified = True
        return self.cleaned_data


# ============================================================
# ADMIN SITE
# ============================================================

class RestrictedAdminSite(UnfoldAdminSite):
    """Unfold tabanlı, yalnızca yetkili e-postaya açık admin site."""

    site_header = "EMOG Yönetim"
    site_title = "EMOG Yönetim"
    index_title = "Yönetim Paneli"

    def __init__(self, name="admin"):
        super().__init__(name)
        self.login_form = OTPAdminAuthenticationForm

    def has_permission(self, request):
        return is_authorized_admin(request.user)

    def get_app_list(self, request, app_label=None):
        if not self.has_permission(request):
            return []
        return super().get_app_list(request, app_label)

    def get_urls(self):
        from django.urls import path

        from audit.admin_views import dashboard_view, features_view

        custom = [
            path("", self.admin_view(dashboard_view), name="index"),
            path("dashboard/", self.admin_view(dashboard_view), name="dashboard"),
            path("features/", self.admin_view(features_view), name="features"),
        ]
        return custom + super().get_urls()

    def index(self, request, extra_context=None):
        from audit.admin_views import dashboard_view

        return dashboard_view(request, extra_context)

    def login(self, request, extra_context=None):
        response = super().login(request, extra_context)
        user = getattr(request, "user", None)
        if getattr(response, "status_code", 0) in (301, 302) and is_authorized_admin(user):
            logger.info("ADMIN LOGIN OK - User: %s | IP: %s", user.email, client_ip(request))
        return response


# ============================================================
# MIDDLEWARE
# ============================================================

class SuperuserOnlyAdminMiddleware:
    """
    Admin URL'sine erişimi yalnızca yetkili superuser'a açar.

    - Anonim kullanıcılar SADECE admin login/logout sayfasına ulaşabilir.
    - Giriş yapmış yetkisiz kullanıcılar 403 alır.
    - Uygulamanın diğer URL'leri etkilenmez.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_prefix = admin_url_prefix()

    def __call__(self, request):
        path = request.path

        if not path.startswith(self.admin_prefix):
            return self.get_response(request)

        if path.startswith(("/static/", "/media/")):
            return self.get_response(request)

        if path in (self.admin_prefix + "login/", self.admin_prefix + "logout/"):
            return self.get_response(request)

        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            # Anonim istekler admin login sayfasına yönlendirilir (Django davranışı)
            return self.get_response(request)

        if not is_authorized_admin(user):
            logger.warning(
                "UNAUTHORIZED ADMIN ACCESS - User: %s | IP: %s | Path: %s",
                user.email,
                client_ip(request),
                path,
            )
            return deny(request)

        return self.get_response(request)


# ============================================================
# TEK SUPERUSER SİNYALLERİ
# ============================================================

def is_authorized_account(user):
    """Kullanıcı adı veya e-posta ile yetkili admin hesabı mı?"""
    return is_authorized_identity(
        getattr(user, "email", None),
        user.get_username() if hasattr(user, "get_username") else None,
    )


def enforce_single_superuser(sender, instance, **kwargs):
    """Sadece yetkili hesap superuser olabilir; diğerleri otomatik düşürülür."""
    if is_authorized_account(instance):
        instance.is_superuser = True
        instance.is_staff = True
        return

    if instance.is_superuser or instance.is_staff:
        logger.warning(
            "BLOCKED ADMIN PRIVILEGES - User: %s / %s (ID: %s)",
            instance.get_username(),
            instance.email,
            instance.pk,
        )
        instance.is_superuser = False
        instance.is_staff = False


def prevent_multiple_superusers():
    """Yetkisiz superuser/staff hesapları düşürür, yetkili hesabı yükseltir."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    demoted = 0

    unauthorized = User.objects.filter(is_superuser=True) | User.objects.filter(is_staff=True)
    for user in unauthorized.distinct():
        if is_authorized_account(user):
            User.objects.filter(pk=user.pk).update(is_superuser=True, is_staff=True)
            continue
        User.objects.filter(pk=user.pk).update(is_superuser=False, is_staff=False)
        logger.warning("DEMOTED UNAUTHORIZED ADMIN - %s / %s", user.get_username(), user.email)
        demoted += 1

    return demoted
