"""
WhatsApp üzerinden EMOG'u uzaktan yönetme köprüsü.

Akış:
  WhatsApp mesajı -> whatsapp_service -> /api/whatsapp/webhook/
  -> yetki kontrolü -> mevcut assistant_message niyet/ajan akışı
  -> cevap veya görev sonucu WhatsApp'a geri gönderilir.

Güvenlik:
  - Varsayılan olarak yalnızca bağlı hesabın kendi numarasına gelen
    mesajlar ve "Kendine mesaj" sohbeti kabul edilir.
  - Ek numaralar WHATSAPP_REMOTE_ALLOWED_NUMBERS ile verilebilir.
  - Botun kendi gönderdiği mesajlar "🤖 EMOG" önekiyle işaretlenir ve
    tekrar komut olarak işlenmez.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, Tuple

from django.conf import settings
from django.test import RequestFactory

from .models import User, WhatsAppSession

logger = logging.getLogger(__name__)

REMOTE_PREFIX = "🤖 EMOG"
_REMOTE_MAX_CHARS = int(getattr(settings, "WHATSAPP_REMOTE_MAX_REPLY_CHARS", 8000) or 8000)
_CANCEL_RE = re.compile(r"^(dur|durdur|iptal|iptal et|stop|cancel|vazgeç)\b", re.I)
_STATUS_RE = re.compile(r"^(durum|status|ne durumda|ne oldu|son durum)\s*[.!?]*$", re.I)
_HELP_RE = re.compile(r"^(yardım|yardim|help|komutlar|komutlar neler)\s*[.!?]*$", re.I)
_PROJECTS_RE = re.compile(r"^(projeler|projelerim|projects|proje listesi)\s*[.!?]*$", re.I)
_OPEN_PROJECT_RE = re.compile(r"^(?:proje|project)\s*(?:aç|ac|seç|sec|open)\s+(.+)$", re.I)


class _RemoteSession(dict):
    """assistant_message için istek/session bağlamı (tarayıcı oturumu yok)."""

    modified = False
    session_key = ""

    def __setitem__(self, key, value):
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self.modified = True
        super().__delitem__(key)


def _enabled() -> bool:
    return bool(getattr(settings, "WHATSAPP_REMOTE_ENABLED", True))


def _digits(value: Any) -> str:
    raw = str(value or "").strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return re.sub(r"\D", "", raw)


def _self_numbers(session: WhatsAppSession) -> set:
    numbers = set()
    for value in (session.phone_number, session.wid):
        number = _digits(value)
        if number:
            numbers.add(number)
    return numbers


def _owner_numbers(session: WhatsAppSession) -> set:
    numbers = _self_numbers(session)
    configured = getattr(settings, "WHATSAPP_REMOTE_ALLOWED_NUMBERS", []) or []
    if isinstance(configured, str):
        configured = configured.split(",")
    for value in configured:
        number = _digits(value)
        if number:
            numbers.add(number)
    return numbers


def _message_target(msg_data: Dict[str, Any]) -> str:
    chat = msg_data.get("chat") or {}
    return str(chat.get("id") or msg_data.get("from") or "").strip()


def _chat_number(msg_data: Dict[str, Any]) -> str:
    chat = msg_data.get("chat") or {}
    contact = msg_data.get("contact") or {}
    return _digits(chat.get("id") or contact.get("number") or msg_data.get("from"))


def _is_group(msg_data: Dict[str, Any]) -> bool:
    chat = msg_data.get("chat") or {}
    return bool(chat.get("isGroup")) or str(msg_data.get("from") or "").endswith("@g.us")


def _is_self_chat(session: WhatsAppSession, msg_data: Dict[str, Any]) -> bool:
    numbers = _self_numbers(session)
    chat_number = _chat_number(msg_data)
    return bool(chat_number and chat_number in numbers)


def is_authorized(session: WhatsAppSession, msg_data: Dict[str, Any]) -> Tuple[bool, str]:
    """Mesajın uzaktan komut olarak işlenip işlenemeyeceğini döndürür."""
    if not _enabled():
        return False, "remote_disabled"
    if _is_group(msg_data):
        return False, "group_not_allowed"

    from_me = bool(msg_data.get("fromMe"))
    chat_number = _chat_number(msg_data)
    from_number = _digits((msg_data.get("contact") or {}).get("number") or msg_data.get("from"))
    owners = _owner_numbers(session)
    self_numbers = _self_numbers(session)

    if from_me:
        if not bool(getattr(settings, "WHATSAPP_REMOTE_ALLOW_SELF_CHAT", True)):
            return False, "self_chat_disabled"
        if not chat_number or chat_number not in self_numbers:
            return False, "not_self_chat"
        return True, "self_chat"

    if not from_number:
        return False, "missing_sender"
    if from_number not in owners:
        return False, "sender_not_allowed"
    return True, "owner"


def _get_state(session: WhatsAppSession) -> Dict[str, Any]:
    data = session.session_data if isinstance(session.session_data, dict) else {}
    state = data.get("remote") if isinstance(data.get("remote"), dict) else {}
    return dict(state)


def _save_state(session: WhatsAppSession, **values) -> None:
    data = dict(session.session_data or {}) if isinstance(session.session_data, dict) else {}
    remote = dict(data.get("remote") or {}) if isinstance(data.get("remote"), dict) else {}
    remote.update({key: value for key, value in values.items() if value is not None})
    data["remote"] = remote
    session.session_data = data
    try:
        session.save(update_fields=["session_data", "updated_at"])
    except Exception as exc:
        logger.warning("WhatsApp remote state kaydedilemedi: %s", exc)


def queue_incoming_message(user_id: str, msg_data: Dict[str, Any]) -> None:
    """Webhook isteğini bloklamadan uzaktan komutu işle."""
    threading.Thread(
        target=_process_incoming,
        args=(str(user_id), dict(msg_data or {})),
        daemon=True,
        name="emog-wa-remote",
    ).start()


def _process_incoming(user_id: str, msg_data: Dict[str, Any]) -> None:
    body = str(msg_data.get("body") or "").strip()
    if not body:
        return
    if body.startswith(REMOTE_PREFIX) or body.lower().startswith(("emog:", "emog ·", "emog -")):
        return
    # Proje/ajan raporları kendi kendine komut olarak işlenmesin.
    if body.startswith(("✅", "❌", "🛑", "⏹", "🚀", "⏳")):
        return

    try:
        user = User.objects.filter(id=user_id).first()
        session = WhatsAppSession.objects.filter(user=user).first() if user else None
    except Exception as exc:
        logger.warning("WhatsApp remote kullanıcı/oturum yüklenemedi: %s", exc)
        return
    if not user or not session:
        return

    allowed, reason = is_authorized(session, msg_data)
    if not allowed:
        logger.info("WhatsApp remote mesajı reddedildi (%s): %s", reason, _digits(msg_data.get("from")))
        return

    target = _message_target(msg_data)
    if not target:
        return
    if msg_data.get("fromMe") and _is_self_chat(session, msg_data):
        own_number = _digits(session.phone_number) or _digits(session.wid)
        if own_number:
            target = f"{own_number}@c.us"
    else:
        contact_number = _digits((msg_data.get("contact") or {}).get("number"))
        if contact_number and not target.endswith(("@c.us", "@g.us")):
            target = f"{contact_number}@c.us"

    command = re.sub(r"\s+", " ", body).strip()
    command = re.sub(r"^[/!.]+\s*", "", command)
    if _CANCEL_RE.match(command):
        _cancel_and_reply(user, session, target)
        return
    if _STATUS_RE.match(command):
        _send_text(user.id, target, _status_text(user, session))
        return
    if _HELP_RE.match(command):
        _send_text(user.id, target, _help_text())
        return
    if _PROJECTS_RE.match(command):
        _send_text(user.id, target, _projects_text(user))
        return
    match_project = _OPEN_PROJECT_RE.match(command)
    if match_project:
        _select_project_and_reply(user, session, target, match_project.group(1).strip())
        return

    try:
        response_data = _call_assistant(user, session, body)
    except Exception as exc:
        logger.exception("WhatsApp remote assistant çağrısı başarısız")
        _send_text(user.id, target, "❌ İşlem sırasında hata oluştu: " + str(exc)[:700])
        return

    reply = _format_assistant_reply(response_data)
    run_id = response_data.get("run_id")
    if run_id:
        try:
            from . import views
            views._update_task(
                run_id,
                whatsapp_remote_user_id=str(user.id),
                whatsapp_chat_id=target,
                whatsapp_remote=True,
            )
        except Exception as exc:
            logger.debug("Remote görev hedefi kaydedilemedi: %s", exc)
    state = _get_state(session)
    _save_state(
        session,
        project_id=response_data.get("project_id") or state.get("project_id"),
        conversation_id=state.get("conversation_id"),
        last_target=target,
    )
    _send_text(user.id, target, reply)


def _call_assistant(user: User, session: WhatsAppSession, message: str) -> Dict[str, Any]:
    """Mevcut assistant_message akışını tarayıcı oturumu olmadan çalıştır."""
    from . import project_store, views

    state = _get_state(session)
    project_id = state.get("project_id")
    if project_id and not project_store.read_project(project_id):
        project_id = None
    if not project_id:
        latest = project_store.find_latest_project(user_id=str(user.id))
        if latest:
            project_id = latest.get("id")

    request = RequestFactory().post("/assistant/message/", {"message": message})
    request._emog_user_resolved = user
    request.user_app = user
    request.session = _RemoteSession()
    if project_id:
        request.session["current_project_id"] = project_id
        request.session["current_project_guid"] = project_id
    # Yeni build istekleri yeni proje açabilsin; mevcut proje execute için kullanılır.
    request.session["active_project_explicit"] = False
    conversation_id = state.get("conversation_id")
    if not conversation_id and project_id:
        try:
            from .models import Conversation
            for conv in Conversation.objects.filter(app_user=user).order_by("-updated_at")[:20]:
                if str((conv.meta_data or {}).get("project_id") or "") == str(project_id):
                    conversation_id = str(conv.id)
                    break
        except Exception:
            conversation_id = None
    if conversation_id:
        request.session["chat_conversation_id"] = conversation_id

    data: Dict[str, Any] = {}
    try:
        response = views.assistant_message(request)
        try:
            data = json.loads(response.content.decode("utf-8"))
        except Exception:
            data = {"ok": False, "error": response.content.decode("utf-8", "replace")[:1000]}
    finally:
        new_conversation = request.session.get("chat_conversation_id")
        new_project = request.session.get("current_project_id") or request.session.get("current_project_guid")
        _save_state(
            session,
            conversation_id=new_conversation or conversation_id,
            project_id=new_project or project_id,
        )

    if not isinstance(data, dict):
        data = {"ok": False, "error": "Asistan geçersiz yanıt döndürdü."}
    return data


def _format_assistant_reply(data: Dict[str, Any]) -> str:
    reply = str(data.get("reply") or "").strip()
    if reply:
        return reply
    error = str(data.get("error") or "").strip()
    if error:
        detail = str(data.get("detail") or "").strip()
        return "❌ " + error + (("\n\n" + detail[:1200]) if detail else "")
    intent = str(data.get("intent") or "")
    if intent in ("health", "models"):
        return json.dumps(data, ensure_ascii=False)[:3000]
    return "İşlem alındı."


def _help_text() -> str:
    return (
        "🤖 EMOG WhatsApp kumandası\n\n"
        "Doğal dille yazabilirsin:\n"
        "• \"yeni bir e-ticaret sitesi yap\"\n"
        "• \"bu projedeki hatayı düzelt\"\n"
        "• \"devam et\"\n\n"
        "Kısa komutlar:\n"
        "• durum — çalışan son görev\n"
        "• projeler — proje listesi\n"
        "• proje aç <id/isim> — proje seç\n"
        "• dur / iptal — çalışan görevi durdur\n"
        "• yardım — bu mesaj\n\n"
        "Yalnızca yetkili numaralar komut gönderebilir. "
        "Bot cevapları 🤖 EMOG önekiyle gelir."
    )


def _status_text(user: User, session: WhatsAppSession) -> str:
    from . import project_store, views

    state = _get_state(session)
    project_id = state.get("project_id")
    if not project_id:
        latest_project = project_store.find_latest_project(user_id=str(user.id))
        project_id = latest_project.get("id") if latest_project else None

    lines = ["🤖 EMOG durumu"]
    if project_id:
        project = project_store.read_project(project_id) or {}
        lines.append("Proje: " + str(project.get("name") or project.get("title") or project_id))
        if project.get("status"):
            lines.append("Proje durumu: " + str(project.get("status")))

    active = views._find_active_task(str(user.id), project_id)
    latest = views._latest_task(str(user.id), project_id)
    task = active or latest
    if task:
        text = views._task_display_text(task)
        lines.append("Görev: " + text[:500])
        lines.append("Durum: " + str(task.get("status") or "bilinmiyor"))
        if task.get("run_id"):
            lines.append("Çalıştırma: " + str(task.get("run_id")))
        if task.get("error") and str(task.get("status")) not in ("completed", "running", "queued"):
            lines.append("Hata: " + str(task.get("error"))[:600])
    else:
        lines.append("Şu anda çalışan bir görev yok.")
    lines.append("Komutlar için 'yardım' yazabilirsin.")
    return "\n".join(lines)


def _projects_text(user: User) -> str:
    from . import project_store

    projects = project_store.list_projects(user_id=str(user.id))[:8]
    if not projects:
        return "Henüz proje yok. \"yeni bir site yap\" diyerek başlayabilirsin."
    lines = ["🤖 EMOG projeleri"]
    for project in projects:
        name = project.get("name") or project.get("title") or project.get("id")
        status = project.get("status") or ""
        lines.append(f"• {name} ({status}) — {project.get('id')}")
    return "\n".join(lines)


def _select_project_and_reply(user: User, session: WhatsAppSession, target: str, query: str) -> None:
    from . import project_store

    projects = project_store.list_projects(user_id=str(user.id))
    needle = query.strip().lower()
    match = None
    for project in projects:
        pid = str(project.get("id") or "")
        name = str(project.get("name") or project.get("title") or "")
        if needle and (needle == pid.lower() or pid.lower().startswith(needle)
                       or needle in name.lower()):
            match = project
            break
    if not match:
        _send_text(user.id, target, "Proje bulunamadı. 'projeler' yazarak listeyi görebilirsin.")
        return
    _save_state(session, project_id=match.get("id"), conversation_id="")
    _send_text(
        user.id,
        target,
        f"Proje seçildi: {match.get('name') or match.get('title') or match.get('id')}\n"
        f"ID: {match.get('id')}",
    )


def _cancel_and_reply(user: User, session: WhatsAppSession, target: str) -> None:
    from . import project_store, views

    state = _get_state(session)
    project_id = state.get("project_id")
    if not project_id:
        latest = project_store.find_latest_project(user_id=str(user.id))
        project_id = latest.get("id") if latest else None
    try:
        if project_id:
            views.cancel_project_tasks(project_id, str(user.id))
        else:
            views.cancel_user_tasks(str(user.id), "WhatsApp üzerinden iptal edildi.")
        _send_text(user.id, target, "🛑 Çalışan/kuyruktaki görevler iptal edildi.")
    except Exception as exc:
        logger.warning("WhatsApp remote iptal hatası: %s", exc)
        _send_text(user.id, target, "❌ Görev iptal edilemedi: " + str(exc)[:600])


def send_service_message(user_id: str, target: str, message: str) -> bool:
    """WhatsApp Web servisine doğrudan mesaj gönder (proje entegrasyonundan bağımsız)."""
    import requests

    service_url = str(getattr(settings, "WHATSAPP_SERVICE_URL", "http://localhost:3002") or "").rstrip("/")
    if not service_url or not target or not message:
        return False
    headers = {"Content-Type": "application/json"}
    token = str(getattr(settings, "WHATSAPP_SERVICE_TOKEN", "") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.post(
            f"{service_url}/api/send/{user_id}",
            headers=headers,
            json={"to": target, "message": message},
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning("WhatsApp remote gönderim hatası: %s %s", response.status_code, response.text[:300])
            return False
        return True
    except Exception as exc:
        logger.warning("WhatsApp remote gönderim bağlantı hatası: %s", exc)
        return False


def _chunks(text: str, limit: int = 3500):
    text = str(text or "")
    while text:
        yield text[:limit]
        text = text[limit:]


def _send_text(user_id, target: str, text: str) -> bool:
    body = str(text or "").strip()
    if not body:
        return False
    if not body.startswith(REMOTE_PREFIX):
        body = REMOTE_PREFIX + "\n" + body
    ok = True
    chunk_size = max(500, min(_REMOTE_MAX_CHARS, 3500))
    for chunk in _chunks(body, chunk_size):
        ok = send_service_message(str(user_id), target, chunk) and ok
    return ok



def queue_task_report(run_id: str, status: str, result: Any = None, error: str = "") -> None:
    """Görev sonucunu uzaktan komutu gönderen WhatsApp sohbetine ilet."""
    if not run_id or not status:
        return
    threading.Thread(
        target=_send_task_report,
        args=(str(run_id), str(status), result, str(error or "")),
        daemon=True,
        name="emog-wa-remote-report",
    ).start()


def _send_task_report(run_id: str, status: str, result: Any, error: str) -> None:
    try:
        from . import views

        with views.TASKS_LOCK:
            tasks = views._load_tasks()
        task = next((item for item in tasks if str(item.get("run_id")) == run_id), None)
        if not task:
            return
        target = str(task.get("whatsapp_chat_id") or "")
        user_id = str(task.get("whatsapp_remote_user_id") or task.get("user_id") or "")
        if not target or not user_id:
            return

        task_text = str(task.get("task") or "")
        if "Kullanıcı isteği:" in task_text:
            task_text = task_text.split("Kullanıcı isteği:", 1)[1].strip()
        task_text = task_text[:500]

        if status == "completed":
            text = "✅ Görev tamamlandı.\n\n" + (task_text or "Proje görevi")
            summary = ""
            if isinstance(result, dict):
                summary = str(result.get("output") or result.get("summary") or "")
            if summary:
                text += "\n\n" + summary[:4000]
        elif status == "cancelled":
            text = "🛑 Görev iptal edildi.\n\n" + (task_text or "")
        else:
            text = "❌ Görev başarısız oldu.\n\n" + (task_text or "")
            if error:
                text += "\n\n" + error[:2000]
        _send_text(user_id, target, text)
    except Exception as exc:
        logger.warning("WhatsApp remote görev raporu gönderilemedi: %s", exc)

