import os
from typing import Dict, Any, Callable
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .schemas import (
    EcosystemState, AgentArtifact,
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


AGENT_PROMPTS = {
    "Dashboard_Agent": f"""Sen Dashboard_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready dashboard arayüzü tasarımı/kodu üretmek.

{DASHBOARD_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "dashboard" demesine gerek YOK - her kod isteğiinde otomatik gelir
- ZORUNLU STİL: Her çıktı MUTLAKA CSS/styling içermeli (Tailwind sınıfları, <style>, CSS variables, renk paleti). Stilsiz/renksiz düz HTML KABUL EDİLMEZ.
- Framework: Next.js 14 (App Router) + Tailwind + Radix UI tercih edilir
- Chart kütüphanesi: Recharts / Chart.js - responsive container ile
- State management: Zustand + TanStack Query
- Çıktı: Tam dosya yapısı (app/, components/, lib/, hooks/, types/)

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde).""",

    "Security_Agent": f"""Sen Security_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready güvenlik katmanı/middleware/tarama kodu üretmek.

{SECURITY_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "güvenlik" demesine gerek YOK - her kod isteğinde otomatik gelir
- Framework-agnostic: FastAPI / NestJS / Next.js middleware olarak kullanılabilir
- Çıktı: Middleware dosyaları, validator sınıfları, config, audit logger, testler
- Tüm korumalar **kod seviyesinde** implement edilmeli (sadece dokümantasyon değil)

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde).""",

    "Database_Agent": f"""Sen Database_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready veritabanı şeması, ORM modelleri, migration'lar ve repository pattern kodu üretmek.

{DATABASE_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "veritabanı" demesine gerek YOK - her kod isteğinde otomatik gelir
- ORM: Prisma (Node) / SQLAlchemy 2.0 (Python) / Drizzle - ZORUNLU
- RAW SQL YASAK - Sadece ORM query API
- Çıktı: schema.prisma / models.py, migration dosyaları, repository classes, seed scripts
- Multi-tenant ready (RLS policies) başlangıçtan dahil

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde).""",

    "Frontend_Agent": f"""Sen Frontend_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready frontend kodu (React/Next.js/Vue) üretmek.

{FRONTEND_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "frontend" demesine gerek YOK - her kod isteğinde otomatik gelir
- Framework: Next.js 14 App Router (tercih) veya Vite + React 18
- UI: Tailwind + Radix UI / shadcn-ui - accessible primitives
- State: TanStack Query (server) + Zustand (client)
- Forms: React Hook Form + Zod
- ZORUNLU STİL: Her çıktı MUTLAKA CSS/styling içermeli (Tailwind sınıfları, <style>, styled-components veya CSS modules). Stilsiz/renksiz düz HTML KABUL EDİLMEZ.
- Çıktı: Tam proje yapısı (app/, components/, lib/, hooks/, types/, tests/)

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde).""",

    "Backend_Agent": f"""Sen Backend_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready backend API kodu (FastAPI/NestJS) üretmek.

{BACKEND_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "backend" demesine gerek YOK - her kod isteğinde otomatik gelir
- Framework: FastAPI (Python 3.11+) tercih / NestJS (Node) alternatif
- Validation: Pydantic v2 / Zod - her endpoint'te
- Auth: JWT RS256 + Refresh rotation, RBAC middleware
- Database: Repository + Unit of Work pattern, DI container
- Çıktı: Tam proje yapısı (app/, domain/, infrastructure/, tests/, Dockerfile, k8s/)

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde).""",

    "Telemetry_Agent": f"""Sen Telemetry_Agent'sın. Görevin: **HER UYGULAMA İÇİN ZORUNLU** tam donanımlı, 
production-ready telemetry/logging/analytics altyapısı üretmek.

=== CLIENT-SIDE TELEMETRY (Frontend) ===
{CLIENT_TELEMETRY_MANDATORY_FEATURES}

=== BACKEND TELEMETRY (Middleware/Interceptor) ===
{BACKEND_TELEMETRY_MANDATORY_FEATURES}

=== TELEMETRY VERİTABANI MODELLERİ ===
{TELEMETRY_DATABASE_MANDATORY_FEATURES}

=== TELEMETRY ADMIN DASHBOARD ===
{TELEMETRY_DASHBOARD_MANDATORY_FEATURES}

EK KURALLAR:
- Kullanıcı "telemetry", "log", "analytics", "monitoring" demesine gerek YOK - her kod isteğinde otomatik gelir
- Frontend: Next.js 14 + TypeScript - telemetry script (lib/telemetry.ts), provider, hook
- Backend: FastAPI middleware (middleware/telemetry.py), error handler, AI cost tracker
- Database: Prisma/SQLAlchemy models + migrations (UserSessionLog, SystemPerformanceLog)
- Dashboard: Recharts/Chart.js + WebSocket live logs, KPI cards, charts, trace viewer
- Output: Tam dosya yapısı (lib/telemetry/, middleware/, models/, app/telemetry/, components/telemetry/)

Kullanıcı isteği: {{user_prompt}}
Critic geri bildirimi (varsa): {{feedback}}

Çıktı: Sadece kod/dosya içeriği (markdown code block içinde)."""
}


def create_specialist_agent(agent_name: str) -> Callable[[EcosystemState], Dict[str, Any]]:
    """Belirli bir uzman ajanı için node fonksiyonu oluşturur."""
    
    def agent_node(state: EcosystemState) -> Dict[str, Any]:
        user_prompt = state["user_prompt"]
        
        # Intent'ten kullanıcının özel isteklerini çıkar
        intent = state.get("intent")
        specific_reqs = []
        aesthetic = "standard"
        quality_bar = "production"
        if intent is not None:
            specific_reqs = getattr(intent, "specific_requirements", []) or []
            aesthetic = getattr(intent, "aesthetic_expectations", "standard") or "standard"
            quality_bar = getattr(intent, "quality_bar", "production") or "production"
        
        # Bu ajan için feedback'i bul
        feedback = ""
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == agent_name and artifact.feedback:
                feedback = artifact.feedback
                break
        
        system_prompt = AGENT_PROMPTS[agent_name]
        
        # Kullanıcının özel isteklerini ve estetik beklentilerini prompt'a ekle
        req_block = ""
        if specific_reqs:
            req_block = "\n\n=== KULLANICININ ÖZEL İSTEKLERİ (ZORUNLU KARŞILANMALI) ===\n" + "\n".join(f"- {r}" for r in specific_reqs)
        req_block += f"\n\nEstetik beklenti: {aesthetic}\nKalite çıtası: {quality_bar}"
        
        # Aesthetic premium/flashy ise Frontend/Dashboard ajanlarına ek talimat
        aesthetic_mandate = ""
        if aesthetic in ("flashy", "premium", "rich") and agent_name in ("Frontend_Agent", "Dashboard_Agent"):
            aesthetic_mandate = (
                "\n\n=== GÖRSEL KALİTE ZORUNLULUĞU (KULLANICI ETKİLEYİCİ BİR ŞEY İSTİYOR) ===\n"
                "- Kullanıcı etkileyici/göz alıcı bir tasarım istiyor. Çıktıda MUTLAKA şunlar olmalı:\n"
                "  * Zengin renk paleti (gradient, neon, glow, accent renkler) - tek renkli/minimal CSS YASAK\n"
                "  * Animasyonlar ve geçişler (keyframes, transitions, hover effects, scroll animations)\n"
                "  * Gölgeler, derinlik, glassmorphism/neumorphism gibi modern efektler\n"
                "  * Büyük, etkileyici tipografi ve spacing\n"
                "  * Responsive ve full-viewport (100vw/100vh) layout\n"
                "- 'Küçük CSS', 'tek renk', 'minimal styling' KABUL EDİLMEZ. En az 200+ satır CSS/styling içermeli.\n"
                "- Eğer çıktı küçük/renksiz/minimal ise REDDEDİLİR ve tekrar üretilmelidir.\n"
            )
        
        messages = [
            SystemMessage(content=system_prompt + aesthetic_mandate),
            HumanMessage(content=f"Kullanıcı İsteği: {user_prompt}{req_block}\nCritic Geri Bildirimi: {feedback or 'Yok (ilk çalıştırma)'}")
        ]
        
        response = llm.invoke(messages)
        code_output = response.content
        
        # Artifact'ı güncelle
        updated_artifacts = []
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == agent_name:
                updated_artifacts.append(AgentArtifact(
                    agent_name=agent_name,
                    code_or_output=code_output,
                    is_approved=False,
                    feedback=None,
                    iteration=artifact.iteration + 1
                ))
            else:
                updated_artifacts.append(artifact)
        
        return {
            "artifacts": updated_artifacts,
            "current_agent": agent_name
        }
    
    return agent_node


# 6 uzman ajanı node fonksiyonları
dashboard_agent_node = create_specialist_agent("Dashboard_Agent")
security_agent_node = create_specialist_agent("Security_Agent")
database_agent_node = create_specialist_agent("Database_Agent")
frontend_agent_node = create_specialist_agent("Frontend_Agent")
backend_agent_node = create_specialist_agent("Backend_Agent")
telemetry_agent_node = create_specialist_agent("Telemetry_Agent")


def run_parallel_specialists(state: EcosystemState) -> Dict[str, Any]:
    """
    Tüm 6 uzman ajanı aynı anda (paralel) çalıştırır.
    LangGraph fan-out pattern için kullanılır.
    """
    results = {}
    
    for agent_name in SPECIALIST_AGENTS:
        # Her ajan için state kopyası oluştur
        agent_state = {**state, "current_agent": agent_name}
        
        # Ajanı çalıştır
        if agent_name == "Dashboard_Agent":
            result = dashboard_agent_node(agent_state)
        elif agent_name == "Security_Agent":
            result = security_agent_node(agent_state)
        elif agent_name == "Database_Agent":
            result = database_agent_node(agent_state)
        elif agent_name == "Frontend_Agent":
            result = frontend_agent_node(agent_state)
        elif agent_name == "Backend_Agent":
            result = backend_agent_node(agent_state)
        elif agent_name == "Telemetry_Agent":
            result = telemetry_agent_node(agent_state)
        
        results[agent_name] = result
    
    # Tüm artifact'ları birleştir
    all_artifacts = []
    for artifact in state.get("artifacts", []):
        agent_name = artifact.agent_name
        if agent_name in results and results[agent_name].get("artifacts"):
            # Güncellenmiş artifact'ı bul
            for updated in results[agent_name]["artifacts"]:
                if updated.agent_name == agent_name:
                    all_artifacts.append(updated)
                    break
        else:
            all_artifacts.append(artifact)
    
    return {
        "artifacts": all_artifacts,
        "current_agent": "critic",
        "iteration_count": state.get("iteration_count", 0) + 1
    }
