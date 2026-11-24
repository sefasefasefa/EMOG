"""Subagent Orchestration: paralel agent havuzu ile iş dağılımı.

Mimari:
- AgentPool: Eşzamanlı agent'ları yönetir (thread/process/async)
- Agent: Tek bir görev tipi için uzmanlaşmış çalışan
- TaskQueue: Öncelikli, bağımlılıklı görev kuyruğu
- Coordinator: Planı parçalara böler, agent'lara dağıtır, sonuçları toplar

Kullanım:
    pool = AgentPool(max_workers=8)
    pool.spawn(ResearchAgent, count=3)
    pool.spawn(ExecutorAgent, count=5)
    results = pool.execute(plan, spec, dossier, benchmark)
"""
import asyncio
import logging
import os
import queue
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

from django.conf import settings

from .events import RunLogger
from .plan import BuildPlan, PlanItem

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentTask:
    """Agent'a verilecek atomik görev."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = ""           # "research", "execute_item", "critic", "design_review", ...
    payload: Dict = field(default_factory=dict)
    priority: int = 0
    dependencies: List[str] = field(default_factory=list)  # diğer task id'leri
    assigned_agent: Optional[str] = None
    status: AgentStatus = AgentStatus.IDLE
    result: Any = None
    error: str = ""
    started_at: float = 0
    finished_at: float = 0


@dataclass
class AgentInfo:
    """Agent örneği meta verisi."""
    id: str
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    current_task: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    started_at: float = 0


class BaseAgent(ABC):
    """Tüm agent'ların temel sınıfı."""

    def __init__(
        self,
        agent_id: str,
        run_logger: RunLogger,
        config: Dict = None,
    ):
        self.agent_id = agent_id
        self.log = run_logger
        self.config = config or {}
        self.status = AgentStatus.IDLE
        self.current_task: Optional[AgentTask] = None

    @abstractmethod
    def run(self, task: AgentTask) -> Any:
        """Görevi çalıştır, sonucu döndür. Exception fırlatırsa failed sayılır."""
        pass

    def can_handle(self, task_type: str) -> bool:
        """Bu agent bu görev tipini yerine getirebilir mi?"""
        return task_type in self.supported_types()

    @classmethod
    @abstractmethod
    def supported_types(cls) -> List[str]:
        """Bu agent hangi task türlerini destekler?"""
        pass


class ResearchAgent(BaseAgent):
    """Derin araştırma yapan agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["research_scope", "research_deep_read", "research_synthesize"]

    def run(self, task: AgentTask) -> Any:
        from . import research as research_mod

        if task.type == "research_scope":
            request = task.payload["request"]
            context = task.payload.get("context", "")
            model = task.payload.get("model")
            researcher = research_mod.DeepResearcher(self.log, model=model)
            scope = researcher._scope(request, context)
            return {"scope": scope}

        elif task.type == "research_deep_read":
            harvest = task.payload["harvest"]
            researcher = research_mod.DeepResearcher(self.log, model=task.payload.get("model"))
            notes = researcher._deep_read(harvest)
            return {"documents": notes}

        elif task.type == "research_synthesize":
            request = task.payload["request"]
            scope = task.payload["scope"]
            harvest = task.payload["harvest"]
            notes = task.payload["notes"]
            researcher = research_mod.DeepResearcher(self.log, model=task.payload.get("model"))
            dossier = researcher._synthesize(request, scope, harvest, notes)
            return {"dossier": dossier}

        raise ValueError(f"Bilinmeyen research task: {task.type}")


class BlueprintAgent(BaseAgent):
    """Mavi baskı (spesifikasyon) üreten agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["blueprint"]

    def run(self, task: AgentTask) -> Any:
        from . import blueprint as blueprint_mod

        request = task.payload["request"]
        dossier = task.payload["dossier"]
        benchmark = task.payload["benchmark"]
        model = task.payload.get("model")

        builder = blueprint_mod.BlueprintBuilder(self.log, model=model)
        blueprint = builder.build(request, dossier, benchmark)
        return {"blueprint": blueprint}


