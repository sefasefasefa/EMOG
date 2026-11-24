"""Orchestrator: Decomposes goals, plans, schedules parallel workers."""
import asyncio
import logging
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings

from .plan import BuildPlan, PlanItem, ensure_plan_depth
from .worker_pool import WorkerPool
from .events import RunLogger
from agent_app import task_control

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


@dataclass
class TaskNode:
    """Task with dependency info for scheduling."""
    item: PlanItem
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    ready: bool = False
    completed: bool = False
    failed: bool = False


class Orchestrator:
    """Orchestrates multi-agent task execution."""
    
    def __init__(self, run_logger: RunLogger, worker_pool: WorkerPool, 
                 planning_agent=None, max_workers: int = 3):
        self.log = run_logger
        self.worker_pool = worker_pool
        self.planning_agent = planning_agent
        self.max_workers = max_workers
        self.current_plan: Optional[BuildPlan] = None
        self.task_nodes: Dict[str, TaskNode] = {}
        self.running = False
    
    async def run(self, goal: str, context: Dict = None) -> Dict:
        """Execute a high-level goal end-to-end."""
        self.running = True
        context = context or {}
        plan = None
        stats = None

        try:
            # 1. Generate or get plan
            if self.planning_agent:
                self.log.status("Plan olusturuluyor...", stage="planning", icon="plan")
                timeout = float((getattr(settings, "BUILD_PIPELINE", {}) or {}).get(
                    "planning_timeout_seconds", 240) or 240)
                try:
                    plan = await asyncio.wait_for(
                        self.planning_agent.generate_plan(goal, context),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    self.log.warn(f"Planlama zaman asimina ugradi ({timeout:.0f}s); yedek plan kullaniliyor")
                    plan = self.planning_agent._fallback_plan(goal)
            else:
                # Fallback: use existing plan from context
                plan = context.get("plan")
                if not plan:
                    raise ValueError("Plan yok ve PlanningAgent yok")

            plan = self._normalize_plan(plan, goal)
            # Timeout/yedek plan yolunda da spesifikasyon kapsamini ve
            # tasarim derinligini garanti et.
            plan = ensure_plan_depth(plan, context.get("blueprint") or {})
            self.current_plan = plan
            self._build_task_graph(plan)
            self.log.status(f"Plan hazir: {len(plan)} kalem", stage="planning")

            # 2. Emit plan to UI
            self.log.emit_plan(plan)
            
            # 3. Execute with parallel scheduling
            stats = await self._execute_plan(plan)

            # 4. Final stage
            total = int(stats.get("total") or 0)
            done = int(stats.get("done") or 0)
            failed = int(stats.get("failed") or 0)
            skipped = int(stats.get("skipped") or 0)
            ok = total > 0 and done == total and failed == 0 and skipped == 0
            if ok:
                self.log.stage("done", "Tamamlandi", icon="OK",
                               text=f"Hedef gerceklestirildi: {goal}",
                               progress={"done": done, "total": total})
            else:
                self.log.stage(
                    "error", "Plan tamamlanamadi", icon="HATA",
                    text=f"{done}/{total} kalem tamam, {failed} basarisiz, {skipped} atlandi",
                    progress={"done": done, "total": total},
                )
            return {
                "ok": ok,
                "stats": stats,
                "plan": plan,
                "error": "" if ok else (
                    f"Plan tamamlanamadi: {done}/{total} tamam, "
                    f"{failed} basarisiz, {skipped} atlandi"
                ),
            }
            
        except Exception as e:
            logger.exception("Orchestrator error: %s", e)
            # Log yazimi da patlarsa asil hatayi golgelemesin.
            try:
                self.log.stage("error", f"Hata: {e}", icon="❌")
            except Exception:
                logger.exception("Hata asamasi loglanamadi")
            # Kismi ilerlemeyi koru: arayuz "0/0" yerine gercek durumu gorsun.
            if stats is None and plan is not None:
                try:
                    progress = plan.progress()
                    stats = {
                        "total": int(progress.get("total") or 0),
                        "done": int(progress.get("done") or 0),
                        "failed": int(progress.get("failed") or 0),
                        "skipped": int(progress.get("skipped") or 0),
                    }
                except Exception:
                    logger.exception("Plan ilerlemesi okunamadi")
            return {"ok": False, "error": str(e), "stats": stats or {}, "plan": plan}
        finally:
            self.running = False
    
    def _normalize_plan(self, plan, goal: str) -> BuildPlan:
        if isinstance(plan, BuildPlan):
            build_plan = plan
        elif isinstance(plan, dict):
            build_plan = BuildPlan.from_dict(plan)
        else:
            build_plan = BuildPlan([])

        if not build_plan.items and self.planning_agent:
            self.log.warn("Plan bos dondu; yedek plan kullaniliyor")
            build_plan = self.planning_agent._fallback_plan(goal)
        elif not build_plan.items:
            raise ValueError("Plan bos")

        used = set()
        for index, item in enumerate(build_plan.items, start=1):
            if not isinstance(item, PlanItem):
                item = PlanItem.from_dict(item)
                build_plan.items[index - 1] = item
            base = (item.key or "").strip() or f"item_{index}"
            key = base
            suffix = 2
            while key in used:
                key = f"{base}_{suffix}"
                suffix += 1
            used.add(key)
            item.key = key
            if not item.order:
                item.order = index
            if not item.title:
                item.title = f"Adim {index}"
            if item.status != "done":
                item.status = "pending"
        return build_plan

    def _build_task_graph(self, plan: BuildPlan):
        """Build dependency graph from plan items."""
        self.task_nodes = {}
        
        # Create nodes
        for item in plan.items:
            node = TaskNode(item=item)
            # Resume: daha once tamamlanan kalemler tekrar calistirilmasin.
            node.completed = item.status == "done"
            self.task_nodes[item.key] = node
        
        # Build dependencies (tamamlanmis bagimliliklar dogrudan cozulmus sayilir)
        for item in plan.items:
            node = self.task_nodes[item.key]
            if item.depends_on:
                for dep_key in item.depends_on:
                    dep_node = self.task_nodes.get(dep_key)
                    if dep_node is not None:
                        if dep_node.completed:
                            continue
                        node.dependencies.add(dep_key)
                        dep_node.dependents.add(item.key)
        
        # Mark initial ready tasks
        for node in self.task_nodes.values():
            if not node.dependencies and not node.completed:
                node.ready = True
    
    def _get_ready_tasks(self) -> List[TaskNode]:
        """Get tasks that are ready to run."""
        return [
            node for node in self.task_nodes.values()
            if node.ready and not node.completed and not node.failed
        ]
    
    def _mark_completed(self, key: str, success: bool):
        """Mark task as completed and update dependent readiness."""
        if key not in self.task_nodes:
            return
        node = self.task_nodes[key]
        node.completed = success
        node.failed = not success
        node.ready = False
        node.item.status = "done" if success else "failed"
        
        # Update dependents
        for dep_key in node.dependents:
            if dep_key in self.task_nodes:
                dep_node = self.task_nodes[dep_key]
                dep_node.dependencies.discard(key)
                if not dep_node.dependencies and not dep_node.completed and not dep_node.failed:
                    dep_node.ready = True
    
    def _get_parallel_groups(self, ready_tasks: List[TaskNode]) -> List[List[TaskNode]]:
        """Group ready tasks by phase for parallel execution."""
        # Group by phase
        phase_groups: Dict[str, List[TaskNode]] = {}
        for task in ready_tasks:
            phase = task.item.phase or "default"
            if phase not in phase_groups:
                phase_groups[phase] = []
            phase_groups[phase].append(task)
        
        # Within each phase, all tasks can run in parallel
        # Return flat list - worker pool handles concurrency limit
        result = []
        for phase, tasks in phase_groups.items():
            result.extend(tasks)
        return result
    
    async def _execute_plan(self, plan: BuildPlan) -> Dict:
        """Execute plan with parallel worker scheduling."""
        stats = {"done": 0, "failed": 0, "skipped": 0, "total": len(plan.items)}

        def _cancelled() -> bool:
            return task_control.is_cancelled(self.log.run_id)

        def _mark_remaining_cancelled():
            for node in self.task_nodes.values():
                if not node.completed and not node.failed:
                    node.failed = True
                    node.ready = False
                    node.item.status = "skipped"
                    node.item.result_summary = "Görev iptal edildi."
                    stats["skipped"] += 1
                    self.log.emit_plan_item(node.item)
            if self.current_plan:
                self.current_plan.save(self.log)
        
        # Emit initial progress
        self.log.emit_progress(0, stats["total"], "Plan başlatılıyor")
        
        while True:
            if _cancelled():
                _mark_remaining_cancelled()
                break
            ready = self._get_ready_tasks()
            if not ready:
                # Check if any pending (blocked by failed/unresolvable deps)
                pending = [n for n in self.task_nodes.values() if not n.completed and not n.failed and not n.ready]
                if pending:
                    for node in pending:
                        node.failed = True
                        node.ready = False
                        node.item.status = "skipped"
                        stats["skipped"] += 1
                        self.log.emit_plan_item(node.item)
                        self.log.warn(f"Atlandi (bagimliligi tamamlanamadi): {node.item.title}")
                    if self.current_plan:
                        self.current_plan.save(self.log)
                    continue
                break  # All done
            
            # Group by phase for parallel execution
            parallel_tasks = self._get_parallel_groups(ready)
            
            # Limit to max_workers
            batch = parallel_tasks[:self.max_workers]
            
            self.log.status(
                f"Paralel çalıştırılıyor: {[t.item.title for t in batch]}",
                progress={"done": stats["done"], "total": stats["total"], "running": len(batch)}
            )
            
            # Submit batch to worker pool
            futures = []
            for node in batch:
                node.ready = False  # Mark as dispatched
                future = asyncio.ensure_future(
                    self.worker_pool.submit(node.item, self.current_plan)
                )
                futures.append((node, future))
            
            # Wait for batch completion
            for node, future in futures:
                try:
                    outcome = await future
                    success = outcome.get("ok", False) if isinstance(outcome, dict) else False
                    self._mark_completed(node.item.key, success)
                    
                    if success:
                        stats["done"] += 1
                        self.log.status(f"✓ {node.item.title} tamamlandı")
                    else:
                        stats["failed"] += 1
                        self.log.warn(f"✗ {node.item.title} başarısız")
                        # Continue anyway - dependents will be marked skipped
                    
                except Exception as e:
                    logger.exception(f"Task {node.item.key} error: {e}")
                    self._mark_completed(node.item.key, False)
                    stats["failed"] += 1
                    self.log.warn(f"✗ {node.item.title} hata: {e}")
                
                # Update progress
                self.log.emit_progress(stats["done"], stats["total"], f"İlerleme: {stats['done']}/{stats['total']}")
            
            # Save plan state
            if self.current_plan:
                self.current_plan.save(self.log)

        # Basarisiz/atlanan kalemleri otomatik kurtar. Onceden bu adim yoktu:
        # tek bir kalem dusunce bagimli kalemler "skipped" kaliyor ve is
        # yarim bitiyordu.
        max_retries = max(0, int(_cfg("max_item_retries", 200)))
        for attempt in range(1, max_retries + 1):
            if _cancelled():
                _mark_remaining_cancelled()
                break
            failed_nodes = [n for n in self.task_nodes.values() if n.failed]
            if not failed_nodes:
                break
            self.log.status(
                f"{len(failed_nodes)} kalem yeniden deneniyor (tur {attempt}/{max_retries})",
                stage="retry",
                progress={"done": stats["done"], "total": stats["total"], "retrying": len(failed_nodes)},
            )
            backoff = min(300.0, max(0.0, float(_cfg("retry_backoff_seconds", 2))) * (2 ** min(attempt - 1, 7)))
            if backoff:
                self.log.status(f"{backoff:.0f} sn sonra yeniden denenecek...", stage="retry")
                await asyncio.sleep(backoff)
            for node in failed_nodes:
                node.failed = False
                node.completed = False
                node.ready = True
                node.item.status = "pending"
                node.item.attempts = 0

            # Onceki kalemlere bagli olanlar dogru sirada denensin.
            for node in sorted(failed_nodes, key=lambda n: (n.item.order, n.item.title.lower())):
                try:
                    outcome = await self.worker_pool.submit(node.item, self.current_plan)
                    success = outcome.get("ok", False) if isinstance(outcome, dict) else False
                except Exception as e:
                    logger.exception(f"Retry task {node.item.key} error: {e}")
                    success = False
                self._mark_completed(node.item.key, success)
                if success:
                    self.log.status(f"✓ {node.item.title} yeniden denemede tamamlandı")
                else:
                    self.log.warn(f"✗ {node.item.title} yeniden denemede başarısız")
                if self.current_plan:
                    self.current_plan.save(self.log)

        # Nihai durumu plan uzerinden hesapla: retry sonrasi sayaclar kaymasin.
        stats["total"] = len(self.task_nodes)
        stats["done"] = sum(1 for n in self.task_nodes.values() if n.completed)
        stats["failed"] = sum(1 for n in self.task_nodes.values() if n.failed)
        stats["skipped"] = sum(
            1 for n in self.task_nodes.values() if n.item.status == "skipped"
        )
        return stats
    
    def get_status(self) -> dict:
        return {
            "running": self.running,
            "total_tasks": len(self.task_nodes),
            "completed": sum(1 for n in self.task_nodes.values() if n.completed),
            "failed": sum(1 for n in self.task_nodes.values() if n.failed),
            "running": sum(1 for n in self.task_nodes.values() if not n.completed and not n.failed and not n.ready),
            "pending": sum(1 for n in self.task_nodes.values() if n.ready),
            "workers": self.worker_pool.get_stats()
        }


async def run_orchestrated(goal: str, run_logger: RunLogger, 
                           worker_pool: WorkerPool, planning_agent=None,
                           context: Dict = None, max_workers: int = 3) -> Dict:
    """Convenience function to run orchestration."""
    orchestrator = Orchestrator(run_logger, worker_pool, planning_agent, max_workers)
    return await orchestrator.run(goal, context)