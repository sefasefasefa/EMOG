import json
import logging
import time

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password", "password1", "password2", "new_password1", "new_password2",
    "old_password", "token", "access_token", "refresh_token", "api_key",
    "secret", "otp_token", "authorization", "cookie", "sessionid", "csrfmiddlewaretoken",
    "code", "otp", "totp", "private_key",
}

SKIP_PREFIXES = (
    "/static/", "/media/", "/favicon",
    "/management-portal-v1/static/",
)

SKIP_PATHS = (
    "/task/stream/", "/tools/file-tree", "/background-loop/status",
    "/auth/status", "/runs/list/", "/queue/list/", "/tasks/list/",
)

MAX_BODY_BYTES = 8 * 1024


def _redact(value, depth=0):
    """Hassas alanları maskeler."""
    if depth > 6:
        return "..."
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS:
                out[key] = "***"
            else:
                out[key] = _redact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:50]]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "..."
    return value


class AuditMiddleware(MiddlewareMixin):
    """HTTP isteklerini denetim kaydına yazar (hassas veriler maskelenir)."""

    def __call__(self, request):
        should_log = not self._skip(request)
        start = time.time()

        if should_log:
            request_body = self._read_body(request)
        else:
            request_body = {}

        response = self.get_response(request)

        if should_log:
            try:
                self._log(request, response, int((time.time() - start) * 1000), request_body)
            except Exception as exc:  # denetim asla uygulamayı bozmamalı
                logger.error("Audit log error: %s", exc)

        return response

    def _skip(self, request):
        path = request.path
        if path.startswith(SKIP_PREFIXES):
            return True
        if any(path.startswith(p) for p in SKIP_PATHS):
            return True
        if path.startswith("/audit/"):
            return True
        return False

    def _read_body(self, request):
        if request.method not in ("POST", "PUT", "PATCH"):
            return {}
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            return {"_truncated": True, "_size": length}
        try:
            content_type = (request.content_type or "").lower()
            if content_type.startswith("application/json"):
                return _redact(json.loads(request.body.decode("utf-8")))
            if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
                return _redact({k: v for k, v in request.POST.items()})
        except Exception:
            return {"_parse_error": True}
        return {}

    def _log(self, request, response, duration_ms, request_body):
        from audit.models import AuditLog
        from audit.tracking import extract_profile, is_api_request

        profile = extract_profile(request)
        user = getattr(request, "user", None)
        if user is not None and not user.is_authenticated:
            user = None

        action = {
            "GET": "api_call",
            "HEAD": "api_call",
            "OPTIONS": "api_call",
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }.get(request.method, "api_call")

        status = getattr(response, "status_code", 0)
        api_call = is_api_request(request.path)

        AuditLog.objects.create(
            user=user,
            user_email=getattr(user, "email", "") or "",
            action=action,
            model_name=self._extract_model_name(request.path),
            object_id="",
            object_repr=f"{request.method} {request.path}",
            changes={},
            ip_address=profile["ip_address"],
            user_agent=profile["user_agent"],
            referer=request.META.get("HTTP_REFERER", "")[:500],
            request_method=request.method,
            request_path=request.path[:500],
            request_body=request_body,
            response_status=status,
            response_time_ms=duration_ms,
            is_api=api_call,
            session_key=profile["session_key"],
            country=profile["country"],
            language=profile["language"],
            device=profile["device"],
        )

        self._touch_session(request, profile, api_call, status)

    def _touch_session(self, request, profile, api_call, status):
        """Kullanıcı oturumunu (cihaz/ekran/dil/istek sayısı) günceller."""
        from django.utils import timezone

        from audit.models import VisitorSession

        now = timezone.now()
        try:
            session, _ = VisitorSession.objects.get_or_create(
                session_key=profile["session_key"],
                defaults={
                    "ip_address": profile["ip_address"],
                    "country": profile["country"],
                    "language": profile["language"],
                    "device": profile["device"],
                    "os": profile["os"],
                    "browser": profile["browser"],
                    "user_agent": profile["user_agent"],
                    "referer": request.META.get("HTTP_REFERER", "")[:500],
                    "first_seen": now,
                    "last_seen": now,
                },
            )
        except Exception as exc:
            logger.warning("VisitorSession güncellenemedi: %s", exc)
            return

        session.last_seen = now
        session.request_count = (session.request_count or 0) + 1
        if api_call:
            session.api_count = (session.api_count or 0) + 1
        if status and status >= 400:
            session.error_count = (session.error_count or 0) + 1

        django_user = getattr(request, "user", None)
        if django_user is not None and django_user.is_authenticated and not session.user_id:
            session.user = django_user

        if not session.country and profile["country"]:
            session.country = profile["country"]
        if not session.language and profile["language"]:
            session.language = profile["language"]

        try:
            session.save(
                update_fields=[
                    "last_seen", "request_count", "api_count", "error_count",
                    "user", "country", "language",
                ]
            )
        except Exception as exc:
            logger.warning("VisitorSession kaydedilemedi: %s", exc)

    def _get_client_ip(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    def _extract_model_name(self, path):
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) >= 2 and parts[0] in ("api", "management-portal-v1"):
            return parts[1].rstrip("s")
        return "unknown"