class PlanAgent(BaseAgent):
    """Uygulama planı üreten agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["plan"]

    def run(self, task: AgentTask) -> Any:
        from . import plan as plan_mod

        request = task.payload["request"]
        blueprint = task.payload["blueprint"]
        dossier = task.payload.get("dossier")
        model = task.payload.get("model")

        build_plan = plan_mod.build_plan(request, blueprint, self.log, dossier, model=model)
        return {"plan": build_plan.to_dict()}


class ExecutorAgent(BaseAgent):
    """Plan kalemlerini uygulayan agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["execute_item", "execute_batch"]

    def run(self, task: AgentTask) -> Any:
        from . import executor as executor_mod
        from .plan import BuildPlan, PlanItem

        if task.type == "execute_item":
            item_dict = task.payload["item"]
            plan_dict = task.payload["plan"]
            spec = task.payload["spec"]
            dossier = task.payload.get("dossier")
            benchmark = task.payload.get("benchmark")
            model = task.payload.get("model")
            round_index = task.payload.get("round_index", 0)

            item = PlanItem.from_dict(item_dict)
            plan = BuildPlan.from_dict(plan_dict)

            item_executor = executor_mod.ItemExecutor(
                self.log, spec, dossier, benchmark, model
            )
            result = item_executor.run(item, plan, round_index=round_index)
            return {"item": item.to_dict(), "result": result}

        elif task.type == "execute_batch":
            items = [PlanItem.from_dict(i) for i in task.payload["items"]]
            plan = BuildPlan.from_dict(task.payload["plan"])
            spec = task.payload["spec"]
            dossier = task.payload.get("dossier")
            benchmark = task.payload.get("benchmark")
            model = task.payload.get("model")
            round_index = task.payload.get("round_index", 0)

            plan_executor = executor_mod.PlanExecutor(
                self.log, spec, dossier, benchmark, model
            )
            # Sadece verilen items'ları çalıştır (bağımlılık kontrolü ile)
            results = []
            for item in items:
                if item.status == "pending":
                    result = executor_mod.ItemExecutor(
                        self.log, spec, dossier, benchmark, model
                    ).run(item, plan, round_index=round_index)
                    results.append({"item": item.to_dict(), "result": result})
            return {"results": results}

        raise ValueError(f"Bilinmeyen executor task: {task.type}")


