# Multi-Agent Orchestration & Moderation Ecosystem

LangGraph tabanlı, **Intent Detection → Parallel Specialist Agents → Critic Moderation Loop → Synthesizer** akışında uçtan uca çoklu ajan ekosistemi.

## Mimari Akış

```
┌─────────────────┐
│  Orchestrator   │ ← Niyet Analizi + Dinamik Görev Planlama
│  (Intent Node)  │
└────────┬────────┘
         │ Kod Gerekli
         ▼
┌─────────────────────────────────────────────┐
│        Parallel Specialists (Fan-Out)       │
│  ┌──────────┐ ┌─────────┐ ┌─────────────┐   │
│  │Dashboard │ │Security │ │  Database   │   │
│  │  Agent   │ │ Agent   │ │   Agent     │   │
│  └──────────┘ └─────────┘ └─────────────┘   │
│  ┌──────────┐ ┌─────────┐ ┌─────────────┐   │
│  │ Frontend │ │ Backend │ │ Telemetry   │   │
│  │  Agent   │ │ Agent   │ │  Agent      │   │
│  └──────────┘ └─────────┘ └─────────────┘   │
└─────────────────┬───────────────────────────┘
                  │ Fan-In
                  ▼
┌─────────────────────────────────────────────┐
│           Critic / Moderator                │ ← Acımasız Denetim
│  • SQL Injection kontrolü                   │
│  • XSS/CSRF doğrulama                       │
│  • Dashboard responsive (100vw/100vh)       │
│  • Telemetry: Client/Backend/DB/Dashboard   │
│  • Kullanıcı isteği uyumu                   │
│  • Max 5 iterasyon döngüsü                  │
└─────────────────┬───────────────────────────┘
                  │
         ┌────────┴────────┐
         ▼                 ▼
    APPROVED          REJECTED
         │                 │
         ▼                 ▼
┌─────────────────┐  Parallel Specialists
│  Synthesizer    │  (Feedback ile tekrar)
│  (Birleştirici) │
└────────┬────────┘
         ▼
       END
```

## Zorunlu Görev Listesi (Her Kod İsteğinde)

| Görev | Ajan | Açıklama |
|-------|------|----------|
| 📊 Dashboard | Dashboard_Agent | Full viewport (100vw/100vh), responsive, overflow kontrolü |
| 🛡️ Security | Security_Agent | SQL Injection, XSS, CSRF, Auth, OWASP Top 10 |
| 🗄️ Database | Database_Agent | SQLite/PostgreSQL, parameterized queries, migration-ready |
| 🎨 Frontend | Frontend_Agent | React/Next.js/Vue, TypeScript, accessible |
| ⚙️ Backend | Backend_Agent | FastAPI/Node.js, RESTful, validation, auth |
| 📈 Client Telemetry | Telemetry_Agent | Screen, device, browser, network, performance (FCP/LCP/CLS), Beacon API |
| 🔧 Backend Telemetry | Telemetry_Agent | HTTP log, error tracker, AI cost tracker, OpenTelemetry, structured JSON |
| 🗃️ Telemetry Database | Telemetry_Agent | UserSessionLog, SystemPerformanceLog, indexes, migrations |
| 📊 Telemetry Dashboard | Telemetry_Agent | KPI cards, charts (device/OS/country/screen), live logs, AI panel, alerts |

## Kurulum

```bash
cd multi_agent_ecosystem
pip install -r requirements.txt
cp .env.example .env
# .env dosyasına OPENAI_API_KEY=sk-... ekleyin
```

## Çalıştırma

```bash
# Varsayılan test senaryosu
python main.py

# Özel prompt
python main.py "React ile todo app yap ama class component kullanma, hooks kullan"
python main.py "PostgreSQL ile e-ticaret API yaz, SQL injection koruması olsun"
```

## State Yapısı (EcosystemState)

```python
{
    "user_prompt": str,              # Orijinal istek
    "intent": IntentAnalysis,        # Niyet analizi sonucu
    "tasks": List[str],              # Zorunlu 5 görev
    "artifacts": List[AgentArtifact],# Paralel ajan çıktıları
    "final_output": str,             # Birleştirilmiş proje paketi
    "iteration_count": int,          # Critic loop sayacı
    "current_agent": str,            # Aktif ajan
    "all_approved": bool             # Tümü onaylandı mı?
}
```

## AgentArtifact Yapısı

```python
{
    "agent_name": "Security_Agent",
    "code_or_output": "kod/doküman içeriği",
    "is_approved": True,
    "feedback": None,  # Reddedildiyse Critic'in düzeltme talimatı
    "iteration": 1
}
```

## Critic Denetim Kriterleri

1. **SQL Injection**: String concatenation SQL'de = KESİN RED → Parameterized query zorunlu
2. **XSS**: Output encoding, CSP headers kontrolü
3. **CSRF**: Token, SameSite cookie kontrolü
4. **Dashboard Responsive**: 100vw/100vh, CSS Grid/Flexbox, mobile-first, overflow-yok
5. **İstek Uyumu**: Kullanıcının explicit istemediği teknoloji (örn: "Flask kullanma") = RED
6. **Production Ready**: TypeScript strict, error handling, logging, docs

