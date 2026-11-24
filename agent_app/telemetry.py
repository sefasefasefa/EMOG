"""
Telemetry & Cost Management Middleware
Tracks tokens, costs, enforces budgets, auto-fallback to local models
"""
import os
import json
import time
import threading
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps

# Optional Redis - graceful fallback if not available
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None
    REDIS_AVAILABLE = False

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin


@dataclass
class UsageRecord:
    """Single usage record"""
    timestamp: str
    conversation_id: str
    user_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: int
    success: bool
    endpoint: str


@dataclass
class BudgetConfig:
    """Budget configuration"""
    daily_limit_usd: float = 5.0
    monthly_limit_usd: float = 100.0
    per_conversation_limit_usd: float = 1.0
    per_request_limit_usd: float = 0.10


# Global budget config
_budget_config = BudgetConfig()
_redis_client: Optional["redis.Redis"] = None
_config_lock = threading.Lock()
_redis_available = False


def get_redis() -> Optional["redis.Redis"]:
    """Get Redis client with graceful fallback"""
    global _redis_client, _redis_available
    if not REDIS_AVAILABLE:
        _redis_available = False
        return None
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                retry_on_timeout=False,
            )
            # Test connection
            _redis_client.ping()
            _redis_available = True
        except Exception:
            _redis_client = None
            _redis_available = False
    return _redis_client if _redis_available else None


def is_redis_available() -> bool:
    """Check if Redis is available"""
    global _redis_available
    if _redis_available:
        try:
            client = get_redis()
            if client:
                client.ping()
                return True
        except Exception:
            pass
    _redis_available = False
    return False


def set_budget_config(config: BudgetConfig) -> None:
    """Update budget configuration"""
    global _budget_config
    with _config_lock:
        _budget_config = config


def get_budget_config() -> BudgetConfig:
    """Get current budget configuration"""
    return _budget_config


