import os
from typing import Any, Dict
from langgraph.graph import StateGraph, END

from .schemas import EcosystemState
from .orchestrator import orchestrator_node
from .specialist_agents import (
    run_parallel_specialists,
    dashboard_agent_node,
    security_agent_node,
    database_agent_node,
    frontend_agent_node,
    backend_agent_node
)
from .critic_agent import critic_node, should_continue_after_critic
from .synthesizer import synthesizer_node
from .agents.coordinator import (
    run_multi_agent_flow,
    run_after_critic_flow,
)
from .agents.discovery_agent import DiscoveryAgent
from .agents.research_agent import ResearchAgent
from .agents.reasoning_agent import ReasoningAgent
from .agents.strategy_agent import StrategyAgent
from .agents.experiment_agent import ExperimentAgent
from .agents.learning_agent import LearningAgent
from .agents.optimization_agent import OptimizationAgent
from .agents.verification_agent import VerificationAgent

# Yeni ajan node fonksiyonları (state -> state)
discovery_agent = DiscoveryAgent()
research_agent = ResearchAgent()
reasoning_agent = ReasoningAgent()
strategy_agent = StrategyAgent()
experiment_agent = ExperimentAgent()
learning_agent = LearningAgent()
optimization_agent = OptimizationAgent()
verification_agent = VerificationAgent()


def new_agents_phase(state: EcosystemState) -> Dict[str, Any]:
    """
    Discovery, Research, Reasoning, Strategy ajanslarını çalıştırır.
    Her birinin kendi sorumluluğu vardır; aynı çıktıyı üretmez.
    """
    from multi_agent_ecosystem.agents.coordinator import run_multi_agent_flow
    return run_multi_agent_flow(state)


def post_critic_revision(state: EcosystemState) -> Dict[str, Any]:
    """
    Critic red verdiğinde çalışır.
    Discovery yeni alternatifler araştırır; Strategy stratejiyi revize eder.
    """
    from multi_agent_ecosystem.agents.coordinator import run_after_critic_flow
    rejected = [
        a.agent_name for a in state.get("artifacts", [])
        if not a.is_approved and a.feedback
    ]
    return run_after_critic_flow(state, rejected)


def experiment_phase(state: EcosystemState) -> Dict[str, Any]:
    return experiment_agent.run(state)


def learning_phase(state: EcosystemState) -> Dict[str, Any]:
    return learning_agent.run(state)


def verification_phase(state: EcosystemState) -> Dict[str, Any]:
    return verification_agent.run(state)


def optimization_phase(state: EcosystemState) -> Dict[str, Any]:
    return optimization_agent.run(state)


def create_ecosystem_graph() -> StateGraph:
    """
    Multi-Agent Orchestration & Moderation Ecosystem LangGraph'i oluşturur.

    GENİŞLENMİŞ AKIŞ:
    1. orchestrator (Niyet analizi + Planlama / Planner)
       ├─ Kod gerekmiyorsa -> END (direct_response)
       └─ Kod gerekiyorsa -> parallel_specialists (Fan-Out)

    2. parallel_specialists (6 Uzman Ajan Paralel)
       ├─ Dashboard_Agent, Security_Agent, Database_Agent,
       │   Frontend_Agent, Backend_Agent, Telemetry_Agent

    3. new_agents_phase (Yeni Uzman Ajanslar)
       ├─ Discovery_Agent: yeni fikirler ve yöntemler
       ├─ Research_Agent: bilgi ve kaynak araştırması
       ├─ Reasoning_Agent: mantıksal problem çözme
       └─ Strategy_Agent: çözüm stratejileri

    4. critic (Denetleyici - Fan-In)
       ├─ Tümü APPROVED -> experiment_phase
       ├─ Max iterasyon -> experiment_phase (zorla devam)
       └─ Reddedilen var -> post_critic_revision (Discovery + Strategy revizyon)

    5. post_critic_revision -> critic (döngü)

    6. experiment_phase (Hipotez Test Etme)
    7. learning_phase (Deneyimlerden Öğrenme)
    8. verification_phase (Sonuçları Doğrulama)
    9. optimization_phase (Performans İyileştirme)

    10. synthesizer (Birleştirici) -> END
    """

    workflow = StateGraph(EcosystemState)

    # Mevcut node'lar
    workflow.add_node("orchestrator", orchestrator_node)
    workflow.add_node("parallel_specialists", run_parallel_specialists)
    workflow.add_node("critic", critic_node)
    workflow.add_node("synthesizer", synthesizer_node)

    # Yeni ajan node'ları
    workflow.add_node("new_agents_phase", new_agents_phase)
    workflow.add_node("post_critic_revision", post_critic_revision)
    workflow.add_node("experiment_phase", experiment_phase)
    workflow.add_node("learning_phase", learning_phase)
    workflow.add_node("verification_phase", verification_phase)
    workflow.add_node("optimization_phase", optimization_phase)

    # Entry point
    workflow.set_entry_point("orchestrator")

    # Orchestrator'dan sonraki yönlendirme
    def route_after_orchestrator(state: EcosystemState) -> str:
        intent = state.get("intent")
        if intent and intent.is_code_required:
            return "parallel_specialists"
        return "end"

    workflow.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "parallel_specialists": "parallel_specialists",
            "end": END
        }
    )

    # Paralel uzmanlar -> yeni ajanslar
    workflow.add_edge("parallel_specialists", "new_agents_phase")

    # Yeni ajanslar -> critic
    workflow.add_edge("new_agents_phase", "critic")

    # Critic'ten sonraki yönlendirme
    def route_after_critic(state: EcosystemState) -> str:
        if state.get("all_approved", False):
            return "experiment_phase"
        if state.get("iteration_count", 0) >= 5:
            return "experiment_phase"
        return "post_critic_revision"

    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "experiment_phase": "experiment_phase",
            "post_critic_revision": "post_critic_revision",
        }
    )

    # Critic revizyonundan sonra tekrar critic'e
    workflow.add_edge("post_critic_revision", "critic")

    # Son akış: experiment -> learning -> verification -> optimization -> synthesizer
    workflow.add_edge("experiment_phase", "learning_phase")
    workflow.add_edge("learning_phase", "verification_phase")
    workflow.add_edge("verification_phase", "optimization_phase")
    workflow.add_edge("optimization_phase", "synthesizer")

    # Synthesizer'dan sonra bitir
    workflow.add_edge("synthesizer", END)

    return workflow.compile()


# Graph instance
ecosystem_graph = create_ecosystem_graph()


def get_graph_visualization() -> str:
    """Graph yapısını mermaid diagram olarak döndürür (debug için)."""
    return """
graph TD
    A[orchestrator] -->|Kod Gerekli| B[parallel_specialists]
    A -->|Sadece Yanıt| Z[END]

    B --> C[new_agents_phase]
    C --> D[Discovery_Agent]
    C --> E[Research_Agent]
    C --> F[Reasoning_Agent]
    C --> G[Strategy_Agent]

    D --> H[critic]
    E --> H
    F --> H
    G --> H

    H -->|Tümü Onaylandı| I[experiment_phase]
    H -->|Max Iterasyon| I
    H -->|Reddedilen Var| J[post_critic_revision]

    J --> D
    J --> G
    J --> H

    I --> K[learning_phase]
    K --> L[verification_phase]
    L --> M[optimization_phase]
    M --> N[synthesizer]

    N --> Z
"""
