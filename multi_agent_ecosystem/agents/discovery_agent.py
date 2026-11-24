"""
EMOG Discovery Agent - Yeni Fikirler ve Yöntemler

Sorumluluk:
- Verilen görev için yeni keşif yöntemleri ve alternatifler üretir.
- agent_app.discovery_engine'ı kullanır.
- Critic bir stratejiyi reddediğinde yeni alternatifler araştırır.
- Aynı cevabı üretmez; her çalıştırma farklı keşif yöntemleri önerir.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult
from agent_app.discovery_engine import discovery_engine

logger = logging.getLogger(__name__)


class DiscoveryAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Discovery_Agent",
            task_type="info_search",
            temperature=0.8,
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

        try:
            discovery_result = discovery_engine.discover(
                user_prompt,
                context={
                    "specific_requirements": specific_reqs,
                    "critic_feedback": feedback,
                },
                max_hypotheses=5,
            )
        except Exception as exc:
            logger.warning("Discovery engine error: %s", exc)
            discovery_result = None

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Discovery Ajanısın. Görevin: verilen görev için yeni fikirler, "
                    "yöntemler ve alternatif çözüm yolları üretmek. Aynı cevabı üretme; "
                    "her seferinde farklı keşif yöntemleri öner. Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Critic geri bildirimi (varsa): {feedback or 'Yok'}\n"
                    f"Keşif motoru sonuçları: {discovery_result.reasoning if discovery_result else 'Yok'}\n"
                    f"Önerilen yöntemler: "
                    f"{', '.join([h.name for h in (discovery_result.hypotheses or [])]) if discovery_result else 'Yok'}\n\n"
                    "Bu bilgilere dayanarak yeni ve farklı alternatif yöntemler öner."
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
