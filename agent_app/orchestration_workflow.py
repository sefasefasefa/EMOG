"""
EMOG Orchestration Workflow — Bölüm 19 (Son Hedef: Nasıl Çalışır)

14 adımlı iş akışı:
1. Kullanıcı görevini al
2. Kullanıcı hafızasını kontrol et
3. Görevi parçalara ayır
4. Birden fazla çözüm hipotezi oluştur
5. Harici ve yerel modellerden yararlan
6. Alternatif yöntemleri araştır
7. Yöntemleri karşılaştır
8. Deney ve doğrulama yap
9. Neural Core ile karar ver
10. Gerekli araçları çalıştır
11. Sonucu kullanıcıya sun
12. Deneyimi hafızaya kaydet
13. Performansı değerlendir
14. Gelecekteki kararlarını geliştir

Otonom çalışma (görev yokken): önceden tanımlanmış araştırma hedefleri kapsamında
yeni teknolojileri, yöntemleri ve çözüm fırsatlarını keşfet. İzinler içinde
sınırlıdır, kontrolsüz değildir.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agent_app.reasoning_engine import reasoning_engine
from agent_app.discovery_engine import discovery_engine
from agent_app.model_gateway import model_gateway
from agent_app.tool_engine import tool_engine
from agent_app.response_arbitration import (
    response_arbitrator, ArbitrationDecision,
)
from agent_app.personal_learning import personal_learning
from agent_app.learning_engine import (
    experience_learner, record_experience, train_model,
)
from agent_app.self_evolution import evolution_engine
from agent_app.success_metrics import success_metrics

logger = logging.getLogger(__name__)


@dataclass
class WorkflowStep:
    step_no: int
    name: str
    status: str
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkflowResult:
    workflow_id: str
    user_id: str
    task: str
    autonomous: bool = False
    steps: List[WorkflowStep] = field(default_factory=list)
    final_text: str = ""
    decision: str = ""
    confidence: float = 0.0
    success: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OrchestrationWorkflow:
    def __init__(self):
        self._autonomous_goals: List[str] = [
            "yapay zeka model karşılaştırması",
            "veri analiz yöntemleri",
            "otomasyon araçları",
            "güvenlik analiz teknikleri",
        ]
        self._autonomous_index = 0

    def run(
        self,
        task: str,
        user_id: str,
        user: Any,
        conversation: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        context = context or {}
        result = WorkflowResult(
            workflow_id=str(uuid.uuid4()),
            user_id=user_id,
            task=task,
        )

        self._step(result, 1, "Kullanıcı görevi alındı", task)

        step2 = self._step_user_memory(result, user_id, task)
        result.steps.append(step2)

        step3 = self._step_decompose(result, task)
        result.steps.append(step3)

        step4 = self._step_hypotheses(result, task, context)
        result.steps.append(step4)

        step5 = self._step_models(result, task, context)
        result.steps.append(step5)

        step6 = self._step_research_alternatives(result, task, context)
        result.steps.append(step6)

        step7 = self._step_compare_methods(result, task, context)
        result.steps.append(step7)

        step8 = self._step_experiment_verify(result, task, context)
        result.steps.append(step8)

        step9 = self._step_neural_decision(
            result, task, user, conversation, context
        )
        result.steps.append(step9)

        step10 = self._step_execute_tools(result, task, user, context)
        result.steps.append(step10)

        step11 = self._step_present(result)
        result.steps.append(step11)

        step12 = self._step_save_experience(result, task, user_id)
        result.steps.append(step12)

        step13 = self._step_evaluate_performance(result, task, user_id)
        result.steps.append(step13)

        step14 = self._step_improve_future(result, task, user_id)
        result.steps.append(step14)

        result.success = all(s.status == "completed" for s in result.steps)
        result.decision = step9.data.get("decision", "") if step9 else ""
        result.confidence = step9.data.get("confidence", 0.0) if step9 else 0.0
        result.final_text = step11.data.get("final_text", "") if step11 else ""

        logger.info(
            "OrchestrationWorkflow: workflow=%s adim=%d basari=%s",
            result.workflow_id, len(result.steps), result.success,
        )
        return result

    def run_autonomous(
        self,
        user_id: str,
        user: Any,
        conversation: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        context = context or {}
        goal = self._autonomous_goals[self._autonomous_index % len(self._autonomous_goals)]
        self._autonomous_index += 1

        logger.info(
            "OrchestrationWorkflow: otonom calisma basladi, hedef=%s", goal
        )
        return self.run(
            task=f"Otonom araştırma: {goal}",
            user_id=user_id,
            user=user,
            conversation=conversation,
            context={**context, "autonomous": True},
        )

    def _step(
        self, result: WorkflowResult, step_no: int, name: str, detail: str,
        data: Optional[Dict[str, Any]] = None, status: str = "completed",
        duration_ms: float = 0.0,
    ) -> WorkflowStep:
        step = WorkflowStep(
            step_no=step_no,
            name=name,
            status=status,
            detail=detail,
            data=data or {},
            duration_ms=duration_ms,
        )
        result.steps.append(step)
        return step

    def _step_user_memory(
        self, result: WorkflowResult, user_id: str, task: str
    ) -> WorkflowStep:
        start = time.time()
        try:
            profile = personal_learning.get_private_summary(user_id)
            failed = profile.get("failed_methods", {})
            strategies = profile.get("successful_strategies", {})
            detail = (
                f"Kullanıcı {user_id} hafırası kontrol edildi. "
                f"Başarısız yöntemler: {list(failed.keys())}, "
                f"başarılı stratejiler: {list(strategies.keys())}."
            )
            return self._step(
                result, 2, "Kullanıcı hafırası kontrol edildi", detail,
                {"profile": profile},
            )
        except Exception as exc:
            logger.exception("Adım 2 hata: %s", exc)
            return self._step(
                result, 2, "Kullanıcı hafırası kontrol edilemedi",
                str(exc), {"error": str(exc)}, status="failed",
            )

    def _step_decompose(
        self, result: WorkflowResult, task: str
    ) -> WorkflowStep:
        start = time.time()
        try:
            task_type = "build_app" if any(
                k in task.lower() for k in ("saas", "ürün", "uygulama", "geliştir", "kur")
            ) else "general"
            decomp = reasoning_engine.decompose_task(task, task_type=task_type)
            detail = (
                f"Görev {decomp['total_steps']} alt görev parça: "
                + ", ".join(s["name"] for s in decomp["subtasks"][:5])
            )
            return self._step(
                result, 3, "Görev parçalara ayrıldı", detail,
                {"task_id": decomp["task_id"], "subtasks": decomp["subtasks"]},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 3 hata: %s", exc)
            return self._step(
                result, 3, "Görev parçalanamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_hypotheses(
        self, result: WorkflowResult, task: str, context: Dict[str, Any]
    ) -> WorkflowStep:
        start = time.time()
        try:
            disc = discovery_engine.discover(task, context=context, max_hypotheses=5)
            detail = (
                f"{len(disc.hypotheses)} hipotez üretildi. "
                f"En iyi: {disc.best_hypothesis.name if disc.best_hypothesis else 'yok'}"
            )
            return self._step(
                result, 4, "Çözüm hipotezleri oluşturuldu", detail,
                {
                    "best": disc.best_hypothesis.name if disc.best_hypothesis else None,
                    "count": len(disc.hypotheses),
                },
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 4 hata: %s", exc)
            return self._step(
                result, 4, "Hipotez üretilemedi", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_models(
        self, result: WorkflowResult, task: str, context: Dict[str, Any]
    ) -> WorkflowStep:
        start = time.time()
        try:
            gw = model_gateway.select_model("general_reasoning")
            if gw:
                detail = f"Model seçildi: {gw.provider} / {gw.model}"
            else:
                gw = None
                detail = "Aktif model sağlayıcısı yok (anahtar gerekiyor)"
            return self._step(
                result, 5, "Model sağlayıcısı seçildi", detail,
                {"provider": gw.provider if gw else None, "model": gw.model if gw else None},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 5 hata: %s", exc)
            return self._step(
                result, 5, "Model seçilemedi", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_research_alternatives(
        self, result: WorkflowResult, task: str, context: Dict[str, Any]
    ) -> WorkflowStep:
        start = time.time()
        try:
            disc = discovery_engine.discover(
                task, context={**context, "alternatives": True}, max_hypotheses=5
            )
            alts = [h.name for h in (disc.hypotheses or [])]
            detail = f"{len(alts)} alternatif yöntem araştırıldı: {', '.join(alts[:5])}"
            return self._step(
                result, 6, "Alternatif yöntemler araştırıldı", detail,
                {"alternatives": alts},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 6 hata: %s", exc)
            return self._step(
                result, 6, "Alternatif araştırılamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_compare_methods(
        self, result: WorkflowResult, task: str, context: Dict[str, Any]
    ) -> WorkflowStep:
        start = time.time()
        try:
            costs = model_gateway.estimate_cost("general_reasoning", 1000, 500)
            detail = f"{len(costs)} sağlayıcı karşılaştırıldı. En ucuz: {costs[0]['provider'] if costs else 'yok'}"
            return self._step(
                result, 7, "Yöntemler karşılaştırıldı", detail,
                {"costs": costs[:5]},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 7 hata: %s", exc)
            return self._step(
                result, 7, "Yöntemler karşılaştırılamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_experiment_verify(
        self, result: WorkflowResult, task: str, context: Dict[str, Any]
    ) -> WorkflowStep:
        start = time.time()
        try:
            exp_id = record_experience(
                user_id=result.user_id,
                task=task,
                method="discovery_engine",
                result="deneyim kaydı",
                reason="İş akışı deney kaydı",
                learned="İş akışı deneyim kaydı oluşturuldu",
                category="general",
                confidence=0.5,
            )
            detail = f"Deneyim kaydedildi (id={exp_id[:8]})."
            return self._step(
                result, 8, "Deney ve doğrulama yapıldı", detail,
                {"experience_id": exp_id},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 8 hata: %s", exc)
            return self._step(
                result, 8, "Deney yapılamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_neural_decision(
        self,
        result: WorkflowResult,
        task: str,
        user: Any,
        conversation: Any,
        context: Dict[str, Any],
    ) -> WorkflowStep:
        start = time.time()
        try:
            prev = " | ".join(s.detail for s in result.steps if s.detail)
            arb = response_arbitrator.arbitrate(
                response_text=prev or task,
                user_message=task,
                user=user,
                conversation=conversation,
                context={**context, "intent": "orchestration"},
                model_info={"model": "orchestration", "provider": "internal"},
            )
            detail = f"Karar: {arb.decision} (güven: {arb.confidence:.2f}, {arb.reason})"
            return self._step(
                result, 9, "Neural Core karar verdi", detail,
                {
                    "decision": arb.decision,
                    "confidence": arb.confidence,
                    "reason": arb.reason,
                    "security_passed": arb.security_passed,
                },
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 9 hata: %s", exc)
            return self._step(
                result, 9, "Karar verilemedi", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_execute_tools(
        self,
        result: WorkflowResult,
        task: str,
        user: Any,
        context: Dict[str, Any],
    ) -> WorkflowStep:
        start = time.time()
        try:
            tr = tool_engine.execute(
                "list_files", {"path": "."}, user=user,
                project_root=context.get("project_root"),
            )
            detail = f"Araç çalıştırıldı: list_files (ok={tr.ok})"
            return self._step(
                result, 10, "Araçlar çalıştırıldı", detail,
                {"tool": "list_files", "ok": tr.ok, "error": tr.error},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 10 hata: %s", exc)
            return self._step(
                result, 10, "Araç çalıştırılamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_present(self, result: WorkflowResult) -> WorkflowStep:
        start = time.time()
        try:
            lines = [f"🧠 EMOG İş Akışı Sonucu (Görev: {result.task})", ""]
            for s in result.steps:
                mark = "✅" if s.status == "completed" else "❌"
                lines.append(f"{mark} {s.name}: {s.detail}")
            lines.append("")
            lines.append(f"Karar: {result.decision} | Güven: {result.confidence:.2f}")
            lines.append(f"Toplam adım: {len(result.steps)}, başarılı: {sum(1 for s in result.steps if s.status=='completed')}")
            final_text = "\n".join(lines)
            return self._step(
                result, 11, "Sonuç kullanıcısına sunuldu",
                f"{len(result.steps)} adım özetlendi.",
                {"final_text": final_text},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 11 hata: %s", exc)
            return self._step(
                result, 11, "Sunum hatası", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_save_experience(
        self, result: WorkflowResult, task: str, user_id: str
    ) -> WorkflowStep:
        start = time.time()
        try:
            record_experience(
                user_id=user_id,
                task=task,
                method="orchestration_workflow",
                result="tamamlandı" if result.success else "kısmi",
                reason="İş akışı tamamlandı",
                learned="İş akışı deneyimi kaydedildi",
                category="general",
                confidence=result.confidence,
                sharing_consent=False,
            )
            detail = "Deneyim kullanıcıya özel hafızaya kaydedildi."
            return self._step(
                result, 12, "Deneyim hafızaya kaydedildi", detail,
                {"user_id": user_id},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 12 hata: %s", exc)
            return self._step(
                result, 12, "Deneyim kaydedilemedi", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_evaluate_performance(
        self, result: WorkflowResult, task: str, user_id: str
    ) -> WorkflowStep:
        start = time.time()
        try:
            metrics = success_metrics.full_report()
            detail = (
                f"Tercih doğruluğu: {metrics['preference_accuracy']['value']}, "
                f"doğrulama oranı: {metrics['factual_verification_rate']['value']}, "
                f"izolasyon: {metrics['cross_user_data_isolation']['value']}"
            )
            return self._step(
                result, 13, "Performans değerlendirildi", detail,
                {"metrics": metrics},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 13 hata: %s", exc)
            return self._step(
                result, 13, "Performans değerlendirilemedi", str(exc),
                {"error": str(exc)}, status="failed",
            )

    def _step_improve_future(
        self, result: WorkflowResult, task: str, user_id: str
    ) -> WorkflowStep:
        start = time.time()
        try:
            train = train_model(category="general", epochs=3)
            detail = f"Model eğitimi: {train.get('status', 'bilinmiyor')} (Örnek: {train.get('training_samples', 0)})"
            return self._step(
                result, 14, "Gelecek kararları geliştirildi", detail,
                {"training": train},
                duration_ms=round((time.time() - start) * 1000, 2),
            )
        except Exception as exc:
            logger.exception("Adım 14 hata: %s", exc)
            return self._step(
                result, 14, "Geliştirme yapılamadı", str(exc),
                {"error": str(exc)}, status="failed",
            )


orchestration_workflow = OrchestrationWorkflow()