class CriticAgent(BaseAgent):
    """Eleştiri (gap analizi) yapan agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["critic"]

    def run(self, task: AgentTask) -> Any:
        from . import critic as critic_mod
        from .plan import BuildPlan

        plan = BuildPlan.from_dict(task.payload["plan"])
        spec = task.payload["spec"]
        dossier = task.payload.get("dossier")
        benchmark = task.payload.get("benchmark")
        model = task.payload.get("model")
        round_index = task.payload.get("round_index", 0)

        critic = critic_mod.Critic(self.log, spec, dossier, benchmark, model)
        report = critic.review(plan, round_index=round_index)
        items = critic.to_items(report.get("missing_items") or [])
        return {"report": report, "items": items}


class DesignReviewAgent(BaseAgent):
    """Görsel tasarım denetimi yapan agent."""

    @classmethod
    def supported_types(cls) -> List[str]:
        return ["design_review"]

    def run(self, task: AgentTask) -> Any:
        from . import design_review as design_review_mod
        from .plan import BuildPlan

        plan = BuildPlan.from_dict(task.payload["plan"])
        spec = task.payload["spec"]
        request = task.payload["request"]
        model = task.payload.get("model")
        project_root = task.payload["project_root"]
        base_url = task.payload.get("base_url")

        result = design_review_mod.run_design_review_sync(
            self.log, project_root, base_url=base_url, model=model
        )
        items = design_review_mod.to_critic_items(result)
        return {"result": vars(result), "items": items}


# Agent registry - yeni agent tipleri buraya eklenir
AGENT_REGISTRY: Dict[str, Type[BaseAgent]] = {
    "research": ResearchAgent,
    "blueprint": BlueprintAgent,
    "plan": PlanAgent,
    "executor": ExecutorAgent,
    "critic": CriticAgent,
    "design_review": DesignReviewAgent,
}


class TaskQueue:
    """Öncelikli, bağımlılıklı görev kuyruğu."""

    def __init__(self):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._tasks: Dict[str, AgentTask] = {}
        self._completed: set = set()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)

    def add(self, task: AgentTask) -> str:
        with self._lock:
            self._tasks[task.id] = task
            # PriorityQueue: düşük sayı = yüksek öncelik
            self._queue.put((-task.priority, task.id))
            self._condition.notify()
            return task.id

    def add_batch(self, tasks: List[AgentTask]) -> List[str]:
        return [self.add(t) for t in tasks]

    def get_next(self, agent_type: str) -> Optional[AgentTask]:
        """Agent'in yapabileceği bir sonraki hazır görevi al."""
        with self._condition:
            while True:
                # Kuyrukta uygun görev ara
                temp = []
                found = None
                while not self._queue.empty():
                    _, task_id = self._queue.get()
                    task = self._tasks.get(task_id)
                    if not task:
                        continue
                    if task.status != AgentStatus.IDLE:
                        temp.append((-task.priority, task_id))
                        continue
                    # Bağımlılıklar tamamlandı mı?
                    deps_done = all(d in self._completed for d in task.dependencies)
                    if deps_done and self._can_handle(agent_type, task.type):
                        task.status = AgentStatus.RUNNING
                        task.started_at = time.monotonic()
                        found = task
                        break
                    temp.append((-task.priority, task_id))

                # Geri koy
                for item in temp:
                    self._queue.put(item)

                if found:
                    return found

                # Kuyruk boş veya uygun görev yok
                if self._queue.empty():
                    return None

                # Biraz bekle
                self._condition.wait(timeout=0.5)

    def _can_handle(self, agent_type: str, task_type: str) -> bool:
        agent_cls = AGENT_REGISTRY.get(agent_type)
        if not agent_cls:
            return False
        return agent_cls.supported_types().__contains__(task_type)

    def complete(self, task_id: str, result: Any = None, error: str = ""):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = AgentStatus.COMPLETED if not error else AgentStatus.FAILED
                task.result = result
                task.error = error
                task.finished_at = time.monotonic()
                self._completed.add(task_id)
                self._condition.notify_all()

    def get_result(self, task_id: str) -> Any:
        task = self._tasks.get(task_id)
        return task.result if task else None

    def all_done(self) -> bool:
        with self._lock:
            return all(t.status in (AgentStatus.COMPLETED, AgentStatus.FAILED)
                       for t in self._tasks.values())

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == AgentStatus.IDLE)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == AgentStatus.RUNNING)


