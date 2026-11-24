from pydantic import BaseModel, Field
from typing import TypedDict, List, Optional, Literal


class IntentAnalysis(BaseModel):
    """Kullanıcı isteğinin niyet analizi sonucu."""
    is_code_required: bool = Field(
        description="Kod yazılması gerekiyor mu? (True: geliştirme gerekli, False: sadece soru/cevap)"
    )
    intent_summary: str = Field(
        description="Kullanıcı amacının kısa özeti"
    )
    required_components: List[str] = Field(
        default_factory=list,
        description="Gerekli bileşenler: dashboard, security, database, frontend, backend"
    )
    specific_requirements: List[str] = Field(
        default_factory=list,
        description="Kullanıcının ÖZEL olarak istediği şeyler (örn: 'güzel CSS', 'şatafatlı', 'renkli', 'animasyonlu', 'modern tasarım'). Kullanıcının tam olarak ne istediğini buraya yaz. Boş bırakma."
    )
    aesthetic_expectations: str = Field(
        default="standard",
        description="Görsel kalite beklentisi: 'minimal', 'standard', 'rich', 'flashy', 'premium'. Kullanıcı 'güzel', 'şatafatlı', 'göz alıcı' derse 'flashy' veya 'premium' olmalı."
    )
    quality_bar: str = Field(
        default="production",
        description="Kalite çıtası: 'prototype', 'production', 'premium'. Kullanıcı etkileyici bir şey istiyorsa 'premium'."
    )


class AgentArtifact(BaseModel):
    """Uzman ajanların ürettiği çıktı (kod/doküman) yapısı."""
    agent_name: str = Field(
        description="Raporu veya kodu üreten ajanın adı (Örn: Security_Agent, Dashboard_Agent)"
    )
    code_or_output: str = Field(
        description="Üretilen kod veya teknik doküman içeriği"
    )
    is_approved: bool = Field(
        default=False,
        description="Critic tarafından onaylandı mı?"
    )
    feedback: Optional[str] = Field(
        default=None,
        description="Critic'ten gelen düzeltme talimatı (reddedildiyse)"
    )
    iteration: int = Field(
        default=0,
        description="Bu ajan için kaçıncı iterasyon"
    )


class CriticEvaluation(BaseModel):
    """Critic ajanı değerlendirme çıktısı."""
    agent_name: str = Field(description="Değerlendirilen ajanın adı")
    status: Literal["APPROVED", "REJECTED"] = Field(
        description="Onaylandı mı yoksa reddedildi mi?"
    )
    reason: Optional[str] = Field(
        default=None,
        description="Reddedildiyse somut gerekçesi (güvenlik açığı, standart ihlali, vb.)"
    )
    correction_guidelines: Optional[str] = Field(
        default=None,
        description="Ajanın bir sonraki turda yapması gereken spesifik düzeltme"
    )


class EcosystemState(TypedDict):
    """LangGraph ekosistem durumu - tüm ajanlar arası paylaşılan state."""
    user_prompt: str
    intent: IntentAnalysis
    tasks: List[str]
    artifacts: List[AgentArtifact]
    final_output: str
    iteration_count: int
    current_agent: str
    all_approved: bool


# ============================================================
# ZORUNLU STANDARTLAR - HER KOD İSTEĞİNDE OTOMATİK UYGULANAN
# ============================================================

DASHBOARD_MANDATORY_FEATURES = """
ZORUNLU DASHBOARD ÖZELLİKLERİ (Tüm uygulamalar için otomatik dahil):
1. FULL VIEWPORT BOUNDS: 100vw / 100vh / 100dvh - ekranın her pikselini kullan, taşma YOK
2. RESPONSIVE GRID: CSS Grid + Flexbox, mobile-first (320px → 4K), container queries
3. LAYOUT: Sidebar (collapsible), Top header, Main content, Footer - tüm breakpointlerde çalışır
4. CHARTS/GRAPHS: Chart.js / Recharts / ApexCharts - responsive container, auto-resize
5. REAL-TIME: WebSocket / SSE entegrasyonu hazır, loading states, error boundaries
6. THEME: Light/Dark mode (CSS variables), system preference detection, persist localStorage
7. ACCESSIBILITY: WCAG 2.1 AA - ARIA labels, keyboard nav, focus visible, color contrast
8. INTERNATIONALIZATION: i18n ready (react-i18next / vue-i18n), RTL desteği
9. STATE: Persistent UI state (sidebar open, filters, pagination) - URL sync + localStorage
10. ERROR HANDLING: Global error boundary, toast notifications, retry mechanisms
11. PERFORMANCE: Code splitting, lazy loading, virtualized lists, memoization
12. PWA READY: Service worker, manifest, offline fallback, install prompt
"""

