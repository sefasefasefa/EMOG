"""Özellik anahtarı değişikliklerini denetim kaydına yazar."""
import logging

logger = logging.getLogger(__name__)


def write_feature_audit(flag, user=None):
    try:
        from audit.models import AuditLog

        AuditLog.objects.create(
            user=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
            user_email=getattr(user, "email", "") or "",
            action=AuditLog.Action.UPDATE,
            model_name="feature_flag",
            object_id=str(flag.pk),
            object_repr=f"{flag.label or flag.key} -> {'AÇIK' if flag.enabled else 'KAPALI'}",
            changes={
                "key": flag.key,
                "enabled": flag.enabled,
                "default": flag.default_enabled,
            },
            ip_address=None,
            request_path="/admin/features/",
            request_method="POST",
        )
    except Exception as exc:
        logger.warning("Feature audit yazılamadı: %s", exc)