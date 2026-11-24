"""
Kapalı özelliklere ait URL'leri engelleyen middleware.

Uygulamanın kendi endpoint'leri (API, panel, projeler) engellenir.
Admin paneli, statik dosyalar, auth ve oturum 2FA yolları her zaman açık kalır.
"""
import logging

from django.http import JsonResponse
from django.http import HttpResponseNotFound

logger = logging.getLogger(__name__)

ALWAYS_ALLOWED = (
    "/static/",
    "/media/",
    "/admin/",
    "/management-portal-v1/",
    "/favicon",
)


class FeatureGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from audit.feature_store import blocked_paths

        path = request.path

        if path.startswith(ALWAYS_ALLOWED):
            return self.get_response(request)

        for prefix, keys in blocked_paths().items():
            if path == prefix or path.startswith(prefix):
                if request.path_info == "/auth/status/":
                    return self.get_response(request)

                logger.info(
                    "FEATURE BLOCKED - path=%s features=%s user=%s",
                    path, keys, getattr(request.user, "email", "anonim"),
                )

                if request.headers.get("accept", "").startswith("application/json") or request.headers.get(
                    "x-requested-with"
                ) == "XMLHttpRequest":
                    return JsonResponse(
                        {
                            "error": "Bu özellik yönetici panelinden devre dışı bırakılmış.",
                            "disabled_features": keys,
                            "path": path,
                        },
                        status=403,
                    )

                return HttpResponseNotFound(
                    "<!doctype html><html><head><meta charset='utf-8'><title>404</title></head>"
                    "<body style=\"font-family:system-ui,sans-serif;background:#0a0f1a;color:#e2e8f0;"
                    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0\">"
                    "<div style='text-align:center'>"
                    "<h1 style='color:#94a3b8;font-size:44px;margin:0 0 8px'>404</h1>"
                    "<p>Bu bölüm şu anda devre dışı.</p>"
                    "</div></body></html>",
                    content_type="text/html",
                )

        return self.get_response(request)