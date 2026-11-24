"""
EMOG Reasoning Agent - Mantıksal Problem Çözme

Sorumluluk:
- Karmaşık görevleri mantıksal adımlara parçalar.
- agent_app.reasoning_engine'ı kullanır (dekompozisyon + doğrulama).
- Sonuç otomatik doğru kabul edilmez; kanıt ve test beklenir.
- Aynı cevabı üretmez; farklı mantıksal yaklaşımlar dener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult
from agent_app.reasoning_engine import reasoning_engine

logger = logging.getLogger(__name__)


class ReasoningAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Reasoning_Agent",
            task_type="general_reasoning",
            temperature=0.6,
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

        task_type = "build_app" if any(
            k in user_prompt.lower()
            for k in ("saas", "ürün", "product", "uygulama", "geliştir", "kur")
        ) else "general"

        decomp = reasoning_engine.decompose_task(user_prompt, task_type=task_type)

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Reasoning Ajanısın. Görevin: karmaşık görevleri mantıksal "
                    "adımlara parçalamak ve her adımı doğrulanabilir hale getirmek. "
                    "Sonucu otomatik doğru kabul etme; kanıt ve test gerekli. "
                    "Aynı cevabı üretme; farklı mantıksal yaklaşımlar dene. "
                    "Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Critic geri bildirimi (varsa): {feedback or 'Yok'}\n"
                    f"Görev dekompozisyonu: {decomp['task_name']} -> {decomp['total_steps']} adım\n"
                    f"Adımlar: " + ", ".join(
                        [f"{s['name']}: {s['description']}" for s in decomp["subtasks"]]
                    )
                    + "\n\nHer adım için mantıklı bir açıklama ve doğrulama yöntemi öner."
                ),
            },
        ]

        result = self._call_model(messages)
        artifact = self._make_artifact(state, result)

        existing = state.get("artifacts", [])
        updated = [a for a in existing if a.agent_name != self.agent_name]
        updated.append(artifact)

        reasoning_context = dict(state)
        reasoning_context["reasoning_task_id"] = decomp["task_id"]
        reasoning_context["reasoning_subtasks"] = decomp["subtasks"]

        return {
            "artifacts": updated,
            "current_agent": self.agent_name,
            "__reasoning_context__": reasoning_context,
        }
