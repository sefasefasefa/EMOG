"""
Istek baglantilari (request binding) - isteminin AYNI KISI oldugunu dogrulama.

Her istekte iki dogrulama yapilir:

1. ISTEMCI PARMAK IZI (CLIENT FINGERPRINT)
   Access token, token verildiginde istemcinin parmak izini tasir.
   Her istekte parmak izi yeniden hesaplanir ve token ile karsilastirilir.
   Tokeni calan bir baska cihaz/kullanici agenti ile kullanilamaz.

   Bilesenler:
   - User-Agent (tam)
   - Accept-Language
   - Sec-CH-UA / Sec-CH-UA-Mobile (istemci ipuclari)
   - Ekran boyutu (istemciden gelen hint)

   SAKLANMAZ: parmak izi ham veri olarak degil, HMAC-SHA256 ile
   geri donusu olmayan sekilde saklanir.

2. IP BINDING (CIDR esnekligi ile)
   Mobil kullanicilar IP'si degisir (mobil veri, VPN, proxy).
   Bu yuzden tam IP degil, IP'nin /24 (IPv4) veya /48 (IPv6)
   blogu eslestirilir. Gerekirse tam IP eslestirmeye gecilir.
   Yine de uzak IP + ayni token = ihlal sayilir.

3. CSRF DOUBLE-SUBMIT
   Token, csrf_exempt endpoint'lerde de kullanilabilir olmali.
   Sunucu okunabilir bir CSRF cookie'si + imzali degeri uretir.
   Istemci degeri header'da geri gondermek ZORUNDADIR.
   Saldirganin tarayiciya cookie yazamadigi (cross-site) durumda
   header eslesmez ve istek reddedilir.
"""
import hashlib
import hmac
import logging
import secrets

from django.conf import settings

logger = logging.getLogger(__name__)

CSRF_COOKIE = "emog_csrf"

# Istemci ipuclari (varsa parmak izine dahil edilir)
CLIENT_HINTS = (
    "HTTP_SEC_CH_UA",
    "HTTP_SEC_CH_UA_MOBILE",
    "HTTP_SEC_CH_UA_PLATFORM",
    "HTTP_SEC_CH_UA_PLATFORM_VERSION",
)


# ============================================================
# YARDIMCILAR
# ============================================================

def signing_key():
    return getattr(settings, "JWT_SECRET", None) or settings.SECRET_KEY


def _ip_of(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _network_of(ip):
    """IP'nin ağ bloğu (IPv4 /24, IPv6 /48)."""
    if not ip:
        return ""
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:3])
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0"
    return ip


def _ua_family(request):
    """User-Agent'dan tarayici ailesi (sürüm hariç).

    Sürüm numarası her güncellemede değiştiği için çıkarılır.
    """
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    for token in ("Edg/", "OPR/", "SamsungBrowser/", "Chrome/", "CriOS/",
                  "Firefox/", "FxiOS/", "Safari/"):
        if token in ua:
            return token.rstrip("/")
    return ua.split("/")[0][:30] or "unknown"


# ============================================================
# PARMAK IZI
# ============================================================

def fingerprint_parts(request):
    """Parmak izini olusturan bilesenler."""
    hints = {key: (request.META.get(key) or "")[:80] for key in CLIENT_HINTS}
    return {
        "ua": (request.META.get("HTTP_USER_AGENT") or "")[:400],
        "ua_family": _ua_family(request),
        "lang": (request.META.get("HTTP_ACCEPT_LANGUAGE") or "")[:80],
        "hints": hints,
        # Istemci ekran genisligi (varsa) - farkli cihaz tespiti icin
        "screen": request.COOKIES.get("emog_screen", "")[:16],
    }


