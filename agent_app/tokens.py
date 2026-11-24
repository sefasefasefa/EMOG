"""
Kısa ömürlü access token + her seferinde yenilenen refresh token.

Access token  : JWT (HS256), varsayılan 15 dakika, imzalı, HttpOnly cookie
Refresh token : opak rastgele, DB'de SHA-256 hash olarak saklanır,
                HER KULLANIMDA yenilenir (rotation) + reuse detection

Reuse detection: Kullanılmış (rotasyonlu) bir refresh token tekrar
gönderilirse token ailesinin tamamı iptal edilir (token hırsızlığı tespiti).
"""
import hashlib
import logging
import secrets
from datetime import timedelta

import jwt
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

ACCESS_COOKIE = "emog_at"
REFRESH_COOKIE = "emog_rt"


def access_ttl():
    return timedelta(minutes=int(getattr(settings, "ACCESS_TOKEN_TTL_MINUTES", 15)))


def refresh_ttl():
    return timedelta(days=int(getattr(settings, "REFRESH_TOKEN_TTL_DAYS", 30)))


def jwt_secret():
    return getattr(settings, "JWT_SECRET", None) or settings.SECRET_KEY


def jwt_algorithm():
    return getattr(settings, "JWT_ALGORITHM", "HS256")


def hash_token(raw):
    """Refresh token'ın SHA-256 hash'i (DB'de düz metin saklanmaz)."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ============================================================
# ACCESS TOKEN (JWT)
# ============================================================

def create_access_token(user, extra_claims=None, request=None):
    now = timezone.now()
    ttl = access_ttl()

    payload = {
        "sub": str(user.id),
        "typ": "access",
        "username": user.username,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(16),
        "iss": "emog",
    }

    # Istemci baglantisi: token kimin icin uretildi?
    # Her istekte ayni parmak iziyle gelmelidir.
    if request is not None:
        from agent_app.binding import binding_required, compute_fingerprint, _network_of

        if binding_required():
            payload["fp"] = compute_fingerprint(request)
            payload["net"] = _network_of(_client_ip(request))

    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, jwt_secret(), algorithm=jwt_algorithm())
    return token if isinstance(token, str) else token.decode()


def decode_access_token(token):
    """Access token doğrular. Geçersiz/süresi dolmuşsa None döner."""
    if not token:
        return None

    try:
        return jwt.decode(
            token,
            jwt_secret(),
            algorithms=[jwt_algorithm()],
            issuer="emog",
            options={"require": ["sub", "exp", "iat", "typ"]},
        )
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Geçersiz access token: %s", exc)
        return None


def access_token_remaining_seconds():
    return int(access_ttl().total_seconds())


# ============================================================
# REFRESH TOKEN (rotating)
# ============================================================

@transaction.atomic
def issue_refresh_token(user, request=None, family=None, session=None):
    """
    Yeni refresh token üretir ve DB'ye yazar.

    `family` verilmezse yeni token ailesi başlatılır.
    Her çağrı yeni bir satır oluşturur (rotasyon).
    """
    from agent_app.models import RefreshToken

    raw = secrets.token_urlsafe(48)
    now = timezone.now()

    record = RefreshToken.objects.create(
        user=user,
        token_hash=hash_token(raw),
        family=str(family) if family else secrets.token_hex(16),
        session=str(session) if session else secrets.token_hex(12),
        expires_at=now + refresh_ttl(),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:400] if request else ""),
        ip_address=_client_ip(request),
    )

    return raw, record


def _client_ip(request):
    if not request:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def rotate_refresh_token(raw_token, request=None):
    """
    Refresh token'ı yeniler (rotation).

    Returns: (yeni_access, yeni_refresh, hata_kodu)
    Başarılı: (access, refresh, None)
    Hata: (None, None, 'invalid' | 'expired' | 'revoked' | 'reuse_detected')
    """
    from agent_app.models import RefreshToken

    if not raw_token:
        return None, None, "invalid"

    token_hash = hash_token(raw_token)
    record = RefreshToken.objects.filter(token_hash=token_hash).select_related("user").first()

    if record is None:
        return None, None, "invalid"

    # Reuse detection: kullanılmış veya iptal edilmiş token tekrar gönderildi
    if record.is_revoked or record.rotated_at is not None:
        revoked_family = record.family
        count = RefreshToken.objects.filter(
            family=revoked_family, is_revoked=False
        ).exclude(pk=record.pk).update(
            is_revoked=True,
            revoked_at=timezone.now(),
            revoked_reason="reuse_detected",
        )
        logger.critical(
            "REFRESH TOKEN REUSE DETECTED - user=%s family=%s, %d token iptal edildi",
            record.user.email,
            revoked_family,
            count + 1,
        )
        _log_security_event(
            record.user,
            "refresh_token_reuse",
            "high",
            f"Refresh token yeniden kullanımı tespit edildi ({count + 1} token iptal)",
            request,
        )
        return None, None, "reuse_detected"

    if record.expires_at <= timezone.now():
        record.is_revoked = True
        record.revoked_at = timezone.now()
        record.revoked_reason = "expired"
        record.save(update_fields=["is_revoked", "revoked_at", "revoked_reason"])
        return None, None, "expired"

    # Kullanıcı pasifse tüm aile iptal
    if not record.user.is_active:
        RefreshToken.objects.filter(family=record.family, is_revoked=False).update(
            is_revoked=True, revoked_at=timezone.now(), revoked_reason="user_inactive"
        )
        return None, None, "revoked"

    # Rotasyon: eski token'ı kullanıldı olarak işaretle, yeni token üret
    record.rotated_at = timezone.now()
    record.is_revoked = True
    record.revoked_at = timezone.now()
    record.revoked_reason = "rotated"
    record.save(update_fields=["rotated_at", "is_revoked", "revoked_at", "revoked_reason"])

    new_refresh, _ = issue_refresh_token(
        record.user, request=request, family=record.family, session=record.session
    )
    access = create_access_token(record.user, request=request)

    return access, new_refresh, None


def revoke_all_for_user(user, reason="logout"):
    """Kullanıcının tüm refresh token'larını iptal eder."""
    from agent_app.models import RefreshToken

    return RefreshToken.objects.filter(user=user, is_revoked=False).update(
        is_revoked=True,
        revoked_at=timezone.now(),
        revoked_reason=reason,
    )


