"""
Proje iskeleti (scaffold) - her oluşturulan projeye zorunlu dosyalar.

EMOG'un oluşturduğu / içe aktardığı HER proje şu dosyalari alir:
  1. .gitignore      - sirlar, anahtarlar, veritabani, log dosyalari ASLA commit edilmez
  2. .env.example    - tum ortam degiskenlerinin ANAHTAR ADLARI (degerler bos)
  3. SECURITY.md     - guvenlik standardi ozeti
  4. FORM_VALIDATION_STANDARD.md - form dogrulama standardi
  5. ERROR_TRACKING_STANDARD.md - hata takibi standardi
  6. WHATSAPP_STANDARD.md - WhatsApp entegrasyon standardi
  7. pre-commit kurali (varsa .pre-commit-config.yaml)

Bu dosyalar mevcutsa UZERINE YAZILMAZ (kullanici ozeliklerini korur).
"""
import os
from pathlib import Path

# ============================================================
# YIGIN (stack) TESPITI
# ============================================================

STACK_HINTS = {
    "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "manage.py"],
    "node": ["package.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
    "java": ["pom.xml", "build.gradle"],
    "php": ["composer.json"],
    "ruby": ["Gemfile"],
    "dotnet": ["*.csproj"],
}


def detect_stack(project_dir):
    """Proje dizininden teknoloji yiginini tespit eder."""
    try:
        entries = [p.name for p in Path(project_dir).iterdir()]
    except Exception:
        return ["python"]

    stacks = []
    for stack, hints in STACK_HINTS.items():
        for hint in hints:
            if hint in entries:
                stacks.append(stack)
                break

    return stacks or ["python"]


# ============================================================
# .gitignore
# ============================================================

def build_gitignore(stacks=None):
    """Yigina gore .gitignore icerigi uretir."""
    stacks = stacks or ["python"]

    base = """# ============================================================
# SIRLAR - ASLA COMMIT EDILMEZ
# ============================================================
.env
.env.*
!.env.example
*.env.bak
*.env.backup*
secrets.json
secrets.yaml
secrets.yml
config.local.*
*.pem
*.key
*.p12
*.pfx
*.crt
id_rsa
id_rsa.pub
id_ed25519
id_ecdsa
*.keystore
*.jks

# Kimlik dogrulama / oturum
*.session
*.sqlite3
*.db
session.json

# Zero-Trust vault
.vault_key
.vault_secrets/
.mtls_ca/
mtls/
certs/

# 2FA / TOTP
admin-2fa-qr.png
*totp*.json
*otpauth*
*.otp

# ============================================================
# LOGLAR
# ============================================================
*.log
logs/
log/
debug.log
npm-debug.log*
yarn-error.log*
omni_proxy.log
server.log
audit_logs/

# ============================================================
# VERITABANI
# ============================================================
*.sqlite
*.sqlite3
*.db
db/*.db
postgres-data/
mysql-data/
mongo-data/

# ============================================================
# DERLEME / BAGIMLILIK
# ============================================================
"""

    python_block = """__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
dist/
*.egg-info/
.eggs/
pip-wheel-metadata/
venv/
.venv/
env/
ENV/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
.coverage.*
htmlcov/
.tox/
.hypothesis/

# Django
staticfiles/
media/
*.log

"""
    node_block = """node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
.npm/
.yarn/cache
.yarn/install-state.gz
dist/
build/
.next/
.nuxt/
.output/
.svelte-kit/
.turbo/
.cache/
coverage/
.eslintcache

# Yarn 2+
.yarn/*
!.yarn/patches
!.yarn/plugins
!.yarn/releases
!.yarn/sdks
!.yarn/versions
.pnp.*

"""
    other_block = {
        "go": "# Go\n*.exe\n*.exe~\n*.dll\n*.so\n*.dylib\nvendor/\n\n",
        "rust": "# Rust\n/target/\n**/*.rs.bk\nCargo.lock\n\n",
        "java": "# Java\n*.class\n*.jar\n*.war\n*.ear\n.gradle/\nbuild/\ntarget/\n.mvn/\n\n",
        "php": "# PHP\n/vendor/\ncomposer.phar\n.phpunit.result.cache\n.php-cs-fixer.cache\n\n",
        "ruby": "# Ruby\n.bundle/\nvendor/bundle/\n*.gem\n.rspec_status\n\n",
        "dotnet": "# .NET\n[Bb]in/\n[Oo]bj/\n*.user\n*.suo\n.vs/\n\n",
    }

    output = base

    for stack in stacks:
        if stack == "python":
            output += python_block
        elif stack == "node":
            output += node_block
        elif stack in other_block:
            output += other_block[stack]

    output += """# ============================================================
# IDE / OS
# ============================================================
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store
Thumbs.db
desktop.ini

# ============================================================
# CI / TEMPORARY
# ============================================================
.cache/
tmp/
temp/
*.tmp
*.bak
coverage/
"""

    return output


# ============================================================
# .env.example
# ============================================================

def build_env_example(project_dir=None, stacks=None):
    """Yigina gore .env.example uretir (degerler BOS)."""
    stacks = stacks or ["python"]
    entries = Path(project_dir) if project_dir else None

    def has(name):
        try:
            return entries is not None and (entries / name).exists()
        except Exception:
            return False

    header = """# ============================================================
# ORTAM DEGISKENLERI
# ============================================================
# Bu dosyayi .env olarak kopyalayin ve degerleri kendi anahtarlarinizla
# doldurun. .env dosyasi .gitignore'dadir, ASLA commit edilmez.
#
#   cp .env.example .env
#
# GUVENLIK: API anahtarlari ve sirlar ASLA koda gomulmez.
# Uretimde zayif/ornek deger kullanilamaz (uygulama refuse-to-start yapar).

# ---------- KRITIK (zorunlu) ----------
# 50+ karakter rastgele uretin:
#   python -c "import secrets; print(secrets.token_urlsafe(64))"
"""

    sections = {"python": [], "node": [], "other": []}

    if "python" in stacks or has("requirements.txt") or has("pyproject.toml"):
        sections["python"] = [
            ("SECRET_KEY", "Uygulama imzalama anahtari (session, CSRF, JWT, parola)"),
            ("EMOG_JWT_SECRET", "JWT imzalama anahtari (SECRET_KEY'dan farkli olmali)"),
            ("DATABASE_URL", "Veritabani baglanti adresi"),
            ("DB_USER", "Veritabani kullanici adi"),
            ("DB_PASSWORD", "Veritabani parolasi"),
            ("DEBUG", "true | false"),
            ("ALLOWED_HOSTS", "virgulle ayirilmis domainler ('*' YASAK)"),
        ]

    if "node" in stacks or has("package.json"):
        sections["node"] = [
            ("JWT_SECRET", "JWT imzalama anahtari"),
            ("JWT_REFRESH_SECRET", "Refresh token imzalama anahtari"),
            ("SESSION_SECRET", "Oturum imzalama anahtari"),
            ("DATABASE_URL", "Veritabani baglanti adresi"),
            ("ENCRYPTION_KEY", "Veri sifreleme anahtari (32 byte)"),
            ("NODE_ENV", "development | production"),
        ]

    for provider in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
                     "NVIDIA_API_KEY", "GITHUB_TOKEN", "GITHUB_CLIENT_ID",
                     "GITHUB_CLIENT_SECRET"]:
        sections["other"].append((provider, f"{provider} degeri"))

    for provider in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
                     "REDIS_URL", "CORS_ALLOWED_ORIGINS", "LOG_LEVEL"]:
        sections["other"].append((provider, f"{provider} degeri"))

    output = header

    for label, key in (("PYTHON", "python"), ("NODE", "node")):
        if not sections[key]:
            continue
        output += f"\n# ---------- {label} ----------\n"
        for name, desc in sections[key]:
            output += f"\n# {desc}\n{name}=\n"

    output += "\n# ---------- DIS SERVISLER ----------\n"
    for name, desc in sections["other"]:
        output += f"\n# {desc}\n{name}=\n"

    return output


