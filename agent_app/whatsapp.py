"""
WhatsApp entegrasyonu.

Desteklenen sağlayıcılar:
  - Meta WhatsApp Cloud API (resmi)
  - Twilio WhatsApp
  - Generic webhook (test/özel)

Token'lar veritabanında değil, kullanıcının session meta alanında saklanır.
"""
import json
import logging
import os

import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

PROVIDERS = {
    "meta": {
        "label": "Meta Cloud API",
        "fields": ["phone_number_id", "access_token"],
        "send_url": "https://graph.facebook.com/v21.0/{phone_number_id}/messages",
    },
    "twilio": {
        "label": "Twilio",
        "fields": ["account_sid", "auth_token", "from_number"],
        "send_url": "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
    },
    "webhook": {
        "label": "Webhook",
        "fields": ["webhook_url"],
        "send_url": None,
    },
}


def _user_meta(user):
    user.meta = user.meta or {}
    return user.meta


def whatsapp_config(user):
    """Kullanıcının WhatsApp yapılandırmasını döndürür."""
    meta = _user_meta(user)
    config = meta.get("integrations", {}).get("whatsapp", {})
    return {
        "configured": bool(config.get("access_token") or config.get("auth_token") or config.get("webhook_url")),
        "provider": config.get("provider", ""),
        "phone_number_id": config.get("phone_number_id", ""),
        "from_number": config.get("from_number", ""),
        "webhook_url": config.get("webhook_url", ""),
        "phone": config.get("phone", ""),
    }


def save_whatsapp_config(user, provider, values):
    meta = _user_meta(user)
    integrations = meta.get("integrations", {})
    entry = {
        "provider": provider,
        "phone_number_id": values.get("phone_number_id", ""),
        "from_number": values.get("from_number", ""),
        "webhook_url": values.get("webhook_url", ""),
        "phone": values.get("phone", ""),
        "access_token": values.get("access_token", ""),
        "auth_token": values.get("auth_token", ""),
        "account_sid": values.get("account_sid", ""),
    }
    integrations["whatsapp"] = entry
    meta["integrations"] = integrations
    user.meta = meta
    user.save(update_fields=["meta"])
    return whatsapp_config(user)


def clear_whatsapp_config(user):
    meta = _user_meta(user)
    integrations = meta.get("integrations", {})
    integrations.pop("whatsapp", None)
    meta["integrations"] = integrations
    user.meta = meta
    user.save(update_fields=["meta"])
    return whatsapp_config(user)


def send_whatsapp_message(user, to, message):
    """
    WhatsApp mesajı gönderir.

    Returns: (başarılı, yanıt_metni, hata)
    """
    config = whatsapp_config(user)
    provider = config.get("provider")

    if not config.get("configured"):
        return False, "WhatsApp bağlı değil", "not_configured"

    if not to or not message:
        return False, "Alıcı ve mesaj zorunludur", "invalid_input"

    if provider == "meta":
        return _send_meta(config, to, message)
    if provider == "twilio":
        return _send_twilio(config, to, message)
    if provider == "webhook":
        return _send_webhook(config, to, message)

    return False, f"Bilinmeyen sağlayıcı: {provider}", "unknown_provider"


def _send_meta(config, to, message):
    phone_number_id = config.get("phone_number_id")
    access_token = config.get("access_token")

    if not phone_number_id or not access_token:
        return False, "Meta için phone_number_id ve access_token gerekli", "missing_config"

    url = PROVIDERS["meta"]["send_url"].format(phone_number_id=phone_number_id)
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        data = response.json()
        if response.status_code >= 400:
            message_text = (data.get("error") or {}).get("message", response.text[:200])
            return False, f"Meta hatası: {message_text}", "api_error"
        return True, "Mesaj gönderildi", None
    except requests.RequestException as exc:
        logger.error("WhatsApp Meta gönderim hatası: %s", exc)
        return False, f"Bağlantı hatası: {exc}", "connection_error"