SECURITY_MANDATORY_FEATURES = """
ZORUNLU GÜVENLİK ÖZELLİKLERİ (Tüm uygulamalar için otomatik dahil):
1. SQL INJECTION: Parameterized queries ZORUNLU, ORM (Prisma/SQLAlchemy/Drizzle), raw SQL YASAK
2. XSS PREVENTION: Output encoding (DOMPurify), CSP headers strict, Trusted Types
3. CSRF PROTECTION: Double-submit cookie / Synchronizer token, SameSite=Strict/Lax
4. AUTHENTICATION: JWT (RS256) access token (KISA OMUR: 5-15 dk) + her kullanimda yenilenen
   (rotating) refresh token + REUSE DETECTION, HttpOnly Secure cookies, ASLA localStorage
5. AUTHORIZATION: RBAC/ABAC middleware, resource-level permissions, rate limiting
6. HEADERS: Helmet.js / Secure headers (HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy)
7. INPUT VALIDATION: Zod / Pydantic / Joi - schema validation her endpoint'te
8. SANITIZATION: HTML sanitize, file upload validation (type, size, magic bytes)
9. SECRETS: .env management, Vault/SealedSecrets ready, no hardcoded secrets
10. AUDIT LOG: Structured logging (Pino/Winston), security event tracking, SIEM ready
11. DEPENDENCY SCAN: npm audit / pip-audit / Snyk entegrasyonu CI/CD'de
17. PROJE ISKELETI / .gitignore (ZORUNLU): EMOG'un olusturdugu veya
    ice aktardigi HER proje ilk anda .gitignore, .env.example ve SECURITY.md
    dosyalarini alir; bu dosyalar olmadan proje tamamlanmis sayilmaz.
    .gitignore icerigi yigina gore UYARLANIR (python/node/go/rust/java/php/
    ruby/.NET) ve sunlar ZORUNLUDUR: sirlar (.env, *.key, *.pem, id_rsa),
    2FA/TOTP (admin-2fa-qr.png, *totp*.json - seed icerir), log'lar,
    veritabanlari (*.sqlite3, *.db), zero-trust vault. ONEMLI: `.env` ignore
    edilir ama `.env.example` COMMIT EDILIR (istisna `!.env.example`).
    .env.example yalnizca anahtar ADLARINI icerir, degerler BOS olur.
    Var olan dosyalar EZILMEZ, kullanici ozelikleri korunur. CI denetimi:
    dosyalar var mi, .env deseni var mi, `.env` git tarafindan takipte mi?
    (takipte ise build kirilir). Detay: PROJECT_SCAFFOLD_STANDARD.md
16. SIR YONETIMI (ZORUNLU): API anahtarlari ve sirlar ASLA koda gomulmez,
    yalnizca .env/ortam degiskeninden okunur. Kritik sir icin
    os.getenv(..., "varsayilan") deseni YASAKTIR (bilinen anahtarla imzalama
    = token forging); eksikse hata firlatilir. Zayif/ornek degerler
    (django-insecure-, your-secret, change-me, dev-secret, supersecret)
    KABUL EDILMEZ: DEBUG'de uyari, uretimde REFUSE-TO-START. Merkezi sir
    yoneticisi (get/require/mask/redact) zorunludur; API anahtarlari loglanmaz,
    Authorization/Bearer basliklari maskelenir; debug endpointleri deger
    dondurmez (sadece VAR/YOK); .env + *.key + admin-2fa-qr.png .gitignore'da
    olmalidir; CI'da sizinti taramasi ZORUNLUDUR ve bulgu build'i kirar.
    Sizinti durumunda SILMEK DEGIL ROTASYON gerekir.
    Detay: SECRETS_STANDARD.md
15. ISTEK BAGLANTISI / REQUEST BINDING (ZORUNLU): Her istekte isteminin ayni
    kisi oldugu dogrulanir. (a) ISTEMCI PARMAK IZI: token'a HMAC-SHA256 ile
    ozetlenmis parmak izi gomulur (UA ailesi + dil + Sec-CH-UA ipuclari +
    ekran); her istekte yeniden hesaplanir ve karsilastirilir, uyusmazlik ->
    401 + TUM token'lar iptal. (b) IP BLOGU (/24, /48) binding; mobil
    toleransi ZORUNLU. (c) CSRF double-submit: csrf_exempt endpoint'lerde de
    imzali cookie + X-CSRF-Token header eslesmesi zorunlu, global middleware
    ile uygulanir. Ihlal -> SecurityEvent critical. Ham parmak izi SAKLANMAZ.
    Detay: REQUEST_BINDING_STANDARD.md
14. SUNUCU TARAFI YETKILENDIRME (ZORUNLU): Arayuzde gizlemek yetki DEGILDIR.
    Yetki DAIMA sunucuda dogrulanir. Katmanlar: (1) middleware tum istekleri tek
    noktadan kontrol eder, (2) HER mutasyon view'inin basinda acik guard vardir,
    (3) nesne sahipligi (IDOR) kontrolu, (4) feature kapaliyken sunucu reddeder.
    Seviyeler: public / authenticated / feature / admin / owner. FAIL-CLOSED:
    kontrol hatasi, sahiplik kaydi yoksa veya anahtar okunamazsa IZIN YOK.
    Admin-only endpointler statik shared-secret DEGIL kullanici tabanli kontrol
    kullanir. Test zorunlu: anonim 401, gecersiz token 401, IDOR 403, feature
    kapali 403 feature_disabled, traversal 400 unsafe_path.
    Detay: AUTHORIZATION_STANDARD.md
13. RATE LIMITING / BRUTE FORCE (ZORUNLU): Kademeli yavaslatma (5:2sn, 8:1dk, 12:5dk,
    16:30dk, 20:1saat kilit), kullanici+IP kombinasyonu + ayri IP genel siniri
    (30 deneme/10dk), 15 dk pencere, basarili giriste sifirlama, 429 + Retry-After,
    kilitliyken dogru parola bile reddedilir (bilgi sizdirmaz), kullanici varligi
    sizdirilmaz, SecurityEvent kaydi. Detay: RATE_LIMITING_STANDARD.md
12. TOKEN ROTATION (ZORUNLU): Access token 5-15 dk kisa omurlu, refresh token her
    kullanimda yenilenir, DB'de SHA-256 hash ile saklanir, kullanilmis token tekrar
    sunulursa token AILESI iptal edilir (reuse detection), proaktif + seffaf + reaktif
    otomatik yenileme (3 katman). Detay: TOKEN_ARCHITECTURE.md
11. OWASP TOP 10: Tüm maddeler için kod seviyesinde koruma
"""