# ============================================================
# SECURITY.md
# ============================================================

SECURITY_MD = """# Guvenlik Standardi

Bu proje asagidaki guvenlik kurallarina UYMAK ZORUNDADIR.

## API anahtarlari ve sirlar

- API anahtarlari ve sirlar **ASLA koda gomulmez**
- Tum anahtarlar `.env` dosyasindan okunur
- `.env` `.gitignore`'dadir, commit edilemez
- `.env.example` yalnizca anahtar ADLARINI icerir (degerler bos)
- Kritik sirlar icin `os.getenv(..., "varsayilan")` deseni **YASAKTIR**
  (bilinen bir anahtarla imzalama = token forging)
- Zayif/ornek degerler kabul edilmez:
  `django-insecure-`, `your-secret`, `change-me`, `dev-secret`, `supersecret`
- Uretimde `DEBUG=false` ve `ALLOWED_HOSTS` gercek domainler olmalidir (`'*'` yasak)

## Kimlik dogrulama

- Access token **kisa omurlu** (5-15 dakika)
- Refresh token **her kullanimda yenilenir** (rotating)
- Refresh token veritabaninda yalnizca SHA-256 hash ile saklanir
- Kullanilmis refresh token tekrar sunulursa token **AILESI** iptal edilir
- Parolalar PBKDF2/bcrypt/argon2 ile hashlenir, duz metin saklanmaz

## Yetkilendirme

- Arayuzde gizlemek **yetki degildir**; yetki DAIMA sunucuda dogrulanir
- Her mutasyon endpoint'inin basinda acik yetki kontrolu vardir
- Sahanipligi kontrolu (IDOR korumasi) zorunludur
- Path traversal engeli zorunludur

## Istek baglantisi

- Her istekte istemci parmak izi dogrulanir
- CSRF double-submit uygulanir

## Giris deneme siniri

- Kademeli yavaslatma uygulanir (5/8/12/16/20 deneme)
- IP bazli genel sinir vardir

## Denetim

- Guvenlik olaylari `SecurityEvent` olarak kaydedilir
- API anahtarlari **loglanmaz**

## Form Validation

- Tum formlar **DAIMA** sunucuda dogrulanir
- `request.POST.get()` ile manuel form isleme **YASAKTIR**
- Django Forms kullanilir: `form.is_valid()` + `form.cleaned_data`
- Client-side validation sadece UX icin, guvenlik icin degil

## Tarama

```bash
python manage.py secrets_scan   # veya proje aracina gore esdeger komut
```

## Bir ihlal tespit edilirse

Silmek yetmez - anahtar **rotasyona** tabi tutulmalidir.
"""


