"""Staff-only views for the local administration panel."""

import functools
import json

from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from agent_app.admin_models import AdminAuditLog, FeatureToggle, SystemSetting
from agent_app.models import Conversation, ImageJob, User, VideoJob


_TRUE_VALUES = {"1", "on", "true", "yes"}
_FALSE_VALUES = {"0", "off", "false", "no"}


def _staff_required(view_func):
    """Allow only authenticated Django staff or superusers."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = getattr(request, "user", None)
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        is_privileged = bool(
            getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
        )

        if not (is_authenticated and is_privileged):
            return HttpResponseForbidden("Yalnızca yöneticiler erişebilir.")

        return view_func(request, *args, **kwargs)

    return wrapper


def _request_data(request):
    """Return form or JSON request data as a mapping."""
    content_type = request.content_type or ""
    if "application/json" in content_type:
        try:
            data = json.loads(request.body or b"{}")
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Geçersiz JSON verisi.") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON verisi bir nesne olmalıdır.")
        return data
    return request.POST


def _parse_bool(value, field_name):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    raise ValueError(f"{field_name} değeri 'true' veya 'false' olmalıdır.")


def _create_audit_log(request, action, target):
    return AdminAuditLog.objects.create(
        admin_user=request.user,
        action=action,
        target=target,
        ip=request.META.get("REMOTE_ADDR"),
    )


def _maintenance_enabled():
    return SystemSetting.objects.filter(
        key="maintenance_mode",
        value="true",
    ).exists()


def _render_toggles(request, *, status=200, error=None):
    return render(
        request,
        "agent_app/admin_toggles.html",
        {
            "toggles": FeatureToggle.objects.all(),
            "error": error,
        },
        status=status,
    )


@_staff_required
@require_GET
def admin_dashboard(request):
    """Render the local administration dashboard."""
    stats = {
        "user_count": User.objects.count(),
        "conversation_count": Conversation.objects.count(),
        "media_job_count": VideoJob.objects.count() + ImageJob.objects.count(),
        "active_toggles_count": FeatureToggle.objects.filter(is_active=True).count(),
        "maintenance_mode": _maintenance_enabled(),
    }
    recent_logs = (
        AdminAuditLog.objects.select_related("admin_user")
        .order_by("-timestamp")[:10]
    )
    toggles = FeatureToggle.objects.all()

    return render(
        request,
        "agent_app/admin_dashboard.html",
        {
            "stats": stats,
            "recent_logs": recent_logs,
            "toggles": toggles,
        },
    )


@_staff_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def admin_feature_toggles(request):
    """List feature toggles or update one and record the action."""
    if request.method == "GET":
        return _render_toggles(request)

    try:
        data = _request_data(request)
    except ValueError as exc:
        return _render_toggles(request, status=400, error=str(exc))

    name = str(data.get("name") or "").strip()
    if not name:
        return _render_toggles(
            request,
            status=400,
            error="Özellik adı zorunludur.",
        )
    if "is_active" not in data:
        return _render_toggles(
            request,
            status=400,
            error="Özellik durumu zorunludur.",
        )

    try:
        is_active = _parse_bool(data.get("is_active"), "is_active")
    except ValueError as exc:
        return _render_toggles(request, status=400, error=str(exc))

    toggle = FeatureToggle.objects.filter(name=name).first()
    if toggle is None:
        return _render_toggles(
            request,
            status=404,
            error="Özellik toggle bulunamadı.",
        )

    toggle.is_active = is_active
    toggle.save(update_fields=["is_active", "updated_at"])
    _create_audit_log(request, "toggle_feature", toggle.name)

    return redirect("admin_feature_toggles")


@_staff_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def admin_toggle_maintenance(request):
    """Show maintenance controls or enable/disable maintenance mode."""
    if request.method == "GET":
        return render(
            request,
            "agent_app/admin_maintenance.html",
            {"maintenance_mode": _maintenance_enabled()},
        )

    try:
        data = _request_data(request)
    except ValueError as exc:
        return render(
            request,
            "agent_app/admin_maintenance.html",
            {"maintenance_mode": _maintenance_enabled(), "error": str(exc)},
            status=400,
        )

    value_key = next(
        (
            key
            for key in ("enabled", "is_active", "maintenance_mode", "value")
            if key in data
        ),
        None,
    )
    if value_key is None:
        enabled = not _maintenance_enabled()
    else:
        try:
            enabled = _parse_bool(data.get(value_key), value_key)
        except ValueError as exc:
            return render(
                request,
                "agent_app/admin_maintenance.html",
                {"maintenance_mode": _maintenance_enabled(), "error": str(exc)},
                status=400,
            )

    setting_value = "true" if enabled else "false"
    SystemSetting.objects.update_or_create(
        key="maintenance_mode",
        defaults={"value": setting_value},
    )
    _create_audit_log(
        request,
        "maintenance_on" if enabled else "maintenance_off",
        "maintenance_mode",
    )

    return redirect("admin_dashboard")


@_staff_required
@require_GET
def admin_system_logs(request):
    """Render the latest 50 administration audit entries."""
    logs = (
        AdminAuditLog.objects.select_related("admin_user")
        .order_by("-timestamp")[:50]
    )
    return render(
        request,
        "agent_app/admin_logs.html",
        {"logs": logs},
    )
