"""Model çıktısından JSON ayıklama yardımcıları."""
import json
import re


def parse_json(text, expected_type=None):
    """Metin içindeki ilk geçerli JSON değerini döndür; bulamazsa None."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    candidates = [cleaned]
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if expected_type is not None and not isinstance(value, expected_type):
            continue
        return value
    return None
