"""Circuit Breaker for error isolation and resilience."""
import asyncio
import logging
import time
from typing import Callable, Any, Optional, TypeVar, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3          # Failures before opening
    success_threshold: int = 2          # Successes in half-open before closing
    timeout: float = 30.0               # Seconds before half-open
    excluded_exceptions: tuple = ()     # Exceptions that don't count as failures


class CircuitOpenError(Exception):
    """Raised when circuit is open."""
    def __init__(self, circuit_name: str, retry_after: float):
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{circuit_name}' is open. Retry after {retry_after:.1f}s")


@dataclass
class CircuitBreaker:
    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    success_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0, init=False)
    last_state_change: float = field(default_factory=time.time, init=False)
    
    def __post_init__(self):
        self._lock = asyncio.Lock()
    
    @property
    def is_open(self) -> bool:
        if self.state == CircuitState.OPEN:
            # Check if timeout elapsed for half-open transition
            if time.time() - self.last_failure_time >= self.config.timeout:
                return False  # Will transition to half-open on next call
            return True
        return False
    
    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.config.timeout:
                return True  # Allow test request
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return False
    
    async def call(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if not self.can_execute():
                retry_after = self.config.timeout - (time.time() - self.last_failure_time)
                raise CircuitOpenError(self.name, max(0, retry_after))
            
            was_half_open = self.state == CircuitState.HALF_OPEN
        
        try:
            # Execute the function
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            async with self._lock:
                self._on_success(was_half_open)
            
            return result
            
        except self.config.excluded_exceptions:
            # Don't count excluded exceptions as failures
            raise
        except Exception as e:
            async with self._lock:
                self._on_failure()
            raise
    
    def _on_success(self, was_half_open: bool):
        self.failure_count = 0
        
        if was_half_open:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
    
    def _on_failure(self):
        self.failure_count += 1
        self.success_count = 0
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
        elif self.state == CircuitState.CLOSED and self.failure_count >= self.config.failure_threshold:
            self._transition_to(CircuitState.OPEN)
    
    def _transition_to(self, new_state: CircuitState):
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        
        if new_state == CircuitState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
        elif new_state == CircuitState.HALF_OPEN:
            self.success_count = 0
        
        logger.info(f"Circuit '{self.name}': {old_state.value} -> {new_state.value}")
    
    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "can_execute": self.can_execute(),
            "last_failure_time": self.last_failure_time,
            "last_state_change": self.last_state_change
        }
    
    def reset(self):
        """Manually reset circuit to closed."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.last_state_change = time.time()


class CircuitBreakerRegistry:
    """Registry of named circuit breakers."""
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        async with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config or CircuitBreakerConfig())
            return self._breakers[name]
    
    async def call(self, name: str, func: Callable[..., Awaitable[T]], *args, 
                   config: Optional[CircuitBreakerConfig] = None, **kwargs) -> T:
        breaker = await self.get_or_create(name, config)
        return await breaker.call(func, *args, **kwargs)
    
    def get_all_status(self) -> dict:
        return {name: b.get_status() for name, b in self._breakers.items()}
    
    def reset_all(self):
        for breaker in self._breakers.values():
            breaker.reset()


# Decorator for easy use
def circuit_breaker(name: str, config: Optional[CircuitBreakerConfig] = None):
    """Decorator to add circuit breaker to async function."""
    registry = CircuitBreakerRegistry()
    
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            breaker = await registry.get_or_create(name, config)
            return await breaker.call(func, *args, **kwargs)
        return wrapper
    return decorator


# Global registry
_global_circuit_registry: Optional[CircuitBreakerRegistry] = None

def get_circuit_registry() -> CircuitBreakerRegistry:
    global _global_circuit_registry
    if _global_circuit_registry is None:
        _global_circuit_registry = CircuitBreakerRegistry()
    return _global_circuit_registry