FORM_VALIDATION_STANDARD_MD = """# Form Validation Standard - Mandatory

All forms must be validated server-side. Client-side validation is optional but never a substitute.

## Rules

1. **Always use Django Forms** - Never manually parse `request.POST.get()` for form data
2. **Always call `form.is_valid()`** - Never trust raw POST data
3. **Always use `form.cleaned_data`** - Never access `form.data` or `request.POST` directly after validation
4. **Define validation in the Form class** - Use `clean_<field>()` methods and `clean()` for cross-field validation
5. **Return form errors to template** - Render `form.errors` and `form.non_field_errors()` in templates

## Correct Pattern

```python
# forms.py
from django import forms

class TaskForm(forms.Form):
    task = forms.CharField(
        label="Task",
        widget=forms.Textarea(attrs={"rows": 8}),
        max_length=5000,
    )
    model = forms.ChoiceField(required=False)

    def clean_task(self):
        task = self.cleaned_data["task"].strip()
        if not task:
            raise forms.ValidationError("Task cannot be empty")
        return task

# views.py
def my_view(request):
    form = TaskForm()
    if request.method == "POST":
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.cleaned_data["task"]
            model = form.cleaned_data.get("model")
            # process validated data
    return render(request, "template.html", {"form": form})
```

## Anti-Patterns (Forbidden)

```python
# BAD: Manual validation
task = request.POST.get("task", "").strip()
if not task:
    return error
# No validation, no cleaning, no CSRF protection

# BAD: Using form but accessing raw data
form = TaskForm(request.POST)
if form.is_valid():
    task = request.POST.get("task")  # WRONG - bypasses cleaning
    task = form.data["task"]         # WRONG - raw uncleaned data

# BAD: Skipping is_valid()
form = TaskForm(request.POST)
task = form.cleaned_data["task"]  # KeyError if invalid
```

## Applies To

- All EMOG projects (EMOG, EMOG-1, EMOG-2, etc.)
- All emıg projects
- Any Django project generated by this ecosystem

## Enforcement

- Code reviews must reject PRs with manual `request.POST.get()` form handling
- CI should flag views that don't use Django Forms for POST data
- New projects scaffolded with this standard pre-configured
"""


