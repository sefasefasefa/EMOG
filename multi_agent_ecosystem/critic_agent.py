import os
from typing import Dict, Any, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from .schemas import (
    EcosystemState, AgentArtifact, CriticEvaluation,
    SPECIALIST_AGENTS, MAX_ITERATIONS,
    DASHBOARD_MANDATORY_FEATURES, SECURITY_MANDATORY_FEATURES,
    DATABASE_MANDATORY_FEATURES, FRONTEND_MANDATORY_FEATURES,
    BACKEND_MANDATORY_FEATURES,
    CLIENT_TELEMETRY_MANDATORY_FEATURES, BACKEND_TELEMETRY_MANDATORY_FEATURES,
    TELEMETRY_DATABASE_MANDATORY_FEATURES, TELEMETRY_DASHBOARD_MANDATORY_FEATURES
)

llm = ChatOpenAI(
    model=os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
    temperature=0,
    api_key=os.getenv("NVIDIA_API_KEY"),
    base_url="https://integrate.api.nvidia.com/v1"
)

critic_parser = JsonOutputParser(pydantic_object=CriticEvaluation)


# Ajan başına detaylı denetim kriterleri
CRITIC_CHECKLISTS = {
    "Dashboard_Agent": DASHBOARD_MANDATORY_FEATURES,
    "Security_Agent": SECURITY_MANDATORY_FEATURES,
    "Database_Agent": DATABASE_MANDATORY_FEATURES,
    "Frontend_Agent": FRONTEND_MANDATORY_FEATURES,
    "Backend_Agent": BACKEND_MANDATORY_FEATURES,
    "Telemetry_Agent": f"{CLIENT_TELEMETRY_MANDATORY_FEATURES}\n{BACKEND_TELEMETRY_MANDATORY_FEATURES}\n{TELEMETRY_DATABASE_MANDATORY_FEATURES}\n{TELEMETRY_DASHBOARD_MANDATORY_FEATURES}",
}

