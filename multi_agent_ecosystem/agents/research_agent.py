"""
EMOG Research Agent - Bilgi ve Kaynak Araştırması

Sorumluluk:
- Verilen görev için bilgi ve kaynakları araştırır.
- Keşfedilen yöntemleri derinleştiren kaynak toplayıcı.
- Aynı cevabı üretmez; farklı kaynak açılarından araştırma yapar.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Research_Agent",
            task_type="info_search",
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

        try:
            from agent_app.discovery_engine import discovery_engine

            discovery_result = discovery_engine.discover(
                user_prompt, context={"research": True}, max_hypotheses=3
            )
            methods = ", ".join(
                [f"{h.name} ({h.data_source})" for h in (discovery_result.hypotheses or [])]
            ) if discovery_result else "Yok"
        except Exception as exc:
            logger.warning("Research discovery lookup error: %s", exc)
            methods = "Yok"

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Research Ajanısın. Görevin: verilen görev için bilgi ve kaynakları "
                    "araştırmak, kanıt toplayıcı olarak çalışmak. Aynı cevabı üretme; farklı "
                    "kaynak açılarından araştırma yap. Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Critic geri bildirimi (varsa): {feedback or 'Yok'}\n"
                    f"Keşfedilen veri kaynakları: {methods}\n\n"
                    "Bu görev için hangi bilgiye, kaynağa ve kanıta ihtiyaç var? "
                    "Araştırma planı ve kaynak önerileri sun."
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
