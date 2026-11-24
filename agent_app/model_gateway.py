"""
EMOG Model Gateway - Harici ve yerel model bağlantıları için merkezi araç.

Sadece ortam değişkeninde API anahtarı olan sağlayıcılar aktif olur;
anahtarı yoksa ilgili sağlayıcı direkt atlanır (pas geçer), sistem çökmez.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.conf import settings

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ModelEndpoint:
    provider: str
    model: str
    base_url: str
    api_key_env: str
    active: bool = False
    cost_input_per_1k: float = 0.0
    cost_output_per_1k: float = 0.0
    timeout: float = 60.0
    reasoning_effort: Optional[str] = None
    last_error: Optional[str] = None
    success_count: int = 0
    fail_count: int = 0
    total_latency: float = 0.0


class ModelGateway:
    def __init__(self) -> None:
        self.endpoints: List[ModelEndpoint] = []
        self.local_base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434").strip()
        self.local_models = [m.strip() for m in os.getenv("LOCAL_LLM_MODELS", "").split(",") if m.strip()]
        self._build_endpoints()

        # NVIDIA görev-modelleri (settings.NVIDIA_TASK_MODELS): her görev anahtarı
        # kendi modeline eşlenir; gateway select_model bu haritayı kullanır.
        task_map = getattr(settings, "NVIDIA_TASK_MODELS", {}) or {}
        self.task_model_map: Dict[str, str] = {k: v for k, v in task_map.items() if v}

        self.task_routing: Dict[str, List[str]] = {
            "general_reasoning": ["nvidia:default", "nvidia:agent", "openai", "anthropic", "deepseek", "kimi", "qwen", "local"],
            "code_research": ["nvidia:coding", "nvidia:research", "deepseek", "qwen", "nvidia:default", "openai", "anthropic", "local"],
            "info_search": ["nvidia:research", "nvidia:default", "openai", "anthropic", "deepseek", "local"],
            "preference_evaluation": ["internal:neural_core"],
            "permission_check": ["internal:binary_core"],
            "memory": ["internal:experience_engine"],
            "alternative_generation": ["internal:discovery_engine"],
        }

    def _add(self, provider, model, base_url, key_env, input_cost, output_cost, timeout=60.0,
             reasoning_effort=None) -> None:
        key = os.getenv(key_env, "").strip()
        if not key:
            logger.info("ModelGateway: %s atlandı (API anahtarı yok)", provider)
            return
        self.endpoints.append(ModelEndpoint(
            provider=provider, model=model, base_url=base_url,
            api_key_env=key_env, active=True,
            cost_input_per_1k=input_cost, cost_output_per_1k=output_cost,
            timeout=timeout, reasoning_effort=reasoning_effort,
        ))
        logger.info("ModelGateway: %s aktif (%s)", provider, model)

    def _build_endpoints(self) -> None:
        nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
        nvidia_base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        nvidia_timeout = float(os.getenv("NVIDIA_TIMEOUT", "120") or 120)
        if nvidia_key:
            # Tek bir NVIDIA endpoint'i (default model) + her görev-specific model ayrı endpoint olarak.
            self._add(
                "nvidia",
                os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
                nvidia_base,
                "NVIDIA_API_KEY", 0.0, 0.0,
                timeout=nvidia_timeout,
                reasoning_effort="none",
            )
            task_map = getattr(settings, "NVIDIA_TASK_MODELS", {}) or {}
            seen_models = {os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b")}
            for task_key, model_id in task_map.items():
                if not model_id or model_id in seen_models:
                    continue
                self._add(
                    f"nvidia:{task_key}",
                    model_id,
                    nvidia_base,
                    "NVIDIA_API_KEY", 0.0, 0.0,
                    timeout=nvidia_timeout,
                    reasoning_effort="none",
                )
                seen_models.add(model_id)
                logger.info("ModelGateway: nvidia:%s aktif (%s)", task_key, model_id)
        self._add(
            "openai",
            os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "OPENAI_API_KEY", 0.15, 0.60,
        )
        self._add(
            "anthropic",
            os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307"),
            os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
            "ANTHROPIC_API_KEY", 0.25, 1.25,
        )
        self._add(
            "deepseek",
            os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "DEEPSEEK_API_KEY", 0.14, 0.28,
        )
        self._add(
            "kimi",
            os.getenv("KIMI_MODEL", "moonshot-v1-8k"),
            os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
            "KIMI_API_KEY", 0.0, 0.0,
        )
        self._add(
            "qwen",
            os.getenv("QWEN_MODEL", "qwen-turbo"),
            os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "QWEN_API_KEY", 0.0, 0.0,
        )
        if self.local_models:
            for m in self.local_models:
                self.endpoints.append(ModelEndpoint(
                    provider="local", model=m, base_url=self.local_base_url,
                    api_key_env="", active=True,
                    cost_input_per_1k=0.0, cost_output_per_1k=0.0,
                    timeout=180.0,
                ))
                logger.info("ModelGateway: yerel model aktif (%s)", m)

    def select_model(self, task_type: str) -> Optional[ModelEndpoint]:
        order = self.task_routing.get(task_type, ["nvidia", "openai", "deepseek", "local"])
        for token in order:
            if token.startswith("internal:"):
                engine = token.split(":", 1)[1]
                return ModelEndpoint(
                    provider="internal", model=engine, base_url="",
                    api_key_env="", active=True,
                )
            # nvidia:<task_key> -> görev-specific NVIDIA modeli
            if token.startswith("nvidia:"):
                task_key = token.split(":", 1)[1]
                model_id = self.task_model_map.get(task_key)
                if model_id:
                    for ep in self.endpoints:
                        if ep.provider == token and ep.active and ep.model == model_id:
                            return ep
                # fallback: genel nvidia endpoint'i
                for ep in self.endpoints:
                    if ep.provider == "nvidia" and ep.active:
                        return ep
                continue
            for ep in self.endpoints:
                if ep.provider == token and ep.active:
                    return ep
        for ep in self.endpoints:
            if ep.active:
                return ep
        return None

    def active_providers(self) -> List[str]:
        return sorted({ep.provider for ep in self.endpoints if ep.active})

    def status(self) -> Dict[str, Any]:
        return {
            "active_providers": self.active_providers(),
            "active_endpoint_count": sum(1 for e in self.endpoints if e.active),
            "endpoints": [
                {
                    "provider": e.provider,
                    "model": e.model,
                    "active": e.active,
                    "success": e.success_count,
                    "fail": e.fail_count,
                    "avg_latency_s": round(e.total_latency / max(e.success_count, 1), 3),
                    "last_error": e.last_error,
                }
                for e in self.endpoints
            ],
        }

    def complete(
        self,
        task_type: str,
        messages: List[Dict[str, str]],
        preferred_provider: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        ep = self.select_model(task_type)
        if ep is None:
            return {
                "ok": False,
                "error": "Hiçbir aktif model sağlayıcısı bulunamadı.",
                "task_type": task_type,
            }

        if ep.provider == "internal":
            return {
                "ok": True,
                "provider": "internal",
                "model": ep.model,
                "route": "internal",
                "task_type": task_type,
                "text": "",
            }

        order = self._endpoint_order(task_type, preferred_provider)
        last_error = ""
        for candidate in order:
            if not candidate.active:
                continue
            try:
                start = time.time()
                text = self._call_provider(candidate, messages, temperature=temperature, max_tokens=max_tokens)
                latency = time.time() - start
                candidate.success_count += 1
                candidate.total_latency += latency
                candidate.last_error = None
                return {
                    "ok": True,
                    "provider": candidate.provider,
                    "model": candidate.model,
                    "text": text,
                    "latency": round(latency, 3),
                    "task_type": task_type,
                }
            except Exception as exc:
                candidate.fail_count += 1
                candidate.last_error = str(exc)[:200]
                last_error = candidate.last_error
                logger.warning("ModelGateway: %s/%s hata: %s", candidate.provider, candidate.model, exc)
                continue

        return {
            "ok": False,
            "error": last_error or "Tüm aktif sağlayıcılar hata verdi.",
            "task_type": task_type,
        }

    def complete_multi(
        self,
        task_type: str,
        messages: List[Dict[str, str]],
        providers: Optional[List[str]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        order = self._endpoint_order(task_type)
        if providers:
            order = [e for e in order if e.provider in providers]

        results: List[Dict[str, Any]] = []
        for ep in order:
            if not ep.active:
                continue
            try:
                start = time.time()
                text = self._call_provider(ep, messages, temperature=temperature, max_tokens=max_tokens)
                latency = time.time() - start
                ep.success_count += 1
                ep.total_latency += latency
                ep.last_error = None
                results.append({
                    "ok": True,
                    "provider": ep.provider,
                    "model": ep.model,
                    "text": text,
                    "latency": round(latency, 3),
                })
            except Exception as exc:
                ep.fail_count += 1
                ep.last_error = str(exc)[:200]
                results.append({
                    "ok": False,
                    "provider": ep.provider,
                    "model": ep.model,
                    "error": ep.last_error,
                })

        return {
            "ok": any(r.get("ok") for r in results),
            "task_type": task_type,
            "count": len(results),
            "results": results,
        }

    def estimate_cost(
        self,
        task_type: str,
        input_tokens: int,
        output_tokens: int,
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for ep in self.endpoints:
            if not ep.active or ep.provider == "internal":
                continue
            cost = (input_tokens / 1000.0) * ep.cost_input_per_1k + \
                   (output_tokens / 1000.0) * ep.cost_output_per_1k
            out.append({
                "provider": ep.provider,
                "model": ep.model,
                "estimated_cost_usd": round(cost, 6),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            })
        out.sort(key=lambda x: x["estimated_cost_usd"])
        return out

    def _endpoint_order(self, task_type: str, preferred: Optional[str] = None) -> List[ModelEndpoint]:
        preferred_list = [preferred] if preferred else self.task_routing.get(task_type, [])
        ordered: List[ModelEndpoint] = []
        seen = set()
        for token in preferred_list:
            if token.startswith("internal:"):
                continue
            for ep in self.endpoints:
                if ep.provider == token and ep.active and id(ep) not in seen:
                    ordered.append(ep)
                    seen.add(id(ep))
        for ep in self.endpoints:
            if ep.active and id(ep) not in seen:
                ordered.append(ep)
                seen.add(id(ep))
        return ordered

    def _call_provider(
        self,
        ep: ModelEndpoint,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> str:
        api_key = os.getenv(ep.api_key_env, "").strip() if ep.api_key_env else ""

        if ep.provider == "openai":
            return self._call_openai_chat(ep, api_key, messages, temperature, max_tokens,
                                          reasoning_effort=ep.reasoning_effort)
        if ep.provider in ("deepseek", "kimi", "qwen"):
            return self._call_openai_chat(ep, api_key, messages, temperature, max_tokens,
                                          reasoning_effort=ep.reasoning_effort)
        if ep.provider == "anthropic":
            return self._call_anthropic(ep, api_key, messages, temperature, max_tokens,
                                        reasoning_effort=ep.reasoning_effort)
        if ep.provider == "nvidia":
            return self._call_nvidia(ep, api_key, messages, temperature, max_tokens,
                                     reasoning_effort=ep.reasoning_effort)
        if ep.provider == "local":
            return self._call_local(ep, messages, temperature, max_tokens,
                                    reasoning_effort=ep.reasoning_effort)
        raise ValueError(f"Bilinmeyen sağlayıcı: {ep.provider}")

    def _call_openai_chat(
        self, ep: ModelEndpoint, api_key: str, messages: List[Dict[str, str]],
        temperature: float, max_tokens: Optional[int],
        reasoning_effort: Optional[str] = None,
    ) -> str:
        url = ep.base_url.rstrip("/") + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": ep.model,
            "messages": messages,
            "temperature": temperature,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with httpx.Client(timeout=ep.timeout) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(
        self, ep: ModelEndpoint, api_key: str, messages: List[Dict[str, str]],
        temperature: float, max_tokens: Optional[int],
        reasoning_effort: Optional[str] = None,
    ) -> str:
        url = ep.base_url.rstrip("/") + "/messages"
        system = ""
        user_messages: List[Dict[str, str]] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                user_messages.append(m)
        payload: Dict[str, Any] = {
            "model": ep.model,
            "system": system,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or 1024,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        with httpx.Client(timeout=ep.timeout) as client:
            resp = client.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return "".join(block.get("text", "") for block in data.get("content", []))

    def _call_nvidia(
        self, ep: ModelEndpoint, api_key: str, messages: List[Dict[str, str]],
        temperature: float, max_tokens: Optional[int],
        reasoning_effort: Optional[str] = None,
    ) -> str:
        url = ep.base_url.rstrip("/") + "/chat/completions"
        payload: Dict[str, Any] = {
            "model": ep.model,
            "messages": messages,
            "temperature": temperature,
        }
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        else:
            payload["reasoning_effort"] = "none"
        if max_tokens:
            payload["max_tokens"] = max_tokens
        with httpx.Client(timeout=ep.timeout) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_local(
        self, ep: ModelEndpoint, messages: List[Dict[str, str]],
        temperature: float, max_tokens: Optional[int],
        reasoning_effort: Optional[str] = None,
    ) -> str:
        url = ep.base_url.rstrip("/") + "/api/chat"
        payload: Dict[str, Any] = {
            "model": ep.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if reasoning_effort:
            payload["options"]["reasoning_effort"] = reasoning_effort
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        with httpx.Client(timeout=ep.timeout) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("message", {}).get("content", "")


# Global instance
model_gateway = ModelGateway()
