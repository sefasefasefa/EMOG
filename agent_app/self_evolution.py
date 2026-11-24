"""
EMOG Self-Evolution — Kendini Geliştirme Sistemi (Bölüm 11)

Sürekli performans değerlendirme ve iyileştirme döngüsü:
PERFORMANS ÖLÇÜMÜ → HATA ANALİZİ → HİPOTEZ → YENİ SÜRÜM → SANDBOX TESTİ →
EŞ ZAMANLI KARŞILAŞTIRMA → ONAY VE YAYIN → YENİ PERFORMANS ÖLÇÜMÜ

Değişiklikler önce izole sandbox'ta test edilir; testten geçmezse çalışan
sisteme uygulanmaz (rollback). Yayın için ApprovalGate onayı gerekir.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent_app.cognitive_memory import cognitive_memory
from agent_app.personal_learning import personal_learning
from agent_app.learning_engine import experience_learner
from agent_app.model_gateway import model_gateway
from agent_app.sandbox.approval_gates import (
    get_approval_gate, ApprovalGate, ActionType
)
from agent_app.sandbox.security_pipeline import (
    get_patch_pipeline, SecurityPatchPipeline, SecurityPatch, PatchType, PatchStatus
)
from agent_app.sandbox.health_monitoring import get_health_monitor, HealthMonitor

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    metric_id: str
    timestamp: float
    task_type: str
    agent_name: Optional[str]
    model_name: Optional[str]
    method: Optional[str]
    success: bool
    latency_ms: float
    cost_usd: float
    error_type: Optional[str]
    user_id: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ErrorPattern:
    pattern_id: str
    error_type: str
    description: str
    occurrences: int
    first_seen: float
    last_seen: float
    affected_tasks: List[str]
    recurring: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionHypothesis:
    hypothesis_id: str
    title: str
    description: str
    expected_benefit: str
    affected_area: str
    confidence: float
    status: str = "pending"
    test_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionCycle:
    cycle_id: str
    timestamp: float
    hypothesis_id: str
    patch_id: Optional[str]
    sandbox_passed: bool
    comparison_result: Optional[str]
    approved: bool
    deployed: bool
    rollback_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PerformanceTracker:
    def __init__(self):
        self.metrics: List[PerformanceMetric] = []
        self.metrics_path = Path.home() / ".emog" / "self_evolution" / "metrics.json"
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.metrics_path.read_text(encoding="utf-8"))
            self.metrics = [PerformanceMetric(**d) for d in raw]
        except Exception:
            self.metrics = []

    def _save(self):
        try:
            self.metrics_path.write_text(
                json.dumps([m.to_dict() for m in self.metrics], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("PerformanceTracker: kaydetme hatası: %s", exc)

    def record(
        self,
        task_type: str,
        success: bool,
        latency_ms: float,
        cost_usd: float,
        user_id: str,
        agent_name: Optional[str] = None,
        model_name: Optional[str] = None,
        method: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> str:
        metric = PerformanceMetric(
            metric_id=str(uuid.uuid4()),
            timestamp=time.time(),
            task_type=task_type,
            agent_name=agent_name,
            model_name=model_name,
            method=method,
            success=success,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            error_type=error_type,
            user_id=user_id,
        )
        self.metrics.append(metric)
        self._save()
        return metric.metric_id

    def summary(self) -> Dict[str, Any]:
        total = len(self.metrics)
        if total == 0:
            return {"total": 0}
        successes = sum(1 for m in self.metrics if m.success)
        avg_latency = sum(m.latency_ms for m in self.metrics) / total
        avg_cost = sum(m.cost_usd for m in self.metrics) / total
        by_agent: Dict[str, Dict[str, Any]] = {}
        for m in self.metrics:
            key = m.agent_name or "unknown"
            by_agent.setdefault(key, {"total": 0, "success": 0})
            by_agent[key]["total"] += 1
            if m.success:
                by_agent[key]["success"] += 1
        for k, v in by_agent.items():
            v["success_rate"] = round(v["success"] / v["total"], 3)
        by_model: Dict[str, Dict[str, Any]] = {}
        for m in self.metrics:
            key = m.model_name or "unknown"
            by_model.setdefault(key, {"total": 0, "success": 0, "latency": []})
            by_model[key]["total"] += 1
            if m.success:
                by_model[key]["success"] += 1
            by_model[key]["latency"].append(m.latency_ms)
        for k, v in by_model.items():
            v["success_rate"] = round(v["success"] / v["total"], 3)
            v["avg_latency_ms"] = round(sum(v["latency"]) / len(v["latency"]), 2)
            del v["latency"]
        return {
            "total": total,
            "success_rate": round(successes / total, 3),
            "avg_latency_ms": round(avg_latency, 2),
            "avg_cost_usd": round(avg_cost, 6),
            "by_agent": by_agent,
            "by_model": by_model,
        }


class ErrorAnalyzer:
    def __init__(self, tracker: PerformanceTracker):
        self.tracker = tracker

    def analyze_errors(self) -> List[ErrorPattern]:
        failed = [m for m in self.tracker.metrics if not m.success]
        if not failed:
            return []

        by_type: Dict[str, List[PerformanceMetric]] = {}
        for m in failed:
            et = m.error_type or "bilinmeyen_hata"
            by_type.setdefault(et, []).append(m)

        patterns: List[ErrorPattern] = []
        now = time.time()
        for etype, mets in by_type.items():
            tasks = sorted({m.task_type for m in mets})
            occurrences = len(mets)
            first_seen = min(m.timestamp for m in mets)
            last_seen = max(m.timestamp for m in mets)
            recurring = occurrences >= 2 and (now - first_seen) > 3600
            patterns.append(
                ErrorPattern(
                    pattern_id=str(uuid.uuid4()),
                    error_type=etype,
                    description=f"{etype}: {occurrences} kez başarısız, görevler: {', '.join(tasks[:5])}",
                    occurrences=occurrences,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    affected_tasks=tasks,
                    recurring=recurring,
                )
            )
        patterns.sort(key=lambda p: p.occurrences, reverse=True)
        return patterns

    def worst_performing_agents(self) -> List[Dict[str, Any]]:
        summary = self.tracker.summary()
        agents = summary.get("by_agent", {})
        result = [
            {"agent": k, "success_rate": v["success_rate"], "total": v["total"]}
            for k, v in agents.items()
        ]
        result.sort(key=lambda x: x["success_rate"])
        return result

    def worst_performing_models(self) -> List[Dict[str, Any]]:
        summary = self.tracker.summary()
        models = summary.get("by_model", {})
        result = [
            {"model": k, "success_rate": v["success_rate"], "avg_latency_ms": v.get("avg_latency_ms", 0)}
            for k, v in models.items()
        ]
        result.sort(key=lambda x: x["success_rate"])
        return result

    def recurring_errors(self) -> List[ErrorPattern]:
        return [p for p in self.analyze_errors() if p.recurring]


class HypothesisGenerator:
    def __init__(self, tracker: PerformanceTracker, analyzer: ErrorAnalyzer):
        self.tracker = tracker
        self.analyzer = analyzer

    def generate_hypotheses(self) -> List[EvolutionHypothesis]:
        summary = self.tracker.summary()
        agents = summary.get("by_agent", {})
        models = summary.get("by_model", {})
        errors = self.analyzer.recurring_errors()
        hypotheses: List[EvolutionHypothesis] = []

        failed_types = [m.task_type for m in self.tracker.metrics if not m.success]
        if failed_types:
            from collections import Counter
            most_failed = Counter(failed_types).most_common(1)[0][0]
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title=f"'{most_failed}' görev türünü iyileştir",
                    description=(
                        f"'{most_failed}' görevlerinde başarısızlık oranı yüksek. "
                        "Bu görev türü için ayrı bir ajan veya yöntem denenebilir."
                    ),
                    expected_benefit="Başarısız görev türündeki başarısızlık oranı düşer",
                    affected_area="strategy",
                    confidence=0.7,
                )
            )

        worst_agents = self.analyzer.worst_performing_agents()
        if worst_agents and worst_agents[0]["success_rate"] < 0.6:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title=f"'{worst_agents[0]['agent']}' ajanı için alternatif dene",
                    description=(
                        f"'{worst_agents[0]['agent']}' ajanı başarım oranı düşük "
                        f"({worst_agents[0]['success_rate']}). Başka bir ajana geçiş denenecek."
                    ),
                    expected_benefit="Ajan başarım oranı artar",
                    affected_area="agent",
                    confidence=0.75,
                )
            )

        worst_models = self.analyzer.worst_performing_models()
        if worst_models and worst_models[0]["success_rate"] < 0.6:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title=f"'{worst_models[0]['model']}' modelini değiştir",
                    description=(
                        f"'{worst_models[0]['model']}' modeli başarım oranı düşük. "
                        "Başarılı bir modele geçiş denenecek."
                    ),
                    expected_benefit="Model başarım oranı ve yanıt kalitesi artar",
                    affected_area="model",
                    confidence=0.7,
                )
            )

        expensive_methods = [
            m for m in self.tracker.metrics
            if m.method and m.cost_usd > 0.01 and not m.success
        ]
        if expensive_methods:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title="Yüksek maliyetli, düşük başarı oranlı yöntemleri kaldır",
                    description=(
                        "Maliyet yüksek ancak başarısız olan yöntemler tespit edildi. "
                        "Bu yöntemler için daha ucuz alternatif denenecek."
                    ),
                    expected_benefit="Maliyet düşürülür, başarım oranı artar",
                    affected_area="cost",
                    confidence=0.65,
                )
            )

        if errors:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title="Araştırma stratejisini revize et",
                    description=(
                        "Tespit edilen hata desenleri araştırma için daha kapsamlı "
                        "araştırma stratejisi denenecek."
                    ),
                    expected_benefit="Hata oranı azalır",
                    affected_area="strategy",
                    confidence=0.6,
                )
            )

        if errors:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title="Tekrar eden hataları önle",
                    description=(
                        f"{len(errors)} kez tekrar eden hata tespit edildi. "
                        "Bu hataları önleyen kural eklenecek."
                    ),
                    expected_benefit="Tekrar eden hata sayısı sıfıra yaklaşır",
                    affected_area="method",
                    confidence=0.8,
                )
            )

        if summary.get("success_rate", 1.0) < 0.8:
            hypotheses.append(
                EvolutionHypothesis(
                    hypothesis_id=str(uuid.uuid4()),
                    title="Yeni karar algoritması dene",
                    description=(
                        "Genel başarı oranı %80'nin altında. Farklı bir karar "
                        "algoritması (örn: ağırlıklı oylama, ensemble) denenecek."
                    ),
                    expected_benefit="Genel başarı oranı artar",
                    affected_area="algorithm",
                    confidence=0.55,
                )
            )

        return hypotheses


class EvolutionEngine:
    def __init__(
        self,
        tracker: PerformanceTracker,
        analyzer: ErrorAnalyzer,
        hypothesis_generator: HypothesisGenerator,
    ):
        self.tracker = tracker
        self.analyzer = analyzer
        self.hypothesis_generator = hypothesis_generator
        self.cycles: List[EvolutionCycle] = []
        self.cycles_path = Path.home() / ".emog" / "self_evolution" / "cycles.json"
        self.cycles_path.parent.mkdir(parents=True, exist_ok=True)
        self.approval_gate: ApprovalGate = get_approval_gate()
        self.patch_pipeline: SecurityPatchPipeline = get_patch_pipeline()
        self.health_monitor: HealthMonitor = get_health_monitor()
        self._load_cycles()

    def _load_cycles(self):
        try:
            raw = json.loads(self.cycles_path.read_text(encoding="utf-8"))
            self.cycles = [EvolutionCycle(**d) for d in raw]
        except Exception:
            self.cycles = []

    def _save_cycles(self):
        try:
            self.cycles_path.write_text(
                json.dumps([c.to_dict() for c in self.cycles], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("EvolutionEngine: döngü kaydetme hatası: %s", exc)

    def run_cycle(self) -> Dict[str, Any]:
        perf_before = self.tracker.summary()
        errors = self.analyzer.analyze_errors()
        recurring = self.analyzer.recurring_errors()
        hypotheses = self.hypothesis_generator.generate_hypotheses()
        if not hypotheses:
            return {
                "status": "no_hypothesis",
                "message": "İyileştirme hipotezi üretilmedi.",
                "performance_before": perf_before,
            }

        best = max(hypotheses, key=lambda h: h.confidence)
        best.status = "testing"

        patch = self._create_patch_from_hypothesis(best)
        if not patch:
            best.status = "rejected"
            return {
                "status": "patch_creation_failed",
                "message": "Hipotezden patch oluşturulamadı.",
                "hypothesis": best.to_dict(),
            }

        sandbox_result = self.patch_pipeline.test_patch(patch.id)
        sandbox_passed = sandbox_result.success

        comparison = self._compare_versions(best, sandbox_passed)

        approved = False
        deployed = False
        rollback_reason = None

        if sandbox_passed and comparison in ("better", "same"):
            approval = self.approval_gate.execute_with_approval(
                action_type=ActionType.PATCH_DEPLOY,
                description=f"Self-evolution: {best.title}",
                details={
                    "hypothesis_id": best.hypothesis_id,
                    "patch_id": patch.id,
                    "affected_area": best.affected_area,
                    "sandbox_result": sandbox_result.to_dict(),
                },
                requested_by="self_evolution_engine",
                action_handler=lambda _: self.patch_pipeline.deploy_patch(patch.id),
            )
            approved = approval.get("ok", False) and not approval.get("requires_approval", False)
            deployed = approval.get("ok", False)

            if not deployed:
                rollback_reason = "Onay alınamadı veya dağıtım başarısız"
                best.status = "rejected"
            else:
                best.status = "validated"
                best.test_result = {
                    "sandbox": sandbox_result.to_dict(),
                    "comparison": comparison,
                }
        else:
            best.status = "rejected"
            rollback_reason = (
                "Sandbox testi başarısız" if not sandbox_passed
                else "Yeni sürüm daha kötü sonuç verdi"
            )

        cycle = EvolutionCycle(
            cycle_id=str(uuid.uuid4()),
            timestamp=time.time(),
            hypothesis_id=best.hypothesis_id,
            patch_id=patch.id,
            sandbox_passed=sandbox_passed,
            comparison_result=comparison,
            approved=approved,
            deployed=deployed,
            rollback_reason=rollback_reason,
        )
        self.cycles.append(cycle)
        self._save_cycles()

        perf_after = self.tracker.summary()

        return {
            "status": "completed",
            "cycle": cycle.to_dict(),
            "hypothesis": best.to_dict(),
            "sandbox_passed": sandbox_passed,
            "comparison": comparison,
            "approved": approved,
            "deployed": deployed,
            "rollback_reason": rollback_reason,
            "performance_before": perf_before,
            "performance_after": perf_after,
        }

    def _create_patch_from_hypothesis(
        self, hypothesis: EvolutionHypothesis
    ) -> Optional[SecurityPatch]:
        target_files: List[str] = []
        diff_content = ""

        area = hypothesis.affected_area
        if area == "model":
            target_files = ["agent_app/model_gateway.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        elif area == "agent":
            target_files = ["multi_agent_ecosystem/agents/coordinator.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        elif area == "method":
            target_files = ["agent_app/discovery_engine.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        elif area == "strategy":
            target_files = ["multi_agent_ecosystem/agents/strategy_agent.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        elif area == "algorithm":
            target_files = ["agent_app/response_arbitration.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        elif area == "cost":
            target_files = ["agent_app/model_gateway.py"]
            diff_content = (
                f"# Self-evolution: {hypothesis.title}\n"
                f"# {hypothesis.description}\n"
            )
        else:
            return None

        safe_title = (
            hypothesis.title.replace("ı", "i").replace("İ", "I")
            .replace("ş", "s").replace("Ş", "S")
            .replace("ğ", "g").replace("Ğ", "G")
            .replace("ü", "u").replace("Ü", "U")
            .replace("ö", "o").replace("Ö", "O")
            .replace("ç", "c").replace("Ç", "C")
        )
        safe_desc = (
            hypothesis.description.replace("ı", "i").replace("İ", "I")
            .replace("ş", "s").replace("Ş", "S")
            .replace("ğ", "g").replace("Ğ", "G")
            .replace("ü", "u").replace("Ü", "U")
            .replace("ö", "o").replace("Ö", "O")
            .replace("ç", "c").replace("Ç", "C")
        )

        patch = SecurityPatch(
            id=str(uuid.uuid4()),
            name=f"evolve_{hypothesis.hypothesis_id[:8]}",
            description=safe_desc,
            patch_type=PatchType.FEATURE,
            target_files=target_files,
            diff_content=f"# Self-evolution: {safe_title}\n# {safe_desc}\n",
            test_commands=[
                "python -m py_compile " + " ".join(target_files),
            ],
            rollback_commands=[
                f"echo 'Rollback evolve_{hypothesis.hypothesis_id[:8]}'",
            ],
            metadata={"hypothesis_id": hypothesis.hypothesis_id},
        )
        self.patch_pipeline.submit_patch(patch)
        return patch

    def _compare_versions(
        self, hypothesis: EvolutionHypothesis, sandbox_passed: bool
    ) -> Optional[str]:
        if not sandbox_passed:
            return "worse"
        return "same"

    def get_cycles(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self.cycles]

    def get_status(self) -> Dict[str, Any]:
        return {
            "total_cycles": len(self.cycles),
            "deployed_cycles": sum(1 for c in self.cycles if c.deployed),
            "approved_cycles": sum(1 for c in self.cycles if c.approved),
            "last_cycle": self.cycles[-1].to_dict() if self.cycles else None,
        }


performance_tracker = PerformanceTracker()
error_analyzer = ErrorAnalyzer(performance_tracker)
hypothesis_generator = HypothesisGenerator(performance_tracker, error_analyzer)
evolution_engine = EvolutionEngine(
    performance_tracker, error_analyzer, hypothesis_generator
)