def compute_fingerprint(request):
    """Parmak izini HMAC-SHA256 ile ozetle (ham veri saklanmaz)."""
    parts = fingerprint_parts(request)

    payload = "|".join([
        parts["ua_family"],
        parts["lang"],
        parts["screen"],
        *[f"{k}={v}" for k, v in sorted(parts["hints"].items())],
    ])

    return hmac.new(
        signing_key().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def short_fingerprint(request):
    """Hata mesajlarinda gostermek icin kisa parmak izi."""
    return compute_fingerprint(request)[:12]


def binding_required():
    return bool(getattr(settings, "REQUEST_BINDING_ENABLED", True))


def ip_strict():
    return bool(getattr(settings, "REQUEST_BINDING_STRICT_IP", False))


# ============================================================
# DOGRULAMA
# ============================================================

def verify_binding(request, claims):
    """
    Istek ile token sahibinin AYNI istemci oldugunu dogrular.

    Returns: (basarili, hata_kodu)
    """
    if not binding_required():
        return True, None

    token_fp = claims.get("fp")
    if not token_fp:
        # Eski token (fp'siz) - geriye donuk uyum: kontrol etme
        return True, None

    current_fp = compute_fingerprint(request)

    if not hmac.compare_digest(str(token_fp), current_fp):
        logger.warning(
            "BINDING MISMATCH (fingerprint) - path=%s fp=%s",
            request.path, current_fp[:12],
        )
        return False, "client_binding_mismatch"

    token_net = claims.get("net")
    if token_net:
        current_net = _network_of(_ip_of(request))
        if not hmac.compare_digest(str(token_net), current_net):
            # Ayni ag degilse sert reddet
            if ip_strict() or not _ip_changed_tolerable(claims, current_net):
                logger.warning(
                    "BINDING MISMATCH (ip network) - path=%s token_net=%s current_net=%s",
                    request.path, token_net, current_net,
                )
                return False, "ip_binding_mismatch"

    return True, None


def _ip_changed_tolerable(claims, current_net):
    """
    Mobil kullanicilar IP'si degisebilir.

    Tolerans: token yeni olusturulduktan sonra kisa sure icinde
    ve kullanici ayniysa degisiklik kabul edilir (ayni kisi).
    """
    iat = claims.get("iat")
    if not iat:
        return False
    import time

    age = int(time.time()) - int(iat)
    return age <= getattr(settings, "REQUEST_BINDING_IP_GRACE_SECONDS", 900)


def describe_client(request):
    """Loglar icin istemci aciklamasi (gizli veri icermez)."""
    return {
        "fingerprint": short_fingerprint(request),
        "ip": _ip_of(request),
        "network": _network_of(_ip_of(request)),
        "ua_family": _ua_family(request),
        "language": (request.META.get("HTTP_ACCEPT_LANGUAGE") or "")[:32],
    }


# ============================================================
# CSRF (DOUBLE-SUBMIT)
# ============================================================

def make_csrf_pair():
    """
    CSRF double-submit degeri uretir.

    Returns: (raw_token, signed_value)
    """
    raw = secrets.token_urlsafe(32)
    return raw, sign_csrf(raw)


def sign_csrf(raw):
    """CSRF degerini HMAC ile imzalar."""
    signature = hmac.new(
        signing_key().encode(),
        raw.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{raw}.{signature}"


def verify_csrf(request):
    """
    CSRF double-submit dogrulamasi.

    Kaynak: cookie (emog_csrf) ile header/body gonderilen deger ayni olmali.
    Client gonderir: raw token (emog_csrf_raw cookie)
    Server dogrular: sign(raw) == cookie_value
    """
    if not getattr(settings, "CSRF_BINDING_ENABLED", True):
        return True, None

    cookie_value = request.COOKIES.get(CSRF_COOKIE)
    if not cookie_value:
        return False, "csrf_cookie_missing"

    sent = (
        request.headers.get("X-CSRF-Token")
        or request.headers.get("X-Csrf-Token")
        or ""
    )
    if not sent:
        body_sent = ""
        if request.content_type and "json" in request.content_type:
            try:
                import json

                body_sent = (json.loads(request.body or b"{}") or {}).get("csrf_token", "")
            except Exception:
                body_sent = ""
        sent = body_sent

    if not sent:
        return False, "csrf_token_missing"

    # Client sends RAW token. Sign it and compare with cookie (signed).
    expected_signed = sign_csrf(sent)
    if not hmac.compare_digest(str(cookie_value), expected_signed):
        return False, "csrf_token_mismatch"

    # Also verify the signature is valid
    raw = str(sent).split(".")[0]
    if not hmac.compare_digest(expected_signed, sign_csrf(raw)):
        return False, "csrf_signature_invalid"

    return True, None


def set_csrf_cookie(response, raw_token=None):
    """CSRF cookie'sini yazar (okunabilir - JS header gonderir)."""
    if not getattr(settings, "CSRF_BINDING_ENABLED", True):
        return response

    if raw_token is None:
        raw_token = secrets.token_urlsafe(32)

    # Varsayilan 1 gun: access token 15 dk'da doluyor ve yenileme istegi bu
    # cookie'ye bagli. 1 saatlik omur, 1 saatten uzun sure hareketsiz kalan
    # oturumlarda sessiz yenilemeyi imkansiz kiliyordu.
    max_age = int(getattr(settings, "CSRF_COOKIE_MAX_AGE", 86400))

    response.set_cookie(
        CSRF_COOKIE,
        sign_csrf(raw_token),
        httponly=False,
        secure=not settings.DEBUG,
        samesite=getattr(settings, "CSRF_COOKIE_SAMESITE", "Lax"),
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        "emog_csrf_raw",
        raw_token,
        httponly=False,
        secure=not settings.DEBUG,
        samesite=getattr(settings, "CSRF_COOKIE_SAMESITE", "Lax"),
        max_age=max_age,
        path="/",
    )
    return response


# ============================================================
# GUVENLIK OLAYI
# ============================================================

def log_binding_violation(request, code, claims=None, user=None, revoke=True):
    """
    Baglanti ihlalini kaydeder.

    revoke=True  -> parmak izi / IP uyusmazligi: token calinma isaretidir,
                    kullanicinin TUM token'lari iptal edilir.
    revoke=False -> CSRF hatasi: YALNIZCA istek reddedilir.
                    (Saldirganin herhangi bir CSRF istegi ile kullanicinin
                    oturumunu kapatmasina izin verilmemelidir)
    """
    client = describe_client(request)
    email = ""
    user_id = None

    if claims:
        email = claims.get("email", "") or ""
    if user is not None:
        email = getattr(user, "email", "") or email
        user_id = getattr(user, "id", None)

    revoked = 0
    if revoke:
        revoked = revoke_user_tokens(user_id, email)

    logger.critical(
        "REQUEST BINDING VIOLATION - code=%s path=%s user=%s fp=%s net=%s revoked=%d",
        code, request.path, email or "?", client["fingerprint"], client["network"], revoked,
    )

    try:
        from audit.models import SecurityEvent

        SecurityEvent.objects.create(
            event_type="request_binding_violation",
            severity="critical" if revoke else "high",
            user=None,
            ip_address=(client["ip"] or "127.0.0.1")[:45],
            title=f"İstek bağlantı ihlali: {code} ({email or 'bilinmiyor'})",
            description=(
                f"path={request.path} ua={client['ua_family']} "
                f"net={client['network']} dil={client['language']}"
            ),
            details={
                "code": code,
                "fingerprint": client["fingerprint"],
                "network": client["network"],
                "user_agent": client["ua_family"],
                "language": client["language"],
                "tokens_revoked": revoked,
            },
        )
    except Exception as exc:
        logger.debug("Security event yazılamadı: %s", exc)


def revoke_user_tokens(user_id, email):
    """İhlal sonrası kullanıcının token ailesini iptal et."""
    if not email:
        return 0

    try:
        from agent_app.models import RefreshToken
        from agent_app.tokens import hash_token  # noqa

        return RefreshToken.objects.filter(
            user__email=email, is_revoked=False
        ).update(is_revoked=True, revoked_reason="binding_violation")
    except Exception:
        return 0