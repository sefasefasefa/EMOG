# Error Tracking Standard - Mandatory

All errors must be tracked with full context. Logs stored on USER's device, sent only on error.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Client JS     │     │   Django App    │     │  User's Browser │
│  (error.js)     │────▶│  (middleware)   │     │  (localStorage) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │ 1. Capture all steps  │ 2. Capture request    │ 3. Store logs
        │    (clicks, AJAX,     │    context, stack     │    locally
        │     navigation)       │    traces             │    (ring buffer)
        │                       │                       │
        ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ERROR OCCURS                                 │
└─────────────────────────────────────────────────────────────────┘
        │                       │                       │
        └───────────────────────┼───────────────────────┘
                                ▼
                    ┌─────────────────────┐
                    │  POST /api/logs/    │
                    │  (batched logs)     │
                    └─────────────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │  Developer receives │
                    │  error report with  │
                    │  FULL context       │
                    └─────────────────────┘
```

## Rules

1. **Every request tracked** - Middleware captures: method, path, user, IP, headers, body (sanitized), session
2. **Every step logged** - Client captures: clicks, form submissions, AJAX calls, navigation, console errors
3. **Ring buffer** - Max 500 entries per session, auto-evicts oldest
4. **Send on error only** - Logs stay on user's device until error occurs
5. **Full stack traces** - Both client (JS stack) and server (Python traceback)
6. **Code location** - File, line, function for every log entry
7. **30-step checkpoint** - Every 30 user actions, create a sync point

## Server-Side (Django Middleware)

```python
# agent_app/error_tracking.py
class ErrorTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Capture request context
        request.error_context = {
            "request_id": uuid.uuid4().hex[:12],
            "method": request.method,
            "path": request.path,
            "user": str(request.user) if request.user.is_authenticated else "anonymous",
            "ip": get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "session_key": request.session.session_key,
            "timestamp": timezone.now().isoformat(),
            "body": sanitize_body(request.body) if request.method in ("POST", "PUT", "PATCH") else None,
        }
        
        # Track SQL queries, cache hits, etc.
        response = self.get_response(request)
        
        # Attach context to response for client correlation
        response["X-Request-ID"] = request.error_context["request_id"]
        return response
    
    def process_exception(self, request, exception):
        # Capture full traceback + request context
        log_error(
            request=request,
            exception=exception,
            context=request.error_context,
            traceback=traceback.format_exc(),
        )
        return None  # Let default handler process
```

## Client-Side (error.js)

```javascript
// static/js/error-tracking.js
class ErrorTracker {
    constructor() {
        this.logs = [];
        this.maxLogs = 500;
        this.stepCount = 0;
        this.sessionId = this.getOrCreateSessionId();
        this.init();
    }
    
    init() {
        // Capture unhandled errors
        window.addEventListener("error", (e) => this.captureError(e.error, "unhandled"));
        window.addEventListener("unhandledrejection", (e) => this.captureError(e.reason, "promise"));
        
        // Capture console.error
        const origError = console.error;
        console.error = (...args) => {
            this.addLog("console_error", { args: args.map(String) });
            origError.apply(console, args);
        };
        
        // Capture clicks, form submits, fetch/XHR
        document.addEventListener("click", (e) => this.logAction("click", e.target), true);
        document.addEventListener("submit", (e) => this.logAction("submit", e.target), true);
        this.patchFetch();
        this.patchXHR();
    }
    
    addLog(type, data) {
        this.logs.push({
            timestamp: new Date().toISOString(),
            type,
            data,
            step: ++this.stepCount,
            sessionId: this.sessionId,
            url: window.location.href,
            userAgent: navigator.userAgent,
        });
        if (this.logs.length > this.maxLogs) this.logs.shift();
        
        // Every 30 steps, create checkpoint
        if (this.stepCount % 30 === 0) {
            this.createCheckpoint();
        }
    }
    
    captureError(error, source) {
        const payload = {
            sessionId: this.sessionId,
            requestId: this.getRequestId(),
            error: {
                message: error?.message || String(error),
                stack: error?.stack,
                source,
            },
            logs: this.logs, // Full context!
            checkpoints: this.checkpoints,
        };
        
        // Send to server
        navigator.sendBeacon("/api/logs/error/", JSON.stringify(payload));
    }
    
    createCheckpoint() {
        this.checkpoints.push({
            step: this.stepCount,
            logsCount: this.logs.length,
            timestamp: new Date().toISOString(),
        });
    }
}

// Auto-init
window.errorTracker = new ErrorTracker();
```

## API Endpoint

```python
# agent_app/views/logs.py
@csrf_exempt
@require_POST
def receive_error_logs(request):
    """Receive batched logs from client on error."""
    try:
        payload = json.loads(request.body)
        session_id = payload.get("sessionId")
        error = payload.get("error")
        logs = payload.get("logs", [])
        checkpoints = payload.get("checkpoints", [])
        
        # Store in database for developer review
        ErrorReport.objects.create(
            session_id=session_id,
            request_id=payload.get("requestId"),
            error_message=error.get("message"),
            error_stack=error.get("stack"),
            error_source=error.get("source"),
            client_logs=logs,
            checkpoints=checkpoints,
            user_agent=payload.get("userAgent"),
            url=payload.get("url"),
            created_at=timezone.now(),
        )
        
        # Notify developer (email, Slack, etc.)
        notify_developer(error_report)
        
        return JsonResponse({"ok": True})
    except Exception as e:
        logger.exception("Failed to receive error logs")
        return JsonResponse({"ok": False}, status=500)
```

## Data Sanitization

**NEVER log:**
- Passwords, tokens, API keys
- Credit card numbers
- PII (emails, names) - hash if needed
- Authorization headers
- CSRF tokens

**Sanitize function:**
```python
SENSITIVE_KEYS = {"password", "token", "secret", "key", "authorization", "csrf", "cvv", "ssn"}

def sanitize_body(body):
    if not body:
        return None
    try:
        data = json.loads(body)
    except:
        return "[binary]"
    
    def clean(obj):
        if isinstance(obj, dict):
            return {k: "[REDACTED]" if any(s in k.lower() for s in SENSITIVE_KEYS) else clean(v) 
                    for k, v in obj.items()}
        elif isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj
    
    return clean(data)
```

## Database Model

```python
# agent_app/models/error_report.py
class ErrorReport(models.Model):
    session_id = models.CharField(max_length=64, db_index=True)
    request_id = models.CharField(max_length=32, db_index=True)
    error_message = models.TextField()
    error_stack = models.TextField()
    error_source = models.CharField(max_length=32)  # unhandled, promise, server
    client_logs = models.JSONField()  # Full user action history
    checkpoints = models.JSONField()  # 30-step sync points
    user_agent = models.TextField()
    url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
```

## Applies To

- EMOG core project
- All projects created by EMOG (EMOG-1, EMOG-2, etc.)
- All emıg projects

## Enforcement

- Scaffold includes `error_tracking.py` middleware automatically
- Scaffold includes `error-tracking.js` in base template
- CI checks for middleware in `MIDDLEWARE` setting
- CI checks for script tag in base template