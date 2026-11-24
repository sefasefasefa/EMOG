"""
EMOG güvenlik sinyalleri.

- Sadece settings.SUPERUSER_EMAIL superuser olabilir
- Yetkili admin hesabı silinemez
- Giriş/çıkış ve yetki değişiklikleri denetim kaydına yazılır
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def authorized_email():
    return (getattr(settings, "SUPERUSER_EMAIL", "sefaspro@gmail.com") or "").strip().lower()


def _is_authorized(instance):
    return (instance.email or "").strip().lower() == authorized_email()


@receiver(pre_save, sender=get_user_model())
def enforce_single_superuser(sender, instance, **kwargs):
    """Yalnızca yetkili e-posta superuser olabilir."""
    if _is_authorized(instance):
        instance.is_superuser = True
        instance.is_staff = True
        return

    if instance.is_superuser:
        logger.warning(
            "BLOCKED SUPERUSER PROMOTION - User: %s (ID: %s)", instance.email, instance.pk
        )
        instance.is_superuser = False
        instance.is_staff = False


@receiver(post_save, sender=get_user_model())
def log_user_created(sender, instance, created, **kwargs):
    """Yeni kullanıcı oluşturulduğunda denetim kaydı yazar."""
    if not created:
        return
    try:
        from audit.models import AuditLog

        AuditLog.objects.create(
            user=instance,
            user_email=instance.email,
            action=AuditLog.Action.ROLE_CHANGE,
            model_name="user",
            object_id=str(instance.pk),
            object_repr=instance.email,
            ip_address=None,
            request_path="/system/",
        )
    except Exception as exc:
        logger.warning("Audit log yazılamadı: %s", exc)


@receiver(pre_delete, sender=get_user_model())
def prevent_authorized_admin_deletion(sender, instance, **kwargs):
    """Yetkili admin hesabının silinmesini engeller."""
    if _is_authorized(instance):
        logger.critical("BLOCKED DELETION OF AUTHORIZED ADMIN - %s", instance.email)
        raise PermissionError("Yetkili yönetici hesabı silinemez.")


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    _write_security_event(
        request,
        user,
        event_type="login",
        severity="low",
        title=f"{user.email} giriş yaptı",
    )


@receiver(user_logged_out)
def revoke_tokens_on_logout(sender, request, user, **kwargs):
    """Çıkışta kullanıcının tüm refresh token'larını iptal eder."""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    try:
        from agent_app.tokens import revoke_all_for_user

        revoke_all_for_user(user, reason="logout")
    except Exception as exc:
        logger.warning("Token iptali başarısız: %s", exc)


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    _write_security_event(
        request,
        user,
        event_type="logout",
        severity="low",
        title=f"{user.email} çıkış yaptı",
    )


def _write_security_event(request, user, event_type, severity, title):
    try:
        from audit.models import SecurityEvent

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else request.META.get("REMOTE_ADDR", "127.0.0.1")
        )
        SecurityEvent.objects.create(
            event_type=event_type,
            severity=severity,
            user=user if getattr(user, "is_authenticated", False) else None,
            ip_address=ip or "127.0.0.1",
            title=title,
        )
    except Exception as exc:
        logger.warning("Security event yazılamadı: %s", exc)
