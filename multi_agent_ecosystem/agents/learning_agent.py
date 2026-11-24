"""
EMOG Learning Agent - Deneyimlerden Öğrenme

Sorumluluk:
- Önceki çalışmaların geri bildirimlerinden öğrenir.
- Cognitive Kernel / Experience Memory kullanır.
- Critic'in red nedenlerini analiz eder ve iyileştirme kuralları çıkarır.
- Aynı hatayı tekrar etmez.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult
from agent_app.cognitive_kernel import ck0

logger = logging.getLogger(__name__)


class LearningAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Learning_Agent",
            task_type="general_reasoning",
            temperature=0.6,
        )

    def run(self, state: EcosystemState) -> Dict[str, Any]:
        user_prompt = state.get("user_prompt", "")
        intent = state.get("intent")
        specific_reqs = []
        if intent is not None:
            specific_reqs = getattr(intent, "specific_requirements", []) or []

        all_feedback = []
        approved = 0
        rejected = 0
        for artifact in state.get("artifacts", []):
            if artifact.feedback:
                all_feedback.append(f"{artifact.agent_name}: {artifact.feedback}")
            if artifact.is_approved:
                approved += 1
            else:
                rejected += 1

        try:
            past_experiences = ck0.memory.get_experiences_by_category(
                intent.intent_summary if intent else "general"
            )
            past_exp_summary = [
                {
                    "selected_option": e.get("selected_option", {}).get("text", "")[:100],
                    "score": e.get("feedback_reward"),
                }
                for e in past_experiences[-5:]
            ]
        except Exception as exc:
            logger.warning("Learning memory lookup error: %s", exc)
            past_exp_summary = []

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Learning Ajanısın. Görevin: önceki çalışmaların geri "
                    "bildirimlerinden öğrenmek, Critic'in red nedenlerini analiz etmek "
                    "ve iyileştirme kuralları çıkarmak. Aynı hatayı tekrar etme. "
                    "Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Onaylanan artifact sayısı: {approved}\n"
                    f"Reddedilen artifact sayısı: {rejected}\n"
                    f"Geri bildirimler: {' | '.join(all_feedback) if all_feedback else 'Yok'}\n"
                    f"Geçmiş deneyimler: {past_exp_summary}\n\n"
                    "Bu deneyimlerden hangi dersleri çıkardın? Hangisi tekrar etmemeli? "
                    "İyileştirme kuralları neler?"
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