AGENT_SPECIFIC_CRITERIA = {
    "Dashboard_Agent": """
DASHBOARD AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):
✓ 100vw / 100vh / 100dvh - full viewport kullanımı
✓ CSS Grid + Flexbox responsive layout, mobile-first
✓ Taşma (overflow) YOK - layout kaymaz, sadece content scroll
✓ Sidebar (collapsible), Header, Main, Footer - tüm breakpointlerde
✓ Chart.js/Recharts responsive container, auto-resize
✓ WebSocket/SSE real-time entegrasyonu hazır
✓ Light/Dark mode (CSS variables), system preference, localStorage persist
✓ WCAG 2.1 AA - ARIA, keyboard nav, focus visible, contrast
✓ i18n ready, RTL desteği
✓ UI state persist (URL sync + localStorage)
✓ Error boundary, toast notifications, retry
✓ Code splitting, lazy loading, virtualized lists
✓ PWA ready - service worker, manifest, offline fallback""",
    
    "Security_Agent": """
SECURITY AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):
✓ SQL Injection: Parameterized queries ZORUNLU, ORM only, raw SQL YOK
✓ XSS: Output encoding (DOMPurify), CSP strict, Trusted Types
✓ CSRF: Double-submit cookie / Synchronizer token, SameSite=Strict
✓ Auth: JWT RS256 + Refresh rotation, HttpOnly Secure cookies
✓ AuthZ: RBAC/ABAC middleware, resource-level permissions, rate limit
✓ Headers: HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy
✓ Validation: Zod/Pydantic schema her endpoint'te
✓ Sanitization: HTML sanitize, file upload (type, size, magic bytes)
✓ Secrets: .env management, Vault ready, no hardcoded secrets
✓ Audit: Structured logging, security events, SIEM ready
✓ Dependency scan: npm audit / pip-audit CI/CD
✓ OWASP Top 10: Tüm maddeler kod seviyesinde korumalı
- PROJE ISKELETI (ZORUNLU): .gitignore, .env.example ve SECURITY.md dosyalari proje kokinde MEVCUT olmali
  * .gitignore icinde '.env' deseni ZORUNLU (.env.example istisnali ile)
  * Anahtar dosya kaliplari: *.key, *.pem, id_rsa, id_ed25519
  * 2FA/TOTP dosyalari ignore edilmeli (admin-2fa-qr.png, *totp*.json)
  * .env.example YALNIZCA anahtar adlari icerir, degerler BOS olmali
  * .env dosyasi repoda IZLENMIYOR olmali (git ls-files kontrolu)
  * .gitignore yigina gore uyarlanmis olmali (python/node/go/rust/...)""",
    
    "Database_Agent": """
DATABASE AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):
✓ ORM ONLY: Prisma/SQLAlchemy/Drizzle - RAW SQL KESİN YOK
✓ Migrations: Versionlı, rollback, CI/CD otomatik
✓ Schema: FK, UNIQUE, CHECK, NOT NULL, DEFAULT
✓ Indexing: Composite, covering, partial, EXPLAIN ANALYZE
✓ Connection: Pooling (PgBouncer), retry, health checks
✓ Transactions: Explicit boundaries, isolation levels, deadlock handling
✓ Soft deletes: deleted_at pattern, global query filters
✓ Audit: created_at, updated_at, created_by triggers/hooks
✓ Types: UUID, JSONB, ENUM, CITEXT, generated columns
✓ Seeding: Deterministic, factory pattern for tests
✓ Backup: PITR, automated backup scripts
✓ Multi-tenant ready: RLS policies hazır""",
    
    "Frontend_Agent": """
FRONTEND AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):
✓ Framework: Next.js 14 App Router / Vite + React 18
✓ TypeScript: Strict mode, noImplicitAny, exhaustive checks
✓ State: TanStack Query (server) + Zustand/Jotai (client)
✓ Forms: React Hook Form + Zod / VueUse + VeeValidate
✓ UI: Tailwind + Radix UI / shadcn-ui - accessible primitives
✓ Routing: File-based / React Router v6 - nested, lazy, guards
✓ API: TanStack Query + Axios/Ky - interceptors, retry, cache, optimistic
✓ Testing: Vitest + Testing Library (unit), Playwright (e2e), MSW
✓ Bundling: Vite/Turbopack - code splitting, tree shaking
✓ A11Y: ESLint jsx-a11y, axe-core CI, semantic HTML, focus mgmt
✓ Performance: memo, useMemo, useCallback, virtualization
✓ Error Boundary: Global + per-route, fallback, Sentry""",
    
    "Backend_Agent": """
BACKEND AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):
✓ Framework: FastAPI 0.109+ / NestJS 10+ - async first
✓ Validation: Pydantic v2 / Zod - her endpoint'te, coercion
✓ Auth Middleware: JWT RS256 verify, scope/role, rate limit, IP allowlist
✓ DB Layer: Repository + Unit of Work, Dependency Injection
✓ API Design: RESTful proper codes, OpenAPI 3.1 auto-docs (Scalar)
✓ Error Handling: RFC 7807 Problem Details, structured logging
✓ Observability: OpenTelemetry (traces, metrics, logs), health/ready
✓ Caching: Redis async, Cache-Control, ETag, stale-while-revalidate
✓ Queues: BullMQ/Celery/Redis Streams - retries, DLQ, scheduling
✓ File Upload: Streaming, multipart, virus scan, CDN signed URLs
✓ Security: CORS strict, rate limit per user/IP, body size limit
✓ Deployment: Docker multi-stage, distroless, non-root, k8s manifests""",
    
    "Telemetry_Agent": """
TELEMETRY AJANI DENETİM LİSTESİ (Hepsi ZORUNLU):

=== CLIENT-SIDE TELEMETRY (Frontend) ===
✓ Telemetry script (lib/telemetry.ts) - auto-load, no-block
✓ Screen/Viewport: innerWidth/Height, screen.width/height, devicePixelRatio
✓ Device/Browser: userAgent, platform, language, hardwareConcurrency, maxTouchPoints
✓ Network: IP (backend header), GeoIP country/city, timezone (Intl.DateTimeFormat)
✓ Performance: PerformanceObserver (FCP, LCP, CLS, TTFB, INP), Navigation Timing
✓ SPA Navigation: History API listener, route changes, scroll depth, session duration
✓ GDPR/CCPA: Consent banner, anonymize IP, no PII, localStorage opt-out
✓ Transport: navigator.sendBeacon / fetch keepalive, batch (30s/50 events), retry
✓ Offline Queue: IndexedDB buffer, exponential backoff, sync on online

=== BACKEND TELEMETRY (Middleware) ===
✓ Middleware (middleware/telemetry.py) - her request'te çalışır
✓ HTTP Log: user_id (JWT), method, path, query, referer, status, latency_ms, UA
✓ Error Tracker: 5xx, DB deadlock, unhandled exception - msg, stack, request_body
✓ AI/API Log: model, prompt/completion tokens, total_cost_usd, latency_ms
✓ Structured JSON Log: correlation_id (trace), levels (debug/info/warn/error/critical)
✓ Sampling: Adaptive (%10-100), error'larda %100
✓ OpenTelemetry: OTLP export, Prometheus /metrics endpoint

=== TELEMETRY DATABASE ===
✓ UserSessionLog model: UUID PK, user_id FK, session_id, IP, geo, UA, device, screen, viewport, DPR, language, tz, referrer, landing/exit, duration, pages, timestamps
✓ SystemPerformanceLog model: UUID PK, trace_id, span_id, user_id, session_id, endpoint, method, status, latency, error_msg, stack_trace, error_type, db_query, db_duration, ai_model, ai_tokens, ai_cost, ai_latency, memory, cpu, cache_hit
✓ Indexes: created_at DESC, user_id+created_at, session_id, trace_id, endpoint+status, error_type, ai_model
✓ Migration: Versionlı, rollback, CI/CD auto

=== TELEMETRY DASHBOARD ===
✓ KPI Cards: Unique Visitors, Active Sessions, Avg Page Load (p50/p95/p99), Error Rate%, Avg API Latency, AI Cost USD, Token Usage
✓ Charts: Device/OS/Browser/Country/Screen Res/Language (Recharts, responsive, interactive)
✓ Performance: Page Load time series (p50/p95/p99), API Latency histogram, Core Web Vitals (FCP/LCP/CLS/INP/TTFB), Error Rate trend
✓ Live Logs: Table (Trace ID, Method, Endpoint, Status, Latency, User, Time), filters, search, detail modal (req/res body, stack, AI tokens)
✓ WebSocket/SSE live updates
✓ AI Panel: Model usage, cost trend, cost/request, slow/failed calls
✓ Alert Panel: Thresholds (error rate>5%, p95>2s, AI cost>$10/day), notifications (Email/Slack/Webhook/PagerDuty)
"""
}