def revoke_family(raw_token, reason="logout"):
    """Tek bir token ailesini iptal eder."""
    from agent_app.models import RefreshToken

    record = RefreshToken.objects.filter(token_hash=hash_token(raw_token)).first()
    if record is None:
        return 0

    return RefreshToken.objects.filter(family=record.family, is_revoked=False).update(
        is_revoked=True,
        revoked_at=timezone.now(),
        revoked_reason=reason,
    )


def purge_expired():
    """Süresi dolmuş token kayıtlarını siler (bakım görevi)."""
    from agent_app.models import RefreshToken

    cutoff = timezone.now() - timedelta(days=1)
    deleted, _ = RefreshToken.objects.filter(expires_at__lt=cutoff).delete()
    return deleted


def _log_security_event(user, event_type, severity, title, request=None):
    try:
        from audit.models import SecurityEvent

        # SecurityEvent.user FK'si Django auth.User'ı referanslar;
        # uygulama kullanıcısı UUID taşıdığı için bağlanmadan kaydedilir.
        SecurityEvent.objects.create(
            event_type=event_type,
            severity=severity,
            user=None,
            ip_address=_client_ip(request) or "127.0.0.1",
            title=title,
            description=f"user={getattr(user, 'email', '')}",
        )
    except Exception as exc:
        logger.warning("Security event yazılamadı: %s", exc)


# ============================================================
# ÇEREZ YÖNETİMİ
# ============================================================

def set_auth_cookies(response, access_token, refresh_token):
    """Access ve refresh token'ı HttpOnly cookie olarak yazar."""
    max_age = int(refresh_ttl().total_seconds())
    secure = not settings.DEBUG
    samesite = getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax")

    response.set_cookie(
        ACCESS_COOKIE, access_token,
        httponly=True, secure=secure, samesite=samesite,
        max_age=access_token_remaining_seconds(),
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh_token,
        httponly=True, secure=secure, samesite=samesite, max_age=max_age,
        path="/auth/",
    )
    # JS'in yenileme zamanlaması için okunabilir TTL cookie'si (token içermez)
    response.set_cookie(
        "emog_token_ttl", str(access_token_remaining_seconds()),
        httponly=False, secure=secure, samesite=samesite,
        max_age=access_token_remaining_seconds(),
    )

    # CSRF double-submit degeri (csrf_exempt endpoint'ler icin)
    try:
        from agent_app.binding import set_csrf_cookie

        set_csrf_cookie(response)
    except Exception:
        pass

    return response


def clear_auth_cookies(response):
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE, path="/auth/")
    response.delete_cookie("emog_token_ttl")

    try:
        from agent_app.binding import CSRF_COOKIE

        response.delete_cookie(CSRF_COOKIE)
        response.delete_cookie("emog_csrf_raw")
    except Exception:
        pass

    return response


def read_tokens(request):
    """Request'ten access ve refresh token'ları okur (cookie > header)."""
    access = request.COOKIES.get(ACCESS_COOKIE)
    refresh = request.COOKIES.get(REFRESH_COOKIE)

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        access = auth_header[7:].strip()

    return access, refresh


def token_info(request):
    """Frontend için token durum bilgisi (token'ları açıklamadan).

    Şeffaf yenileme sırasında yeni üretilen access token varsa onun
    bilgisi döndürülür.
    """
    access = getattr(request, "_new_access_token", None)
    rotated = bool(access)

    if not access:
        access, _ = read_tokens(request)

    claims = decode_access_token(access)
    ttl_minutes = int(access_ttl().total_seconds() // 60)

    if claims is None:
        return {
            "authenticated": False,
            "access_expired": True,
            "access_expires_in": 0,
            "ttl_minutes": ttl_minutes,
            "rotated": rotated,
        }

    return {
        "authenticated": True,
        "access_expired": False,
        "access_expires_in": max(0, claims.get("exp", 0) - int(timezone.now().timestamp())),
        "ttl_minutes": ttl_minutes,
        "rotated": rotated,
        "user_id": claims.get("sub"),
    }