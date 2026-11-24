"""
Request Binding Middleware - isteminin ayni kisi oldugunu her istekte dogrular.

Kontrol edilenler:
  1. ACCESS TOKEN PARMAK IZI: token'daki fp ile mevcut istegin fp'si ayni mi?
     Farkliysa -> 401 (token baska istemcide kullaniliyor = calinma isareti)
  2. IP BLOGU (net): token'da kayitli ag ile mevcut ag ayni mi?
     Mobil tolerans: token yeni ise kisa surede IP degisimi kabul edilir
  3. CSRF DOUBLE-SUBMIT: csrf_exempt mutasyon isteklerinde
     X-CSRF-Token header'i cookie ile eslesmeli

Ihlal durumunda:
  - 401 (baglanti hatasi) veya 403 (csrf hatasi)
  - Kullanici token'larinin TUMU iptal edilir
  - SecurityEvent (severity: critical) yazilir
"""
import logging

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

# CSRF korumasi gereken mutasyon yollari (csrf_exempt olanlar dahil)
CSRF_PROTECTED_PREFIXES = (
    "/auth/login/api/",
    "/auth/register/api/",
    "/auth/logout/",
    "/auth/refresh/",
    "/tools/write-file/",
    "/projects/api/",
    "/github/create/",
    "/github/push/",
    "/github/disconnect/",
    "/github/device/",
    "/github/debug/set-token/",
    "/settings/save/",
    "/whatsapp/connect/",
    "/whatsapp/disconnect/",
    "/whatsapp/send/",
    "/run_task/",
    "/run_task_no_csrf/",
    "/exec_code_no_csrf/",
    "/assistant/message/",
    "/background-loop/",
    "/preview/analyze-design/",
    "/preview/visual-qa/",
    "/sandbox/",
)

SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")

# CSRF cookie'si henuz olmayabilen yollar (kimlik dogrulamanin kendisi).
# Saldirgan kurbanin tarayicisinda oturum acamaz; ayrica SameSite=Lax
# korumasi uygulanir. Cookie varsa yine de dogrulanir.
CSRF_OPTIONAL_PREFIXES = (
    "/auth/login/api/",
    "/auth/register/api/",
    "/auth/verify-email/",
    "/auth/resend-verification/",
    "/assistant/message/",
)


class RequestBindingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        from agent_app.binding import (
            log_binding_violation,
            verify_binding,
            verify_csrf,
        )
        from agent_app.tokens import decode_access_token, read_tokens

        if request.path.startswith(("/static/", "/media/")):
            return None

        access, _ = read_tokens(request)

        # --- 1) Access token varsa parmak izi dogrula ---
        if access:
            claims = decode_access_token(access)

            if claims is not None:
                ok, code = verify_binding(request, claims)
                if not ok:
                    log_binding_violation(request, code, claims=claims)
                    return JsonResponse(
                        {
                            "error": (
                                "Oturum bağlantısı doğrulanamadı. "
                                "Güvenlik nedeniyle oturum sonlandırıldı."
                            ),
                            "code": code,
                            "reauth_required": True,
                        },
                        status=401,
                    )

        # --- 2) CSRF double-submit (mutasyon istekleri) ---
        if request.method not in SAFE_METHODS and request.path.startswith(
            CSRF_PROTECTED_PREFIXES
        ):
            from agent_app.binding import CSRF_COOKIE

            has_cookie = bool(request.COOKIES.get(CSRF_COOKIE))

            if request.path.startswith(CSRF_OPTIONAL_PREFIXES) and not has_cookie:
                # Henuz oturum yok: gecir (SameSite + dogrulanamayan kaynak)
                return None

            csrf_ok, csrf_code = verify_csrf(request)
            if not csrf_ok:
                claims = decode_access_token(access) if access else None
                # CSRF hatasinda token iptali YAPILMAZ: aksi halde
                # siteler-arasi basit bir istekle kullanici oturumu kapatilabilir
                log_binding_violation(
                    request, csrf_code, claims=claims, revoke=False
                )
                return JsonResponse(
                    {
                        "error": "CSRF doğrulaması başarısız. Sayfayı yenileyin.",
                        "code": csrf_code,
                    },
                    status=403,
                )

        return None