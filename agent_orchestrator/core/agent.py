"""
Base Agent Classes
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum
import uuid
import asyncio
from datetime import datetime


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


class AgentCapability(Enum):
    WEB_SEARCH = "web_search"
    BROWSER_AUTOMATION = "browser_automation"
    FILE_OPERATIONS = "file_operations"
    CODE_EXECUTION = "code_execution"
    API_CALLS = "api_calls"
    MOUSE_CONTROL = "mouse_control"
    KEYBOARD_CONTROL = "keyboard_control"
    SCREEN_CAPTURE = "screen_capture"
    ACADEMIC_SEARCH = "academic_search"
    GITHUB_OPS = "github_ops"


@dataclass
class AgentConfig:
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    capabilities: List[AgentCapability] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    permissions: Dict[str, bool] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    agent_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    def __init__(self, config: AgentConfig):
        self.config = config
        self.status = AgentStatus.IDLE
        self.current_task: Optional[Task] = None
        self.task_history: List[Task] = []
        self._lock = asyncio.Lock()

    @abstractmethod
    async def execute(self, task: Task) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def validate_task(self, task: Task) -> bool:
        pass

    async def run_task(self, task: Task) -> Task:
        async with self._lock:
            if self.status == AgentStatus.RUNNING:
                raise RuntimeError("Agent is already running a task")

            self.status = AgentStatus.RUNNING
            self.current_task = task
            task.status = "running"
            task.started_at = datetime.now()

        try:
            if not await self.validate_task(task):
                raise ValueError("Task validation failed")

            result = await self.execute(task)
            task.result = result
            task.status = "completed"
            task.completed_at = datetime.now()

        except Exception as e:
            task.status = "error"
            task.error = str(e)
            task.completed_at = datetime.now()
            self.status = AgentStatus.ERROR
            raise

        finally:
            async with self._lock:
                self.task_history.append(task)
                self.current_task = None
                self.status = AgentStatus.IDLE

        return task

    def get_status(self) -> Dict[str, Any]:
        return {
            "agent_id": self.config.agent_id,
            "name": self.config.name,
            "status": self.status.value,
            "capabilities": [c.value for c in self.config.capabilities],
            "current_task": self.current_task.task_id if self.current_task else None,
            "tasks_completed": len([t for t in self.task_history if t.status == "completed"]),
            "tasks_failed": len([t for t in self.task_history if t.status == "error"]),
        }

    def has_capability(self, capability: AgentCapability) -> bool:
        return capability in self.config.capabilities

    def has_permission(self, permission: str) -> bool:
        return self.config.permissions.get(permission, False)