class AgentPool:
    """Eşzamanlı agent havuzu."""

    def __init__(
        self,
        run_logger: RunLogger,
        max_workers: int = None,
        agent_counts: Dict[str, int] = None,
    ):
        self.log = run_logger
        self.max_workers = max_workers or int(_cfg("subagent_max_workers", 8))
        self.agent_counts = agent_counts or {
            "research": 1,
            "blueprint": 1,
            "plan": 1,
            "executor": int(_cfg("subagent_executors", 4)),
            "critic": 1,
            "design_review": 1,
        }
        self.task_queue = TaskQueue()
        self.agents: Dict[str, BaseAgent] = {}
        self.agent_infos: Dict[str, AgentInfo] = {}
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._futures: Dict[str, Future] = {}
        self._shutdown = False
        self._lock = threading.Lock()

    def spawn(self, agent_type: str, count: int = None) -> List[str]:
        """Belirtilen tipte agent örnekleri oluştur."""
        count = count or self.agent_counts.get(agent_type, 1)
        agent_cls = AGENT_REGISTRY.get(agent_type)
        if not agent_cls:
            raise ValueError(f"Bilinmeyen agent tipi: {agent_type}")

        spawned = []
        for i in range(count):
            agent_id = f"{agent_type}-{uuid.uuid4().hex[:6]}"
            agent = agent_cls(agent_id, self.log)
            with self._lock:
                self.agents[agent_id] = agent
                self.agent_infos[agent_id] = AgentInfo(
                    id=agent_id, agent_type=agent_type
                )
            spawned.append(agent_id)
        self.log.status(f"Agent havuzu: {agent_type} x{count} başlatıldı")
        return spawned

    def spawn_all(self) -> Dict[str, List[str]]:
        """Tüm agent tiplerini yapılandırmaya göre başlat."""
        result = {}
        for agent_type, count in self.agent_counts.items():
            result[agent_type] = self.spawn(agent_type, count)
        return result

    def submit_task(self, task: AgentTask) -> str:
        return self.task_queue.add(task)

    def submit_tasks(self, tasks: List[AgentTask]) -> List[str]:
        return self.task_queue.add_batch(tasks)

    def _worker(self, agent_id: str):
        """Agent çalışma döngüsü."""
        agent = self.agents.get(agent_id)
        if not agent:
            return

        info = self.agent_infos[agent_id]
        agent_type = info.agent_type

        while not self._shutdown:
            task = self.task_queue.get_next(agent_type)
            if not task:
                if self.task_queue.all_done():
                    break
                time.sleep(0.2)
                continue

            info.status = AgentStatus.RUNNING
            info.current_task = task.id
            info.started_at = time.monotonic()
            task.assigned_agent = agent_id

            self.log.info(f"[{agent_id}] görev alındı: {task.type} ({task.id})")

            try:
                result = agent.run(task)
                self.task_queue.complete(task.id, result=result)
                info.tasks_completed += 1
                self.log.info(f"[{agent_id}] görev tamamlandı: {task.type}")
            except Exception as exc:
                self.task_queue.complete(task.id, error=str(exc))
                info.tasks_failed += 1
                self.log.log("exception", f"[{agent_id}] görev hatası: {exc}")
            finally:
                info.status = AgentStatus.IDLE
                info.current_task = None

    def run(self, initial_tasks: List[AgentTask] = None) -> Dict[str, Any]:
        """Havuzu çalıştır, tüm görevler bitene kadar bekle."""
        if initial_tasks:
            self.submit_tasks(initial_tasks)

        # Agent'ları başlat
        futures = []
        for agent_id in self.agents:
            fut = self.executor.submit(self._worker, agent_id)
            futures.append(fut)
            self._futures[agent_id] = fut

        # Bitmesini bekle
        for fut in futures:
            fut.result()

        # Sonuçları topla
        return self._collect_results()

    def _collect_results(self) -> Dict[str, Any]:
        results = {}
        for task_id, task in self.task_queue._tasks.items():
            if task.result is not None:
                results[task_id] = {
                    "type": task.type,
                    "result": task.result,
                    "agent": task.assigned_agent,
                }
        return results

    def shutdown(self):
        self._shutdown = True
        self.executor.shutdown(wait=True)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_agents": len(self.agents),
                "by_type": {
                    t: sum(1 for a in self.agent_infos.values() if a.agent_type == t)
                    for t in set(a.agent_type for a in self.agent_infos.values())
                },
                "tasks_completed": sum(a.tasks_completed for a in self.agent_infos.values()),
                "tasks_failed": sum(a.tasks_failed for a in self.agent_infos.values()),
                "queue_pending": self.task_queue.pending_count(),
                "queue_running": self.task_queue.running_count(),
            }


