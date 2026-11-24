"""Agent Registry & Message Bus for inter-agent communication."""
import asyncio
import logging
import uuid
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    STATUS = "status"
    ERROR = "error"
    PLAN_UPDATE = "plan_update"
    AGENT_REGISTER = "agent_register"
    AGENT_UNREGISTER = "agent_unregister"
    BROADCAST = "broadcast"


@dataclass
class AgentMessage:
    type: MessageType
    from_agent: str
    to_agent: str  # "broadcast" for all
    payload: Dict[str, Any]
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'AgentMessage':
        return cls(
            type=MessageType(data["type"]),
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            payload=data["payload"],
            correlation_id=data.get("correlation_id", uuid.uuid4().hex[:8]),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z")
        )


@dataclass
class AgentInfo:
    agent_id: str
    agent_type: str  # "orchestrator", "worker", "planning", "custom"
    capabilities: List[str] = field(default_factory=list)
    status: str = "idle"  # idle, busy, error, offline
    metadata: Dict = field(default_factory=dict)
    registered_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    last_heartbeat: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class AgentRegistry:
    """Central registry and message bus for agents."""
    
    def __init__(self):
        self.agents: Dict[str, AgentInfo] = {}
        self.message_bus: asyncio.Queue = asyncio.Queue()
        self.subscribers: Dict[str, List[Callable[[AgentMessage], Awaitable[None]]]] = {}
        self._running = False
        self._dispatch_task: Optional[asyncio.Task] = None
    
    async def start(self):
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("AgentRegistry started")
    
    async def stop(self):
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        logger.info("AgentRegistry stopped")
    
    def register(self, agent_id: str, agent_type: str, capabilities: List[str] = None, metadata: Dict = None) -> AgentInfo:
        """Register a new agent."""
        info = AgentInfo(
            agent_id=agent_id,
            agent_type=agent_type,
            capabilities=capabilities or [],
            metadata=metadata or {}
        )
        self.agents[agent_id] = info
        logger.info(f"Agent registered: {agent_id} ({agent_type})")
        return info
    
    def unregister(self, agent_id: str):
        """Unregister an agent."""
        if agent_id in self.agents:
            del self.agents[agent_id]
            logger.info(f"Agent unregistered: {agent_id}")
    
    def update_status(self, agent_id: str, status: str, metadata: Dict = None):
        """Update agent status."""
        if agent_id in self.agents:
            self.agents[agent_id].status = status
            self.agents[agent_id].last_heartbeat = datetime.utcnow().isoformat() + "Z"
            if metadata:
                self.agents[agent_id].metadata.update(metadata)
    
    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        return self.agents.get(agent_id)
    
    def list_agents(self, agent_type: str = None) -> List[AgentInfo]:
        agents = list(self.agents.values())
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]
        return agents
    
    def subscribe(self, agent_id: str, handler: Callable[[AgentMessage], Awaitable[None]]):
        """Subscribe to messages for this agent."""
        if agent_id not in self.subscribers:
            self.subscribers[agent_id] = []
        self.subscribers[agent_id].append(handler)
    
    def unsubscribe(self, agent_id: str, handler: Callable):
        if agent_id in self.subscribers:
            self.subscribers[agent_id] = [h for h in self.subscribers[agent_id] if h != handler]
    
    async def send(self, message: AgentMessage):
        """Send message to specific agent or broadcast."""
        await self.message_bus.put(message)
    
    async def broadcast(self, message: AgentMessage):
        """Broadcast to all agents."""
        message.to_agent = "broadcast"
        await self.message_bus.put(message)
    
    async def request_response(self, message: AgentMessage, timeout: float = 30.0) -> Optional[AgentMessage]:
        """Send request and wait for response with same correlation_id."""
        future = asyncio.get_event_loop().create_future()
        correlation_id = message.correlation_id
        
        # Register temporary response handler
        async def response_handler(msg: AgentMessage):
            if msg.correlation_id == correlation_id and not future.done():
                future.set_result(msg)
        
        self.subscribe(message.to_agent, response_handler)
        await self.send(message)
        
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.unsubscribe(message.to_agent, response_handler)
    
    async def _dispatch_loop(self):
        while self._running:
            try:
                message = await self.message_bus.get()
                await self._dispatch(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Message dispatch error: {e}")
    
    async def _dispatch(self, message: AgentMessage):
        """Dispatch message to subscribers."""
        targets = []
        
        if message.to_agent == "broadcast":
            # Send to all subscribers except sender
            for agent_id, handlers in self.subscribers.items():
                if agent_id != message.from_agent:
                    targets.extend([(agent_id, h) for h in handlers])
        else:
            # Direct message
            handlers = self.subscribers.get(message.to_agent, [])
            targets.extend([(message.to_agent, h) for h in handlers])
        
        for agent_id, handler in targets:
            try:
                await handler(message)
            except Exception as e:
                logger.exception(f"Handler error for {agent_id}: {e}")


class BaseAgent:
    """Base class for all agents."""
    
    def __init__(self, agent_id: str, agent_type: str, registry: AgentRegistry, 
                 capabilities: List[str] = None, metadata: Dict = None):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.registry = registry
        self.capabilities = capabilities or []
        self.metadata = metadata or {}
        self.running = False
        
        # Register self
        registry.register(agent_id, agent_type, capabilities, metadata)
        registry.subscribe(agent_id, self._handle_message)
    
    async def start(self):
        self.running = True
        self.registry.update_status(self.agent_id, "idle")
        logger.info(f"Agent {self.agent_id} started")
    
    async def stop(self):
        self.running = False
        self.registry.update_status(self.agent_id, "offline")
        self.registry.unregister(self.agent_id)
        logger.info(f"Agent {self.agent_id} stopped")
    
    async def _handle_message(self, message: AgentMessage):
        """Override in subclass."""
        pass
    
    async def send(self, to_agent: str, msg_type: MessageType, payload: Dict):
        """Send message to another agent."""
        message = AgentMessage(
            type=msg_type,
            from_agent=self.agent_id,
            to_agent=to_agent,
            payload=payload
        )
        await self.registry.send(message)
    
    async def broadcast(self, msg_type: MessageType, payload: Dict):
        """Broadcast to all agents."""
        message = AgentMessage(
            type=msg_type,
            from_agent=self.agent_id,
            to_agent="broadcast",
            payload=payload
        )
        await self.registry.broadcast(message)
    
    async def request(self, to_agent: str, msg_type: MessageType, payload: Dict, timeout: float = 30.0) -> Optional[AgentMessage]:
        """Send request and wait for response."""
        message = AgentMessage(
            type=msg_type,
            from_agent=self.agent_id,
            to_agent=to_agent,
            payload=payload
        )
        return await self.registry.request_response(message, timeout)
    
    def update_status(self, status: str, metadata: Dict = None):
        self.registry.update_status(self.agent_id, status, metadata)


# Global registry instance
_global_registry: Optional[AgentRegistry] = None

def get_registry() -> AgentRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry

async def init_registry() -> AgentRegistry:
    global _global_registry
    _global_registry = AgentRegistry()
    await _global_registry.start()
    return _global_registry

async def shutdown_registry():
    global _global_registry
    if _global_registry:
        await _global_registry.stop()
        _global_registry = None