class CostTracker:
    """Track and enforce cost budgets (with optional Redis)"""
    
    def __init__(self):
        self.redis = get_redis()
        self._enabled = is_redis_available()
        # In-memory fallback for when Redis is unavailable
        self._memory_store: Dict[str, float] = {}
    
    def _daily_key(self, user_id: str, date: str = None) -> str:
        date = date or datetime.utcnow().strftime('%Y-%m-%d')
        return f"emog:cost:daily:{user_id}:{date}"
    
    def _monthly_key(self, user_id: str, month: str = None) -> str:
        month = month or datetime.utcnow().strftime('%Y-%m')
        return f"emog:cost:monthly:{user_id}:{month}"
    
    def _conversation_key(self, conversation_id: str) -> str:
        return f"emog:cost:conv:{conversation_id}"
    
    def _get(self, key: str) -> float:
        """Get value from Redis or memory fallback"""
        if self._enabled and self.redis:
            try:
                val = self.redis.get(key)
                if val is not None:
                    return float(val)
            except Exception:
                pass
        return self._memory_store.get(key, 0.0)
    
    def _set(self, key: str, value: float) -> None:
        """Set value in Redis or memory fallback"""
        if self._enabled and self.redis:
            try:
                self.redis.set(key, str(value))
                return
            except Exception:
                pass
        self._memory_store[key] = value
    
    def _incrbyfloat(self, key: str, amount: float) -> float:
        """Increment by float in Redis or memory fallback"""
        if self._enabled and self.redis:
            try:
                return float(self.redis.incrbyfloat(key, amount))
            except Exception:
                pass
        current = self._memory_store.get(key, 0.0)
        new_val = current + amount
        self._memory_store[key] = new_val
        return new_val
    
    def record_usage(self, record: UsageRecord) -> Dict[str, Any]:
        """Record usage and check budgets"""
        # Increment counters
        self._incrbyfloat(self._daily_key(record.user_id), record.cost_usd)
        self._incrbyfloat(self._monthly_key(record.user_id), record.cost_usd)
        self._incrbyfloat(self._conversation_key(record.conversation_id), record.cost_usd)
        
        # Store detailed record (optional, Redis only)
        if self._enabled and self.redis:
            try:
                record_key = f"emog:cost:record:{record.timestamp}:{record.conversation_id}"
                pipe = self.redis.pipeline()
                pipe.set(record_key, json.dumps({
                    'conversation_id': record.conversation_id,
                    'user_id': record.user_id,
                    'model': record.model,
                    'input_tokens': record.input_tokens,
                    'output_tokens': record.output_tokens,
                    'total_tokens': record.total_tokens,
                    'cost_usd': record.cost_usd,
                    'latency_ms': record.latency_ms,
                    'success': record.success,
                    'endpoint': record.endpoint,
                }))
                pipe.expire(record_key, 86400 * 30)  # 30 days
                pipe.execute()
            except Exception:
                pass
        
        # Check budgets
        return self.check_budgets(record.user_id, record.conversation_id)
    
    def check_budgets(self, user_id: str, conversation_id: str) -> Dict[str, Any]:
        """Check if any budgets exceeded"""
        config = get_budget_config()
        
        daily_cost = self._get(self._daily_key(user_id))
        monthly_cost = self._get(self._monthly_key(user_id))
        conv_cost = self._get(self._conversation_key(conversation_id))
        
        budgets = {
            'daily': {
                'used': daily_cost,
                'limit': config.daily_limit_usd,
                'exceeded': daily_cost >= config.daily_limit_usd,
            },
            'monthly': {
                'used': monthly_cost,
                'limit': config.monthly_limit_usd,
                'exceeded': monthly_cost >= config.monthly_limit_usd,
            },
            'conversation': {
                'used': conv_cost,
                'limit': config.per_conversation_limit_usd,
                'exceeded': conv_cost >= config.per_conversation_limit_usd,
            }
        }
        
        any_exceeded = any(b['exceeded'] for b in budgets.values())
        
        return {
            'budgets': budgets,
            'any_exceeded': any_exceeded,
            'fallback_required': any_exceeded,
            'redis_available': self._enabled,
        }
    
    def get_usage_stats(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """Get usage statistics"""
        stats = {}
        
        for i in range(days):
            date = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
            key = self._daily_key(user_id, date)
            cost = self._get(key)
            stats[date] = cost
        
        return {
            'daily': stats,
            'total': sum(stats.values()),
        }
    
    def reset_budgets(self, user_id: str) -> None:
        """Reset budgets for user (admin action)"""
        if self._enabled and self.redis:
            try:
                pattern = f"emog:cost:*:{user_id}:*"
                keys = self.redis.keys(pattern)
                if keys:
                    self.redis.delete(*keys)
            except Exception:
                pass
        # Also clear memory store
        keys_to_delete = [k for k in self._memory_store.keys() if f":{user_id}:" in k]
        for k in keys_to_delete:
            del self._memory_store[k]


# Global instance
_cost_tracker: Optional[CostTracker] = None


def get_cost_tracker() -> CostTracker:
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a model call"""
    from agent_app.tasks.llm import MODEL_CONFIGS, _calculate_cost
    config = MODEL_CONFIGS.get(model, {})
    return _calculate_cost(config, input_tokens, output_tokens)


def track_llm_call(func: Callable) -> Callable:
    """Decorator to track LLM API calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        conversation_id = kwargs.get('conversation_id', 'unknown')
        user_id = kwargs.get('user_id', 'anonymous')
        model = kwargs.get('model', 'unknown')
        
        # Call the function
        result = func(*args, **kwargs)
        
        # Extract usage from result
        latency_ms = int((time.time() - start) * 1000)
        
        if isinstance(result, dict):
            input_tokens = result.get('input_tokens', 0)
            output_tokens = result.get('output_tokens', 0)
            total_tokens = result.get('tokens_used', input_tokens + output_tokens)
            success = result.get('success', True)
        else:
            input_tokens = output_tokens = total_tokens = 0
            success = True
        
        cost = calculate_cost(model, input_tokens, output_tokens)
        
        # Record
        tracker = get_cost_tracker()
        record = UsageRecord(
            timestamp=datetime.utcnow().isoformat(),
            conversation_id=conversation_id,
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            success=success,
            endpoint=func.__name__
        )
        
        budget_status = tracker.record_usage(record)
        result['_budget'] = budget_status
        
        return result
    
    return wrapper


class BudgetMiddleware(MiddlewareMixin):
    """Django middleware to enforce budget limits"""
    
    def process_request(self, request):
        # Check budget before processing
        if request.path.startswith('/api/') or request.path.startswith('/chat/'):
            user_id = getattr(request.user, 'id', 'anonymous') if hasattr(request, 'user') else 'anonymous'
            
            tracker = get_cost_tracker()
            budgets = tracker.check_budgets(user_id, request.GET.get('conversation_id', 'unknown'))
            
            if budgets['any_exceeded']:
                return JsonResponse({
                    'ok': False,
                    'error': 'Budget exceeded',
                    'budgets': budgets,
                    'fallback': 'Switching to local model',
                }, status=429)
        
        return None


def get_fallback_model(current_model: str) -> str:
    """Get fallback model when budget exceeded"""
    # Priority: local free models > cheaper models > current
    fallbacks = [
        'nvidia/nemotron-3-ultra',  # Free via NVIDIA
        'deepseek/deepseek-chat',   # Very cheap
        'openai/gpt-4o-mini',       # Cheaper OpenAI
    ]
    
    for fb in fallbacks:
        if fb != current_model and fb in MODEL_CONFIGS:
            return fb
    
    return 'nvidia/nemotron-3-ultra'


# Global model fallback resolver
def resolve_model_with_budget(requested_model: str, user_id: str) -> str:
    """
    Resolve model with budget awareness.
    Returns fallback if budget exceeded.
    """
    tracker = get_cost_tracker()
    budgets = tracker.check_budgets(user_id, 'unknown')
    
    if budgets['fallback_required']:
        return get_fallback_model(requested_model)
    
    return requested_model