ERROR_TRACKING_STANDARD_MD = """# Error Tracking Standard - Mandatory

All errors must be tracked with full context. Logs stored on USER's device, sent only on error.

## Architecture

\`\`\`
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
\`\`\`

## Rules

1. **Every request tracked** - Middleware captures: method, path, user, IP, headers, body (sanitized), session
2. **Every step logged** - Client captures: clicks, form submissions, AJAX calls, navigation, console errors
3. **Ring buffer** - Max 500 entries per session, auto-evicts oldest
4. **Send on error only** - Logs stay on user's device until error occurs
5. **Full stack traces** - Both client (JS stack) and server (Python traceback)
6. **Code location** - File, line, function for every log entry
7. **30-step checkpoint** - Every 30 user actions, create a sync point

## Server-Side (Django Middleware)

\`\`\`python
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
\`\`\`

## Client-Side (error.js)

\`\`\`javascript
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
\`\`\`

## API Endpoint

\`\`\`python
# agent_app/views/logs.py
@csrf_exempt
@require_POST
def receive_error_logs(request):
    \"\"\"Receive batched logs from client on error.\"\"\"
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
\`\`\`

## Data Sanitization

**NEVER log:**
- Passwords, tokens, API keys
- Credit card numbers
- PII (emails, names) - hash if needed
- Authorization headers
- CSRF tokens

**Sanitize function:**
\`\`\`python
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
\`\`\`

## Database Model

\`\`\`python
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
\`\`\`

## Applies To

- EMOG core project
- All projects created by EMOG (EMOG-1, EMOG-2, etc.)
- All emıg projects

## Enforcement

- Scaffold includes \`error_tracking.py\` middleware automatically
- Scaffold includes \`error-tracking.js\` in base template
- CI checks for middleware in \`MIDDLEWARE\` setting
- CI checks for script tag in base template
"""


WHATSAPP_STANDARD_MD = """# WhatsApp Integration Standard - Mandatory

EMOG projects include WhatsApp Web integration allowing each user to connect their own WhatsApp account.

## Architecture

\`\`\`
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Frontend      │     │   Django API    │     │  WhatsApp Svc   │
│  (whatsapp.js)  │────▶│  (REST + WS)    │────▶│  (Node.js)      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │  QR Display         │  Auth/Proxy           │  Headless
        │  Chat UI            │  Message Storage      │  whatsapp-web.js
        │  Real-time Sync     │  Webhook Handler      │  Multi-session
        ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│              USER'S WHATSAPP ACCOUNT (QR Login)                 │
└─────────────────────────────────────────────────────────────────┘
\`\`\`

## Rules

1. **Per-user isolation** - Each user connects their own WhatsApp account via QR code
2. **Background operation** - WhatsApp service runs headless, invisible to user
3. **Real-time sync** - WebSocket for instant message delivery
4. **Local message storage** - All messages saved in Django for history/search
5. **Media support** - Images, videos, audio, documents
6. **End-to-end encryption preserved** - WhatsApp's E2EE maintained
7. **Auto-reconnect** - Service handles disconnections gracefully

## Server-Side (Django)

\`\`\`python
# agent_app/models.py - WhatsAppSession, WhatsAppMessage, WhatsAppChat, WhatsAppContact
# agent_app/whatsapp_views.py - REST API + Webhook
# agent_app/urls.py - /api/whatsapp/* endpoints
\`\`\`

## Client-Side (whatsapp.js)

\`\`\`javascript
// static/js/whatsapp.js
class WhatsAppManager {
    init(userId) { /* WebSocket + REST */ }
    initialize() { /* Start QR flow */ }
    sendMessage() { /* Send text/media */ }
    // Real-time events: message_received, message_ack, etc.
}
\`\`\`

## WhatsApp Service (Node.js)

\`\`\`javascript
// whatsapp_service/src/index.js
// Multiple clients via whatsapp-web.js with LocalAuth
// REST API on port 3002 + WebSocket for real-time
\`\`\`

## Database Models

- **WhatsAppSession** - User's connection status, QR code, account info
- **WhatsAppMessage** - Full message history (inbound/outbound)
- **WhatsAppChat** - Chat list with unread counts, last message
- **WhatsAppContact** - Contact list with names, numbers

## API Endpoints

- `GET /api/whatsapp/status/` - Connection status
- `GET /api/whatsapp/qr/` - Current QR code
- `POST /api/whatsapp/init/` - Start connection
- `POST /api/whatsapp/disconnect/` - Logout
- `POST /api/whatsapp/send/` - Send message
- `GET /api/whatsapp/chats/` - Chat list
- `GET /api/whatsapp/contacts/` - Contact list
- `GET /api/whatsapp/messages/<chat_id>/` - Message history
- `POST /api/whatsapp/chat/<chat_id>/<action>/` - Archive, mute, read
- `POST /api/whatsapp/webhook/` - Service webhook (internal)

## Frontend Page

- `/whatsapp/` - Full WhatsApp Web interface
- QR code display for authentication
- Chat list with search
- Message thread with media support
- Real-time updates via WebSocket

## Settings (Environment Variables)

\`\`\`
WHATSAPP_SERVICE_URL=http://localhost:3002
WHATSAPP_SERVICE_TOKEN=your-service-token
\`\`\`

## Applies To

- EMOG core project
- All projects created by EMOG (EMOG-1, EMOG-2, etc.)
- All emıg projects

## Enforcement

- Scaffold includes `whatsapp_service/` directory
- Scaffold includes `whatsapp.js`, `whatsapp.css` in static
- Scaffold includes `whatsapp.html` template
- Django models and views auto-included
- CI checks for WhatsApp service health endpoint
"""


