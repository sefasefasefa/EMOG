"""
LLM Ensemble Tasks - Parallel model execution with consensus
"""
import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from celery import shared_task
from ..celery_app import app

from openai import OpenAI


@dataclass
class ModelResponse:
    """Response from a single model"""
    model: str
    content: str
    latency_ms: int
    tokens_used: int
    cost_usd: float
    success: bool
    error: Optional[str] = None


@dataclass
class EnsembleResult:
    """Final ensemble result"""
    selected_response: ModelResponse
    all_responses: List[ModelResponse]
    consensus_score: float
    evaluation: Dict[str, Any]


def _default_nvidia_model():
    try:
        from django.conf import settings as dj_settings
        return (
            getattr(dj_settings, "FASTEST_MODEL", None)
            or (getattr(dj_settings, "NVIDIA_TASK_MODELS", {}) or {}).get("default")
            or getattr(dj_settings, "OMNIROUTE_DEFAULT_MODEL", None)
            or "nvidia/nemotron-3-super-120b-a12b"
        )
    except Exception:
        return os.environ.get("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b")


# Model configurations
MODEL_CONFIGS = {
    'openai/gpt-4o': {
        'client': 'openai',
        'cost_per_1k_input': 0.005,
        'cost_per_1k_output': 0.015,
        'max_tokens': 4096,
    },
    'anthropic/claude-3.5-sonnet': {
        'client': 'anthropic',
        'cost_per_1k_input': 0.003,
        'cost_per_1k_output': 0.015,
        'max_tokens': 4096,
    },
    'deepseek/deepseek-chat': {
        'client': 'openai',  # DeepSeek uses OpenAI-compatible API
        'base_url': 'https://api.deepseek.com/v1',
        'cost_per_1k_input': 0.00014,
        'cost_per_1k_output': 0.00028,
        'max_tokens': 4096,
    },
    'nvidia/nemotron-3-super-120b-a12b': {
        'client': 'nvidia',
        'base_url': os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1'),
        'cost_per_1k_input': 0.0,
        'cost_per_1k_output': 0.0,
        'max_tokens': 4096,
    },
}


def _get_default_ensemble_models():
    nvidia = _default_nvidia_model()
    if nvidia not in MODEL_CONFIGS:
        MODEL_CONFIGS[nvidia] = {
            'client': 'nvidia',
            'base_url': os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1'),
            'cost_per_1k_input': 0.0,
            'cost_per_1k_output': 0.0,
            'max_tokens': 4096,
        }
    models = [nvidia]
    for candidate in ('openai/gpt-4o', 'anthropic/claude-3.5-sonnet', 'deepseek/deepseek-chat'):
        prefix = candidate.split('/')[0].upper()
        if os.environ.get(f"{prefix}_API_KEY"):
            models.append(candidate)
    return models


# Default ensemble models (can be overridden per task)
DEFAULT_ENSEMBLE_MODELS = _get_default_ensemble_models()


def _get_client(model_name: str) -> tuple:
    """Get OpenAI-compatible client for model"""
    config = MODEL_CONFIGS.get(model_name)
    if not config:
        # Allow unknown nvidia models by deriving config from settings/env
        if model_name.startswith("nvidia/"):
            config = {
                'client': 'nvidia',
                'base_url': os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1'),
                'cost_per_1k_input': 0.0,
                'cost_per_1k_output': 0.0,
                'max_tokens': 4096,
            }
            MODEL_CONFIGS[model_name] = config
        else:
            config = {}
    client_type = config.get('client', 'openai')
    base_url = config.get('base_url')
    if client_type == 'nvidia' and not base_url:
        base_url = os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
    api_key = config.get('api_key') or os.environ.get(f"{model_name.split('/')[0].upper()}_API_KEY")
    if client_type == 'nvidia' and not api_key:
        api_key = os.environ.get("NVIDIA_API_KEY")

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=60
    ), config


