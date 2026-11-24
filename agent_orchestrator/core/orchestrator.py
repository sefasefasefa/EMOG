"""
Agent Orchestrator - Manages multiple agents and task distribution
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import asyncio
import uuid
from datetime import datetime
from enum import Enum
import logging

from .agent import BaseAgent, AgentConfig, Task, AgentCapability, AgentStatus

logger = logging.getLogger(__name__)


class TaskPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class QueuedTask:
    task: Task
    priority: TaskPriority = TaskPriority.NORMAL
    scheduled_at: datetime = field(default_factory=datetime.now)
    retries: int = 0
    max_retries: int = 3


class AgentOrchestrator:
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.running_tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, Task] = {}
        self.failed_tasks: Dict[str, Task] = {}
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    def register_agent(self, agent: BaseAgent) -> None:
        self.agents[agent.config.agent_id] = agent
        logger.info(f"Registered agent: {agent.config.name} ({agent.config.agent_id})")

    def unregister_agent(self, agent_id: str) -> bool:
        if agent_id in self.agents:
            del self.agents[agent_id]
            logger.info(f"Unregistered agent: {agent_id}")
            return True
        return False

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        return self.agents.get(agent_id)

    def list_agents(self) -> List[Dict[str, Any]]:
        return [agent.get_status() for agent in self.agents.values()]

    def find_agents_by_capability(self, capability: AgentCapability) -> List[BaseAgent]:
        return [agent for agent in self.agents.values() if agent.has_capability(capability)]

    def find_best_agent(self, task: Task) -> Optional[BaseAgent]:
        required_caps = task.payload.get("required_capabilities", [])
        
        # If no required capabilities specified, try to infer from task type
        if not required_caps:
            task_type = task.payload.get("type", "")
            if task_type in ("deep_research", "web_search", "academic_search", "github_search", "api_call", "summarize"):
                required_caps = [AgentCapability.WEB_SEARCH, AgentCapability.ACADEMIC_SEARCH, AgentCapability.GITHUB_OPS, AgentCapability.API_CALLS]
            elif task_type in ("automation", "mouse_move", "mouse_click", "keyboard_type", "screenshot", "browser_open", "browser_navigate", "browser_click", "browser_type"):
                required_caps = [AgentCapability.MOUSE_CONTROL, AgentCapability.KEYBOARD_CONTROL, AgentCapability.SCREEN_CAPTURE, AgentCapability.BROWSER_AUTOMATION, AgentCapability.FILE_OPERATIONS]
        
        if not required_caps:
            # Fallback: return any idle agent
            candidates = [agent for agent in self.agents.values() if agent.status == AgentStatus.IDLE]
            if not candidates:
                return None
            return min(candidates, key=lambda a: len(a.task_history))

        candidates = []
        for agent in self.agents.values():
            if agent.status != AgentStatus.IDLE:
                continue
            if all(agent.has_capability(cap) for cap in required_caps):
                candidates.append(agent)

        if not candidates:
            return None

        return min(candidates, key=lambda a: len(a.task_history))

    async def submit_task(self, task: Task, priority: TaskPriority = TaskPriority.NORMAL) -> str:
        queued_task = QueuedTask(task=task, priority=priority)
        await self.task_queue.put((-priority.value, queued_task))
        logger.info(f"Task queued: {task.task_id} ({task.name})")
        return task.task_id

    async def submit_task_to_agent(self, task: Task, agent_id: str) -> Task:
        agent = self.agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        task.agent_id = agent_id
        self.running_tasks[task.task_id] = task
        result_task = await agent.run_task(task)

        if result_task.status == "completed":
            self.completed_tasks[task.task_id] = result_task
        else:
            self.failed_tasks[task.task_id] = result_task

        del self.running_tasks[task.task_id]
        return result_task

    async def start_worker(self) -> None:
        if self._running:
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Orchestrator worker started")

    async def stop_worker(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        # Close all agents
        for agent in self.agents.values():
            if hasattr(agent, 'close'):
                await agent.close()
        
        logger.info("Orchestrator worker stopped")

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                _, queued_task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                task = queued_task.task

                if task.task_id in self.running_tasks:
                    continue

                agent = self.find_best_agent(task)
                if not agent:
                    if queued_task.retries < queued_task.max_retries:
                        queued_task.retries += 1
                        await asyncio.sleep(5 * queued_task.retries)
                        await self.task_queue.put((-queued_task.priority.value, queued_task))
                    else:
                        task.status = "failed"
                        task.error = "No suitable agent found"
                        self.failed_tasks[task.task_id] = task
                        logger.error(f"Task failed: {task.task_id} - No suitable agent")
                    continue

                task.agent_id = agent.config.agent_id
                self.running_tasks[task.task_id] = task

                asyncio.create_task(self._execute_task(task, agent))

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)

    async def _execute_task(self, task: Task, agent: BaseAgent) -> None:
        try:
            result_task = await agent.run_task(task)
            if result_task.status == "completed":
                self.completed_tasks[task.task_id] = result_task
            else:
                self.failed_tasks[task.task_id] = result_task
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            self.failed_tasks[task.task_id] = task
            logger.error(f"Task execution failed: {task.task_id} - {e}")
        finally:
            self.running_tasks.pop(task.task_id, None)

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        for task_dict in [self.running_tasks, self.completed_tasks, self.failed_tasks]:
            if task_id in task_dict:
                task = task_dict[task_id]
                return {
                    "task_id": task.task_id,
                    "name": task.name,
                    "status": task.status,
                    "agent_id": task.agent_id,
                    "result": task.result,
                    "error": task.error,
                    "created_at": task.created_at.isoformat(),
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                }
        return None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "agents_registered": len(self.agents),
            "agents_idle": len([a for a in self.agents.values() if a.status == AgentStatus.IDLE]),
            "agents_running": len([a for a in self.agents.values() if a.status == AgentStatus.RUNNING]),
            "queue_size": self.task_queue.qsize(),
            "running_tasks": len(self.running_tasks),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
        }