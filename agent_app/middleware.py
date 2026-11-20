"""
EMOG projesine özel middleware'ler.

- MaintenanceMiddleware: Sistem bakım modunda (SystemSetting key="maintenance_mode",
  value="true")yken /emog-admin/ hariç tüm istekleri 503 maintenance sayfasına yönlendirir.
  Yöneticiler (is_staff / is_superuser) her erişebilir.
- FeatureToggleMiddleware: Belirli üretim endpoints'lerini ilgili FeatureToggle kapalıysa
  engeller (şu an /api/video/generate/ ve /api/image/generate/ için video_generation /
  image_generation toggle'larını kontrol eder).
"""

from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponse
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin


class MaintenanceMiddleware(MiddlewareMixin):
    """Sistem bakım modu koruması."""

    def process_request(self, request):
        try:
            from agent_app.admin_models import SystemSetting

            maintenance = SystemSetting.objects.filter(
                key="maintenance_mode", value="true"
            ).exists()
        except Exception:
            # Tablolar henüz oluşturulmamış olabilir (ilk migrate önce) — pas geç.
            return None

        if not maintenance:
            return None

        # Yöneticiler ve admin paneli her erişebilir.
        if (
            request.path.startswith("/emog-admin/")
            or getattr(request, "user", None) is not None
            and (request.user.is_staff or request.user.is_superuser)
        ):
            return None

        return render(
            request,
            "agent_app/admin_maintenance.html",
            {
                "message": "Sistem şu anda bakımda. Lütfen daha sonra tekrar deneyin.",
            },
            status=503,
        )


class FeatureToggleMiddleware(MiddlewareMixin):
    """Belirli üretim endpoints'ini ilgili FeatureToggle kapalıysa engeller."""

    # Yol öneki -> (toggle_name, hata mesajı)
    TOGGLE_ROUTES = {
        "/api/video/generate/": ("video_generation", "Video üretimi şu anda kapalı."),
        "/api/image/generate/": ("image_generation", "Görsel üretimi şu anda kapalı."),
    }

    def process_request(self, request):
        path = request.path

        for prefix, (toggle_name, message) in self.TOGGLE_ROUTES.items():
            if not path.startswith(prefix):
                continue

            try:
                from agent_app.admin_models import FeatureToggle

                toggle = FeatureToggle.objects.filter(name=toggle_name).first()
            except Exception:
                # Tablolar henüz oluşturulmamış olabilir — pas geç.
                return None

            if toggle is not None and not toggle.is_active:
                return HttpResponseForbidden(message)

        return None