DATABASE_MANDATORY_FEATURES = """
ZORUNLU VERİTABANI ÖZELLİKLERİ (Tüm uygulamalar için otomatik dahil):
1. ORM ONLY: Prisma / SQLAlchemy / Drizzle - RAW SQL KESİN YASAK
2. MIGRATIONS: Versionlı, rollback destekli, CI/CD'de otomatik
3. SCHEMA: FK, UNIQUE, CHECK constraints, NOT NULL, DEFAULT değerler
4. INDEXING: Composite indexes, covering indexes, partial indexes, EXPLAIN ANALYZE
5. CONNECTION: Pooling (PgBouncer / pgx), retry logic, health checks
6. TRANSACTIONS: Explicit transaction boundaries, isolation levels, deadlock handling
7. SOFT DELETES: deleted_at pattern, global query filters
8. AUDIT: created_at, updated_at, created_by triggers / ORM hooks
9. TYPES: UUID (pgcrypto), JSONB, ENUM, CITEXT, generated columns
10. SEEDING: Deterministic seed data, factory pattern for tests
11. BACKUP: Point-in-time recovery, automated backup scripts
12. MULTI-TENANT READY: Row-level security (RLS) policies hazır
"""

FRONTEND_MANDATORY_FEATURES = """
ZORUNLU FRONTEND ÖZELLİKLERİ (Tüm uygulamalar için otomatik dahil):
1. FRAMEWORK: React 18+ / Next.js 14+ (App Router) / Vue 3 + Vite
2. TYPESCRIPT: Strict mode, noImplicitAny, exhaustive switch checks
3. STATE: TanStack Query / SWR (server state), Zustand / Jotai / Pinia (client state)
4. FORMS: React Hook Form + Zod / VueUse + VeeValidate - validation, dirty tracking
5. UI SYSTEM: Tailwind CSS + Radix UI / shadcn-ui / Headless UI - accessible primitives
6. ROUTING: File-based / React Router v6 / Vue Router 4 - nested, lazy, guards
7. API LAYER: TanStack Query + Axios/Ky - interceptors, retry, caching, optimistic updates
8. TESTING: Vitest + Testing Library (unit), Playwright (e2e), MSW (mocking)
9. BUNDLING: Vite / Turbopack - code splitting, tree shaking, CSS modules
10. A11Y: ESLint jsx-a11y, axe-core CI, semantic HTML, focus management
11. PERFORMANCE: React.memo, useMemo, useCallback, virtualization (TanStack Virtual)
12. ERROR BOUNDARY: Global + per-route, fallback UI, error reporting (Sentry)
"""

