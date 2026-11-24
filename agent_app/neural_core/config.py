"""NEURAL CORE yapılandırması.

Ayarlar `settings.NEURAL_CORE` altında tutulur; eksik anahtarlar güvenli
varsayılanlarla tamamlanır. Böylece testler ve eski kurulumlar bozulmaz.
"""
import os
from copy import deepcopy

from django.conf import settings


DEFAULTS = {
    "OFFLINE": False,
    "ALLOW_OLLAMA": False,
    "DEFAULT_MODE": "full",
    "MAX_PLAN_STEPS": 10,
    "MAX_ALTERNATIVES": 4,
    "MAX_RESEARCH_QUERIES": 3,
    "RESEARCH_RESULTS": 6,
    "MAX_GOAL_CHARS": 4000,
    "MAX_PREFERENCES": 200,
    "MAX_CONTEXT_EXPERIENCES": 20,
    "MAX_LESSONS": 10,
    "PROVIDER_ORDER": ["nvidia", "openai", "deepseek", "anthropic", "ollama"],
    "PROVIDERS": {
        "nvidia": {
            "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "api_key_env": "NVIDIA_API_KEY",
            "model": os.environ.get("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-chat",
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com/v1",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-3-5-haiku-latest",
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "api_key_env": "",
            "model": "llama3.1",
        },
    },
    "LLM_TIMEOUT": 60,
    "TOOL_ROOTS": ["projects", "task_runs"],
    "TOOL_MAX_READ_BYTES": 200_000,
    "TOOL_MAX_WRITE_BYTES": 100_000,
    "TOOL_COMMAND_TIMEOUT": 30,
    "TOOL_COMMAND_ALLOWLIST": [
        "python", "python3", "py", "pip", "pytest", "git", "node", "npm",
        "dir", "echo", "type", "ls", "cat", "Get-ChildItem", "Get-Content",
    ],
    "TOOL_REQUIRE_APPROVAL": True,
    "AUTONOMY_MAX_GOALS": 10,
    "AUTONOMY_MIN_INTERVAL_MINUTES": 15,
    "AUTONOMY_DEFAULT_INTERVAL_MINUTES": 60,
    "SYNC_RUNS": False,
    "SAFE_POLICY_KEYS": [
        "planner.depth",
        "research.enabled",
        "gateway.preferred",
        "prompt.addendum",
        "tools.require_approval",
    ],
}


def get_config():
    """Birleştirilmiş yapılandırmayı döndür (settings > varsayılan)."""
    config = deepcopy(DEFAULTS)
    overrides = getattr(settings, "NEURAL_CORE", None) or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key == "PROVIDERS" and isinstance(value, dict):
                merged = deepcopy(config.get("PROVIDERS", {}))
                for provider, provider_config in value.items():
                    if isinstance(provider_config, dict):
                        merged.setdefault(provider, {}).update(provider_config)
                    else:
                        merged[provider] = provider_config
                config["PROVIDERS"] = merged
            else:
                config[key] = value
    return config


def offline():
    """Ağ çağrıları kapalı mı?"""
    return bool(get_config().get("OFFLINE"))
