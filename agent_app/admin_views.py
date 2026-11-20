"""Staff-only views for the local administration panel."""

import functools
import json

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.template import TemplateDoesNotExist, loader
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST, require_http_methods

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


def _serialize_toggle(toggle):
    return {
        "name": toggle.name,
        "is_active": toggle.is_active,
        "description": toggle.description,
        "created_at": toggle.created_at.isoformat(),
        "updated_at": toggle.updated_at.isoformat(),
    }


def _serialize_audit_log(log):
    admin_user = log.admin_user
    username = None
    if admin_user is not None:
        username = (
            admin_user.get_username()
            if hasattr(admin_user, "get_username")
            else str(admin_user)
        )

    return {
        "admin_user": username,
        "action": log.action,
        "target": log.target,
        "timestamp": log.timestamp.isoformat(),
        "ip": log.ip,
    }


def _create_audit_log(request, action, target):
    return AdminAuditLog.objects.create(
        admin_user=request.user,
        action=action,
        target=target,
        ip=request.META.get("REMOTE_ADDR"),
    )


@_staff_required
@require_GET
def admin_dashboard(request):
    """Return dashboard statistics, optionally rendering the admin template."""
    video_job_count = VideoJob.objects.count()
    image_job_count = ImageJob.objects.count()
    active_toggle_count = FeatureToggle.objects.filter(is_active=True).count()
    stats = {
        "total_users": User.objects.count(),
        "total_conversations": Conversation.objects.count(),
        "total_video_jobs": video_job_count,
        "total_image_jobs": image_job_count,
        "total_jobs": video_job_count + image_job_count,
        "active_feature_toggles": active_toggle_count,
        "active_toggles": active_toggle_count,
        "maintenance_mode": SystemSetting.objects.filter(
            key="maintenance_mode", value="true"
        ).exists(),
    }
    recent_logs = [
        _serialize_audit_log(log)
        for log in AdminAuditLog.objects.select_related("admin_user").order_by(
            "-timestamp"
        )[:5]
    ]
    toggles = [
        _serialize_toggle(toggle) for toggle in FeatureToggle.objects.all()
    ]
    context = {
        "stats": stats,
        "recent_logs": recent_logs,
        "logs": recent_logs,
        "toggles": toggles,
    }

    try:
        template = loader.get_template("agent_app/admin_dashboard.html")
    except TemplateDoesNotExist:
        payload = {"stats": stats, "recent_logs": recent_logs, "logs": recent_logs}
        payload.update(stats)
        return JsonResponse(payload)

    return HttpResponse(template.render(context, request))


@_staff_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def admin_feature_toggles(request):
    """List feature toggles or update one and record the action."""
    if request.method == "GET":
        toggles = [
            _serialize_toggle(toggle) for toggle in FeatureToggle.objects.all()
        ]
        return JsonResponse({"toggles": toggles, "count": len(toggles)})

    try:
        data = _request_data(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    name = str(data.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name alanı zorunludur."}, status=400)
    if "is_active" not in data:
        return JsonResponse(
            {"error": "is_active alanı zorunludur."},
            status=400,
        )

    try:
        is_active = _parse_bool(data.get("is_active"), "is_active")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    toggle = FeatureToggle.objects.filter(name=name).first()
    if toggle is None:
        return JsonResponse({"error": "Özellik toggle bulunamadı."}, status=404)

    toggle.is_active = is_active
    toggle.save(update_fields=["is_active", "updated_at"])
    _create_audit_log(request, "toggle_feature", toggle.name)

    return JsonResponse({"success": True, "toggle": _serialize_toggle(toggle)})


@_staff_required
@csrf_protect
@require_POST
def admin_toggle_maintenance(request):
    """Enable or disable maintenance mode and record the action."""
    try:
        data = _request_data(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    value_key = next(
        (
            key
            for key in ("enabled", "is_active", "maintenance_mode", "value")
            if key in data
        ),
        None,
    )
    if value_key is None:
        current = SystemSetting.objects.filter(key="maintenance_mode").first()
        enabled = not bool(current and current.value == "true")
    else:
        try:
            enabled = _parse_bool(data.get(value_key), value_key)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

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

    return JsonResponse(
        {
            "success": True,
            "maintenance_mode": enabled,
            "value": setting_value,
        }
    )


@_staff_required
@require_GET
def admin_system_logs(request):
    """Return the latest 50 administration audit entries."""
    logs = [
        _serialize_audit_log(log)
        for log in AdminAuditLog.objects.select_related("admin_user").order_by(
            "-timestamp"
        )[:50]
    ]
    return JsonResponse({"logs": logs, "count": len(logs)})
