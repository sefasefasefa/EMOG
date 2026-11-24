"""
Sunucu tarafi yetkilendirme katmanı.

Arayüzdeki gizleme bir güvenlik önlemi DEĞİLDİR. Yetki her zaman
sunucuda doğrulanır:

  - public      : kimlik gerekmez (landing, sağlık, model listesi)
  - authenticated: geçerli access token (JWT) şart
  - feature     : üstüne özellik anahtarı açık olmalı
  - admin       : sadece yetkili hesap (SUPERUSER_EMAIL / whoches)
  - owner       : kaynağın sahibi olmak gerekir (IDOR koruması)

Kullanım:
    @require_auth
    @require_feature("tools_write_file")
    @require_admin
    def view(request): ...
"""
import functools
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)

# ============================================================
# YARDIMCI
# ============================================================

# API olarak islenen yol onekleri (her zaman JSON doner, sayfaya yonlendirilmez)
API_PREFIXES = (
    "/api/",
    "/audit/",
    "/whatsapp/",
    "/tools/",
    "/projects/api/",
    "/sandbox/",
    "/background-loop/",
    "/preview/",
    "/omni/",
    "/nvidia/",
    "/queue/",
    "/tasks/list/",
    "/task/",
    "/runs/list/",
    "/assistant/",
    "/exec_code",
    "/run_task",
    "/github/status/",
    "/github/repos/",
    "/github/create/",
    "/github/push/",
    "/github/device/",
    "/github/oauth/",
    "/github/disconnect/",
    "/github/debug/",
    "/settings/save/",
    "/auth/login/api/",
    "/auth/register/api/",
    "/auth/logout/",
    "/auth/settings/",
    "/user/presence/",
    "/auth/refresh/",
    "/auth/verify-email/",
    "/auth/resend-verification/",
    "/auth/oauth/",
)


def _wants_json(request):
    path = request.path or ""
    # /whatsapp/ bir tarayıcı sayfasıdır; alt uçlar (/whatsapp/status/ vb.)
    # API olarak JSON döner.
    if path != "/whatsapp/" and path.startswith(API_PREFIXES):
        return True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    if "application/json" in (request.headers.get("accept") or ""):
        return True
    return False


def is_page_request(request):
    """Tarayici sayfa istegi mi, API cagrisi mi?"""
    return not _wants_json(request)


def _deny(request, message, code, status, **extra):
    payload = {"error": message, "code": code, **extra}
    logger.warning(
        "AUTHZ DENIED - %s %s from %s (%s)",
        request.method, request.path,
        request.META.get("REMOTE_ADDR", "?"), code,
    )

    if status == 401 and is_page_request(request):
        # Tarayici sayfa isteginde login sayfasina yonlendir
        from django.shortcuts import redirect
        from urllib.parse import quote

        next_url = request.get_full_path()
        return redirect(f"/auth/login/?next={quote(next_url)}")

    return JsonResponse(payload, status=status)


def current_user(request):
    """Request'ten uygulama kullanıcısını çözer (JWT doğrulamalı)."""
    cached = getattr(request, "_emog_user_resolved", None)
    if cached is not None:
        return cached

    user = None
    try:
        from agent_app.auth_views import get_current_user

        user = get_current_user(request)
    except Exception as exc:
        logger.debug("Kullanici cozumleme hatasi: %s", exc)

    request._emog_user_resolved = user
    request.user_app = user
    return user


def is_admin(user):
    if user is None:
        return False
    try:
        from agent_app.security import is_authorized_identity

        return is_authorized_identity(user.email, user.username)
    except Exception:
        return False


def feature_enabled(key):
    try:
        from audit.feature_store import is_enabled

        return bool(is_enabled(key))
    except Exception:
        # Özellik durumu okunamıyorsa kapalı kabul et (fail-closed).
        logger.exception("Özellik durumu okunamadı: %s", key)
        return False


# ============================================================
# DEKORATÖRLER
# ============================================================

def require_auth(view_func=None, *, message=None):
    """
    Geçerli access token (JWT) şart. Yoksa 401.
    Access token süresi dolmuşsa backend şeffaf yenileme dener.
    """
    if view_func is None:
        return functools.partial(require_auth, message=message)

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = current_user(request)

        if user is None:
            return _deny(
                request,
                message or "Oturum gerekli. Lütfen giriş yapın.",
                "authentication_required",
                401,
                reauth_required=True,
            )

        if not getattr(user, "is_active", True):
            return _deny(request, "Hesap devre dışı.", "account_disabled", 403)

        response = view_func(request, *args, **kwargs)

        # Şeffaf yenileme sırasında üretilen cookie'leri yaz
        try:
            from agent_app.auth_views import maybe_set_rotated_cookies

            response = maybe_set_rotated_cookies(request, response)
        except Exception:
            pass

        return response

    return wrapper


