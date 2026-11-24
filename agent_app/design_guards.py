"""
EMOG Design Guards — Bölüm 18 (Neural Core Tasarım İlkeleri)

Sistemin sıradan bir LLM arayüzüne dönüşmesini önlemek için çalışma zamanında
uygulanan koruma kuralları. Her kuralı ihlal eden işlem engellenir veya
uyarıyla işaretlenir.

10 Tasarım İlkesi:
 1. Ana modelin cevabı nihai karar değildir.
 2. Her görevde yalnızca tek bir çözüm yolu araştırılmaz.
 3. Başarısız yöntemler ve nedenleri kaydedilir.
 4. Kullanıcıların kişisel hafızaları birbirinden ayrılır.
 5. Ortak öğrenme yalnızca izinli ve temizlenmiş verilerden yapılır.
 6. Yeni yöntemler kanıt ve deneylerle değerlendirilir.
 7. Model ağırlıkları kontrolsüz şekilde güncellenmez.
 8. Sistem kendi performansını ölçer.
 9. Harici model sağlayıcısı değiştirilebilir.
 10. Bilgisayar işlemleri izin ve sandbox kontrollerine tabidir.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import json as _json

logger = logging.getLogger(__name__)


@dataclass
class GuardViolation:
    principle: str
    principle_no: int
    message: str
    blocked: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DesignGuards:
    def __init__(self):
        self.violations: List[GuardViolation] = []
        self.violations_path = Path.home() / ".emog" / "design_guards" / "violations.json"
        self.violations_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        try:
            raw = _json.loads(self.violations_path.read_text(encoding="utf-8"))
            self.violations = [GuardViolation(**d) for d in raw]
        except Exception:
            self.violations = []

    def _save(self):
        try:
            self.violations_path.write_text(
                _json.dumps([v.to_dict() for v in self.violations], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Design Guards: kayıt hatası: %s", exc)

    def _record(self, principle_no: int, principle: str, message: str, blocked: bool) -> GuardViolation:
        v = GuardViolation(
            principle=principle,
            principle_no=principle_no,
            message=message,
            blocked=blocked,
        )
        self.violations.append(v)
        self._save()
        return v

    def require_arbitration(
        self, model_text: str, user_message: str, user: Any,
        conversation: Any, context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from agent_app.response_arbitration import response_arbitrator

        arb = response_arbitrator.arbitrate(
            response_text=model_text,
            user_message=user_message,
            user=user,
            conversation=conversation,
            context=context or {},
        )
        if arb.decision == "REJECT":
            self._record(
                1, "Ana model cevabi nihai karar degildir",
                f"Model cevabi reddedildi: {arb.reason}", True,
            )
            return {"allowed": False, "decision": arb.decision, "reason": arb.reason,
                    "final_text": arb.final_text}
        if arb.decision == "REGENERATE":
            self._record(
                1, "Ana model cevabi nihai karar degildir",
                "Model cevabi yeniden uretilmesi gerekti", False,
            )
            return {"allowed": False, "decision": arb.decision, "reason": arb.reason,
                    "final_text": arb.final_text, "needs_regenerate": True}
        return {"allowed": True, "decision": arb.decision, "reason": arb.reason,
                "final_text": arb.final_text}

    def require_multiple_hypotheses(self, hypotheses: List[Any]) -> Dict[str, Any]:
        count = len(hypotheses or [])
        if count < 2:
            self._record(
                2, "Tek bir cozum yolu arastirilmaz",
                f"Sadece {count} yontem onerildi; en az 2 gerekli", False,
            )
            return {"ok": False, "reason": "Coklu hipotez gerekli", "count": count}
        return {"ok": True, "count": count}

    def record_failed_method(
        self, user_id: str, task: str, method: str, reason: str,
    ) -> str:
        from agent_app.learning_engine import record_experience
        return record_experience(
            user_id=user_id, task=task, method=method, result="başarısız",
            reason=reason, learned="Bu yöntem başarısız oldu, alternatif ara",
            category="general", confidence=0.9,
        )

    def check_isolation(
        self, owner_user_id: str, viewer_user_id: str,
        task_type: str, owner_data_visible: bool,
    ) -> Dict[str, Any]:
        from agent_app.success_metrics import success_metrics
        if owner_data_visible:
            self._record(
                4, "Kullanici kisisel hafiza ayrilmalidir",
                f"Kullanici {viewer_user_id}, kullanici {owner_user_id} verisini gördü", True,
            )
            success_metrics.record_isolation(
                owner_user_id=owner_user_id, viewer_user_id=viewer_user_id,
                task_type=task_type, owner_data_visible=True,
            )
            return {"isolated": False, "violation": True}
        success_metrics.record_isolation(
            owner_user_id=owner_user_id, viewer_user_id=viewer_user_id,
            task_type=task_type, owner_data_visible=False,
        )
        return {"isolated": True, "violation": False}

    def allow_shared_learning(
        self, sharing_consent: bool, context: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not sharing_consent:
            self._record(
                5, "Ortak ogrenme izni ve temizlenmis verilerden",
                "Paylaşım izni yok; ortak öğrenme engellendi", True,
            )
            return {"allowed": False, "reason": "Izinti yok"}
        from agent_app.personal_learning import personal_learning
        clean = personal_learning._anonymize_context(context)
        return {"allowed": True, "clean_context": clean}

    def require_verification(
        self, method: str, verified: bool, confidence: float,
    ) -> Dict[str, Any]:
        if not verified:
            self._record(
                6, "Yeni yontemler kanıt ve deneylerle degerlendirilir",
                f"Yontem '{method}' dogrulanmamis", False,
            )
            return {"verified": False, "reason": "Kanit/deney gerekli"}
        return {"verified": True, "confidence": confidence}

    def require_rollback_gate(
        self, improvement: float, threshold: float = 0.05,
    ) -> Dict[str, Any]:
        if improvement < threshold:
            self._record(
                7, "Model agirliklari kontrolsuz guncellenmez",
                f"Gelisme yetersiz: {improvement:.4f} < esik {threshold}", True,
            )
            return {"allowed": False, "reason": "Geri alma tetiklendi",
                    "improvement": improvement}
        return {"allowed": True, "improvement": improvement}

    def record_performance(
        self, task_type: str, success: bool, latency_ms: float,
        cost_usd: float, user_id: str, agent_name: Optional[str] = None,
        model_name: Optional[str] = None, method: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> str:
        from agent_app.self_evolution import performance_tracker
        return performance_tracker.record(
            task_type=task_type, success=success, latency_ms=latency_ms,
            cost_usd=cost_usd, user_id=user_id, agent_name=agent_name,
            model_name=model_name, method=method, error_type=error_type,
        )

    def switch_provider(
        self, new_provider: str,
    ) -> Dict[str, Any]:
        from agent_app.model_gateway import model_gateway
        providers = model_gateway.active_providers()
        if new_provider not in providers:
            self._record(
                9, "Harici model saglayicisi degistirilebilir",
                f"Saglayici '{new_provider}' aktif degil: {providers}", False,
            )
            return {"allowed": False, "reason": "Saglayici aktif degil",
                    "available": providers}
        return {"allowed": True, "provider": new_provider, "available": providers}

    def require_sandbox(
        self, tool_name: str, arguments: Dict[str, Any], user: Any,
    ) -> Dict[str, Any]:
        from agent_app.tool_engine import tool_engine
        tr = tool_engine.execute(tool_name, arguments, user=user)
        if not tr.ok:
            self._record(
                10, "Bilgisayar islemleri izin ve sandbox kontrolunde",
                f"Arac '{tool_name}' hatasi: {tr.error}", True,
            )
        return {"ok": tr.ok, "tool": tr.tool, "error": tr.error,
                "approval_required": tr.approval_required}

    def report(self) -> Dict[str, Any]:
        by_principle: Dict[int, int] = {}
        for v in self.violations:
            by_principle[v.principle_no] = by_principle.get(v.principle_no, 0) + 1
        return {
            "total_violations": len(self.violations),
            "by_principle": by_principle,
            "violations": [v.to_dict() for v in self.violations],
        }


design_guards = DesignGuards()
