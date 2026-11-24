"""
EMOG Optimization Agent - Performans İyileştirme

Sorumluluk:
- Diğer ajansların çıktılarını analiz eder, performans iyileştirmeleri önerir.
- Model maliyetleri, gecikme, başarı oranlarını karşılaştırır.
- Aynı cevabı üretmez; farklı optimizasyon yaklaşımları dener.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from multi_agent_ecosystem.agents.base_agent import BaseAgent, AgentResult
from agent_app.model_gateway import model_gateway

logger = logging.getLogger(__name__)


class OptimizationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="Optimization_Agent",
            task_type="general_reasoning",
            temperature=0.7,
        )

    def run(self, state: EcosystemState) -> Dict[str, Any]:
        user_prompt = state.get("user_prompt", "")
        intent = state.get("intent")
        specific_reqs = []
        if intent is not None:
            specific_reqs = getattr(intent, "specific_requirements", []) or []

        gw_status = model_gateway.status()
        active_providers = gw_status.get("active_providers", [])
        endpoints = gw_status.get("endpoints", [])

        other_outputs = {}
        for artifact in state.get("artifacts", []):
            if artifact.agent_name != self.agent_name:
                other_outputs[artifact.agent_name] = artifact.code_or_output[:300] if artifact.code_or_output else ""

        messages = [
            {
                "role": "system",
                "content": (
                    "Sen EMOG Optimization Ajanısın. Görevin: diğer ajansların "
                    "çıktılarını analiz etmek, performans iyileştirmeleri önermek, "
                    "model maliyetlerini ve gecikmeleri karşılaştırmak. "
                    "Aynı cevabı üretme; farklı optimizasyon yaklaşımları dene. "
                    "Çıktı Türkçe olmalı."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Görev: {user_prompt}\n"
                    f"Özel istekler: {', '.join(specific_reqs) if specific_reqs else 'Yok'}\n"
                    f"Aktif model sağlayıcıları: {', '.join(active_providers) if active_providers else 'Yok'}\n"
                    f"Model endpoint durumları: {endpoints}\n"
                    f"Diğer ajans çıkışları: {other_outputs}\n\n"
                    "Bu çıktıları nasıl optimize edersin? Hangi model daha uygun? "
                    "Maliyet-güvenlik denesi nasıl kurulur? İyileştirme önerileri sun."
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
