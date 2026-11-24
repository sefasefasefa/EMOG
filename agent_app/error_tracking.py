"""
Error Tracking Middleware - Captures full request context for debugging.
Logs stored on user's device, sent to server only when errors occur.
"""
import json
import traceback
import uuid
import logging
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password", "token", "secret", "key", "authorization", "csrf",
    "cvv", "ssn", "api_key", "apikey", "access_token", "refresh_token",
    "private_key", "secret_key", "signing_key", "encryption_key",
    "jwt", "sessionid", "session_id", "otp", "totp", "mfa",
}

def get_client_ip(request):
    """Extract client IP from request."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")

def sanitize_body(body):
    """Remove sensitive data from request body."""
    if not body:
        return None
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "[binary]"
    except Exception:
        return "[unparseable]"
    
    def clean(obj):
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if any(s in k.lower() for s in SENSITIVE_KEYS) else clean(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj
    
    return clean(data)

def sanitize_headers(headers):
    """Remove sensitive headers."""
    sanitized = {}
    for k, v in headers.items():
        if any(s in k.lower() for s in SENSITIVE_KEYS):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized

class ErrorTrackingMiddleware(MiddlewareMixin):
    """
    Middleware to capture request context for error tracking.
    Attaches context to request.error_context for use in exception handler.
    """
    
    def process_request(self, request):
        request_id = uuid.uuid4().hex[:12]
        request.error_context = {
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "full_path": request.get_full_path(),
            "user": str(request.user) if hasattr(request, "user") and request.user.is_authenticated else "anonymous",
            "user_id": request.user.id if hasattr(request, "user") and request.user.is_authenticated else None,
            "ip": get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "session_key": request.session.session_key if hasattr(request, "session") else None,
            "timestamp": timezone.now().isoformat(),
            "headers": sanitize_headers(dict(request.META)),
            "body": sanitize_body(request.body) if request.method in ("POST", "PUT", "PATCH", "DELETE") else None,
            "query_params": dict(request.GET),
        }
        return None
    
    def process_response(self, request, response):
        # Attach request ID to response for client correlation
        if hasattr(request, "error_context"):
            response["X-Request-ID"] = request.error_context["request_id"]
        return response
    
    def process_exception(self, request, exception):
        """Capture full exception with context."""
        if not hasattr(request, "error_context"):
            return None
        
        error_data = {
            "request_id": request.error_context["request_id"],
            "exception_type": type(exception).__name__,
            "exception_message": str(exception),
            "traceback": traceback.format_exc(),
            # logger.error aşağıda %(path)s / %(user)s bekliyor; bunlar
            # eksik olunca KeyError fırlıyor ve asıl hata gizleniyordu.
            "path": request.error_context.get("path", ""),
            "user": request.error_context.get("user", "anonymous"),
            "context": request.error_context,
        }
        
        # Log to server logs
        logger.error(
            "Unhandled exception: %(exception_type)s: %(exception_message)s\n"
            "Request ID: %(request_id)s\n"
            "Path: %(path)s\n"
            "User: %(user)s\n"
            "Traceback:\n%(traceback)s",
            error_data,
            extra={"request_id": request.error_context["request_id"]},
        )
        
        # Store in database if model exists
        self._store_error_report(error_data)
        
        return None
    
    def _store_error_report(self, error_data):
        """Store error report in database if available."""
        try:
            from .models import ErrorReport
            ErrorReport.objects.create(
                request_id=error_data["request_id"],
                exception_type=error_data["exception_type"],
                exception_message=error_data["exception_message"][:1000],
                traceback=error_data["traceback"][:10000],
                context=error_data["context"],
            )
        except Exception:
            # Model might not exist or DB unavailable
            pass


class ClientErrorTrackingMiddleware(MiddlewareMixin):
    """
    Middleware to handle client-side error reports sent via /api/logs/error/
    """
    
    def process_request(self, request):
        if request.path == "/api/logs/error/" and request.method == "POST":
            return self._handle_client_error_report(request)
        return None
    
    def _handle_client_error_report(self, request):
        """Process client-side error report with full action history."""
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except Exception as e:
            logger.warning("Invalid client error report: %s", e)
            from django.http import JsonResponse
            return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
        
        session_id = payload.get("sessionId")
        request_id = payload.get("requestId")
        error = payload.get("error", {})
        logs = payload.get("logs", [])
        checkpoints = payload.get("checkpoints", [])
        
        # Log summary
        logger.error(
            "Client error report received\n"
            "Session: %(session_id)s\n"
            "Request: %(request_id)s\n"
            "Error: %(message)s\n"
            "Source: %(source)s\n"
            "Steps: %(steps)d\n"
            "Checkpoints: %(checkpoints)d",
            {
                "session_id": session_id,
                "request_id": request_id,
                "message": error.get("message", "")[:500],
                "source": error.get("source", "unknown"),
                "steps": len(logs),
                "checkpoints": len(checkpoints),
            },
        )
        
        # Store full report
        self._store_client_error_report(
            session_id=session_id,
            request_id=request_id,
            error=error,
            logs=logs,
            checkpoints=checkpoints,
            user_agent=payload.get("userAgent", ""),
            url=payload.get("url", ""),
        )
        
        # Notify developer
        self._notify_developer(session_id, request_id, error, logs, checkpoints)
        
        from django.http import JsonResponse
        return JsonResponse({"ok": True})
    
    def _store_client_error_report(self, session_id, request_id, error, logs, checkpoints, user_agent, url):
        """Store client error report in database."""
        try:
            from .models import ClientErrorReport
            ClientErrorReport.objects.create(
                session_id=session_id,
                request_id=request_id,
                error_message=error.get("message", "")[:1000],
                error_stack=error.get("stack", "")[:10000],
                error_source=error.get("source", "unknown"),
                client_logs=logs,
                checkpoints=checkpoints,
                user_agent=user_agent,
                url=url,
            )
        except Exception as e:
            logger.warning("Failed to store client error report: %s", e)
    
    def _notify_developer(self, session_id, request_id, error, logs, checkpoints):
        """Notify developer of error (email, Slack, etc.)."""
        try:
            # Import notification system if available
            from .notifications import notify_error
            notify_error(
                subject=f"Client Error: {error.get('message', 'Unknown')[:100]}",
                message=self._format_error_notification(session_id, request_id, error, logs, checkpoints),
            )
        except ImportError:
            # Notification system not configured
            pass
        except Exception as e:
            logger.warning("Failed to notify developer: %s", e)
    
    def _format_error_notification(self, session_id, request_id, error, logs, checkpoints):
        """Format error notification for developer."""
        lines = [
            f"Session: {session_id}",
            f"Request ID: {request_id}",
            f"Error: {error.get('message', 'Unknown')}",
            f"Source: {error.get('source', 'unknown')}",
            f"Stack:\n{error.get('stack', 'No stack trace')}",
            "",
            f"Total steps captured: {len(logs)}",
            f"Checkpoints: {len(checkpoints)}",
            "",
            "Last 20 actions:",
        ]
        
        for log in logs[-20:]:
            lines.append(
                f"  [{log.get('timestamp', '')}] Step {log.get('step', '?')}: "
                f"{log.get('type', '?')} - {log.get('data', {})}"
            )
        
        return "\n".join(lines)