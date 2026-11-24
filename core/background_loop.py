"""
Continuous Background Loop System
A persistent background process that runs continuously, executes code towards goals,
self-heals issues, and generates new subtasks autonomously.
"""
import asyncio
import json
import time
import uuid
import threading
import traceback
import logging
from typing import Dict, List, Any, Optional, Callable, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager
import sqlite3
import os


class LoopState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


class GoalStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SELF_HEALING = "self_healing"


class TaskPriority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class Goal:
    id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Any] = None
    subtasks: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3


@dataclass
class Subtask:
    id: str
    goal_id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    code: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retry_count: int = 0


@dataclass
class LoopMetrics:
    loop_id: str
    started_at: float
    goals_completed: int = 0
    goals_failed: int = 0
    subtasks_generated: int = 0
    self_heals_performed: int = 0
    errors_encountered: int = 0
    last_activity: float = field(default_factory=time.time)
    uptime_seconds: float = 0


class BackgroundLoopDB:
    """SQLite-based persistent storage for background loop state."""
    
    def __init__(self, db_path: str = "./background_loop.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    error TEXT,
                    result TEXT,
                    subtasks TEXT,
                    metadata TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subtasks (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    code TEXT,
                    result TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    retry_count INTEGER DEFAULT 0,
                    FOREIGN KEY (goal_id) REFERENCES goals(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS loop_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    loop_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_subtasks_goal ON subtasks(goal_id)
            """)
            conn.commit()
    
    @asynccontextmanager
    async def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    async def save_goal(self, goal: Goal):
        async with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO goals 
                (id, description, status, priority, created_at, updated_at, completed_at, error, result, subtasks, metadata, retry_count, max_retries)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                goal.id, goal.description, goal.status.value, goal.priority.value,
                goal.created_at, goal.updated_at, goal.completed_at,
                goal.error, json.dumps(goal.result) if goal.result else None,
                json.dumps(goal.subtasks), json.dumps(goal.metadata),
                goal.retry_count, goal.max_retries
            ))
    
    async def get_goal(self, goal_id: str) -> Optional[Goal]:
        async with self.get_connection() as conn:
            row = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            )
            if row:
                return Goal(
                    id=row['id'],
                    description=row['description'],
                    status=GoalStatus(row['status']),
                    priority=TaskPriority(row['priority']),
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    completed_at=row['completed_at'],
                    error=row['error'],
                    result=json.loads(row['result']) if row['result'] else None,
                    subtasks=json.loads(row['subtasks']) if row['subtasks'] else [],
                    metadata=json.loads(row['metadata']) if row['metadata'] else {},
                    retry_count=row['retry_count'],
                    max_retries=row['max_retries']
                )
        return None
    
    async def get_goals_by_status(self, status: GoalStatus) -> List[Goal]:
        async with self.get_connection() as conn:
            rows = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.execute("SELECT * FROM goals WHERE status = ? ORDER BY priority DESC, created_at ASC", (status.value,)).fetchall()
            )
            return [self._row_to_goal(row) for row in rows]
    
    def _row_to_goal(self, row) -> Goal:
        return Goal(
            id=row['id'],
            description=row['description'],
            status=GoalStatus(row['status']),
            priority=TaskPriority(row['priority']),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            completed_at=row['completed_at'],
            error=row['error'],
            result=json.loads(row['result']) if row['result'] else None,
            subtasks=json.loads(row['subtasks']) if row['subtasks'] else [],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
            retry_count=row['retry_count'],
            max_retries=row['max_retries']
        )
    
    async def save_subtask(self, subtask: Subtask):
        async with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO subtasks
                (id, goal_id, description, status, code, result, error, created_at, started_at, completed_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                subtask.id, subtask.goal_id, subtask.description, subtask.status.value,
                subtask.code, json.dumps(subtask.result) if subtask.result else None,
                subtask.error, subtask.created_at, subtask.started_at,
                subtask.completed_at, subtask.retry_count
            ))
    
    async def get_subtasks_for_goal(self, goal_id: str) -> List[Subtask]:
        async with self.get_connection() as conn:
            rows = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.execute("SELECT * FROM subtasks WHERE goal_id = ? ORDER BY created_at", (goal_id,)).fetchall()
            )
            return [self._row_to_subtask(row) for row in rows]
    
    def _row_to_subtask(self, row) -> Subtask:
        return Subtask(
            id=row['id'],
            goal_id=row['goal_id'],
            description=row['description'],
            status=GoalStatus(row['status']),
            code=row['code'],
            result=json.loads(row['result']) if row['result'] else None,
            error=row['error'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
            retry_count=row['retry_count']
        )
    
    async def save_loop_state(self, key: str, value: Any):
        async with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO loop_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time())
            )
    
    async def get_loop_state(self, key: str, default: Any = None) -> Any:
        async with self.get_connection() as conn:
            row = await asyncio.get_event_loop().run_in_executor(
                None, lambda: conn.execute("SELECT value FROM loop_state WHERE key = ?", (key,)).fetchone()
            )
            if row:
                return json.loads(row['value'])
        return default
    
    async def record_metric(self, loop_id: str, metric_type: str, value: float):
        async with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO metrics (loop_id, metric_type, value, timestamp) VALUES (?, ?, ?, ?)",
                (loop_id, metric_type, value, time.time())
            )
    
    async def get_metrics(self, loop_id: str, metric_type: str = None, limit: int = 1000) -> List[Dict]:
        async with self.get_connection() as conn:
            if metric_type:
                rows = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: conn.execute(
                        "SELECT * FROM metrics WHERE loop_id = ? AND metric_type = ? ORDER BY timestamp DESC LIMIT ?",
                        (loop_id, metric_type, limit)
                    ).fetchall()
                )
            else:
                rows = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: conn.execute(
                        "SELECT * FROM metrics WHERE loop_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (loop_id, limit)
                    ).fetchall()
                )
            return [dict(row) for row in rows]


class GoalExecutor:
    """Executes code to achieve a goal using the agent's capabilities."""
    
    def __init__(self, agent_runner: Callable = None):
        self.agent_runner = agent_runner
        self.execution_history: List[Dict] = []
    
    async def execute(self, goal: Goal, context: Dict = None) -> Dict:
        """Execute code to achieve the goal."""
        start_time = time.time()
        
        # Build execution context
        exec_context = {
            "goal": goal.description,
            "priority": goal.priority.name,
            "context": context or {},
            "previous_attempts": goal.retry_count,
        }
        
        try:
            if self.agent_runner:
                # Use the provided agent runner
                result = await self.agent_runner(goal.description, exec_context)
            else:
                # Default: use the project scanner tools to execute
                result = await self._execute_with_tools(goal.description, exec_context)
            
            execution_time = time.time() - start_time
            
            record = {
                "goal_id": goal.id,
                "timestamp": time.time(),
                "execution_time": execution_time,
                "success": True,
                "result": result,
            }
            self.execution_history.append(record)
            
            return {"success": True, "result": result, "execution_time": execution_time}
            
        except Exception as e:
            execution_time = time.time() - start_time
            error_msg = f"{type(e).__name__}: {str(e)}"
            traceback_str = traceback.format_exc()
            
            record = {
                "goal_id": goal.id,
                "timestamp": time.time(),
                "execution_time": execution_time,
                "success": False,
                "error": error_msg,
                "traceback": traceback_str,
            }
            self.execution_history.append(record)
            
            return {"success": False, "error": error_msg, "traceback": traceback_str, "execution_time": execution_time}
    
    async def _execute_with_tools(self, goal_description: str, context: Dict) -> Dict:
        """Execute using the project scanner tools."""
        from .project_scanner import run_self_healing_loop
        
        # This would integrate with the existing self-healing loop
        # For now, return a placeholder
        return {
            "action": "execute_with_tools",
            "goal": goal_description,
            "context": context,
            "note": "Integrate with project_scanner.run_self_healing_loop"
        }


class SelfHealingLoop:
    """Automatically detects and fixes issues during goal execution."""
    
    def __init__(self, db: BackgroundLoopDB, executor: GoalExecutor):
        self.db = db
        self.executor = executor
        self.heal_attempts: Dict[str, int] = defaultdict(int)
        self.max_heal_attempts = 3
    
    async def attempt_heal(self, goal: Goal, error: str, traceback_str: str) -> bool:
        """Attempt to self-heal the error and retry."""
        goal_id = goal.id
        self.heal_attempts[goal_id] += 1
        
        if self.heal_attempts[goal_id] > self.max_heal_attempts:
            logging.warning(f"Max heal attempts reached for goal {goal_id}")
            return False
        
        logging.info(f"Attempting self-heal for goal {goal_id} (attempt {self.heal_attempts[goal_id]})")
        
        # Update goal status
        goal.status = GoalStatus.SELF_HEALING
        goal.updated_at = time.time()
        await self.db.save_goal(goal)
        
        # Analyze error and generate fix
        fix_strategy = await self._analyze_error(error, traceback_str)
        
        if not fix_strategy:
            return False
        
        # Apply fix and retry
        try:
            # Apply the fix strategy
            await self._apply_fix(fix_strategy)
            
            # Retry the goal
            goal.retry_count += 1
            goal.status = GoalStatus.IN_PROGRESS
            goal.updated_at = time.time()
            await self.db.save_goal(goal)
            
            # Re-execute
            result = await self.executor.execute(goal)
            
            if result.get("success"):
                logging.info(f"Self-heal successful for goal {goal.id}")
                await self.db.record_metric(goal.id, "self_heal_success", 1)
                return True
            else:
                return await self.attempt_heal(goal, result.get("error", ""), result.get("traceback", ""))
                
        except Exception as e:
            logging.error(f"Self-heal failed for goal {goal.id}: {e}")
            return False
    
    async def _analyze_error(self, error: str, traceback_str: str) -> Optional[Dict]:
        """Analyze error and determine fix strategy."""
        error_lower = error.lower()
        
        strategies = {
            "modulenotfounderror": {
                "type": "install_dependency",
                "action": "extract_missing_module_and_install"
            },
            "importerror": {
                "type": "fix_import",
                "action": "fix_import_path_or_install"
            },
            "syntaxerror": {
                "type": "fix_syntax",
                "action": "fix_syntax_error"
            },
            "attributeerror": {
                "type": "fix_attribute",
                "action": "add_missing_attribute_or_fix_typo"
            },
            "keyerror": {
                "type": "fix_key_access",
                "action": "use_get_method_or_add_default"
            },
            "indexerror": {
                "type": "fix_bounds",
                "action": "add_bounds_check"
            },
            "typeerror": {
                "type": "fix_type",
                "action": "add_type_check_or_conversion"
            },
        }
        
        for error_type, strategy in strategies.items():
            if error_type in error.lower():
                return {
                    "error_type": error_type,
                    "strategy": strategy,
                    "original_error": error,
                    "traceback": traceback_str
                }
        
        # Default strategy for unknown errors
        return {
            "error_type": "unknown",
            "strategy": {"type": "retry_with_context", "action": "retry_with_more_context"},
            "original_error": error,
            "traceback": traceback_str
        }
    
    async def _apply_fix(self, fix_strategy: Dict) -> bool:
        """Apply the fix strategy."""
        strategy = fix_strategy.get("strategy", {})
        action = strategy.get("action")
        
        logging.info(f"Applying fix strategy: {action}")
        
        # This would integrate with the self-healing code system
        # For now, return True to indicate fix was attempted
        return True


class SubtaskGenerator:
    """Generates subtasks from high-level goals."""
    
    def __init__(self, llm_client: Callable = None):
        self.llm_client = llm_client
    
    async def generate_subtasks(self, goal: Goal, context: Dict = None) -> List[Subtask]:
        """Generate subtasks for a goal."""
        if self.llm_client:
            return await self._generate_with_llm(goal, context)
        else:
            return await self._generate_heuristic(goal, context)
    
    async def _generate_with_llm(self, goal: Goal, context: Dict) -> List[Subtask]:
        """Generate subtasks using LLM."""
        prompt = f"""
        Break down this goal into specific, actionable subtasks:
        Goal: {goal.description}
        Priority: {goal.priority.name}
        Context: {json.dumps(context or {})}
        
        Return a JSON array of subtasks with:
        - description: specific actionable step
        - estimated_complexity: low/medium/high
        - dependencies: list of subtask IDs this depends on
        """
        
        try:
            response = await self.llm_client(prompt)
            subtasks_data = json.loads(response)
            
            subtasks = []
            for i, st_data in enumerate(subtasks_data):
                subtask = Subtask(
                    id=f"{goal.id}_sub_{i}",
                    goal_id=goal.id,
                    description=st_data.get("description", ""),
                    status=GoalStatus.PENDING
                )
                subtasks.append(subtask)
            
            return subtasks
        except Exception as e:
            logging.error(f"LLM subtask generation failed: {e}")
            return await self._generate_heuristic(goal, context)
    
    async def _generate_heuristic(self, goal: Goal, context: Dict) -> List[Subtask]:
        """Generate subtasks using heuristic rules."""
        desc = goal.description.lower()
        subtasks = []
        
        # Common patterns
        if any(kw in desc for kw in ["api", "rest", "endpoint"]):
            subtasks.extend([
                Subtask(id=f"{goal.id}_sub_1", goal_id=goal.id, description="Design API endpoints and data models"),
                Subtask(id=f"{goal.id}_sub_2", goal_id=goal.id, description="Implement API routes and handlers"),
                Subtask(id=f"{goal.id}_sub_3", goal_id=goal.id, description="Add input validation and error handling"),
                Subtask(id=f"{goal.id}_sub_4", goal_id=goal.id, description="Write tests for API endpoints"),
            ])
        elif any(kw in desc for kw in ["database", "db", "model", "schema"]):
            subtasks.extend([
                Subtask(id=f"{goal.id}_sub_1", goal_id=goal.id, description="Design database schema"),
                Subtask(id=f"{goal.id}_sub_2", goal_id=goal.id, description="Create migration scripts"),
                Subtask(id=f"{goal.id}_sub_3", goal_id=goal.id, description="Implement data access layer"),
            ])
        elif any(kw in desc for kw in ["ui", "frontend", "component", "page"]):
            subtasks.extend([
                Subtask(id=f"{goal.id}_sub_1", goal_id=goal.id, description="Create component structure and layout"),
                Subtask(id=f"{goal.id}_sub_2", goal_id=goal.id, description="Implement component logic and state"),
                Subtask(id=f"{goal.id}_sub_3", goal_id=goal.id, description="Add styling and responsive design"),
            ])
        elif any(kw in desc for kw in ["test", "testing"]):
            subtasks.extend([
                Subtask(id=f"{goal.id}_sub_1", goal_id=goal.id, description="Write unit tests"),
                Subtask(id=f"{goal.id}_sub_2", goal_id=goal.id, description="Write integration tests"),
                Subtask(id=f"{goal.id}_sub_3", goal_id=goal.id, description="Set up test coverage reporting"),
            ])
        else:
            # Generic breakdown
            subtasks.extend([
                Subtask(id=f"{goal.id}_sub_1", goal_id=goal.id, description=f"Analyze requirements for: {goal.description}"),
                Subtask(id=f"{goal.id}_sub_2", goal_id=goal.id, description="Implement core functionality"),
                Subtask(id=f"{goal.id}_sub_3", goal_id=goal.id, description="Test and validate implementation"),
                Subtask(id=f"{goal.id}_sub_4", goal_id=goal.id, description="Document and cleanup"),
            ])
        
        # Assign IDs
        for i, st in enumerate(subtasks):
            st.id = f"{goal.id}_sub_{i}"
        
        return subtasks


class BackgroundLoopManager:
    """Main manager for the continuous background loop."""
    
    def __init__(
        self,
        db_path: str = "./background_loop.db",
        agent_runner: Callable = None,
        llm_client: Callable = None,
        check_interval: float = 5.0,
        max_concurrent_goals: int = 3,
    ):
        self.db = BackgroundLoopDB(db_path)
        self.executor = GoalExecutor(agent_runner)
        self.healer = SelfHealingLoop(self.db, self.executor)
        self.generator = SubtaskGenerator(llm_client)
        
        self.check_interval = check_interval
        self.max_concurrent_goals = max_concurrent_goals
        
        self.state = LoopState.STOPPED
        self.loop_id = f"loop_{uuid.uuid4().hex[:8]}"
        self.metrics = LoopMetrics(loop_id=self.loop_id, started_at=time.time())
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # Active goals tracking
        self._active_goals: Dict[str, Goal] = {}
        self._subtask_queue: asyncio.Queue = asyncio.Queue()
        
        # Callbacks
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        
        # Setup logging
        self.logger = logging.getLogger(f"background_loop.{self.loop_id}")
        self.logger.setLevel(logging.INFO)
    
    def register_callback(self, event: str, callback: Callable):
        """Register event callback."""
        self._callbacks[event].append(callback)
    
    async def _trigger_callback(self, event: str, *args, **kwargs):
        for callback in self._callbacks[event]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(*args, **kwargs)
                else:
                    callback(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"Callback error for {event}: {e}")
    
    async def start(self) -> bool:
        """Start the background loop."""
        async with self._lock:
            if self.state == LoopState.RUNNING:
                return True
            
            self.state = LoopState.STARTING
            self.logger.info(f"Starting background loop {self.loop_id}")
            
            # Restore state from DB
            await self._restore_state()
            
            self._running = True
            self.state = LoopState.RUNNING
            self.metrics.started_at = time.time()
            
            # Save initial state
            await self.db.save_loop_state("state", self.state.value)
            await self.db.save_loop_state("loop_id", self.loop_id)
            await self.db.save_loop_state("metrics", asdict(self.metrics))
            
            # Start main loop
            self._task = asyncio.create_task(self._main_loop())
            
            await self._trigger_callback("started", self.loop_id)
            self.logger.info(f"Background loop {self.loop_id} started")
            return True
    
    async def stop(self, graceful: bool = True) -> bool:
        """Stop the background loop."""
        async with self._lock:
            if self.state == LoopState.STOPPED:
                return True
            
            self.logger.info(f"Stopping background loop {self.loop_id}")
            self.state = LoopState.STOPPING
            self._running = False
            
            if self._task:
                if graceful:
                    # Wait for current operations to complete
                    await asyncio.wait_for(self._task, timeout=30.0)
                else:
                    self._task.cancel()
                    try:
                        await self._task
                    except asyncio.CancelledError:
                        pass
            
            # Save final state
            await self._save_state()
            await self.db.save_loop_state("state", LoopState.STOPPED.value)
            
            await self._trigger_callback("stopped", self.loop_id)
            self.state = LoopState.STOPPED
            self.logger.info(f"Background loop {self.loop_id} stopped")
            return True
    
    async def pause(self) -> bool:
        """Pause the background loop."""
        if self.state == LoopState.RUNNING:
            self.state = LoopState.PAUSED
            await self.db.save_loop_state("state", self.state.value)
            self.logger.info("Background loop paused")
            return True
        return False
    
    async def resume(self) -> bool:
        """Resume the background loop."""
        if self.state == LoopState.PAUSED:
            self.state = LoopState.RUNNING
            await self.db.save_loop_state("state", self.state.value)
            self.logger.info("Background loop resumed")
            return True
        return False
    
    async def _main_loop(self):
        """Main background loop."""
        self.logger.info("Main loop started")
        
        while self._running:
            try:
                if self.state == LoopState.PAUSED:
                    await asyncio.sleep(self.check_interval)
                    continue
                
                # Update metrics
                self.metrics.uptime_seconds = time.time() - self.metrics.started_at
                self.metrics.last_activity = time.time()
                await self.db.save_loop_state("metrics", asdict(self.metrics))
                
                # Process goals
                await self._process_goals()
                
                # Process subtask queue
                await self._process_subtask_queue()
                
                # Check for new goals from external sources
                await self._check_external_goals()
                
                # Periodic maintenance
                await self._periodic_maintenance()
                
            except Exception as e:
                self.logger.error(f"Main loop error: {e}")
                self.metrics.errors_encountered += 1
                await self.db.record_metric(self.loop_id, "loop_error", 1)
                await self._trigger_callback("error", e)
                
                # Brief pause on error
                await asyncio.sleep(self.check_interval * 2)
            
            await asyncio.sleep(self.check_interval)
        
        self.logger.info("Main loop ended")
    
    async def _process_goals(self):
        """Process active goals."""
        # Get pending goals
        pending_goals = await self.db.get_goals_by_status(GoalStatus.PENDING)
        
        # Start new goals up to concurrency limit
        active_count = len([g for g in self._active_goals.values() if g.status == GoalStatus.IN_PROGRESS])
        
        for goal in pending_goals:
            if len(self._active_goals) >= self.max_concurrent_goals:
                break
            
            if goal.id not in self._active_goals:
                self._active_goals[goal.id] = goal
                asyncio.create_task(self._execute_goal(goal))
    
    async def _execute_goal(self, goal: Goal):
        """Execute a single goal."""
        goal_id = goal.id
        goal.status = GoalStatus.IN_PROGRESS
        goal.updated_at = time.time()
        await self.db.save_goal(goal)
        
        await self.db.record_metric(self.loop_id, "goal_started", 1)
        await self._trigger_callback("goal_started", goal)
        
        try:
            # Generate subtasks if needed
            if not goal.subtasks:
                subtasks = await self.generator.generate_subtasks(goal)
                for subtask in subtasks:
                    await self.db.save_subtask(subtask)
                    goal.subtasks.append(subtask.id)
                    await self._subtask_queue.put(subtask)
                await self.db.save_goal(goal)
            
            # Execute goal
            context = {"loop_id": self.loop_id, "goal_id": goal.id}
            result = await self.executor.execute(goal)
            
            if result.get("success"):
                goal.status = GoalStatus.COMPLETED
                goal.completed_at = time.time()
                goal.result = result.get("result")
                self.metrics.goals_completed += 1
                await self.db.record_metric(self.loop_id, "goal_completed", 1)
                
                # Generate new goals from result
                await self._generate_followup_goals(goal, result.get("result"))
            else:
                # Handle failure with self-healing
                error = result.get("error", "Unknown error")
                traceback_str = result.get("traceback", "")
                
                healed = await self.healer.attempt_heal(goal, result.get("error", ""), result.get("traceback", ""))
                
                if not healed:
                    goal.status = GoalStatus.FAILED
                    goal.error = result.get("error", "Unknown error")
                    self.metrics.goals_failed += 1
                    await self.db.record_metric(self.loop_id, "goal_failed", 1)
            
            goal.updated_at = time.time()
            goal.completed_at = time.time()
            await self.db.save_goal(goal)
            await self.db.record_metric(self.loop_id, "goal_completed_total", 1)
            
            await self._trigger_callback("goal_completed", goal)
            
        except Exception as e:
            self.logger.error(f"Goal execution error for {goal.id}: {e}")
            goal.status = GoalStatus.FAILED
            goal.error = str(e)
            goal.updated_at = time.time()
            await self.db.save_goal(goal)
            self.metrics.goals_failed += 1
            await self.db.record_metric(self.loop_id, "goal_failed", 1)
        finally:
            # Remove from active goals
            self._active_goals.pop(goal_id, None)
    
    async def _process_subtask_queue(self):
        """Process subtasks from the queue."""
        processed = 0
        while not self._subtask_queue.empty() and processed < 10:
            try:
                subtask = await asyncio.wait_for(self._subtask_queue.get(), timeout=0.1)
                await self._execute_subtask(subtask)
                processed += 1
            except asyncio.TimeoutError:
                break
            except Exception as e:
                self.logger.error(f"Subtask processing error: {e}")
    
    async def _execute_subtask(self, subtask: Subtask):
        """Execute a single subtask."""
        subtask.status = GoalStatus.IN_PROGRESS
        subtask.started_at = time.time()
        await self.db.save_subtask(subtask)
        
        self.metrics.subtasks_generated += 1
        await self.db.record_metric(self.loop_id, "subtask_started", 1)
        
        try:
            # Execute subtask code if provided
            if subtask.code and self.executor.agent_runner:
                result = await self.executor.agent_runner(subtask.code)
                subtask.result = result
            
            subtask.status = GoalStatus.COMPLETED
            subtask.completed_at = time.time()
            
        except Exception as e:
            subtask.status = GoalStatus.FAILED
            subtask.error = str(e)
            self.logger.error(f"Subtask {subtask.id} failed: {e}")
        
        subtask.updated_at = time.time()
        await self.db.save_subtask(subtask)
        self.metrics.subtasks_generated += 1
        await self.db.record_metric(self.loop_id, "subtask_completed", 1)
    
    async def _check_external_goals(self):
        """Check for new goals from external sources (API, files, etc.)."""
        # This could poll an API, check a directory, etc.
        pass
    
    async def _periodic_maintenance(self):
        """Periodic maintenance tasks."""
        # Clean up old completed goals
        # Prune old metrics
        # Check disk space
        pass
    
    async def _generate_followup_goals(self, completed_goal: Goal, result: Any):
        """Generate follow-up goals based on completed goal result."""
        # Analyze result and generate follow-up goals
        pass
    
    async def _restore_state(self):
        """Restore loop state from database."""
        # Restore goals
        pending = await self.db.get_goals_by_status(GoalStatus.PENDING)
        in_progress = await self.db.get_goals_by_status(GoalStatus.IN_PROGRESS)
        
        for goal in pending:
            self._active_goals[goal.id] = goal
        
        for goal in in_progress:
            # Reset in-progress goals to pending for restart
            goal.status = GoalStatus.PENDING
            goal.updated_at = time.time()
            await self.db.save_goal(goal)
            self._active_goals[goal.id] = goal
        
        # Restore metrics
        saved_metrics = await self.db.get_loop_state("metrics")
        if saved_metrics:
            for key, value in saved_metrics.items():
                if hasattr(self.metrics, key):
                    setattr(self.metrics, key, value)
        
        self.logger.info(f"Restored state: {len(self._active_goals)} active goals")
    
    async def _save_state(self):
        """Save current state to database."""
        await self.db.save_loop_state("state", self.state.value)
        await self.db.save_loop_state("loop_id", self.loop_id)
        await self.db.save_loop_state("metrics", asdict(self.metrics))
        await self._trigger_callback("state_saved", self.loop_id)
    
    def add_goal(self, description: str, priority: TaskPriority = TaskPriority.NORMAL, metadata: Dict = None) -> str:
        """Add a new goal to the loop."""
        goal = Goal(
            id=f"goal_{uuid.uuid4().hex[:8]}",
            description=description,
            priority=priority,
            metadata=metadata or {},
        )
        
        # Save to DB
        asyncio.create_task(self.db.save_goal(goal))
        
        # Add to active goals
        self._active_goals[goal.id] = goal
        
        self.logger.info(f"Added goal: {goal.id} - {description[:50]}...")
        return goal.id
    
    def get_status(self) -> Dict:
        """Get current loop status."""
        return {
            "loop_id": self.loop_id,
            "state": self.state.value,
            "uptime_seconds": time.time() - self.metrics.started_at if self.metrics.started_at else 0,
            "active_goals": len(self._active_goals),
            "pending_subtasks": self._subtask_queue.qsize(),
            "metrics": asdict(self.metrics),
        }
    
    def get_active_goals(self) -> List[Dict]:
        """Get list of active goals."""
        return [
            {
                "id": g.id,
                "description": g.description,
                "status": g.status.value,
                "priority": g.priority.name,
                "created_at": g.created_at,
                "retry_count": g.retry_count,
            }
            for g in self._active_goals.values()
        ]


# Factory function
async def create_background_loop(
    db_path: str = "./background_loop.db",
    agent_runner: Callable = None,
    llm_client: Callable = None,
    check_interval: float = 5.0,
    max_concurrent_goals: int = 3,
) -> BackgroundLoopManager:
    """Create and initialize a background loop manager."""
    manager = BackgroundLoopManager(
        db_path=db_path,
        agent_runner=agent_runner,
        llm_client=llm_client,
        check_interval=check_interval,
        max_concurrent_goals=max_concurrent_goals,
    )
    await manager.start()
    return manager