GENERAL_CRITERIA = """
GENEL ZORUNLU KRİTERLER (Tüm ajanlar için):
1. **Kullanıcı İsteği Uyumu**: İstek %100 karşılanmış mı? Sapma/yetersizlik var mı?
2. **Production Ready**: Kod kopyalanıp direkt çalıştırılabilir mi? (config, .env.example, README)
3. **Type Safety**: TypeScript strict / Pydantic v2 - any tipi YOK
4. **Error Handling**: Try-catch, graceful degradation, user-friendly messages
5. **Logging/Monitoring**: Structured logs, correlation IDs, metrics hooks
5. **Documentation**: Inline docs (JSDoc/docstring), API docs auto-generated
6. **Testing Ready**: Unit + integration test yapısı, fixtures, mocks
7. **Security by Default**: Güvenlik opt-in değil opt-out (secure by default)
"""

CRITIC_SYSTEM_PROMPT = """Sen bu ekosistemin **Critic (Denetleyici) Ajansın**. Görevin: Her uzman ajanın ürettiği 
çıktıyı **aşağıdaki ZORUNLU STANDARTLARA** göre **acımasızca denetlemek**.

EN ÖNEMLİ KONTROL: Kullanıcının isteği gerçekten karşılandı mı? Kullanıcı "güzel CSS", "şatafatlı", "göz alıcı" gibi bir şey istedi ama çıktı renksiz/minimal/küçük ise KESİN REDDEDİLİR. Bu en kritik kontroldür.

{Agent_Specific_Criteria}

{GENERAL_CRITERIA}

KARAR:
- APPROVED: Yukarıdaki TÜM kontrol listesi maddeleri %100 sağlanıyorsa VE kullanıcının özel isteği karşılanmışsa
- REJECTED: En ufak bile olsa eksik/ihlal varsa (GÜVENLİK İHLALİ = KESİN RED) veya kullanıcı isteği karşılanmamışsa

REJECTED İSE:
- reason: Somut teknik gerekçe (hangi kontrol maddesi başarısız, hangi satır/dosya, hangi kullanıcı isteği karşılanmadı)
- correction_guidelines: Ajanın bir sonraki turda YAPMASI gereken NET EMİR CÜMLECİĞİ

Çıktıyı SADECE aşağıdaki JSON formatında ver:
{format_instructions}"""


