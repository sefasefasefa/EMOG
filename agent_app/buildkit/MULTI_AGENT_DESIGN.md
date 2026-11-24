# EMOG Multi-Agent System Architecture

## Overview
Extend existing EMOG pipeline with autonomous multi-agent orchestration:
- **Orchestrator**: Decomposes goals → plans → schedules parallel workers
- **Worker Pool**: Concurrent ItemExecutor instances with resource limits
- **Planning Agent**: Generates detailed plans from high-level requirements
- **Agent Registry**: Message passing, lifecycle management
- **UI Integration**: Real-time agent activity in chat bubbles

---

## 1. Orchestrator Agent (`orchestrator.py`)

### Responsibilities
- Receive high-level goal from user
- Decompose into sub-tasks with dependencies
- Determine parallelism (which tasks can run concurrently)
- Schedule workers via WorkerPool
- Monitor progress, handle failures, re-plan if needed
- Emit `stage` events for UI tracker

### Interface
```python
class Orchestrator:
    def __init__(self, run_logger, worker_pool, planning_agent, max_workers=3):
        ...
    
    async def run(self, goal: str, context: Dict) -> Dict:
        # 1. PlanningAgent.generate_plan(goal, context)
        # 2. Build dependency graph
        # 3. WorkerPool.schedule(tasks)
        # 4. Monitor + re-plan loop
        # 5. Return final result
```

### Parallelism Strategy
- Group tasks by `phase` (research, blueprint, building, reviewing)
- Within phase: tasks with no mutual dependencies → parallel
- Max concurrent workers = `max_workers` (default 3)

---

## 2. Worker Pool (`worker_pool.py`)

### Responsibilities
- Manage N concurrent ItemExecutor instances
- Queue tasks, dispatch to free workers
- Resource limits: CPU, memory, API rate limits
- Worker lifecycle: spawn, monitor, recycle on error
- Emit per-worker status events

### Interface
```python
class WorkerPool:
    def __init__(self, max_workers=3, run_logger=None):
        self.max_workers = max_workers
        self.workers = []  # List[Worker]
        self.task_queue = asyncio.Queue()
        self.run_logger = run_logger
    
    async def start(self):
        for i in range(self.max_workers):
            w = Worker(id=i, pool=self)
            await w.start()
            self.workers.append(w)
    
    async def submit(self, task: PlanItem, plan: BuildPlan) -> asyncio.Future:
        # Returns future that resolves when task done
        ...
    
    async def shutdown(self):
        for w in self.workers:
            await w.stop()
```

### Worker
```python
class Worker:
    def __init__(self, id: int, pool: WorkerPool):
        self.id = id
        self.pool = pool
        self.current_task = None
        self.executor = ItemExecutor(...)
        self.running = False
    
    async def run_task(self, item: PlanItem, plan: BuildPlan):
        self.current_task = item
        self.pool.run_logger.stage(f"worker_{self.id}", f"Worker {self.id}: {item.title}")
        result = await self.executor.run(item, plan)
        self.current_task = None
        return result
```

---

## 3. Planning Agent (`planning_agent.py`)

### Responsibilities
- Input: high-level goal + project context (files, blueprint, dossier)
- Output: BuildPlan with phases, items, dependencies, acceptance criteria
- Uses LLM with structured output (JSON schema)
- Iterative refinement: generate → critique → improve

### Interface
```python
class PlanningAgent:
    def __init__(self, model: str = "coding"):
        self.model = model
    
    async def generate_plan(self, goal: str, context: Dict) -> BuildPlan:
        # 1. Research (optional): web search, codebase analysis
        # 2. Draft plan with phases
        # 3. Critique & refine
        # 4. Return BuildPlan
        ...
    
    async def refine_plan(self, plan: BuildPlan, feedback: str) -> BuildPlan:
        ...
```

### Plan Structure (extends existing BuildPlan)
```json
{
  "phases": [
    {
      "id": "research",
      "label": "Araştırma",
      "parallel": false,
      "items": [...]
    },
    {
      "id": "building", 
      "label": "Uygulama",
      "parallel": true,
      "items": [
        {"key": "frontend", "depends_on": [], "parallel_group": "ui"},
        {"key": "backend", "depends_on": [], "parallel_group": "api"}
      ]
    }
  ]
}
```