def require_feature(key, message=None):
    """
    Özellik anahtarı açık olmalı + kimlik doğrulaması gerekli.
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not feature_enabled(key):
                return _deny(
                    request,
                    message or f"'{key}' özelliği yönetici panelinden devre dışı bırakılmış.",
                    "feature_disabled",
                    403,
                    feature=key,
                )
            return require_auth(view_func)(request, *args, **kwargs)

        return wrapper

    return decorator


def require_admin(message=None):
    """Sadece yetkili admin hesabı (SUPERUSER_EMAIL / whoches)."""
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = current_user(request)

            if user is None:
                return _deny(
                    request,
                    "Oturum gerekli. Lütfen giriş yapın.",
                    "authentication_required",
                    401,
                    reauth_required=True,
                )

            if not is_admin(user):
                return _deny(
                    request,
                    message or "Bu işlem için yetkiniz yok.",
                    "admin_required",
                    403,
                )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_owner(owner_field="user"):
    """
    Kaynağın sahibi olma şartı (IDOR koruması).

    View, nesneyi `owner` anahtarıyla döndürmeli veya
    `request.emog_owner` kontrolü yapılmalıdır.
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = current_user(request)

            if user is None:
                return _deny(
                    request,
                    "Oturum gerekli. Lütfen giriş yapın.",
                    "authentication_required",
                    401,
                    reauth_required=True,
                )

            owner = getattr(request, "emog_owner", None)

            if owner is None or str(owner) != str(user.id):
                return _deny(
                    request,
                    "Bu kaynağa erişim yetkiniz yok.",
                    "not_owner",
                    403,
                )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def csrf_protect_json(view_func=None):
    """
    CSRF-exempt POST endpoint'leri için JSON tabanlı CSRF doğrulama.

    Cookie tabanlı JWT kullanıldığı için SameSite=Lax tek başına
    yeterli değildir; istek gövdesinde CSRF token zorunlu kılınır.
    """
    if view_func is None:
        return functools.partial(csrf_protect_json)

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from django.middleware.csrf import CsrfViewMiddleware

        middleware = CsrfViewMiddleware(lambda req: None)

        reason = middleware.process_view(request, wrapper, (), {})
        if reason is not None:
            logger.warning("CSRF RED - %s %s", request.method, request.path)
            return _deny(
                request,
                "CSRF doğrulaması başarısız. Sayfayı yenileyip tekrar deneyin.",
                "csrf_failed",
                403,
            )

        return view_func(request, *args, **kwargs)

    return wrapper


# ============================================================
# PATHS
# ============================================================

def check_path_permission(request):
    """
    URL bazlı sunucu tarafı yetki kontrolü.

    Returns: None (izin var) veya JsonResponse (reddedildi)
    """
    path = request.path

    # Admin paneli kendi middleware'i ile korunuyor
    if path.startswith(("/admin/", "/management-portal-v1/")):
        return None

    # Herkese açık yollar
    if path in (
        "/", "/accounts/login/", "/api/whatsapp/webhook/",
        "/auth/login/", "/auth/register/", "/auth/login/api/",
        "/auth/register/api/", "/auth/status/", "/auth/refresh/",
        "/auth/logout/", "/auth/verify-email/", "/auth/resend-verification/",
        "/auth/oauth/login/google", "/auth/oauth/login/github",
        "/auth/oauth/login/apple", "/auth/oauth/login/facebook",
        "/assistant/message/",
    ):
        return None

    if path.startswith("/static/") or path.startswith("/media/"):
        return None

    # Canlı preview testi (Playwright) paylaşılan token ile erişebilir.
    if path.startswith("/preview/site/"):
        import hmac
        from django.conf import settings
        expected = str(getattr(settings, "PREVIEW_QA_TOKEN", "") or "")
        provided = request.META.get("HTTP_X_EMOG_QA_TOKEN", "")
        if expected and provided and hmac.compare_digest(str(provided), expected):
            return None

    user = current_user(request)

    if user is None:
        return _deny(
            request,
            "Oturum gerekli. Lütfen giriş yapın.",
            "authentication_required",
            401,
            reauth_required=True,
        )

    if not getattr(user, "is_active", True):
        return _deny(request, "Hesap devre dışı.", "account_disabled", 403)

    return None


def admin_only_paths():
    """Sadece admin hesabına açık yollar."""
    return (
        "/github/debug/",
        "/settings/save/",
    )