"""
EMOG Verification Agent - Sonuçları Doğrulama

Sorumluluk:
- Diğer ajansların çıktılarını doğrulama/kontrol eder.
- Sonuç kaynak, kanıt ve test sonuçlarıyla doğrulanmadan doğru kabul edilmez.
- Critic Agent ile işbirliği yapar; Critic hata bulursa Verification onu onaylamaz.
- Aynı cevabı üretmez; farklı doğrulama kriterleri uygular.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class VerificationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Verification_Agent",
            task_type="general_reasoning",
            temperature=0.5,
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

        artifact_summaries = []
        critic_feedbacks = {}
        for artifact in state.get("artifacts", []):
            if artifact.agent_name == self.agent_name:
                continue
            summary = f"{artifact.agent_name}: onay={artifact.is_approved}, iterasyon={artifact.iteration}"
            if artifact.feedback:
                critic_feedbacks[artifact.agent_name] = artifact.feedback
                summary += f", eleştiri={artifact.feedback[:100]}"
            artifact_summaries.append(summary)

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Verification Ajanısın. Görevin: diğer ajansların "
                    "çıktılarını doğrulamak. Sonuç kaynak, kanıt ve test sonuçlarıyla "
                    "doğrulanmadan doğru kabul edilmez. Critic Agent ile işbirliği "
                    "yaparsın; Critic hata bulursa ouputu onaylamazsın. "
                    "Aynı cevabı üretme; farklı doğrulama kriterleri uygula. "
                    "Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Critic geri bildirimi (varsa): {feedback or 'Yok'}\n"
                    f"Diğer ajans çıkışları: {' | '.join(artifact_summaries) if artifact_summaries else 'Yok'}\n"
                    f"Critic eleştirileri: {critic_feedbacks if critic_feedbacks else 'Yok'}\n\n"
                    "Her bir çıkışı doğrula. Kaynak, kanıt ve test var mı? "
                    "Kullanıcı özel istekleri karşılandı mı? Onayla veya reddet."
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
