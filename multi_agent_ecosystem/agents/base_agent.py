"""
EMOG Multi-Agent System - Temel Ajan Sınıfı

Tüm yeni ajanslar buradan türetilir. Ortak işlevler:
- Model Gateway üzerinden modele çağrı (anahtar yoksa pas geçer)
- Discovery Engine / Reasoning Engine entegrasyonu
- AgentArtifact üretimi
- Başarı / hata takibi
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from multi_agent_ecosystem.schemas import AgentArtifact, EcosystemState
from agent_app.model_gateway import model_gateway, ModelEndpoint

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    agent_name: str
    output: str
    is_approved: bool = False
    feedback: Optional[str] = None
    iteration: int = 0
    model_used: Optional[str] = None
    provider: Optional[str] = None
    latency: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    def __init__(
        self,
        agent_name: str,
        task_type: str = "general_reasoning",
        temperature: float = 0.7,
    ):
        self.agent_name = agent_name
        self.task_type = task_type
        self.temperature = temperature
        self.success_count = 0
        self.fail_count = 0

    def _call_model(
        self,
        messages: List[Dict[str, str]],
        preferred_provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AgentResult:
        temp = temperature if temperature is not None else self.temperature
        result = model_gateway.complete(
            task_type=self.task_type,
            messages=messages,
            preferred_provider=preferred_provider,
            temperature=temp,
            max_tokens=max_tokens,
        )

        if not result.get("ok"):
            self.fail_count += 1
            return AgentResult(
                agent_name=self.agent_name,
                output="",
                is_approved=False,
                feedback=result.get("error", "Model çağrısı başarısız"),
                model_used=None,
                provider=None,
            )

        self.success_count += 1
        return AgentResult(
            agent_name=self.agent_name,
            output=result.get("text", ""),
            is_approved=False,
            model_used=result.get("model"),
            provider=result.get("provider"),
            latency=result.get("latency", 0.0),
        )

    def _make_artifact(
        self,
        state: EcosystemState,
        result: AgentResult,
        feedback: Optional[str] = None,
    ) -> AgentArtifact:
        existing = next(
            (a for a in state.get("artifacts", []) if a.agent_name == self.agent_name),
            None,
        )
        iteration = (existing.iteration + 1) if existing else 1

        return AgentArtifact(
            agent_name=self.agent_name,
            code_or_output=result.output,
            is_approved=result.is_approved,
            feedback=feedback or result.feedback,
            iteration=iteration,
        )

    def run(self, state: EcosystemState) -> Dict[str, Any]:
        raise NotImplementedError