def critic_node(state: EcosystemState) -> Dict[str, Any]:
    """
    Critic/Denetleyici düğümü.
    Tüm uzman ajanların artifact'larını TEK TEK, DETAYLI KONTROL LİSTESİNE GÖRE denetler.
    """
    user_prompt = state["user_prompt"]
    artifacts = state.get("artifacts", [])
    intent = state.get("intent")
    
    # Kullanıcının özel isteklerini çıkar
    specific_reqs = []
    aesthetic = "standard"
    quality_bar = "production"
    if intent is not None:
        specific_reqs = getattr(intent, "specific_requirements", []) or []
        aesthetic = getattr(intent, "aesthetic_expectations", "standard") or "standard"
        quality_bar = getattr(intent, "quality_bar", "production") or "production"
    
    # Kullanıcı isteği doğrulama bloğu (tüm ajanlar için)
    user_intent_block = "=== KULLANICI İSTEĞİ DOĞRULAMA (EN ÖNEMLİ) ===\n"
    user_intent_block += f"Kullanıcının orijinal isteği: {user_prompt}\n"
    if specific_reqs:
        user_intent_block += "Kullanıcının ÖZEL istekleri:\n" + "\n".join(f"  - {r}" for r in specific_reqs) + "\n"
    user_intent_block += f"Estetik beklenti: {aesthetic}\nKalite çıtası: {quality_bar}\n"
    user_intent_block += (
        "DENETİM KURALI: Üretilen çıktı KULLANICININ ÖZEL İSTEKLERİNİ karşılıyor mu?\n"
        "- Eğer kullanıcı 'güzel CSS', 'şatafatlı', 'göz alıcı', 'renkli', 'animasyonlu' gibi ifadeler kullandıysa:\n"
        "  * Çıktıda zengin renk paleti (gradient, accent, glow) OLMALI - tek renkli/minimal CSS REDDEDİLİR\n"
        "  * Animasyonlar/keyframes/transition'lar OLMALI\n"
        "  * Gölgeler, derinlik, modern efektler (glassmorphism vb.) OLMALI\n"
        "  * Çıktı yeterince uzun ve detaylı olmalı (küçük/eksik CSS REDDEDİLİR)\n"
        "- Eğer çıktı kullanıcının isteğini KARŞILAMIYORSA (örn: kullanıcı 'güzel site' dedi ama çıktı renksiz/minimal) -> KESİN REDDEDİLİR\n"
        "- reason'da hangi özel isteğin karşılanmadığını belirt\n"
        "- correction_guidelines'da ajanın ne yapması gerektiğini NET olarak yaz\n"
    )
    
    evaluations = []
    all_approved = True
    
    for artifact in artifacts:
        agent_name = artifact.agent_name
        
        # Programatik estetik kalite kontrolü (deterministik supervisor)
        heuristic_fail = None
        if agent_name in ("Frontend_Agent", "Dashboard_Agent"):
            out = artifact.code_or_output or ""
            low = out.lower()
            # CSS/stil varlığı kontrolü (her durumda zorunlu)
            style_kw = ["style", "<style", "css", "tailwind", "class=", "className=", "background", "color", "#"]
            style_hits = sum(1 for k in style_kw if k in low)
            if style_hits < 3:
                heuristic_fail = "Çıktıda CSS/stil yok veya çok az ({} adet stil anahtar kelimesi). Her frontend çıktısı mutlaka stil/CSS içermeli.".format(style_hits)
            # Etkileyici tasarım istendiğinde ek kontrol
            elif aesthetic in ("flashy", "premium", "rich"):
                color_kw = ["#", "rgb", "gradient", "color", "background", "box-shadow", "animation", "keyframes", "transition"]
                color_hits = sum(1 for k in color_kw if k in low)
                if len(out) < 800:
                    heuristic_fail = "Çıktı çok kısa ({} karakter). Etkileyici tasarım için en az 800+ karakter CSS/styling içermeli.".format(len(out))
                elif color_hits < 4:
                    heuristic_fail = "Çıktıda yeterli renk/stil anahtar kelimesi yok ({} adet). Zengin renk paleti, gradient, animasyon, gölge içermeli.".format(color_hits)
        
        if heuristic_fail:
            # LLM çağırmadan deterministik red
            eval_obj = CriticEvaluation(
                agent_name=agent_name,
                status="REJECTED",
                reason="Programatik kalite kontrolü başarısız: " + heuristic_fail,
                correction_guidelines=(
                    "Kullanıcı etkileyici/göz alıcı bir tasarım istedi. Çıktıyı baştan üret: "
                    "zengin renk paleti (gradient, accent, glow), animasyonlar (keyframes, transitions), "
                    "gölgeler ve derinlik, glassmorphism/modern efektler, büyük tipografi, "
                    "full-viewport responsive layout. En az 200+ satır CSS/styling içermeli. "
                    "Küçük/renksiz/minimal CSS KABUL EDİLMEZ."
                )
            )
            evaluations.append(eval_obj)
            all_approved = False
            continue
        
        # Ajana özel kriterleri al
        specific_criteria = AGENT_SPECIFIC_CRITERIA.get(agent_name, "")
        checklists = CRITIC_CHECKLISTS.get(agent_name, "")
        
        system_prompt = CRITIC_SYSTEM_PROMPT.format(
            Agent_Specific_Criteria=specific_criteria,
            GENERAL_CRITERIA=GENERAL_CRITERIA,
            format_instructions=critic_parser.get_format_instructions()
        )
        
        # Kullanıcı isteği doğrulama kriterini system prompt'a ekle
        system_prompt = system_prompt + "\n\n" + user_intent_block
        
        human_content = f"""Kullanıcı İsteği: {user_prompt}

Değerlendirilen Ajan: {agent_name}
Ajan Tipi: {agent_name.replace('_Agent', '')}

=== ZORUNLU KONTROL LİSTESİ ===
{checklists}

=== AJANA ÖZEL KRİTERLER ===
{specific_criteria}

=== GENEL KRİTERLER ===
{GENERAL_CRITERIA}

{user_intent_block}

=== ÜRETİLEN ÇIKTI ===
{artifact.code_or_output}

Yukarıdaki TÜM kontrol listelerine ve KULLANICI İSTEĞİ DOĞRULAMA'ya göre bu çıktıyı madde madde denetle.
ÖNCE kullanıcı isteğinin karşılanıp karşılanmadığını kontrol et. Karşılanmıyorsa KESİN REDDEDİLİR.
Her başarısız madde için REJECTED ver ve düzeltme talimatı yaz."""
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content)
        ]
        
        response = llm.invoke(messages)
        parsed = critic_parser.parse(response.content)
        if isinstance(parsed, dict):
            evaluation = CriticEvaluation(**parsed)
        else:
            evaluation = parsed
        evaluation.agent_name = agent_name
        
        evaluations.append(evaluation)
        
        if evaluation.status == "REJECTED":
            all_approved = False
    
    # Artifact'ları güncelle (feedback ve is_approved)
    updated_artifacts = []
    for artifact in artifacts:
        eval_for_agent = next((e for e in evaluations if e.agent_name == artifact.agent_name), None)
        
        if eval_for_agent:
            updated_artifacts.append(AgentArtifact(
                agent_name=artifact.agent_name,
                code_or_output=artifact.code_or_output,
                is_approved=eval_for_agent.status == "APPROVED",
                feedback=eval_for_agent.correction_guidelines if eval_for_agent.status == "REJECTED" else None,
                iteration=artifact.iteration
            ))
        else:
            updated_artifacts.append(artifact)
    
    return {
        "artifacts": updated_artifacts,
        "all_approved": all_approved,
        "current_agent": "router",
        "iteration_count": state.get("iteration_count", 0)
    }


def should_continue_after_critic(state: EcosystemState) -> str:
    """
    Critic'den sonra yönlendirme mantığı:
    - Tümü onaylandıysa -> "synthesizer"
    - Max iterasyona ulaşıldıysa -> "synthesizer" (zorla bitir)
    - Reddedilen varsa ve iterasyon < MAX -> "parallel_specialists" (tekrar düzelt)
    """
    if state.get("all_approved", False):
        return "synthesizer"
    
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "synthesizer"
    
    return "parallel_specialists"
