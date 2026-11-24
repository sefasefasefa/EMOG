"""Proje bazlı entegrasyonlar (şimdilik WhatsApp raporları).

Proje ayarları ``projects/<id>/project.json`` içindeki
``integrations.whatsapp`` alanında tutulur. Mesaj gönderimi iki mevcut
altyapıyı destekler:

1) WhatsApp Web servisi (QR ile bağlanan kullanıcı oturumu)
2) Meta Cloud API / Twilio / webhook yapılandırması (whatsapp.py)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PROJECT_WA_FIELDS = (
    "enabled",
    "recipient",
    "notify_on_start",
    "notify_on_complete",
    "notify_on_error",
)


def get_project_whatsapp_config(project: Dict[str, Any] = None) -> Dict[str, Any]:
    project = project or {}
    integrations = project.get("integrations") or {}
    cfg = integrations.get("whatsapp") if isinstance(integrations, dict) else {}
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "recipient": str(cfg.get("recipient") or "").strip(),
        "notify_on_start": bool(cfg.get("notify_on_start", False)),
        "notify_on_complete": bool(cfg.get("notify_on_complete", True)),
        "notify_on_error": bool(cfg.get("notify_on_error", True)),
    }


def save_project_whatsapp_config(project_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    from . import project_store

    project = project_store.read_project(project_id) or {"id": project_id}
    integrations = dict(project.get("integrations") or {})
    current = get_project_whatsapp_config(project)
    for key in PROJECT_WA_FIELDS:
        if key in values:
            if key in ("enabled", "notify_on_start", "notify_on_complete", "notify_on_error"):
                current[key] = bool(values.get(key))
            else:
                current[key] = str(values.get(key) or "").strip()
    integrations["whatsapp"] = current
    project_store.update_project(project_id, integrations=integrations)
    return current


def whatsapp_connection_state(user) -> Dict[str, Any]:
    """Kullanıcının mevcut WhatsApp bağlantı durumunu döndür."""
    session_connected = False
    phone = ""
    try:
        from .models import WhatsAppSession
        session = WhatsAppSession.objects.filter(user=user).first()
        if session:
            session_connected = bool(session.is_connected)
            phone = session.phone_number or ""
    except Exception:
        pass

    provider_configured = False
    provider = ""
    try:
        from .whatsapp import whatsapp_config
        conf = whatsapp_config(user)
        provider_configured = bool(conf.get("configured"))
        provider = conf.get("provider") or ""
    except Exception:
        pass

    return {
        "session_connected": session_connected,
        "provider_configured": provider_configured,
        "connected": session_connected or provider_configured,
        "phone": phone,
        "provider": provider,
    }


def _digits_recipient(recipient: str) -> str:
    text = str(recipient or "").strip()
    # Grup/JID biçimini koru; telefon numarasını sadece rakamlara indir.
    if text.endswith("@g.us") or text.endswith("@c.us"):
        return text
    return re.sub(r"\D", "", text)
def send_project_whatsapp_message(project_id: str, user_id: str, message: str,
                                  recipient: str = None) -> Tuple[bool, str]:
    """Proje ayarındaki alıcıya WhatsApp mesajı gönder."""
    from . import project_store
    from .models import User

    project = project_store.read_project(project_id)
    if not project:
        return False, "Proje bulunamadı"
    cfg = get_project_whatsapp_config(project)
    target = _digits_recipient(recipient or cfg.get("recipient") or "")
    if not target:
        return False, "WhatsApp alıcı numarası tanımlı değil"
    if not str(message or "").strip():
        return False, "Mesaj boş"

    user = User.objects.filter(id=user_id).first() if user_id else None
    if not user:
        return False, "Kullanıcı bulunamadı"

    # 1) QR ile bağlı WhatsApp Web oturumu
    try:
        from .models import WhatsAppSession
        session = WhatsAppSession.objects.filter(user=user).first()
        if session and session.is_connected:
            service_url = getattr(settings, "WHATSAPP_SERVICE_URL", "http://localhost:3002").rstrip("/")
            headers = {"Content-Type": "application/json"}
            token = getattr(settings, "WHATSAPP_SERVICE_TOKEN", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = requests.post(
                f"{service_url}/api/send/{user.id}",
                headers=headers,
                json={"to": target, "message": message},
                timeout=30,
            )
            if response.status_code == 200:
                return True, "WhatsApp mesajı gönderildi"
            logger.warning("WhatsApp servis hatası: %s %s", response.status_code, response.text[:300])
    except Exception as exc:
        logger.warning("WhatsApp Web gönderimi başarısız: %s", exc)

    # 2) Meta / Twilio / webhook sağlayıcı yapılandırması
    try:
        from .whatsapp import send_whatsapp_message, whatsapp_config
        if whatsapp_config(user).get("configured"):
            provider_target = target
            if provider_target.endswith("@c.us"):
                provider_target = provider_target[:-5]
            if not provider_target.startswith("+"):
                provider_target = "+" + provider_target
            ok, detail, _code = send_whatsapp_message(user, provider_target, message)
            if ok:
                return True, detail
            return False, detail
    except Exception as exc:
        logger.warning("WhatsApp sağlayıcı gönderimi başarısız: %s", exc)

    return False, "WhatsApp bağlı değil. WhatsApp sayfasından QR ile bağlanın veya sağlayıcı yapılandırın."


def send_project_task_report(project_id: str, user_id: str, task: str, status: str,
                             output: str = "", error: str = "", stats: Dict = None) -> bool:
    """Görev tamamlandığında/hata verdiğinde proje WhatsApp raporu gönder."""
    from . import project_store

    project = project_store.read_project(project_id)
    if not project:
        return False
    cfg = get_project_whatsapp_config(project)
    if not cfg.get("enabled"):
        return False
    if status == "completed" and not cfg.get("notify_on_complete"):
        return False
    if status == "started" and not cfg.get("notify_on_start"):
        return False
    if status in ("failed", "cancelled") and not cfg.get("notify_on_error"):
        return False

    name = project.get("name") or project_id
    stats = stats or {}
    plan_line = ""
    if stats:
        plan_line = (
            f"\nPlan: {stats.get('done', 0)}/{stats.get('total', 0)} tamam"
            f", {stats.get('failed', 0)} başarısız"
        )
    label = {
        "completed": "✅ Görev tamamlandı",
        "failed": "❌ Görev başarısız",
        "cancelled": "⏹ Görev iptal edildi",
        "started": "🚀 Görev başladı",
    }.get(status, status)
    body = (
        f"EMOG · {name}\n\n{label}\n"
        f"Görev: {str(task or '')[:500]}{plan_line}\n"
    )
    detail = (output or error or "").strip()
    if detail:
        body += f"\n{detail[:1800]}"
    ok, send_detail = send_project_whatsapp_message(project_id, user_id, body)
    if not ok:
        logger.warning("Proje WhatsApp raporu gönderilemedi (%s): %s", project_id, send_detail)
    return ok

