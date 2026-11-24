"""
Modular Architecture Module
Microservice framework with service registry, dynamic discovery, and inter-service communication.
"""
import asyncio
import json
import time
import uuid
import inspect
import logging
from typing import Dict, List, Any, Optional, Callable, Type, Set
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from contextlib import asynccontextmanager
from abc import ABC, abstractmethod
import weakref


class ServiceState(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNHEALTHY = "unhealthy"


class ServiceScope(Enum):
    SINGLETON = "singleton"      # One instance per application
    SCOPED = "scoped"            # One instance per request/scope
    TRANSIENT = "transient"      # New instance each time


@dataclass
class ServiceConfig:
    name: str
    version: str = "1.0.0"
    description: str = ""
    scope: ServiceScope = ServiceScope.SINGLETON
    dependencies: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    health_check_interval: int = 30
    auto_restart: bool = True
    max_restarts: int = 3
    startup_timeout: float = 30.0
    shutdown_timeout: float = 10.0


@dataclass
class ServiceEndpoint:
    name: str
    method: str  # GET, POST, PUT, DELETE, etc.
    path: str
    handler: Callable
    input_schema: Optional[Dict] = None
    output_schema: Optional[Dict] = None
    middleware: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    rate_limit: Optional[int] = None  # requests per minute


@dataclass
class ServiceHealth:
    service_name: str
    state: ServiceState
    last_check: float
    uptime: float
    restarts: int
    metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    last_error: Optional[str] = None


class ServiceInterface(ABC):
    """Base interface for all microservices."""
    
    @property
    @abstractmethod
    def metadata(self) -> ServiceConfig:
        """Return service metadata."""
        pass
    
    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize service with configuration."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        pass
    
    @abstractmethod
    async def health_check(self) -> ServiceHealth:
        """Perform health check."""
        pass
    
    def get_endpoints(self) -> List[ServiceEndpoint]:
        """Return service endpoints."""
        return []


class ServiceBase(ServiceInterface):
    """Base class for creating microservices."""
    
    def __init__(self, config: Optional[ServiceConfig] = None):
        self.config = config or ServiceConfig(name=self.__class__.__name__.lower().replace("service", ""))
        self._state = ServiceState.STOPPED
        self._start_time: Optional[float] = None
        self._restart_count = 0
        self._endpoints: Dict[str, ServiceEndpoint] = {}
        self._middleware: List[Callable] = []
        self._hooks: Dict[str, List[Callable]] = {}
    
    @property
    def metadata(self) -> ServiceConfig:
        return self.config
    
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the service."""
        try:
            self.config.config.update(config)
            self._state = ServiceState.STARTING
            await self._on_startup()
            self._state = ServiceState.RUNNING
            self._start_time = time.time()
            return True
        except Exception as e:
            self._state = ServiceState.ERROR
            logging.error(f"Service {self.config.name} failed to start: {e}")
            return False
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        self._state = ServiceState.STOPPING
        await self._on_shutdown()
        self._state = ServiceState.STOPPED
    
    async def health_check(self) -> ServiceHealth:
        """Default health check."""
        return ServiceHealth(
            service_name=self.config.name,
            state=self._state,
            last_check=time.time(),
            uptime=time.time() - self._start_time if self._start_time else 0,
            restarts=self._restart_count,
        )
    
    def register_endpoint(self, endpoint: ServiceEndpoint) -> None:
        """Register an API endpoint."""
        self._endpoints[endpoint.name] = endpoint
    
    def get_endpoints(self) -> List[ServiceEndpoint]:
        return list(self._endpoints.values())
    
    def add_middleware(self, middleware: Callable) -> None:
        """Add middleware to the service."""
        self._middleware.append(middleware)
    
    def register_hook(self, hook_name: str, callback: Callable) -> None:
        """Register a hook callback."""
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []
        self._hooks[hook_name].append(callback)
    
    async def trigger_hook(self, hook_name: str, *args, **kwargs) -> List[Any]:
        """Trigger all callbacks for a hook."""
        results = []
        for callback in self._hooks.get(hook_name, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    result = await callback(*args, **kwargs)
                else:
                    result = callback(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logging.error(f"Hook {hook_name} error: {e}")
        return results
    
    async def _on_startup(self) -> None:
        """Override for custom startup logic."""
        pass
    
    async def _on_shutdown(self) -> None:
        """Override for custom shutdown logic."""
        pass


class ServiceRegistry:
    """
    Central registry for service discovery, lifecycle management,
    and inter-service communication.
    """
    
    def __init__(self):
        self._services: Dict[str, ServiceBase] = {}
        self._service_configs: Dict[str, ServiceConfig] = {}
        self._instances: Dict[str, Any] = {}  # For scoped/transient services
        self._message_routes: Dict[str, str] = {}  # message_type -> service_name
        self._event_listeners: Dict[str, List[Callable]] = {}
        self._dependency_graph: Dict[str, Set[str]] = {}
        self._startup_order: List[str] = []
        
    def register(self, service: ServiceBase, config: Optional[Dict] = None) -> bool:
        """Register a service."""
        if service.config.name in self._services:
            logging.warning(f"Service {service.config.name} already registered")
            return False
        
        # Validate dependencies exist
        for dep in service.config.dependencies:
            if dep not in self._services and dep not in self._service_configs:
                logging.warning(f"Service {service.config.name} depends on unregistered service: {dep}")
        
        self._services[service.config.name] = service
        self._service_configs[service.config.name] = service.config
        self._dependency_graph[service.config.name] = set(service.config.dependencies)
        
        # Register endpoints
        for endpoint in service.get_endpoints():
            self._message_routes[f"{service.config.name}.{endpoint.name}"] = service.config.name
        
        # Register hooks
        for hook_name, callbacks in service._hooks.items():
            if hook_name not in self._event_listeners:
                self._event_listeners[hook_name] = []
            self._event_listeners[hook_name].extend(callbacks)
        
        # Recompute startup order
        self._compute_startup_order()
        
        logging.info(f"Registered service: {service.config.name}")
        return True
    
    def unregister(self, service_name: str) -> bool:
        """Unregister a service."""
        if service_name not in self._services:
            return False
        
        service = self._services[service_name]
        asyncio.create_task(service.shutdown())
        
        # Remove message routes
        routes_to_remove = [k for k, v in self._message_routes.items() if v == service_name]
        for route in routes_to_remove:
            del self._message_routes[route]
        
        # Remove from dependency graph
        del self._dependency_graph[service_name]
        for deps in self._dependency_graph.values():
            deps.discard(service_name)
        
        self._compute_startup_order()
        del self._services[service_name]
        del self._service_configs[service_name]
        
        logging.info(f"Unregistered service: {service_name}")
        return True
    
    def get_service(self, name: str) -> Optional[ServiceBase]:
        """Get service by name."""
        return self._services.get(name)
    
    def get_all_services(self) -> Dict[str, ServiceBase]:
        return self._services.copy()
    
    def get_service_names(self) -> List[str]:
        return list(self._services.keys())
    
    def _compute_startup_order(self) -> None:
        """Topological sort for service startup order."""
        visited = set()
        temp = set()
        order = []
        
        def visit(name: str):
            if name in temp:
                raise ValueError(f"Circular dependency detected involving {name}")
            if name in visited:
                return
            temp.add(name)
            for dep in self._dependency_graph.get(name, []):
                if dep in self._services:
                    visit(dep)
            temp.remove(name)
            visited.add(name)
            order.append(name)
        
        for name in self._services:
            visit(name)
        
        self._startup_order = order
    
    async def start_all(self) -> Dict[str, bool]:
        """Start all services in dependency order."""
        results = {}
        for name in self._startup_order:
            service = self._services[name]
            config = self._service_configs[name].config
            success = await service.initialize(config)
            results[name] = success
            if not success and service.config.dependencies:
                # Stop dependent services
                for dep_name in service.config.dependencies:
                    dep = self._services.get(dep_name)
                    if dep:
                        await dep.shutdown()
        return results
    
    async def stop_all(self) -> None:
        """Stop all services in reverse order."""
        for name in reversed(self._startup_order):
            service = self._services.get(name)
            if service:
                await service.shutdown()
    
    async def send_message(self, target: str, message_type: str, payload: Any, sender: str = "") -> Any:
        """Route message to target service."""
        # Direct target
        if target in self._services:
            service = self._services[target]
            endpoint = service._endpoints.get(message_type)
            if endpoint:
                return await endpoint.handler(payload, sender)
        
        # Route by message type
        if message_type in self._message_routes:
            routed_target = self._message_routes[message_type]
            if routed_target in self._services:
                service = self._services[routed_target]
                endpoint = service._endpoints.get(message_type)
                if endpoint:
                    return await endpoint.handler(payload, sender)
        
        # Broadcast to all services that handle this message type
        results = []
        for service in self._services.values():
            endpoint = service._endpoints.get(message_type)
            if endpoint and service.config.name != sender:
                try:
                    if asyncio.iscoroutinefunction(endpoint.handler):
                        result = await endpoint.handler(payload, sender)
                    else:
                        result = endpoint.handler(payload, sender)
                    results.append({"service": service.config.name, "result": result})
                except Exception as e:
                    results.append({"service": service.config.name, "error": str(e)})
        
        return results if results else None
    
    async def broadcast(self, message_type: str, payload: Any, sender: str = "") -> List[Dict]:
        """Broadcast message to all services that handle it."""
        results = []
        for service in self._services.values():
            if service.config.name != sender:
                endpoint = service._endpoints.get(message_type)
                if endpoint:
                    try:
                        if asyncio.iscoroutinefunction(endpoint.handler):
                            result = await endpoint.handler(payload, sender)
                        else:
                            result = endpoint.handler(payload, sender)
                        results.append({"service": service.config.name, "result": result})
                    except Exception as e:
                        results.append({"service": service.config.name, "error": str(e)})
        return results
    
    async def health_check_all(self) -> Dict[str, ServiceHealth]:
        """Check health of all services."""
        results = {}
        for name, service in self._services.items():
            try:
                results[name] = await service.health_check()
            except Exception as e:
                results[name] = ServiceHealth(
                    service_name=name,
                    state=ServiceState.ERROR,
                    last_check=time.time(),
                    uptime=0,
                    restarts=0,
                    errors=[str(e)]
                )
        return results
    
    def get_topology(self) -> Dict:
        """Get service topology."""
        return {
            "services": {
                name: {
                    "state": svc._state.value,
                    "version": svc.config.version,
                    "dependencies": svc.config.dependencies,
                    "endpoints": list(svc._endpoints.keys()),
                    "hooks": list(svc._hooks.keys()),
                }
                for name, svc in self._services.items()
            },
            "routes": self._message_routes.copy(),
            "startup_order": self._startup_order,
        }


class ServiceFactory:
    """Factory for creating standard services."""
    
    _registry: Dict[str, Type[ServiceBase]] = {}
    
    @classmethod
    def register(cls, name: str, service_class: Type[ServiceBase]) -> None:
        cls._registry[name] = service_class
    
    @classmethod
    def create(cls, name: str, config: Optional[ServiceConfig] = None) -> Optional[ServiceBase]:
        if name in cls._registry:
            return cls._registry[name](config)
        return None
    
    @classmethod
    def create_standard_services(cls) -> List[ServiceBase]:
        """Create standard service suite."""
        services = []
        for name, cls in cls._registry.items():
            if hasattr(cls, 'metadata'):
                service = cls()
                services.append(service)
        return services


# Example concrete services
class APIService(ServiceBase):
    """API Gateway service."""
    
    def __init__(self):
        super().__init__(ServiceConfig(
            name="api-gateway",
            dependencies=["auth-service", "database"],
            config={"port": 8000, "rate_limit": 1000}
        ))
        self.register_endpoint(ServiceEndpoint(
            name="http_request",
            method="POST",
            path="/api/{path}",
            handler=self._handle_request
        ))
        self.register_endpoint(ServiceEndpoint(
            name="health_check",
            method="GET",
            path="/health",
            handler=self._handle_health
        ))
    
    async def _handle_request(self, payload: Dict, sender: str) -> Dict:
        return {"status": "ok", "data": "processed", "sender": sender}
    
    async def _handle_health(self, payload: Dict, sender: str) -> Dict:
        return {"status": "healthy", "service": self.config.name}


class AuthService(ServiceBase):
    """Authentication service."""
    
    def __init__(self):
        super().__init__(ServiceConfig(
            name="auth-service",
            dependencies=["database"],
            config={"jwt_secret": "secret", "token_ttl": 3600}
        ))
        self.register_endpoint(ServiceEndpoint(
            name="validate_token",
            method="POST",
            path="/auth/validate",
            handler=self._validate_token
        ))
        self.register_endpoint(ServiceEndpoint(
            name="generate_token",
            method="POST",
            path="/auth/token",
            handler=self._generate_token
        ))
    
    async def _validate_token(self, payload: Dict, sender: str) -> Dict:
        return {"valid": True, "user_id": "123"}
    
    async def _generate_token(self, payload: Dict, sender: str) -> Dict:
        return {"token": "jwt_token_here", "expires_in": 3600}


class DatabaseService(ServiceBase):
    """Database service."""
    
    def __init__(self):
        super().__init__(ServiceConfig(
            name="database",
            config={"pool_size": 10, "host": "localhost"}
        ))
        self.register_endpoint(ServiceEndpoint(
            name="query",
            method="POST",
            path="/db/query",
            handler=self._query
        ))
        self.register_endpoint(ServiceEndpoint(
            name="execute",
            method="POST",
            path="/db/execute",
            handler=self._execute
        ))
    
    async def _query(self, payload: Dict, sender: str) -> Dict:
        return {"rows": [], "count": 0}
    
    async def _execute(self, payload: Dict, sender: str) -> Dict:
        return {"affected": 0}


# Service Factory
class ServiceFactory:
    """Factory for creating standard services."""
    
    @staticmethod
    def create_standard_services() -> List[ServiceBase]:
        """Create standard service suite."""
        return [
            DatabaseService(),
            AuthService(),
            APIService(),
        ]
    
    @staticmethod
    async def start_all(services: List[ServiceBase], registry: ServiceRegistry) -> Dict[str, bool]:
        """Start all services in dependency order."""
        sorted_services = ServiceRegistry._topological_sort(services)
        
        results = {}
        for service in sorted_services:
            success = await service.start()
            results[service.config.name] = success
            if not success and service.config.dependencies:
                # Stop dependent services
                for dep_name in service.config.dependencies:
                    dep = registry.get_service(dep_name)
                    if dep:
                        await dep.stop()
        return results
    
    @staticmethod
    def _topological_sort(services: List[ServiceBase]) -> List[ServiceBase]:
        """Sort services by dependencies."""
        service_map = {s.config.name: s for s in services}
        visited = set()
        result = []
        
        def visit(name: str):
            if name in visited:
                return
            visited.add(name)
            service = service_map.get(name)
            if service:
                for dep in service.config.dependencies:
                    if dep in service_map:
                        visit(dep)
            result.append(service_map[name])
        
        for service in services:
            visit(service.config.name)
        
        return result


# High-level manager
class MicroserviceManager:
    """High-level manager for microservice applications."""
    
    def __init__(self, registry: Optional[ServiceRegistry] = None):
        self.registry = registry or ServiceRegistry()
        self._running = False
    
    async def add_service(self, service: ServiceBase) -> bool:
        return self.registry.register(service)
    
    async def remove_service(self, name: str) -> bool:
        return self.registry.unregister(name)
    
    async def start_all(self) -> Dict[str, bool]:
        return await ServiceFactory.start_all(
            list(self.registry._services.values()), self.registry
        )
    
    async def stop_all(self) -> None:
        await self.registry.stop_all()
    
    async def health_check(self) -> Dict[str, ServiceHealth]:
        return await self.registry.health_check_all()
    
    def get_service(self, name: str) -> Optional[ServiceBase]:
        return self.registry.get_service(name)
    
    def list_services(self) -> List[str]:
        return self.registry.get_service_names()
    
    async def call(self, target: str, action: str, payload: Any) -> Any:
        return await self.registry.send_message(target, action, payload)
    
    def get_topology(self) -> Dict:
        return self.registry.get_topology()


# Example usage
async def main():
    # Create manager
    manager = MicroserviceManager()
    
    # Add standard services
    for service in ServiceFactory.create_standard_services():
        await manager.add_service(service)
    
    # Start all
    results = await manager.start_all()
    print(f"Started services: {results}")
    
    # Check health
    health = await manager.health_check()
    print(f"Health: {health}")
    
    # Get topology
    topology = manager.get_topology()
    print(f"Topology: {json.dumps(topology, indent=2)}")
    
    # Make a call
    result = await manager.call("auth-service", "validate_token", {"token": "test"})
    print(f"Auth result: {result}")
    
    # Stop all
    await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())