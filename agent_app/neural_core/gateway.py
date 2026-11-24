"""Sağlayıcı bağımsız model gateway'i.

Sıra: NVIDIA → OpenAI → DeepSeek → Anthropic → Ollama. Anahtarı tanımlı
olmayan sağlayıcı atlanır. Çevrimdışı modda hiçbir ağ çağrısı yapılmaz;
çağıran taraf deterministik şablonlara düşer.
"""
import json
import logging
import os
import urllib.error
import urllib.request

from django.conf import settings

from . import config as neural_config

logger = logging.getLogger(__name__)


class ModelGateway:
    def __init__(self, config=None):
        self.config = config or neural_config.get_config()
        self.last_error = ""

    # ------------------------------------------------------------------
    # Sağlayıcı keşfi
    # ------------------------------------------------------------------
    def _provider_config(self, name):
        return (self.config.get("PROVIDERS") or {}).get(name) or {}

    def _provider_ready(self, name):
        provider = self._provider_config(name)
        if not provider:
            return False
        if name == "nvidia":
            key = os.environ.get("NVIDIA_API_KEY") or (
                getattr(settings, "OMNIROUTE", {}) or {}
            ).get("API_KEY", "")
            return bool(key and provider.get("base_url"))
        if name == "ollama":
            return bool(self.config.get("ALLOW_OLLAMA") and provider.get("base_url"))
        key_env = provider.get("api_key_env") or ""
        return bool(key_env and os.environ.get(key_env))

    def available(self):
        if neural_config.offline():
            return []
        order = self.config.get("PROVIDER_ORDER") or list(
            (self.config.get("PROVIDERS") or {}).keys()
        )
        return [name for name in order if self._provider_ready(name)]

    def active_provider(self):
        providers = self.available()
        return providers[0] if providers else ""

    # ------------------------------------------------------------------
    # Genel arayüz
    # ------------------------------------------------------------------
    def complete(self, system, user, purpose="neural", temperature=0.2, max_tokens=None):
        """Sağlayıcıları sırayla dene; hiçbiri yanıt vermezse "" döndür."""
        if neural_config.offline():
            return ""
        for name in self.available():
            try:
                text = self._call(name, system, user, purpose, temperature, max_tokens)
            except Exception as exc:  # sağlayıcı hatası bir sonrakine düşer
                self.last_error = f"{name}: {exc}"
                logger.warning("NEURAL gateway %s hatası: %s", name, exc)
                continue
            if text and text.strip():
                return text.strip()
        return ""

    def _call(self, name, system, user, purpose, temperature, max_tokens):
        if name == "nvidia":
            return self._call_nvidia(system, user, purpose, temperature, max_tokens)
        if name == "anthropic":
            return self._call_anthropic(system, user, temperature, max_tokens)
        if name == "ollama":
            return self._call_ollama(system, user, temperature, max_tokens)
        return self._call_openai_compatible(
            name, system, user, temperature, max_tokens
        )

    # ------------------------------------------------------------------
    # Sağlayıcılar
    # ------------------------------------------------------------------
    def _call_nvidia(self, system, user, purpose, temperature, max_tokens):
        from agent_app.buildkit import llm as build_llm

        model = self._provider_config("nvidia").get("model") or None
        return build_llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            purpose=purpose if purpose in ("research", "coding", "agent", "fastest") else "default",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _call_openai_compatible(self, name, system, user, temperature, max_tokens):
        provider = self._provider_config(name)
        key = os.environ.get(provider.get("api_key_env") or "", "")
        payload = {
            "model": provider.get("model") or "",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        data = _post_json(
            f"{(provider.get('base_url') or '').rstrip('/')}/chat/completions",
            payload,
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            return (data["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"geçersiz yanıt: {exc}") from exc

    def _call_anthropic(self, system, user, temperature, max_tokens):
        provider = self._provider_config("anthropic")
        key = os.environ.get(provider.get("api_key_env") or "", "")
        payload = {
            "model": provider.get("model") or "",
            "max_tokens": max_tokens or 2048,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = _post_json(
            f"{(provider.get('base_url') or '').rstrip('/')}/messages",
            payload,
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        blocks = data.get("content") or []
        return "\n".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        ).strip()

    def _call_ollama(self, system, user, temperature, max_tokens):
        provider = self._provider_config("ollama")
        payload = {
            "model": provider.get("model") or "",
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        data = _post_json(
            f"{(provider.get('base_url') or '').rstrip('/')}/api/chat",
            payload,
            {"Content-Type": "application/json"},
        )
        return ((data.get("message") or {}).get("content") or "").strip()


def _post_json(url, payload, headers):
    timeout = int(neural_config.get_config().get("LLM_TIMEOUT", 60))
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
