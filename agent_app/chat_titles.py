"""Sohbet başlıklarını ilk AI cevabından otomatik üretir.

Yeni bir konuşma "Yeni Sohbet" / "EMOG Chat" gibi varsayılan bir başlıkla
açılır. İlk assistant cevabı kaydedildiğinde başlık, cevabın anlattığı şeye
göre güncellenir: önce anında bir özet (heuristic) yazılır, arka planda model
gateway ile daha isabetli bir başlık üretilip üzerine yazılır.
"""
import logging
import re
import threading

from django.db import connection

logger = logging.getLogger(__name__)

# Varsayılan başlıklar: bunlar hâlâ duruyorsa sohbet daha adlandırılmamıştır.
DEFAULT_TITLES = {"", "Yeni Sohbet", "EMOG Chat", "Yeni Proje"}

_TITLE_SYSTEM_PROMPT = (
    "Sen sohbet başlığı üretecisin. Kullanıcı mesajı ve AI cevabına göre "
    "sohbeti en iyi özetleyen kısa bir Türkçe başlık yaz. Sadece başlığı yaz; "
    "tırnak, nokta, emoji veya açıklama ekleme. En fazla 6 kelime."
)


def _clean(text: str, limit: int) -> str:
    """Başlığı görüntülenebilir tek satıra indirger."""
    s = str(text or "").strip()
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"^\s*#{1,6}\s*", "", s)
    s = re.sub(r"[*_`>\[\]]", "", s)
    s = re.sub(r"(?:\U0001F916\s*)?Model:[^\n]*?HTTP:\s*\d+", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip().strip('"\'“”‘’').strip()
    s = s.rstrip(".;:,|-")
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s.strip()


def heuristic_title(user_message: str, assistant_reply: str) -> str:
    """Model çağrısı olmadan, AI cevabının ilk cümlesinden başlık çıkarır."""
    for source in (assistant_reply, user_message):
        cleaned = _clean(source, 120)
        if len(cleaned) < 4:
            continue
        first = re.split(r"(?<=[.!?])\s+|\n", cleaned, maxsplit=1)[0].strip()
        # "Merhaba!" gibi çok kısa ilk cümleler başlık olmaya uygun değil;
        # cevabın biraz daha fazlasını al.
        if len(first) < 15 and len(cleaned) > len(first):
            first = cleaned
        if len(first) >= 4:
            return _clean(first, 60)
    return "Yeni Sohbet"


def _llm_title(user_message: str, assistant_reply: str) -> str:
    try:
        from .model_gateway import model_gateway

        result = model_gateway.complete(
            "general_reasoning",
            [
                {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Kullanıcı: {str(user_message or '')[:600]}\n"
                        f"AI cevabı: {str(assistant_reply or '')[:600]}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=32,
        )
        if not result.get("ok"):
            return ""
        return _clean(result.get("text") or "", 70)
    except Exception as exc:
        logger.debug("sohbet başlığı model çağrısı başarısız: %s", exc)
        return ""


def _refine_in_background(conversation_id, user_message: str, assistant_reply: str) -> None:
    def _run():
        try:
            from .models import Conversation

            title = _llm_title(user_message, assistant_reply)
            if title:
                # Başlık kullanıcı tarafından değiştirilmişse üzerine yazma.
                Conversation.objects.filter(
                    id=conversation_id, title__in=DEFAULT_TITLES
                ).exclude(title=title).update(title=title)
        except Exception as exc:
            logger.debug("sohbet başlığı güncellenemedi: %s", exc)
        finally:
            connection.close()

    threading.Thread(
        target=_run, daemon=True, name="emog-chat-title"
    ).start()


def maybe_title_conversation(conversation, user_message: str, assistant_reply: str) -> None:
    """İlk assistant cevabında konuşmayı adlandırır (varsayılan başlıksa)."""
    if conversation is None:
        return
    current = str(getattr(conversation, "title", "") or "").strip()
    if current not in DEFAULT_TITLES:
        return

    from .models import Conversation

    title = heuristic_title(user_message, assistant_reply) or "Yeni Sohbet"
    updated = Conversation.objects.filter(
        id=conversation.id, title__in=DEFAULT_TITLES
    ).update(title=title)
    if updated:
        conversation.title = title
        _refine_in_background(str(conversation.id), user_message, assistant_reply)
