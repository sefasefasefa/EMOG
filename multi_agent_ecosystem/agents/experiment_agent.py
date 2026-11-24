"""
EMOG Experiment Agent - Hipotez Test Etme

Sorumluluk:
- Strategy Agent'ın ürettiği hipotezleri test eder.
- Küçük ölçekli deneyler, simülasyonlar veya prototiplerle doğrulama yapar.
- Aynı cevabı üretmez; farklı test senaryoları dener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class ExperimentAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Experiment_Agent",
            task_type="general_reasoning",
            temperature=0.7,
        )

    def run(self, state: EcosystemState) -> Dict[str, Any]:
        user_prompt = state.get("user_prompt", "")
        intent = state.get("intent")
        specific_reqs = []
        if intent is not None:
            specific_reqs = getattr(intent, "specific_requirements", []) or []

        feedback = ""
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == self.agent_name and artifact.feedback:
                feedback = artifact.feedback
                break

        strategy_output = ""
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == "Strategy_Agent":
                strategy_output = artifact.code_or_output
                break

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Experiment Ajanısın. Görevin: Strategy Agent'ın ürettiği "
                    "hipotezleri test etmek, küçük ölçekli deneyler ve simülasyonlar "
                    "yapmak. Aynı cevabı üretme; farklı test senaryoları dene. "
                    "Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Critic geri bildirimi (varsa): {feedback or 'Yok'}\n"
                    f"Test edilecek strateji/hipotez: {strategy_output[:1000] if strategy_output else 'Yok'}\n\n"
                    "Bu hipotezi nasıl test edersin? Hangi deneyleri, simülasyonları "
                    "ve prototipleri oluşturursun? Başarı kriterleri neler?"
                ),
            },
        ]

        result = self._call_model(messages)
        artifact = self._make_artifact(state, result)

        existing = state.get("artifacts", [])
        updated = [a for a in existing if a.agent_name != self.agent_name]
        updated.append(artifact)

        return {
            "artifacts": updated,
            "current_agent": self.agent_name,
        }