## Moderation Loop (Critic Loop)

```
Iterasyon 1: Paralel ajanlar çalışır → Critic denetler
    ├── Tümü APPROVED → Synthesizer → END
    ├── Bazı RED → Feedback ile ilgili ajan(lar) tekrar çalışır
    └── Max 5 iterasyon → Zorla Synthesizer → END
```

## Test Senaryosu

**Kullanıcı**: *"Bana yerel veritabanında müşteri siparişlerini tutan ve anlık grafikle gösteren bir yönetim paneli yaz."*

**Beklenen Davranış**:
1. Orchestrator → `is_code_required: true` + 5 görev oluşturur
2. 5 Uzman Ajan Paralel Çalışır (Dashboard, Security, DB, FE, BE)
3. Critic her birini denetler:
   - Database_Agent: Raw SQL concat varsa → RED → "Parameterized query kullan"
   - Dashboard_Agent: 100vh/100vw yoksa → RED → "Full viewport bounds kullan"
   - Security_Agent: CSP/XSS kontrolü eksikse → RED
4. Düzeltmeler yapılır, tekrar denetlenir
5. Tümü APPROVED → Synthesizer birleştirir → Eksiksiz proje paketi

## Dosya Yapısı

```
multi_agent_ecosystem/
├── requirements.txt          # Bağımlılıklar
├── .env.example              # API key şablonu
├── schemas.py                # Pydantic modeller + State + Zorunlu Standartlar
├── orchestrator.py           # Intent + Planlama (6 görev otomatik)
├── specialist_agents.py      # 6 paralel ajan (Telemetry eklendi)
├── critic_agent.py           # Denetleyici + Loop mantığı (Telemetry kontrolleri)
├── synthesizer.py            # Çıktı birleştirici
├── graph.py                  # LangGraph compile
├── main.py                   # Async giriş + test
└── README.md                 # Bu dosya
```

## Telemetry / Logging / Analytics (Her Projede Otomatik)

### Client-Side (Frontend)
- **lib/telemetry.ts** - Auto-load script, PerformanceObserver (FCP, LCP, CLS, TTFB, INP)
- **Screen/Viewport**: innerWidth/Height, screen.width/height, devicePixelRatio
- **Device/Browser**: userAgent, platform, language, hardwareConcurrency, maxTouchPoints
- **Geo/Network**: IP (backend header), GeoIP country/city, timezone
- **SPA Navigation**: History API listener, route changes, scroll depth
- **GDPR/CCPA**: Consent banner, anonymize IP, localStorage opt-out
- **Transport**: navigator.sendBeacon / fetch keepalive, batch (30s/50 events), IndexedDB offline queue

### Backend (Middleware)
- **middleware/telemetry.py** - Her request'te çalışır
- **HTTP Log**: user_id (JWT), method, path, query, referer, status, latency_ms, UA
- **Error Tracker**: 5xx, DB deadlock, unhandled exception - msg, stack, request_body
- **AI/API Log**: model, prompt/completion tokens, total_cost_usd, latency_ms
- **Structured JSON Log**: correlation_id (trace), levels (debug/info/warn/error/critical)
- **OpenTelemetry**: OTLP export, Prometheus /metrics endpoint

### Database (Prisma/SQLAlchemy)
- **UserSessionLog**: UUID PK, user_id FK, session_id, IP, geo, UA, device, screen, viewport, DPR, language, tz, referrer, landing/exit, duration, pages, timestamps
- **SystemPerformanceLog**: UUID PK, trace_id, span_id, user_id, session_id, endpoint, method, status, latency, error_msg, stack_trace, error_type, db_query, db_duration, ai_model, ai_tokens, ai_cost, ai_latency, memory, cpu, cache_hit
- **Indexes**: created_at DESC, user_id+created_at, session_id, trace_id, endpoint+status, error_type, ai_model

### Dashboard (Recharts + WebSocket)
- **KPI Cards**: Unique Visitors, Active Sessions, Avg Page Load (p50/p95/p99), Error Rate%, Avg API Latency, AI Cost USD, Token Usage
- **Charts**: Device/OS/Browser/Country/Screen Res/Language (responsive, interactive)
- **Performance**: Page Load time series (p50/p95/p99), API Latency histogram, Core Web Vitals (FCP/LCP/CLS/INP/TTFB), Error Rate trend
- **Live Logs**: Table (Trace ID, Method, Endpoint, Status, Latency, User, Time), filters, search, detail modal
- **WebSocket/SSE**: Canlı güncelleme (tail -f benzeri)
- **AI Panel**: Model usage, cost trend, cost/request, slow/failed calls
- **Alert Panel**: Thresholds (error rate>5%, p95>2s, AI cost>$10/day), notifications (Email/Slack/Webhook/PagerDuty)

## Geliştirme Notları

- **LangGraph StateGraph** kullanılarak compile edilmiş
- **asyncio** ile asenkron `ainvoke` çalıştırma
- **Structured Output** (Pydantic + JsonOutputParser) zorunlu
- **Max 5 iterasyon** güvenliği ile sonsuz döngü engellenir
- **Fan-Out/Fan-In** pattern ile paralellik