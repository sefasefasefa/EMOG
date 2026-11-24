"""
EMOG Multi-Agent Coordinator - Çoklu Ajan İş Akışı Koordinatörü

Yeni ajansları mevcut LangGraph yapısına entegre eder:
- Discovery_Agent: yeni fikirler ve yöntemler
- Research_Agent: bilgi ve kaynak araştırması
- Reasoning_Agent: mantıksal problem çözme
- Strategy_Agent: çözüm stratejileri
- Experiment_Agent: hipotez test etme
- Learning_Agent: deneyimlerden öğrenme
- Optimization_Agent: performans iyileştirme
- Verification_Agent: sonuçları doğrulama

Etkileşim kuralı: Critic bir stratejiyi reddediğinde Discovery Agent yeni
alternatifler araştırır; Strategy Agent o alternatifler üzerinden stratejiyi
revize eder. Aynı cevap üretilmez; her ajanın kendi sorumluluğu vardır.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from multi_agent_ecosystem.schemas import EcosystemState, AgentArtifact
from multi_agent_ecosystem.agents.discovery_agent import DiscoveryAgent
from multi_agent_ecosystem.agents.research_agent import ResearchAgent
from multi_agent_ecosystem.agents.reasoning_agent import ReasoningAgent
from multi_agent_ecosystem.agents.strategy_agent import StrategyAgent
from multi_agent_ecosystem.agents.experiment_agent import ExperimentAgent
from multi_agent_ecosystem.agents.learning_agent import LearningAgent
from multi_agent_ecosystem.agents.optimization_agent import OptimizationAgent
from multi_agent_ecosystem.agents.verification_agent import VerificationAgent

logger = logging.getLogger(__name__)


class MultiAgentCoordinator:
    def __init__(self):
        self.agents = {
            "Discovery_Agent": DiscoveryAgent(),
            "Research_Agent": ResearchAgent(),
            "Reasoning_Agent": ReasoningAgent(),
            "Strategy_Agent": StrategyAgent(),
            "Experiment_Agent": ExperimentAgent(),
            "Learning_Agent": LearningAgent(),
            "Optimization_Agent": OptimizationAgent(),
            "Verification_Agent": VerificationAgent(),
        }

        self.flow_order = [
            "Discovery_Agent",
            "Research_Agent",
            "Reasoning_Agent",
            "Strategy_Agent",
        ]

    def get_agent(self, agent_name: str):
        return self.agents.get(agent_name)

    def run_agent(self, agent_name: str, state: EcosystemState) -> Dict[str, Any]:
        agent = self.get_agent(agent_name)
        if not agent:
            logger.warning("Bilinmeyen ajan: %s", agent_name)
            return {}
        return agent.run(state)

    def run_flow(self, state: EcosystemState) -> EcosystemState:
        current = dict(state)
        for agent_name in self.flow_order:
            result = self.run_agent(agent_name, current)
            if result:
                current.update(result)
                if result.get("artifacts"):
                    current["artifacts"] = result["artifacts"]
                current["current_agent"] = agent_name
        return current

    def run_after_critic(
        self, state: EcosystemState, rejected_agents: List[str]
    ) -> EcosystemState:
        current = dict(state)

        discovery_result = self.run_agent("Discovery_Agent", current)
        if discovery_result:
            current.update(discovery_result)
            if discovery_result.get("artifacts"):
                current["artifacts"] = discovery_result["artifacts"]

        strategy_result = self.run_agent("Strategy_Agent", current)
        if strategy_result:
            current.update(strategy_result)
            if strategy_result.get("artifacts"):
                current["artifacts"] = strategy_result["artifacts"]

        current["current_agent"] = "critic_revision"
        return current


multi_agent_coordinator = MultiAgentCoordinator()


def run_multi_agent_flow(state: EcosystemState) -> EcosystemState:
    return multi_agent_coordinator.run_flow(state)


def run_after_critic_flow(
    state: EcosystemState, rejected_agents: List[str]
) -> EcosystemState:
    return multi_agent_coordinator.run_after_critic(state, rejected_agents)