BACKEND_MANDATORY_FEATURES = """
ZORUNLU BACKEND ÖZELLİKLERİ (Tüm uygulamalar için otomatik dahil):
1. FRAMEWORK: FastAPI (Python) / NestJS (Node) / Hono (Edge) - async first
2. VALIDATION: Pydantic v2 / Zod - request/response schemas, coercion, custom validators
3. AUTH MIDDLEWARE: JWT verify, scope/role check, rate limit (token bucket), IP allowlist
4. DATABASE LAYER: Repository pattern + Unit of Work, dependency injection
5. API DESIGN: RESTful (proper HTTP codes), OpenAPI 3.1 auto-docs (Scalar/Swagger)
6. ERROR HANDLING: RFC 7807 Problem Details, structured logging (structlog/Pino)
7. OBSERVABILITY: OpenTelemetry (traces, metrics, logs), health/ready endpoints
8. CACHING: Redis (async), Cache-Control headers, ETag, stale-while-revalidate
9. QUEUES: BullMQ / Celery / Redis Streams - retries, dead letter, scheduling
10. FILE UPLOAD: Streaming, multipart, virus scan (ClamAV), CDN signed URLs
11. SECURITY: CORS strict, rate limit per user/IP, body size limit, request timeout
12. DEPLOYMENT: Docker multi-stage, distroless, non-root, health checks, k8s manifests
"""

# ============================================================
# YENİ: TELEMETRY / LOGGING / ANALYTICS - HER PROJEDE ZORUNLU
# ============================================================

CLIENT_TELEMETRY_MANDATORY_FEATURES = """
ZORUNLU CLIENT-SIDE TELEMETRY (Frontend - Her sayfa yükleniminde otomatik):
1. EKRAN VE GÖRÜNÜM: window.innerWidth, innerHeight, screen.width, height, devicePixelRatio
2. CİHAZ VE TARAYICI: navigator.userAgent, platform, language, hardwareConcurrency, maxTouchPoints
3. ERIŞIM VE AĞ: IP (backend header'dan), GeoIP ülke/şehir, timezone (Intl.DateTimeFormat)
4. PERFORMANS: performance.now(), PerformanceObserver (FCP, LCP, CLS, TTFB), Navigation Timing API
5. SAYFA GEZİNTİSİ: SPA route değişiklikleri (History API), scroll depth, session duration
6. GÖNDERİM: Beacon API (navigator.sendBeacon) / fetch keepalive - sessionStorage buffer + batch POST
7. PRIVACY: GDPR/CCPA uyumlu - consent banner, anonymize IP, no PII, localStorage opt-out flag
8. BATCHING: 30sn / 50 event batch, exponential backoff retry, offline queue (IndexedDB)
"""