class SubagentCoordinator:
    """Yüksek seviye koordinatör: planı parçalar, agent'lara dağıtır, döngüyü yönetir."""

    def __init__(
        self,
        run_logger: RunLogger,
        spec: Dict,
        dossier: Dict,
        benchmark: Dict,
        model: str = None,
        project_root: str = None,
        base_url: str = None,
    ):
        self.log = run_logger
        self.spec = spec
        self.dossier = dossier
        self.benchmark = benchmark
        self.model = model
        self.project_root = project_root
        self.base_url = base_url or getattr(settings, "DESIGN_REVIEW_BASE_URL", "http://localhost:8000")

        self.pool = AgentPool(run_logger)
        self.plan: Optional[BuildPlan] = None

    def run_full_pipeline(self, request: str) -> Dict[str, Any]:
        """Tam pipeline'ı subagent'larla çalıştır."""
        self.log.stage("subagent_pipeline", "Subagent pipeline başlıyor")

        # 1. Research (paralel scope + deep_read + synthesize)
        research_result = self._run_research_phase(request)
        if not research_result.get("success"):
            return research_result

        # 2. Benchmark
        benchmark_result = self._run_benchmark_phase(research_result["dossier"])
        if not benchmark_result.get("success"):
            return benchmark_result

        # 3. Blueprint
        blueprint_result = self._run_blueprint_phase(request, research_result["dossier"], benchmark_result["benchmark"])
        if not blueprint_result.get("success"):
            return blueprint_result

        # 4. Plan
        plan_result = self._run_plan_phase(request, blueprint_result["blueprint"], research_result["dossier"])
        if not plan_result.get("success"):
            return plan_result

        # 5. Execute + Critic + Design Review döngüsü
        return self._run_build_loop(request, plan_result["plan"], blueprint_result["blueprint"],
                                    research_result["dossier"], benchmark_result["benchmark"])

    def _run_research_phase(self, request: str) -> Dict[str, Any]:
        self.log.stage("research", "Araştırma fazı (subagent)")

        # Scope
        scope_task = AgentTask(
            type="research_scope",
            payload={"request": request, "context": "", "model": self.model},
            priority=100,
        )
        self.pool.submit_task(scope_task)
        self.pool.run([scope_task])
        scope = self.pool.task_queue.get_result(scope_task.id)["scope"]

        # Harvest (search_many - tek agent yeterli)
        from . import search as search_mod
        queries = scope.get("search_queries", [])[:12]
        harvest = search_mod.search_many(queries, per_query=6, log=lambda m: self.log.info(m))

        # Deep read - paralel
        from .research import DeepResearcher
        researcher = DeepResearcher(self.log, model=self.model)
        # _pick_documents -> URL listesi
        candidates = researcher._pick_documents(harvest["all"])
        # Her 3 URL için bir agent
        deep_read_tasks = []
        for i in range(0, len(candidates), 3):
            chunk = candidates[i:i+3]
            task = AgentTask(
                type="research_deep_read",
                payload={"harvest": {"all": chunk}, "model": self.model},
                priority=90,
                dependencies=[scope_task.id],
            )
            deep_read_tasks.append(task)
        self.pool.submit_tasks(deep_read_tasks)
        self.pool.run(deep_read_tasks)

        # Sonuçları birleştir
        all_notes = []
        for task in deep_read_tasks:
            res = self.pool.task_queue.get_result(task.id)
            if res and "documents" in res:
                all_notes.extend(res["documents"])

        # Synthesize
        synth_task = AgentTask(
            type="research_synthesize",
            payload={"request": request, "scope": scope, "harvest": harvest, "notes": all_notes, "model": self.model},
            priority=80,
            dependencies=[t.id for t in deep_read_tasks],
        )
        self.pool.submit_task(synth_task)
        self.pool.run([synth_task])
        dossier = self.pool.task_queue.get_result(synth_task.id)["dossier"]

        dossier["generated_at"] = time.time()
        dossier["scope"] = scope
        dossier["queries"] = queries
        dossier["documents"] = all_notes
        dossier["url_count"] = len(harvest["all"])

        self.log.save("research", dossier)
        return {"success": True, "dossier": dossier, "harvest": harvest}

    def _run_benchmark_phase(self, dossier: Dict) -> Dict[str, Any]:
        self.log.stage("benchmark", "Rakip analizi (subagent)")
        from . import benchmark as benchmark_mod
        benchmark = benchmark_mod.run_benchmark(
            dossier.get("scope") or {}, {"all": []}, self.log, model=self.model
        )
        return {"success": True, "benchmark": benchmark}

    def _run_blueprint_phase(self, request: str, dossier: Dict, benchmark: Dict) -> Dict[str, Any]:
        self.log.stage("blueprint", "Spesifikasyon (subagent)")
        task = AgentTask(
            type="blueprint",
            payload={"request": request, "dossier": dossier, "benchmark": benchmark, "model": self.model},
            priority=100,
        )
        self.pool.submit_task(task)
        self.pool.run([task])
        blueprint = self.pool.task_queue.get_result(task.id)["blueprint"]
        self.log.save("blueprint", blueprint)
        return {"success": True, "blueprint": blueprint}

    def _run_plan_phase(self, request: str, blueprint: Dict, dossier: Dict) -> Dict[str, Any]:
        self.log.stage("plan", "Plan oluşturma (subagent)")
        task = AgentTask(
            type="plan",
            payload={"request": request, "blueprint": blueprint, "dossier": dossier, "model": self.model},
            priority=100,
        )
        self.pool.submit_task(task)
        self.pool.run([task])
        plan_dict = self.pool.task_queue.get_result(task.id)["plan"]
        from .plan import BuildPlan
        self.plan = BuildPlan.from_dict(plan_dict)
        self.plan.save(self.log)
        self.log.emit_plan(self.plan)
        return {"success": True, "plan": self.plan}

    def _run_build_loop(
        self,
        request: str,
        plan: BuildPlan,
        spec: Dict,
        dossier: Dict,
        benchmark: Dict,
    ) -> Dict[str, Any]:
        self.log.stage("build", "Uygulama döngüsü (subagent)")

        max_rounds = int(_cfg("max_rounds", 1000))
        round_index = 0

        while True:
            round_index += 1
            self.log.stage("build", f"Uygulama turu {round_index}/{max_rounds}")

            # Ready items -> executor agent'larına dağıt
            ready = plan.ready_items()
            if not ready:
                if plan.pending():
                    for item in plan.pending():
                        item.status = "skipped"
                    plan.save(self.log)
                break

            # Batch'ler halinde dağıt (her executor bir batch alır)
            executor_ids = [aid for aid, info in self.pool.agent_infos.items()
                            if info.agent_type == "executor"]
            batch_size = max(1, len(ready) // len(executor_ids))
            tasks = []
            for i, executor_id in enumerate(executor_ids):
                batch = ready[i*batch_size:(i+1)*batch_size]
                if not batch:
                    continue
                task = AgentTask(
                    type="execute_batch",
                    payload={
                        "items": [item.to_dict() for item in batch],
                        "plan": plan.to_dict(),
                        "spec": spec,
                        "dossier": dossier,
                        "benchmark": benchmark,
                        "model": self.model,
                        "round_index": round_index,
                    },
                    priority=100 - round_index,
                )
                tasks.append(task)

            self.pool.submit_tasks(tasks)
            self.pool.run(tasks)

            # Sonuçları plana yaz
            for task in tasks:
                res = self.pool.task_queue.get_result(task.id)
                if res and "results" in res:
                    for r in res["results"]:
                        # Plan item'ını güncelle
                        for item in plan.items:
                            if item.key == r["item"]["key"]:
                                item.status = r["item"]["status"]
                                item.result_summary = r["item"].get("result_summary", "")
                                item.verify_output = r["item"].get("verify_output", "")
                                item.attempts = r["item"].get("attempts", 0)
                                break
            plan.save(self.log)

            # Critic
            critic_task = AgentTask(
                type="critic",
                payload={
                    "plan": plan.to_dict(),
                    "spec": spec,
                    "dossier": dossier,
                    "benchmark": benchmark,
                    "model": self.model,
                    "round_index": round_index,
                },
                priority=90,
            )
            self.pool.submit_task(critic_task)
            self.pool.run([critic_task])
            critic_res = self.pool.task_queue.get_result(critic_task.id)
            report = critic_res["report"]
            critic_items = critic_res["items"]

            added = 0
            if critic_items:
                from . import plan as plan_mod
                added = plan_mod.append_items(plan, critic_items, self.log, phase="polish")

            # Design Review
            design_task = AgentTask(
                type="design_review",
                payload={
                    "plan": plan.to_dict(),
                    "spec": spec,
                    "request": request,
                    "model": self.model,
                    "project_root": self.project_root,
                    "base_url": self.base_url,
                },
                priority=80,
            )
            self.pool.submit_task(design_task)
            self.pool.run([design_task])
            design_res = self.pool.task_queue.get_result(design_task.id)
            design_items = design_res.get("items", [])

            if design_items:
                from . import plan as plan_mod
                added += plan_mod.append_items(plan, design_items, self.log, phase="hardening")

            # Canlı preview fonksiyonel testi
            qa_items: List[Dict] = []
            try:
                from . import preview_qa as preview_qa_mod
                qa_url = preview_qa_mod.detect_preview_url(self.project_root)
                if qa_url:
                    qa_report = preview_qa_mod.run_preview_qa_sync(
                        qa_url, self.log, timeout=120, max_buttons=8, max_links=10,
                    )
                    try:
                        self.log.save("preview_qa", qa_report.to_dict())
                    except Exception:
                        pass
                    qa_items = preview_qa_mod.to_critic_items(qa_report)
                    if qa_items:
                        from . import plan as plan_mod
                        added += plan_mod.append_items(plan, qa_items, self.log, phase="hardening")
            except Exception as exc:
                self.log.warn(f"Canlı test atlandı: {exc}")

            if not added and not critic_items and not qa_items:
                self.log.info("Critic, tasarım denetimi ve canlı test yeni iş bulmadı — döngü bitti")
                break

            if round_index >= max_rounds:
                break

        # Final polish
        self.log.stage("polish", "Son rötuş")
        # ... (polish mantığı)

        progress = plan.progress()
        return {
            "success": progress["done"] > 0,
            "plan": progress,
            "rounds": round_index,
        }


# Kolaylık fonksiyonu
def run_subagent_pipeline(
    run_logger: RunLogger,
    request: str,
    spec: Dict = None,
    dossier: Dict = None,
    benchmark: Dict = None,
    model: str = None,
    project_root: str = None,
    base_url: str = None,
    agent_counts: Dict[str, int] = None,
) -> Dict[str, Any]:
    """Subagent pipeline'ını çalıştır."""
    # Eğer spec/dossier/benchmark verilmediyse research+benchmark+blueprint yap
    if not spec or not dossier or not benchmark:
        coordinator = SubagentCoordinator(
            run_logger, spec or {}, dossier or {}, benchmark or {},
            model=model, project_root=project_root, base_url=base_url
        )
        coordinator.pool.agent_counts = agent_counts or coordinator.pool.agent_counts
        coordinator.pool.spawn_all()
        return coordinator.run_full_pipeline(request)

    # Sadece build loop
    from .plan import BuildPlan
    plan = BuildPlan.from_dict(spec.get("plan", {})) if "plan" in spec else None
    # ... (kısaltılmış)

    return {"success": False, "error": "Plan gerekli"}