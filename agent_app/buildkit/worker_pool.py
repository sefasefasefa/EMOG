"""Worker Pool for concurrent ItemExecutor execution."""
import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


@dataclass
class WorkerTask:
    """Task assigned to a worker."""
    item: 'PlanItem'
    plan: 'BuildPlan'
    round_index: int = 0
    future: asyncio.Future = field(default_factory=asyncio.Future)
    worker_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class Worker:
    """Single worker running ItemExecutor tasks."""
    
    def __init__(self, worker_id: int, pool: 'WorkerPool', run_logger, blueprint: dict, 
                 dossier: dict = None, benchmark: dict = None, model: str = None):
        self.id = worker_id
        self.pool = pool
        self.run_logger = run_logger
        self.blueprint = blueprint
        self.dossier = dossier
        self.benchmark = benchmark
        self.model = model
        self.current_task: Optional[WorkerTask] = None
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Worker {self.id} started")
    
    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Worker {self.id} stopped")
    
    async def _run_loop(self):
        while self.running:
            try:
                task = await self.pool.task_queue.get()
                if task is None:  # Shutdown signal
                    break
                await self._execute_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Worker {self.id} loop error: {e}")
                await asyncio.sleep(1)
    
    async def _execute_task(self, task: WorkerTask):
        self.current_task = task
        task.worker_id = self.id
        task.started_at = datetime.utcnow()
        
        self.run_logger.status(
            f"Worker {self.id}: {task.item.title} başlıyor",
            stage=f"worker_{self.id}",
            progress={"worker": self.id, "task": task.item.key}
        )
        
        try:
            # Import here to avoid circular imports
            from .executor import ItemExecutor
            
            executor = ItemExecutor(
                run_logger=self.run_logger,
                blueprint=self.blueprint,
                dossier=self.dossier,
                benchmark=self.benchmark,
                model=self.model
            )

            from agent_app.project_scanner import (
                clear_project_context,
                get_project_guid,
                get_user_guid,
                set_project_context,
            )

            project_guid = get_project_guid()
            user_guid = get_user_guid()

            def _run_item():
                if project_guid:
                    set_project_context(project_guid, user_guid)
                try:
                    return executor.run(task.item, task.plan, task.round_index)
                finally:
                    clear_project_context()

            timeout = float(_cfg("item_timeout_seconds", 900) or 900)
            loop = asyncio.get_running_loop()
            try:
                outcome = await asyncio.wait_for(
                    loop.run_in_executor(None, _run_item),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Plan kalemi zaman asimina ugradi ({timeout:.0f}s): {task.item.title}"
                )
            
            task.completed_at = datetime.utcnow()
            if not task.future.done():
                task.future.set_result(outcome)
            
            status = "✓ tamamlandı" if outcome.get("ok") else "✗ başarısız"
            self.run_logger.status(
                f"Worker {self.id}: {task.item.title} {status}",
                stage=f"worker_{self.id}",
                progress={"worker": self.id, "task": task.item.key, "status": "done" if outcome.get("ok") else "failed"}
            )
            
        except Exception as e:
            logger.exception(f"Worker {self.id} task error: {e}")
            task.completed_at = datetime.utcnow()
            if not task.future.done():
                task.future.set_exception(e)
            self.run_logger.status(
                f"Worker {self.id}: {task.item.title} hata: {e}",
                stage=f"worker_{self.id}",
                progress={"worker": self.id, "task": task.item.key, "status": "error"}
            )
        finally:
            self.current_task = None
            self.pool.task_queue.task_done()


class WorkerPool:
    """Manages a pool of concurrent workers."""
    
    def __init__(self, max_workers: int = 3, run_logger=None, 
                 blueprint: dict = None, dossier: dict = None, 
                 benchmark: dict = None, model: str = None):
        self.max_workers = max_workers
        self.run_logger = run_logger
        self.blueprint = blueprint
        self.dossier = dossier
        self.benchmark = benchmark
        self.model = model
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.workers: List[Worker] = []
        self.running = False
        self._started = False
    
    async def start(self):
        if self._started:
            return
        self.running = True
        self._started = True
        
        for i in range(self.max_workers):
            worker = Worker(
                worker_id=i,
                pool=self,
                run_logger=self.run_logger,
                blueprint=self.blueprint,
                dossier=self.dossier,
                benchmark=self.benchmark,
                model=self.model
            )
            await worker.start()
            self.workers.append(worker)
        
        self.run_logger.status(f"WorkerPool başlatıldı: {self.max_workers} worker")
        logger.info(f"WorkerPool started with {self.max_workers} workers")
    
    async def stop(self):
        self.running = False
        # Send shutdown signals
        for _ in self.workers:
            await self.task_queue.put(None)
        for worker in self.workers:
            await worker.stop()
        self.workers.clear()
        self._started = False
        self.run_logger.status("WorkerPool durduruldu")
    
    async def submit(self, item: 'PlanItem', plan: 'BuildPlan', round_index: int = 0) -> Any:
        """Submit a task and return a future that resolves with the outcome."""
        if not self._started:
            await self.start()
        
        task = WorkerTask(item=item, plan=plan, round_index=round_index)
        await self.task_queue.put(task)
        return await task.future
    
    async def submit_batch(self, items: List['PlanItem'], plan: 'BuildPlan', round_index: int = 0) -> List[Any]:
        """Submit multiple tasks concurrently."""
        futures = [self.submit(item, plan, round_index) for item in items]
        return await asyncio.gather(*futures, return_exceptions=True)
    
    def get_stats(self) -> dict:
        return {
            "max_workers": self.max_workers,
            "active_workers": sum(1 for w in self.workers if w.current_task is not None),
            "queue_size": self.task_queue.qsize(),
            "workers": [
                {
                    "id": w.id,
                    "current_task": w.current_task.item.key if w.current_task else None,
                    "running": w.running
                }
                for w in self.workers
            ]
        }