BACKEND_TELEMETRY_MANDATORY_FEATURES = """
ZORUNLU BACKEND TELEMETRY (Middleware/Interceptor - Her istekte otomatik):
1. HTTP İSTEK LOG: user_id (JWT'dan), method, path, query, referer, status_code, latency_ms, user_agent
2. HATA YAKALAMA (Error Tracker): 5xx hatalar, DB deadlock, unhandled exception - message, stack_trace, request_body, context
3. AI/API LOG: OpenAI/Claude/Gemini çağrıları - model, prompt_tokens, completion_tokens, total_cost_usd, latency_ms
4. PERFORMANS: Slow query detection (>500ms), memory/CPU spike, cache hit/miss ratio
5. GÜVENLİK: Failed auth attempts, rate limit hits, suspicious payload (SQLi/XSS pattern), IP reputation
6. STRUCTURED LOG: JSON format, correlation_id (request tracing), log levels (debug/info/warn/error/critical)
7. SAMPLING: High-traffic endpoint'lerde %10-100 adaptive sampling, error'larda %100
8. EXPORT: OpenTelemetry (OTLP) + Prometheus metrics endpoint (/metrics), Datadog/Sentry/Datadog entegrasyonu hazır
"""

TELEMETRY_DATABASE_MANDATORY_FEATURES = """
ZORUNLU TELEMETRY VERİTABANI MODELLERİ (Her projede otomatik migration ile oluşturulur):

A. UserSessionLog (Ziyaretçi & Cihaz Analitiği)
- id: UUID (PK)
- user_id: UUID (FK, Nullable - auth varsa)
- session_id: UUID (client-generated, cookie/localStorage)
- ip_address: INET / VARCHAR(45)
- country_code: CHAR(2) (ISO 3166-1 alpha-2)
- city: VARCHAR(100)
- user_agent: TEXT
- device_type: ENUM('mobile','desktop','tablet','bot')
- os: VARCHAR(50) (parsed from UA)
- browser: VARCHAR(50) (parsed from UA)
- browser_version: VARCHAR(20)
- screen_resolution: VARCHAR(20) (örn: '1920x1080')
- viewport_size: VARCHAR(20) (örn: '1920x940')
- device_pixel_ratio: DECIMAL(3,2)
- hardware_concurrency: SMALLINT
- max_touch_points: SMALLINT
- browser_language: VARCHAR(10) (örn: 'tr-TR')
- timezone: VARCHAR(50) (örn: 'Europe/Istanbul')
- referrer: TEXT
- landing_page: VARCHAR(500)
- exit_page: VARCHAR(500)
- session_duration_ms: BIGINT
- pages_viewed: INT
- created_at: TIMESTAMPTZ (default now())
- updated_at: TIMESTAMPTZ

B. SystemPerformanceLog (Teknik Analiz ve Hata Logu)
- id: UUID (PK)
- trace_id: UUID (OpenTelemetry trace correlation)
- span_id: UUID
- user_id: UUID (FK, Nullable)
- session_id: UUID (FK, Nullable)
- endpoint: VARCHAR(500)
- method: VARCHAR(10)
- status_code: SMALLINT
- latency_ms: INT
- request_size_bytes: BIGINT
- response_size_bytes: BIGINT
- error_message: TEXT (Nullable)
- stack_trace: TEXT (Nullable)
- error_type: VARCHAR(100) (örn: 'DatabaseError', 'ValidationError')
- db_query: TEXT (Nullable - slow query ise)
- db_duration_ms: INT (Nullable)
- ai_model: VARCHAR(50) (Nullable - OpenAI, Claude, vb.)
- ai_prompt_tokens: INT (Nullable)
- ai_completion_tokens: INT (Nullable)
- ai_total_cost_usd: DECIMAL(10,6) (Nullable)
- ai_latency_ms: INT (Nullable)
- memory_usage_mb: INT (Nullable)
- cpu_percent: DECIMAL(5,2) (Nullable)
- cache_hit: BOOLEAN (Nullable)
- created_at: TIMESTAMPTZ (default now())

INDEXES (Performans için zorunlu):
- UserSessionLog: (created_at DESC), (user_id, created_at), (session_id), (country_code), (device_type)
- SystemPerformanceLog: (created_at DESC), (trace_id), (endpoint, status_code), (user_id, created_at), (error_type), (ai_model)
"""

