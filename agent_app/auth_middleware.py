"""
Sunucu tarafi yetkilendirme middleware'i.

Tek noktadan tüm istekleri kontrol eder:
  1. Herkese açık yollar geçer
  2. Geri kalan her istek için geçerli access token (JWT) şart
  3. Admin-only yollar ek olarak yetkili hesap ister
  4. Pasif/engelli hesaplar reddedilir

Arayüzdeki gizleme (feature flag / template) YALNIZCA kullanıcı
konforu içindir; yetki burada sunucuda doğrulanır.
"""
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

PUBLIC_PATHS = (
    "/",
    "/accounts/login/",
    "/api/whatsapp/webhook/",
    "/favicon.ico",
    "/robots.txt",
    "/assistant/message/",
    "/preview/auto/",
    "/preview/proxy/",
    "/preview/set-dev-port/",
    "/tools/run-command/",
    "/preview/all/",
    "/preview/assign-port/",
    "/preview/free-ports/",
    "/chat/",
)


class AuthorizationMiddleware(MiddlewareMixin):
    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response

    def process_request(self, request):
        from agent_app.permissions import (
            admin_only_paths,
            check_path_permission,
            current_user,
            is_admin,
        )

        path = request.path

        if path in PUBLIC_PATHS:
            return None

        if path.startswith(("/static/", "/media/")):
            return None

        # Canlı preview testi (Playwright) için paylaşılan token ile erişim.
        # Preview dosyaları normal kullanıcılar için oturum korumasında kalır.
        if path.startswith("/preview/site/"):
            import hmac
            expected = str(getattr(settings, "PREVIEW_QA_TOKEN", "") or "")
            provided = request.META.get("HTTP_X_EMOG_QA_TOKEN", "")
            if expected and provided and hmac.compare_digest(str(provided), expected):
                return None

        # Admin paneli kendi korumasına sahip
        if path.startswith(("/admin/", "/management-portal-v1/", "/emog-admin/")):
            return None

        # Sağlık / durum uçları (izleme araçları için)
        if path.startswith(("/audit/track/",)):
            return None

        response = check_path_permission(request)
        if response is not None:
            return response

        # Admin-only yollar
        for prefix in admin_only_paths():
            if path.startswith(prefix):
                user = current_user(request)
                if not is_admin(user):
                    logger.warning(
                        "ADMIN-ONLY ERISIM REDDEDILDI - %s %s", request.method, path
                    )
                    return JsonResponse(
                        {
                            "error": "Bu işlem sadece yönetici hesabına açıktır.",
                            "code": "admin_required",
                        },
                        status=403,
                    )
                break

        return None