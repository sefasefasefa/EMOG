"""Admin paneli ana sayfası (dashboard) görünümü."""
from django.shortcuts import render

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from audit.dashboard import build_dashboard, max_value
from audit.feature_store import grouped_states, reset_all, set_enabled


def features_view(request):
    """EMOG özellik anahtarları yönetim paneli."""
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "toggle":
            key = request.POST.get("key", "")
            try:
                flag = set_enabled(key, request.POST.get("enabled") == "1", user=request.user)
                state = "açıldı" if flag.enabled else "kapatıldı"
                messages.success(request, f"'{flag.label}' {state}.")
            except KeyError:
                messages.error(request, "Bilinmeyen özellik anahtarı.")

        elif action == "reset":
            changed = reset_all()
            messages.success(request, f"{changed} özellik varsayılan değerine döndürüldü.")

        return redirect(reverse("admin:features"))

    context = {
        "title": "Özellik Anahtarları",
        "features": grouped_states(),
        **(admin_base_context(request) or {}),
    }
    return render(request, "admin/features.html", context)


def admin_base_context(request):
    """Unfold admin şablonlarının ihtiyaç duyduğu ortak veriler."""
    from django.contrib import admin

    try:
        site = admin.site
        return site.each_context(request)
    except Exception:
        return {}


def dashboard_view(request, extra_context=None):
    """EMOG kullanım analitiği paneli."""
    try:
        hours = int(request.GET.get("hours", 24))
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(hours, 24 * 90))

    data = build_dashboard(hours=hours)

    # Çubuk grafik ölçekleri
    data["max"] = {
        "timeline": max([row["total"] for row in data["timeline"]] or [1]),
        "countries": max_value(data["countries"]),
        "languages": max_value(data["languages"]),
        "devices": max_value(data["devices"]),
        "browsers": max_value(data["browsers"]),
        "operating_systems": max_value(data["operating_systems"]),
        "screens": max_value(data["screens"]),
        "methods": max_value(data["methods"]),
        "statuses": max_value(data["statuses"]),
        "top_users": max_value(data["top_users"]),
        "top_endpoints": max_value(data["top_endpoints"]),
        "top_ips": max_value(data["top_ips"]),
    }

    context = {
        "title": "EMOG Kullanım Paneli",
        "dashboard": data,
        **(admin_base_context(request) or {}),
        **(extra_context or {}),
    }
    return render(request, "admin/dashboard.html", context)