TELEMETRY_DASHBOARD_MANDATORY_FEATURES = """
ZORUNLU TELEMETRY ADMIN DASHBOARD (Unified Dashboard - 'Analiz & Telemetri' Sekmesi):

ÖZET KARTLAR (KPIs - Real-time güncellenen):
- Toplam Benzersiz Ziyaretçi (Unique Visitors - 24s / 7g / 30g)
- Aktif Oturum Sayısı (Active Sessions - şu an)
- Ortalama Sayfa Yüklenme Süresi (Avg Page Load - ms, p50/p95/p99)
- Hata Oranı (Error Rate % - son 24s, 5xx/total)
- Ortalama API Latency (p50/p95/p99 ms)
- AI Maliyeti (Total Cost USD - günlük/aylık)
- Toplam Token Kullanımı (Prompt + Completion)

KULLANICI DAĞILIM GRAFİKLERİ (Recharts/Chart.js - Responsive, Interactive):
1. Cihaz Dağılımı: Desktop vs Mobile vs Tablet (Pie/Donut - tooltip ile %)
2. İşletim Sistemi: Windows / macOS / Linux / iOS / Android (Bar - stacked)
3. Tarayıcı Dağılımı: Chrome / Firefox / Safari / Edge / Other (Horizontal Bar)
4. Ülke/Şehir Dağılımı: World map (Choropleth) + Top 10 şehir (Bar)
5. Ekran Çözünürlüğü: Top 10 (1920x1080, 1366x768, 390x844, vb.) - Treemap
6. Dil/Zaman Dilimi: Heatmap (Language vs Timezone)
7. Giriş Sayfaları (Landing Pages) vs Çıkış Sayfaları (Exit Pages) - Sankey

PERFORMANS GRAFİKLERİ:
- Sayfa Yüklenme Süresi Zaman Serisi (Line - p50/p95/p99 bant)
- API Latency Dağılımı (Histogram + Box plot)
- Core Web Vitals: FCP, LCP, CLS, INP, TTFB (Gauge + Trend)
- Hata Oranı Zaman Serisi (Area chart - kırmızı threshold çizgisi ile)

CANLI LOG AKİŞI (Live Logs & Trace Viewer):
- Son 100 istek: Table (Trace ID, Method, Endpoint, Status, Latency, User, Time)
- Filtreler: Status (2xx/4xx/5xx), Endpoint regex, Latency > Xms, Error only, Date range
- Arama: Full-text (endpoint, error_message, user_id, IP)
- Detay Modal: Tıklanında -> Request/Response body, Headers, Stack trace, AI tokens/cost
- WebSocket/SSE ile canlı güncelleme (tail -f benzeri)

AI / OTOMASYON PANELİ:
- Model Bazlı Kullanım: GPT-4o, Claude, Gemini (Stacked Bar - tokens/cost)
- Günlük/Aylık Maliyet Trendi (Line + Projection)
- Maliyet / İstek Ortalaması (Gauge)
- Slowest AI Calls (Table - latency > 10s)
- Failed AI Calls (Error rate by model)

ALARM / ALERT PANELİ:
- Threshold tanımlama: Error rate > 5%, p95 latency > 2s, AI cost > $10/gün
- Notification: Email, Slack, Webhook, PagerDuty
- Alert history: Acknowledged/Resolved tracking
"""

# Tüm zorunlu görevler - her kod isteğinde otomatik eklenir (TELEMETRY EKLENDİ)
MANDATORY_TASKS = [
    f"📊 Dashboard Task: {DASHBOARD_MANDATORY_FEATURES}",
    f"🛡️ Security Task: {SECURITY_MANDATORY_FEATURES}",
    f"🗄️ Database Task: {DATABASE_MANDATORY_FEATURES}",
    f"🎨 Frontend Task: {FRONTEND_MANDATORY_FEATURES}",
    f"⚙️ Backend Task: {BACKEND_MANDATORY_FEATURES}",
    f"📈 Client Telemetry Task: {CLIENT_TELEMETRY_MANDATORY_FEATURES}",
    f"🔧 Backend Telemetry Task: {BACKEND_TELEMETRY_MANDATORY_FEATURES}",
    f"🗃️ Telemetry Database Task: {TELEMETRY_DATABASE_MANDATORY_FEATURES}",
    f"📊 Telemetry Dashboard Task: {TELEMETRY_DASHBOARD_MANDATORY_FEATURES}"
]

SPECIALIST_AGENTS = [
    "Dashboard_Agent",
    "Security_Agent",
    "Database_Agent",
    "Frontend_Agent",
    "Backend_Agent",
    "Telemetry_Agent"
]

MAX_ITERATIONS = 5