def _calculate_cost(config: Dict, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD"""
    input_cost = (input_tokens / 1000) * config.get('cost_per_1k_input', 0)
    output_cost = (output_tokens / 1000) * config.get('cost_per_1k_output', 0)
    return input_cost + output_cost


def _call_model(model_name: str, messages: List[Dict], temperature: float = 0.2, 
                max_tokens: int = 4096) -> ModelResponse:
    """Call a single model and return structured response"""
    import time
    
    client, config = _get_client(model_name)
    max_tokens = min(max_tokens, config.get('max_tokens', 4096))
    
    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort="none",
        )
        latency = int((time.time() - start) * 1000)
        
        content = response.choices[0].message.content or ""
        usage = response.usage
        
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        cost = _calculate_cost(config, input_tokens, output_tokens)
        
        return ModelResponse(
            model=model_name,
            content=content,
            latency_ms=latency,
            tokens_used=input_tokens + output_tokens,
            cost_usd=cost,
            success=True
        )
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return ModelResponse(
            model=model_name,
            content="",
            latency_ms=latency,
            tokens_used=0,
            cost_usd=0.0,
            success=False,
            error=str(e)
        )


def _evaluate_responses(responses: List[ModelResponse], 
                       task_type: str = 'code') -> EnsembleResult:
    """
    Evaluate multiple model responses and select best one.
    Scoring criteria:
    - Structural validity (code blocks, syntax)
    - Latency (lower is better)
    - Cost (lower is better)
    - Token efficiency
    - Success status
    """
    successful = [r for r in responses if r.success]
    
    if not successful:
        return EnsembleResult(
            selected_response=responses[0],
            all_responses=responses,
            consensus_score=0.0,
            evaluation={'error': 'All models failed'}
        )
    
    # Score each response
    scored = []
    for r in successful:
        score = 100.0  # Base score
        
        # Structural checks
        if task_type == 'code':
            if '```' in r.content:
                score += 10
            if r.content.count('```') >= 2:
                score += 5  # Properly closed code blocks
        
        # Latency penalty (1 point per 100ms over 1000ms)
        if r.latency_ms > 1000:
            score -= (r.latency_ms - 1000) / 100
        
        # Cost penalty (10 points per $0.01)
        score -= r.cost_usd * 1000
        
        # Token efficiency (bonus for concise responses)
        if r.tokens_used > 0:
            score += min(10, 2000 / r.tokens_used)
        
        scored.append((score, r))
    
    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    
    # Consensus: how close are the top responses?
    if len(scored) > 1:
        top_score = scored[0][0]
        second_score = scored[1][0]
        consensus = 1.0 - abs(top_score - second_score) / max(abs(top_score), 1)
    else:
        consensus = 1.0
    
    return EnsembleResult(
        selected_response=best,
        all_responses=responses,
        consensus_score=consensus,
        evaluation={
            'scores': {r.model: s for s, r in scored},
            'consensus': consensus,
            'successful_count': len(successful),
            'total_count': len(responses)
        }
    )


@shared_task(bind=True, name='agent_app.tasks.llm.ensemble')
def ensemble_completion(self, messages: List[Dict], 
                       models: List[str] = None,
                       task_type: str = 'code',
                       temperature: float = 0.2,
                       max_tokens: int = 4096) -> Dict[str, Any]:
    """
    Execute ensemble completion across multiple models.
    """
    if models is None:
        models = DEFAULT_ENSEMBLE_MODELS
    
    # Filter to available models
    available = [m for m in models if m in MODEL_CONFIGS]
    if not available:
        available = DEFAULT_ENSEMBLE_MODELS
    
    # Run all models in parallel using thread pool
    with ThreadPoolExecutor(max_workers=len(available)) as executor:
        futures = {
            executor.submit(_call_model, model, messages, temperature, max_tokens): model
            for model in available
        }
        
        responses = []
        for future in futures:
            try:
                responses.append(future.result(timeout=60))
            except Exception as e:
                model = futures[future]
                responses.append(ModelResponse(
                    model=model,
                    content="",
                    latency_ms=0,
                    tokens_used=0,
                    cost_usd=0.0,
                    success=False,
                    error=f"Timeout/Error: {str(e)}"
                ))
    
    # Evaluate and select best
    result = _evaluate_responses(responses, task_type)
    
    return {
        'selected': {
            'model': result.selected_response.model,
            'content': result.selected_response.content,
            'latency_ms': result.selected_response.latency_ms,
            'tokens_used': result.selected_response.tokens_used,
            'cost_usd': result.selected_response.cost_usd,
        },
        'all_responses': [
            {
                'model': r.model,
                'content': r.content[:200] + '...' if len(r.content) > 200 else r.content,
                'latency_ms': r.latency_ms,
                'tokens_used': r.tokens_used,
                'cost_usd': r.cost_usd,
                'success': r.success,
                'error': r.error
            }
            for r in result.all_responses
        ],
        'consensus_score': result.consensus_score,
        'evaluation': result.evaluation
    }


@shared_task(bind=True, name='agent_app.tasks.llm.single')
def single_completion(self, messages: List[Dict], model: str = None,
                     temperature: float = 0.2, max_tokens: int = 4096) -> Dict[str, Any]:
    """
    Single model completion for non-critical tasks.
    """
    if not model:
        model = _default_nvidia_model()
    if model not in MODEL_CONFIGS and not model.startswith("nvidia/"):
        model = _default_nvidia_model()

    response = _call_model(model, messages, temperature, max_tokens)
    
    return {
        'model': response.model,
        'content': response.content,
        'latency_ms': response.latency_ms,
        'tokens_used': response.tokens_used,
        'cost_usd': response.cost_usd,
        'success': response.success,
        'error': response.error
    }


@shared_task(bind=True, name='agent_app.tasks.llm.stream')
def stream_completion(self, messages: List[Dict], model: str = None,
                     temperature: float = 0.2, max_tokens: int = 4096) -> Dict[str, Any]:
    """
    Streaming completion - returns generator-like chunks.
    Note: Celery doesn't support true streaming, so this collects chunks.
    """
    if not model:
        model = _default_nvidia_model()
    if model not in MODEL_CONFIGS and not model.startswith("nvidia/"):
        model = _default_nvidia_model()

    client, config = _get_client(model)
    
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            reasoning_effort="none",
        )
        
        chunks = []
        for chunk in stream:
            if chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)
        
        full_content = ''.join(chunks)
        
        return {
            'model': model,
            'content': full_content,
            'chunks': len(chunks),
            'success': True
        }
    except Exception as e:
        return {
            'model': model,
            'content': '',
            'success': False,
            'error': str(e)
        }