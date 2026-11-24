"""
EMOG Strategy Agent - Çözüm Stratejileri

Sorumluluk:
- Keşfedilen yöntemler ve mantıksal analizden çözüm stratejileri üretir.
- Critic bir stratejiyi reddediğinde Discovery Agent yeni alternatifler arar,
  bu ajanı o alternatifler üzerinden strateji revize eder.
- Aynı cevabı üretmez; farklı stratejik yaklaşımlar dener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class StrategyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Strategy_Agent",
            task_type="general_reasoning",
            temperature=0.7,
        )

    def run(self, state: EcosystemState) -> Dict[str, Any]:
        user_prompt = state.get("user_prompt", "")
        intent = state.get("intent")
        specific_reqs = []
        if intent is not None:
            specific_reqs = getattr(intent, "specific_requirements", []) or []

        feedbacks = {}
        for artifact in state.get("artifacts", []):
            if artifact.feedback:
                feedbacks[artifact.agent_name] = artifact.feedback

        discovery_output = ""
        reasoning_output = ""
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == "Discovery_Agent":
                discovery_output = artifact.code_or_output
            elif artifact.agent_name == "Reasoning_Agent":
                reasoning_output = artifact.code_or_output

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Strategy Ajanısın. Görevin: keşfedilen yöntemler ve "
                    "mantıksal analizden çözüm stratejileri üretmek. Critic bir "
                    "stratejiyi reddediğinde, Discovery Agent'ın yeni alternatifleri "
                    "kullanarak stratejiyi revize et. Aynı cevabı üretme; farklı "
                    "stratejik yaklaşımlar dene. Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Discovery çıktısı: {discovery_output[:800] if discovery_output else 'Yok'}\n"
                    f"Reasoning çıktısı: {reasoning_output[:800] if reasoning_output else 'Yok'}\n"
                    f"Geri bildirimler: {feedbacks if feedbacks else 'Yok'}\n\n"
                    "Bu bilgilere dayanarak bir çözüm stratejisi oluştur. "
                    "Adım adım, eylemlenebilir bir plan sun."
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
