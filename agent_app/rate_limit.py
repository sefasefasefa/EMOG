"""
Giriş deneme sınırı (rate limiting) - brute force koruması.

Kademeli (progressive) yavaşlatma:
  - 1-4 hatalı deneme : normal (kayıt)
  - 5+ hatalı deneme  : uyarı, 2 sn gecikme
  - 8+ hatalı deneme  : kilit 60 sn
  - 12+ hatalı deneme : kilit 300 sn
  - 16+ hatalı deneme : kilit 1800 sn
  - 20+ hatalı deneme : kilit 3600 sn (1 saat)

Kilit ölçütleri: (e-posta veya kullanıcı adı) + IP kombinasyonu
Ayrıca IP bazlı genel sınır da vardır (dağıtık saldırı koruması).

Cache backend'de `ttl()` olmayabilir (locmem) - bu yüzden kilit
zamanı değerin içinde `locked_until` timestamp olarak saklanır.
Başarılı girişte sayaç sıfırlanır.
"""
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def tiers():
    """Kilit eşikleri (settings'ten okunur)."""
    configured = getattr(settings, "LOGIN_LOCKOUT_TIERS", None)
    if configured:
        return [(int(t), int(s)) for t, s in configured]
    return [
        (5, 2),      # 5+: 2 sn gecikme
        (8, 60),     # 8+: 1 dk
        (12, 300),   # 12+: 5 dk
        (16, 1800),  # 16+: 30 dk
        (20, 3600),  # 20+: 1 saat
    ]


def ip_tier():
    """IP bazlı genel sınır."""
    return (
        int(getattr(settings, "LOGIN_IP_MAX_ATTEMPTS", 30)),
        int(getattr(settings, "LOGIN_IP_LOCKOUT_SECONDS", 600)),
    )


def window_seconds():
    return int(getattr(settings, "LOGIN_WINDOW_SECONDS", 900))


def max_attempts():
    return int(getattr(settings, "LOGIN_MAX_ATTEMPTS", 5))


def max_delay_seconds():
    return int(getattr(settings, "LOGIN_MAX_DELAY_SECONDS", 5))


def enabled_for(name):
    """Hangi endpoint'ler sınırlı? (settings'ten okunur)"""
    return bool(getattr(settings, f"LOGIN_LIMIT_{name.upper()}", True))


def _key(identifier, ip):
    """Cache anahtarı (kimlik bilgisi sızdırmadan hash)."""
    raw = f"{identifier or ''}|{ip or ''}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"login_fail:{digest}"


def _ip_key(ip):
    digest = hashlib.sha256(f"ip|{ip or ''}".encode()).hexdigest()[:32]
    return f"login_ip:{digest}"


def _now():
    return int(time.time())


def _load(key):
    value = cache.get(key)
    if not isinstance(value, dict):
        return {"count": 0, "locked_until": 0}
    return {
        "count": int(value.get("count", 0)),
        "locked_until": float(value.get("locked_until", 0)),
    }


def _save(key, state, window):
    cache.set(key, state, window)
    return state


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def get_fail_count(identifier, ip):
    """Mevcut hatalı deneme sayısı."""
    return _load(_key(identifier, ip))["count"]


def register_failure(identifier, ip):
    """
    Hatalı denemeyi kaydeder.

    Returns: (sayaç, kilit_saniyesi)
    """
    window = window_seconds()
    key = _key(identifier, ip)
    ip_cache_key = _ip_key(ip)

    state = _load(key)
    state["count"] += 1

    lockout = 0
    for threshold, seconds in tiers():
        if state["count"] >= threshold:
            lockout = seconds

    ip_state = _load(ip_cache_key)
    ip_state["count"] += 1
    ip_threshold, ip_seconds = ip_tier()
    if ip_state["count"] >= ip_threshold:
        lockout = max(lockout, ip_seconds)

    now = _now()

    state["locked_until"] = now + lockout if lockout else 0

    # IP kilidi ayrı tutulur: kimlikten bağımsız genel sınır
    if ip_state["count"] >= ip_threshold:
        ip_state["locked_until"] = now + ip_seconds
    else:
        ip_state["locked_until"] = 0

    _save(key, state, window + max(t for _, t in tiers()) + 60)
    _save(ip_cache_key, ip_state, window + ip_seconds + 60)

    logger.info(
        "LOGIN FAILURE - id=%s ip=%s count=%d lockout=%ss",
        identifier, ip, state["count"], lockout,
    )
    return state["count"], lockout


def register_success(identifier, ip):
    """Başarılı girişte sayacı sıfırlar."""
    cache.delete(_key(identifier, ip))
    cache.delete(_ip_key(ip))


def remaining_attempts(identifier, ip):
    """Kalan deneme hakkı."""
    return max(0, max_attempts() - get_fail_count(identifier, ip))


def check_login_allowed(identifier, ip):
    """
    Giriş denemesine izin var mı?

    Returns: (izin, bekleme_saniyesi, kalan_hak)
    """
    now = _now()
    key = _key(identifier, ip)
    ip_cache_key = _ip_key(ip)

    state = _load(key)
    if state["locked_until"] > now:
        return False, int(state["locked_until"] - now), 0

    ip_state = _load(ip_cache_key)
    if ip_state["locked_until"] > now:
        return False, int(ip_state["locked_until"] - now), 0

    return True, 0, remaining_attempts(identifier, ip)


def lockout_response(wait_seconds, identifier):
    """429 yanıtı döndürür."""
    from django.http import JsonResponse

    wait_seconds = max(1, int(wait_seconds))
    minutes = max(1, round(wait_seconds / 60))

    return JsonResponse(
        {
            "error": (
                f"Çok fazla başarısız deneme. "
                f"{wait_seconds} saniye sonra tekrar deneyin."
            ),
            "code": "rate_limited",
            "retry_after": wait_seconds,
            "retry_after_minutes": minutes,
            "locked": True,
        },
        status=429,
        headers={
            "Retry-After": str(wait_seconds),
            "X-RateLimit-Remaining": "0",
        },
    )


def apply_delay(seconds):
    """Gecikmeli yanıt (kademeli yavaşlatma)."""
    if not seconds or seconds <= 0:
        return

    actual = min(seconds, max_delay_seconds())
    if actual > 0:
        time.sleep(actual)


def record_security_event(identifier, ip, count, lockout):
    """Güvenlik olayı kaydı."""
    try:
        from audit.models import SecurityEvent

        severity = "critical" if lockout >= 300 else "medium"
        SecurityEvent.objects.create(
            event_type="login_bruteforce",
            severity=severity,
            ip_address=(ip or "127.0.0.1")[:45],
            title=f"Başarısız giriş denemeleri: {count} (kilit {lockout}s)",
            details={"identifier": identifier, "count": count, "lockout": lockout},
        )
    except Exception as exc:
        logger.debug("Security event yazılamadı: %s", exc)