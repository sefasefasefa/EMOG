"""Pipeline icin LLM yardimcilari.

Arastirma, plan ve kritik asamalarinin tamami modelden *yapilandirilmis JSON*
ister. Provider'lar bu istekte tutarli degildir: bazen kod blogu, bazon metin
icinde gomulu JSON, bazen hicbir sey dondurebilir. Bu modul:

  * yaniti JSON'a zorlar (code fence, markdown, tirnak, kesik cikti temizlenir)
  * gecersiz ciktida modele "sadece JSON" diyerek birkac kez tekrar dener
  * amaca gore model secer (arastirma / kodlama / hizli yurutme)
"""
import json
import logging
import re
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

JSON_ONLY_RULE = (
    "Cevabini SADECE geçerli JSON olarak ver. Markdown code fence kullanma, "
    "açıklama yazma, JSON'un önüne/sonuna hiçbir metin ekleme."
)


def pick_model(purpose: str = "research", task_text: str = "", explicit: str = None) -> str:
    """Amaca gore model sec. purpose: research | coding | agent | default."""
    if explicit:
        return explicit
    task_models = getattr(settings, "NVIDIA_TASK_MODELS", {}) or {}
    model = task_models.get(purpose)
    if model:
        return model
    if task_text:
        from agent_app.model_router import select_purpose_model

        model, _key = select_purpose_model(task_text)
        if model:
            return model
    return getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "") or task_models.get("default", "")


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _balanced_span(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """Acik/kapali karakter sayimi ile dengeli bir JSON parcası bul."""
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def extract_json(text: str) -> Any:
    """Metinden JSON degeri cikar. Bulamazsa None doner."""
    if not text:
        return None
    candidates = [_strip_fences(text), text]

    # Kesik ciktiyi onarmaya calis: kapanmamis yapi kaldiysa kapat.
    for candidate in list(candidates):
        repaired = _repair_truncated_json(candidate)
        if repaired and repaired not in candidates:
            candidates.append(repaired)

    for candidate in candidates:
        for attempt in (candidate, candidate.strip()):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                pass

    for candidate in candidates:
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            span = _balanced_span(candidate, open_ch, close_ch)
            if not span:
                continue
            try:
                return json.loads(span)
            except json.JSONDecodeError:
                repaired = _repair_truncated_json(span)
                if repaired:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        continue
    return None


def _repair_truncated_json(text: str) -> str:
    """max_tokens kesildiginde yarim kalan JSON'u onarmaya calis."""
    if not text or '"' not in text:
        return None
    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [s for s in (start_obj, start_arr) if s >= 0]
    if not starts:
        return None
    start = min(starts)
    body = text[start:].rstrip()

    # Kapanmamis tirnak varsa kapat.
    in_string = False
    escaped = False
    for char in body:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
    if in_string:
        body += '"'

    # Sondaki eksik anahtar/deger ciftini kirp.
    body = re.sub(r",\s*$", "", body)
    body = re.sub(r'[,{[]\s*"[^"]*"\s*:\s*$', "", body)
    body = re.sub(r",\s*$", "", body)

    # Kapanislari geriye dogru kapat.
    stack = []
    in_string = False
    escaped = False
    for char in body:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack and ((stack[-1] == "{" and char == "}") or (stack[-1] == "[" and char == "]")):
                stack.pop()

    if stack:
        body = body + "".join("}" if ch == "{" else "]" for ch in reversed(stack))
    return body


def _default_max_tokens(purpose: str) -> int:
    budget = int(getattr(settings, "OMNIROUTE", {}).get("MAX_TOKENS", 2048) or 2048)
    # Arastirma/plan ciktilari uzundur; modelin ic muhakemeye token harcamasin.
    return max(budget, 6144 if purpose == "research" else 4096)


def chat(
    messages,
    *,
    model: str = None,
    purpose: str = "research",
    temperature: float = 0.2,
    max_tokens: int = None,
    tools=None,
) -> str:
    """Tek bir sohbet cagrisi yap, metin icerigini dondur."""
    from agent_app.views import llm_create_resilient

    payload = {
        "model": pick_model(purpose, explicit=model),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or _default_max_tokens(purpose),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    response = llm_create_resilient(**payload)
    return (response.choices[0].message.content or "").strip()


def ask_json(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = None,
    purpose: str = "research",
    temperature: float = 0.2,
    max_tokens: int = None,
    attempts: int = 3,
    default=None,
):
    """Modelden JSON ister. Gecersiz cikti halinde tekrar dener."""
    last_error = None
    for attempt in range(1, max(1, attempts) + 1):
        system = system_prompt if attempt == 1 else f"{system_prompt}\n\n{JSON_ONLY_RULE}"
        user = user_prompt if attempt == 1 else (
            f"{user_prompt}\n\nÖNEMLİ: Son cevabın geçerli JSON değildi. "
            "Bu sefer tamamen JSON döndür, başka hiçbir şey yazma."
        )
        try:
            raw = chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=model,
                purpose=purpose,
                temperature=temperature if attempt == 1 else 0.0,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            last_error = exc
            logger.warning("ask_json LLM hatasi (deneme %d/%d): %s", attempt, attempts, exc)
            continue

        parsed = extract_json(raw)
        if parsed is not None:
            return parsed
        last_error = ValueError("JSON ayristirilamadi")
        logger.warning("ask_json gecersiz JSON (deneme %d/%d): %r", attempt, attempts, (raw or "")[:180])

    logger.error("ask_json basarisiz: %s", last_error)
    return default


def ask_text(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = None,
    purpose: str = "coding",
    temperature: float = 0.3,
    max_tokens: int = None,
) -> str:
    return chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        model=model,
        purpose=purpose,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def ask_model_if_possible(system_prompt: str, user_prompt: str, default, *, purpose="research", **kwargs):
    """LLM kullanilamazsa (ag yok / model yok) varsayilani dondur; pipeline kirilmasin."""
    try:
        return ask_json(system_prompt, user_prompt, **kwargs)
    except Exception as exc:  # pragma: no cover - savunma
        logger.warning("ask_model_if_possible varsayilana dustu: %s", exc)
        return default


def as_list(value, key_hint: str = "") -> list:
    """Model ciktisini her zaman listeye cevir."""
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if v is not None]
    if isinstance(value, dict):
        if key_hint and isinstance(value.get(key_hint), list):
            return [v for v in value[key_hint] if v is not None]
        return [value]
    return [value]


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}