---

## 4. Agent Registry & Communication (`agent_registry.py`)

### Message Types
```python
class AgentMessage:
    type: str  # "task_request", "task_result", "status", "error", "plan_update"
    from_agent: str
    to_agent: str  # or "broadcast"
    payload: Dict
    correlation_id: str
```

### Registry
```python
class AgentRegistry:
    def __init__(self):
        self.agents = {}  # agent_id -> AgentInfo
        self.message_bus = asyncio.Queue()
    
    def register(self, agent_id: str, agent_type: str, capabilities: List[str]):
        ...
    
    async def send(self, msg: AgentMessage):
        ...
    
    async def broadcast(self, msg: AgentMessage):
        ...
```

---

## 5. UI Integration - Agent Activity Bubbles

### New Bubble Types in Chat
- **Orchestrator Bubble**: Shows current high-level phase, % complete
- **Worker Bubbles** (expandable): Each worker shows current task, tool calls
- **Planning Agent Bubble**: Shows plan generation progress

### Frontend Tracker Enhancement
- Agent filter: show/hide orchestrator, workers, planning
- Per-agent progress rings
- Click worker → see its tool call timeline

### SSE Events to Add
```json
{"type": "agent_status", "agent": "orchestrator", "status": "planning", "progress": 30}
{"type": "agent_status", "agent": "worker_1", "status": "running", "task": "frontend", "tool": "write_file"}
{"type": "agent_status", "agent": "worker_2", "status": "idle"}
```

---

## 6. Error Isolation

### Strategy
- Each Worker runs in own try/catch
- Worker crash → log error, mark task failed, recycle worker
- Orchestrator catches WorkerPool exceptions, re-schedules task
- Planning Agent failures → fallback to default plan template

### Circuit Breaker
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=3, timeout=60):
        self.failures = 0
        self.last_failure = 0
        ...
    
    def call(self, func, *args):
        if self.is_open:
            raise CircuitOpenError()
        try:
            result = func(*args)
            self.reset()
            return result
        except Exception as e:
            self.record_failure()
            raise
```

---

## 7. Implementation Order

| Phase | Tasks | Deliverable |
|-------|-------|-------------|
| 1 | WorkerPool + Worker (refactor ItemExecutor for async) | Parallel task execution |
| 2 | Orchestrator + PlanningAgent | Autonomous goal → plan → execute |
| 3 | AgentRegistry + messaging | Inter-agent communication |
| 4 | UI: Agent bubbles + tracker filters | Real-time visibility |
| 5 | Error isolation + circuit breakers | Production resilience |
| 6 | Integration tests | Verified multi-agent flows |

---

## 8. Files to Create/Modify

### New Files
- `agent_app/buildkit/orchestrator.py`
- `agent_app/buildkit/worker_pool.py`
- `agent_app/buildkit/planning_agent.py`
- `agent_app/buildkit/agent_registry.py`
- `agent_app/buildkit/circuit_breaker.py`

### Modified Files
- `agent_app/buildkit/executor.py` → Make ItemExecutor async-compatible
- `agent_app/buildkit/events.py` → Add agent_status event types
- `agent_app/views.py` → Wire Orchestrator instead of PlanExecutor
- `agent_app/templates/agent_app/chat.html` → Agent bubbles, filters

---

## 9. Configuration

```python
# settings.py
MULTI_AGENT = {
    "max_workers": 3,
    "worker_recycle_on_error": True,
    "planning_model": "nvidia/nemotron-3-ultra-550b-a55b",
    "worker_model": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "orchestrator_model": "nvidia/nemotron-3-ultra-550b-a55b",
    "circuit_breaker_threshold": 3,
    "task_timeout_seconds": 300,
}
```

---

## 10. Backward Compatibility

- Existing `PlanExecutor.run()` still works for simple sequential runs
- New `Orchestrator.run()` used when `intent == "build"` and complexity > threshold
- Feature flag: `MULTI_AGENT_ENABLED = True/False`