# ============================================================
# UYGULAMA
# ============================================================

def ensure_project_security(project_dir, force=False):
    """
    Proje dizinine zorunlu guvenlik dosyalarini yazar.

    Var olan dosyalar UZERINE YAZILMAZ (kullanici ozelikleri korunur).
    force=True ise eksik anahtar satirlari eklenir.

    Returns: {"created": [...], "skipped": [...]}
    """
    path = Path(project_dir)
    result = {"created": [], "skipped": []}

    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

    stacks = detect_stack(path)

    files = {
        ".gitignore": build_gitignore(stacks),
        ".env.example": build_env_example(path, stacks),
        "SECURITY.md": SECURITY_MD,
        "FORM_VALIDATION_STANDARD.md": FORM_VALIDATION_STANDARD_MD,
        "ERROR_TRACKING_STANDARD.md": ERROR_TRACKING_STANDARD_MD,
        "WHATSAPP_STANDARD.md": WHATSAPP_STANDARD_MD,
    }

    for name, content in files.items():
        target = path / name

        if target.exists() and not force:
            result["skipped"].append(name)
            continue

        try:
            target.write_text(content, encoding="utf-8", newline="\n")
            result["created"].append(name)
        except Exception:
            result["skipped"].append(name)

    # Var olan .gitignore'a eksik kritik kalipleri ekle
    if (path / ".gitignore").exists() and force:
        _augment_gitignore(path / ".gitignore")

    return result


def _augment_gitignore(gitignore_path):
    """Mevcut .gitignore'a eksik zorunlu kaliplari ekler."""
    try:
        current = gitignore_path.read_text(encoding="utf-8")
    except Exception:
        return

    required = [
        ".env", ".env.*", "!.env.example", "*.key", "*.pem", "*.p12",
        "id_rsa", "id_ed25519", "*.log", "*.sqlite3", "admin-2fa-qr.png",
        ".vault_key", ".vault_secrets/", "*.db",
    ]

    existing_lines = {line.strip() for line in current.split("\n")}

    missing = [pattern for pattern in required if pattern not in existing_lines]

    if not missing:
        return

    addition = (
        "\n# ============================================================\n"
        "# EMOG tarafindan eklendi (zorunlu guvenlik kaliplari)\n"
        "# ============================================================\n"
        + "\n".join(missing)
        + "\n"
    )

    try:
        gitignore_path.write_text(current + addition, encoding="utf-8", newline="\n")
    except Exception:
        pass


def audit_project_security(project_dir):
    """
    Projenin guvenlik dosyalarini denetler.

    Returns: {"ok": bool, "missing": [...], "warnings": [...]}
    """
    path = Path(project_dir)
    missing = []
    warnings = []

    for name in (".gitignore", ".env.example", "SECURITY.md", "FORM_VALIDATION_STANDARD.md", "ERROR_TRACKING_STANDARD.md", "WHATSAPP_STANDARD.md"):
        if not (path / name).exists():
            missing.append(name)

    gitignore = path / ".gitignore"
    if gitignore.exists():
        try:
            content = gitignore.read_text(encoding="utf-8")
            lines = {line.strip() for line in content.split("\n")}

            if ".env" not in lines:
                warnings.append(".gitignore icinde '.env' deseni yok")
            if not any(p in lines for p in ("*.key", "*.pem", "id_rsa")):
                warnings.append(".gitignore icinde anahtar dosya kaliplari yok")
            if not any(l.startswith("!") and ".env.example" in l for l in lines):
                warnings.append(".env.example istisnasi tanimlanmis degil")
        except Exception:
            warnings.append(".gitignore okunamadi")

    # .env commit edilmis mi kontrol et
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(path), "ls-files", "--error-unmatch", ".env"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            warnings.append("KRITIK: .env git tarafindan takipte!")
    except Exception:
        pass

    return {
        "ok": not missing and not warnings,
        "missing": missing,
        "warnings": warnings,
    }