def _send_twilio(config, to, message):
    account_sid = config.get("account_sid")
    auth_token = config.get("auth_token")
    from_number = config.get("from_number")

    if not account_sid or not auth_token or not from_number:
        return False, "Twilio için account_sid, auth_token ve from_number gerekli", "missing_config"

    url = PROVIDERS["twilio"]["send_url"].format(account_sid=account_sid)

    try:
        response = requests.post(
            url,
            data={
                "From": f"whatsapp:{from_number}",
                "To": f"whatsapp:{to}",
                "Body": message,
            },
            auth=(account_sid, auth_token),
            timeout=20,
        )
        if response.status_code >= 400:
            return False, f"Twilio hatası: {response.text[:200]}", "api_error"
        return True, "Mesaj gönderildi", None
    except requests.RequestException as exc:
        logger.error("WhatsApp Twilio gönderim hatası: %s", exc)
        return False, f"Bağlantı hatası: {exc}", "connection_error"


def _send_webhook(config, to, message):
    url = config.get("webhook_url")

    if not url:
        return False, "Webhook URL gerekli", "missing_config"

    try:
        response = requests.post(
            url,
            json={"to": to, "message": message, "source": "emog"},
            timeout=20,
        )
        if response.status_code >= 400:
            return False, f"Webhook hatası ({response.status_code})", "api_error"
        return True, "Webhook çağrıldı", None
    except requests.RequestException as exc:
        logger.error("WhatsApp webhook hatası: %s", exc)
        return False, f"Bağlantı hatası: {exc}", "connection_error"


# ============================================================
# API View'ları
# ============================================================

def _get_app_user(request):
    from agent_app.auth_views import get_current_user

    return get_current_user(request)


def whatsapp_status(request):
    user = _get_app_user(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    config = whatsapp_config(user)
    return JsonResponse({
        "ok": True,
        "configured": config["configured"],
        "provider": config["provider"],
        "phone": config["phone"],
        "from_number": config["from_number"],
        "providers": {key: value["label"] for key, value in PROVIDERS.items()},
    })


@csrf_exempt
@require_POST
def whatsapp_connect(request):
    user = _get_app_user(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    provider = (data.get("provider") or "").strip()
    if provider not in PROVIDERS:
        return JsonResponse(
            {"error": f"Geçersiz sağlayıcı. Seçenekler: {', '.join(PROVIDERS)}"},
            status=400,
        )

    config = save_whatsapp_config(user, provider, data)
    return JsonResponse({"ok": True, "configured": config["configured"], "provider": provider})


@csrf_exempt
@require_POST
def whatsapp_disconnect(request):
    user = _get_app_user(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    clear_whatsapp_config(user)
    return JsonResponse({"ok": True, "configured": False})


@csrf_exempt
@require_POST
def whatsapp_send(request):
    user = _get_app_user(request)
    if not user:
        return JsonResponse({"error": "Authentication required"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        data = {}

    to = (data.get("to") or "").strip()
    message = (data.get("message") or "").strip()[:4096]

    ok, detail, code = send_whatsapp_message(user, to, message)
    return JsonResponse(
        {"ok": ok, "detail": detail, "code": code},
        status=200 if ok else 400,
    )


@csrf_exempt
def whatsapp_webhook(request):
    """Gelen WhatsApp mesajlarını alır (Meta / Twilio callback)."""
    if request.method != "POST":
        return JsonResponse({"ok": True})

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    logger.info("WhatsApp webhook alındı: %s", str(payload)[:300])

    try:
        from audit.models import SecurityEvent

        SecurityEvent.objects.create(
            event_type="whatsapp_webhook",
            severity="low",
            ip_address=(request.META.get("REMOTE_ADDR") or "0.0.0.0")[:45],
            title="WhatsApp webhook mesajı alındı",
            details={"keys": list(payload.keys())[:20]},
        )
    except Exception:
        pass

    return JsonResponse({"ok": True})