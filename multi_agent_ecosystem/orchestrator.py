import os
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from .schemas import (
    EcosystemState, IntentAnalysis, AgentArtifact,
    MANDATORY_TASKS, SPECIALIST_AGENTS, MAX_ITERATIONS
)

llm = ChatOpenAI(
    model=os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
    temperature=0,
    api_key=os.getenv("NVIDIA_API_KEY"),
    base_url="https://integrate.api.nvidia.com/v1"
)

intent_parser = JsonOutputParser(pydantic_object=IntentAnalysis)


ORCHESTRATOR_SYSTEM_PROMPT = """Sen bir Multi-Agent Ecosystem'un Orchestrator (Niyet Anlayıcı ve Planlayıcı) ajansın.
Görevin: Kullanıcının isteğini analiz edip, **KOD YAZIMI GEREKİP GEREKMEDİĞİNİ** belirlemek ve **KULLANICININ ÖZEL İSTEKLERİNİ** tam olarak çıkarmak.

ÖNEMLİ KURAL: **Kod yazımı gerekiyorsa, aşağıdaki 5 ZORUNLU BİLEŞEN HER ZAMAN OTOMATİK DAHİL EDİLİR.**
Kullanıcı "dashboard", "veritabanı", "güvenlik", "frontend", "backend" demesine GEREK YOK.
Herhangi bir uygulama/yazılım/kod isteğinde BU 5 BİLEŞEN KESİNLİKLE ÜRETİLİR:

{mandatory_tasks}

UZMAN AJANLAR (Hepsi paralel çalışır): {specialist_agents}

KARARLAR:
1. Eğer kullanıcı SADECE soru soruyorsa / açıklama istiyorsa / kavram soruyorsa -> is_code_required: false
2. Eğer kullanıcı herhangi bir **uygulama, sistem, panel, API, yazılım, kod, script, tool, bot, service, platform, website, app, script, automation, scraper, crawler, dashboard, admin panel, management system, CRUD, REST API, GraphQL, microservice, CLI tool, desktop app, mobile app, game, bot, AI agent, ML model, data pipeline, ETL, scheduler, worker, queue, cache, auth system, payment integration, notification system, real-time, websocket, file upload, email system, report generator, analytics, logging, monitoring, deployment, CI/CD, infrastructure, kubernetes, docker, terraform, ansible** vb. **KOD/YAZILIM** istiyorsa -> is_code_required: true

KULLANICI ÖZEL İSTEKLERİ (ÇOK ÖNEMLİ):
- Kullanıcının isteğindeki HER sıfatı, niteliği, stili yakala. Örn: "güzel", "şatafatlı", "göz alıcı", "renkli", "animasyonlu", "modern", "minimalist", "dark theme", "neon", "glassmorphism" vb.
- Bu sıfatlar specific_requirements listesine EKLE.
- Kullanıcı etkileyici/görsel bir şey istiyorsa aesthetic_expectations = 'flashy' veya 'premium' yap.
- Kullanıcı "güzel CSS", "şatafatlı site", "gözze çarpan" gibi ifadeler kullanıyorsa aesthetic_expectations = 'premium' olmalı.
- specific_requirements BOŞ BIRAKMA - en azından kullanıcının ana isteğini yaz.

Çıktını SADECE aşağıdaki JSON formatında ver:
{format_instructions}"""


def orchestrator_node(state: EcosystemState) -> Dict[str, Any]:
    """
    Niyet analizi ve dinamik görev planlama düğümü.
    - Kullanıcı isteğini analiz eder
    - Kod gerekmiyorsa: direct_response path
    - Kod gerekiyorsa: 5 ZORUNLU GÖREV (Dashboard, Security, DB, Frontend, Backend) otomatik eklenir
    """
    user_prompt = state["user_prompt"]

    system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(
        mandatory_tasks="\n".join(MANDATORY_TASKS),
        specialist_agents=", ".join(SPECIALIST_AGENTS),
        format_instructions=intent_parser.get_format_instructions()
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Kullanıcı İsteği: {user_prompt}")
    ]

    response = llm.invoke(messages)
    parsed = intent_parser.parse(response.content)
    if isinstance(parsed, dict):
        intent = IntentAnalysis(**parsed)
    else:
        intent = parsed

    # Initialize artifacts list for each specialist agent
    initial_artifacts = [
        AgentArtifact(agent_name=agent, code_or_output="", is_approved=False, iteration=0)
        for agent in SPECIALIST_AGENTS
    ]

    if intent.is_code_required:
        return {
            "intent": intent,
            "tasks": MANDATORY_TASKS.copy(),
            "artifacts": initial_artifacts,
            "iteration_count": 0,
            "current_agent": "parallel_start",
            "all_approved": False,
            "final_output": ""
        }
    else:
        # Kod gerekmiyorsa doğrudan yanıt ver
        return {
            "intent": intent,
            "tasks": [],
            "artifacts": [],
            "final_output": f"Bu istek kod yazımı gerektirmiyor. Özet: {intent.intent_summary}",
            "all_approved": True
        }
