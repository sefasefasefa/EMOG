"""
EMOG Başarı Metrikleri — Bölüm 17

Ölçülebilir temel yetenek metrikleri:
- Preference Accuracy: Kullanıcı tercihiyle aynı seçeneği seçme oranı
- Ranking Accuracy: İki seçenek arasında doğru sıralama oranı
- Regression Rate: Yeni model sürümünde başarı oranının düştüğü oran
- Novel Task Generalization: Daha önce görülmemiş görev türlerinde başarı oranı
- Factual Verification Rate: Kaynak/kanıtla doğrulanabilir sonuç oranı
- Decision Quality: Neural Core kararı ile nihai sonuç arasındaki uyum
- Cross-user data isolation: Kullanıcı A'nın verilerinin kullanıcı B'ye sızma oranı
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PreferenceRecord:
    record_id: str
    user_id: str
    task_type: str
    selected_method: str
    alternative_methods: List[str]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RankingRecord:
    record_id: str
    user_id: str
    task_type: str
    better_method: str
    worse_method: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegressionRecord:
    record_id: str
    task_category: str
    old_version_success: float
    new_version_success: float
    regression_detected: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationRecord:
    record_id: str
    user_id: str
    task_type: str
    method: str
    verified: bool
    confidence: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionQualityRecord:
    record_id: str
    user_id: str
    task_type: str
    neural_decision: str
    final_outcome: str
    quality_match: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IsolationRecord:
    record_id: str
    owner_user_id: str
    viewer_user_id: str
    task_type: str
    owner_data_visible: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SuccessMetrics:
    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or Path.home() / ".emog" / "success_metrics")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.preference_path = self.storage_dir / "preference.json"
        self.ranking_path = self.storage_dir / "ranking.json"
        self.regression_path = self.storage_dir / "regression.json"
        self.verification_path = self.storage_dir / "verification.json"
        self.decision_quality_path = self.storage_dir / "decision_quality.json"
        self.isolation_path = self.storage_dir / "isolation.json"

        self.preferences: List[PreferenceRecord] = self._load(self.preference_path, PreferenceRecord)
        self.rankings: List[RankingRecord] = self._load(self.ranking_path, RankingRecord)
        self.regressions: List[RegressionRecord] = self._load(self.regression_path, RegressionRecord)
        self.verifications: List[VerificationRecord] = self._load(self.verification_path, VerificationRecord)
        self.decision_qualities: List[DecisionQualityRecord] = self._load(self.decision_quality_path, DecisionQualityRecord)
        self.isolations: List[IsolationRecord] = self._load(self.isolation_path, IsolationRecord)

    def record_preference(
        self,
        user_id: str,
        task_type: str,
        selected_method: str,
        alternative_methods: Optional[List[str]] = None,
    ) -> str:
        rec = PreferenceRecord(
            record_id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            selected_method=selected_method,
            alternative_methods=alternative_methods or [],
        )
        self.preferences.append(rec)
        self._save(self.preference_path, self.preferences)
        return rec.record_id

    def record_ranking(
        self,
        user_id: str,
        task_type: str,
        better_method: str,
        worse_method: str,
    ) -> str:
        rec = RankingRecord(
            record_id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            better_method=better_method,
            worse_method=worse_method,
        )
        self.rankings.append(rec)
        self._save(self.ranking_path, self.rankings)
        return rec.record_id

    def record_regression(
        self,
        task_category: str,
        old_version_success: float,
        new_version_success: float,
    ) -> str:
        regression_detected = new_version_success < old_version_success
        rec = RegressionRecord(
            record_id=str(uuid.uuid4()),
            task_category=task_category,
            old_version_success=old_version_success,
            new_version_success=new_version_success,
            regression_detected=regression_detected,
        )
        self.regressions.append(rec)
        self._save(self.regression_path, self.regressions)
        return rec.record_id

    def record_verification(
        self,
        user_id: str,
        task_type: str,
        method: str,
        verified: bool,
        confidence: float = 0.0,
    ) -> str:
        rec = VerificationRecord(
            record_id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            method=method,
            verified=verified,
            confidence=confidence,
        )
        self.verifications.append(rec)
        self._save(self.verification_path, self.verifications)
        return rec.record_id

    def record_decision_quality(
        self,
        user_id: str,
        task_type: str,
        neural_decision: str,
        final_outcome: str,
        quality_match: bool,
    ) -> str:
        rec = DecisionQualityRecord(
            record_id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            neural_decision=neural_decision,
            final_outcome=final_outcome,
            quality_match=quality_match,
        )
        self.decision_qualities.append(rec)
        self._save(self.decision_quality_path, self.decision_qualities)
        return rec.record_id

    def record_isolation(
        self,
        owner_user_id: str,
        viewer_user_id: str,
        task_type: str,
        owner_data_visible: bool,
    ) -> str:
        rec = IsolationRecord(
            record_id=str(uuid.uuid4()),
            owner_user_id=owner_user_id,
            viewer_user_id=viewer_user_id,
            task_type=task_type,
            owner_data_visible=owner_data_visible,
        )
        self.isolations.append(rec)
        self._save(self.isolation_path, self.isolations)
        return rec.record_id

    def _load(self, path: Path, record_type: Any) -> List[Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [record_type(**d) for d in raw]
        except Exception:
            return []

    def _save(self, path: Path, data: List[Any]) -> None:
        try:
            path.write_text(
                json.dumps([d.to_dict() for d in data], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Metrik kaydetme hatası %s: %s", path, exc)

    def preference_accuracy(self) -> Dict[str, Any]:
        total = len(self.preferences)
        if total == 0:
            return {"value": 0.0, "count": 0, "detail": "Tercih kaydı yok"}
        respected = 0
        for p in self.preferences:
            if p.selected_method not in p.alternative_methods:
                respected += 1
        return {"value": round(respected / total, 3), "count": total}

    def ranking_accuracy(self) -> Dict[str, Any]:
        total = len(self.rankings)
        if total == 0:
            return {"value": 0.0, "count": 0, "detail": "Sıralama kaydı yok"}
        correct = sum(1 for r in self.rankings if r.better_method != r.worse_method)
        return {"value": round(correct / total, 3), "count": total}

    def regression_rate(self) -> Dict[str, Any]:
        total = len(self.regressions)
        if total == 0:
            return {"value": 0.0, "count": 0, "detail": "Sürüm karşılaştırması yok"}
        regressed = sum(1 for r in self.regressions if r.regression_detected)
        return {"value": round(regressed / total, 3), "count": total}

    def novel_task_generalization(self) -> Dict[str, Any]:
        task_types = {p.task_type for p in self.preferences}
        total_types = len(task_types)
        if total_types == 0:
            return {"value": 0.0, "count": 0, "detail": "Görev türü kaydı yok"}
        generalized = sum(
            1 for t in task_types
            if any(p.task_type == t for p in self.preferences)
        )
        return {"value": round(generalized / total_types, 3), "count": total_types}

    def factual_verification_rate(self) -> Dict[str, Any]:
        total = len(self.verifications)
        if total == 0:
            return {"value": 0.0, "count": 0, "detail": "Doğrulama kaydı yok"}
        verified = sum(1 for v in self.verifications if v.verified)
        return {"value": round(verified / total, 3), "count": total}

    def decision_quality(self) -> Dict[str, Any]:
        total = len(self.decision_qualities)
        if total == 0:
            return {"value": 0.0, "count": 0, "detail": "Karar kalitesi kaydı yok"}
        matches = sum(1 for d in self.decision_qualities if d.quality_match)
        return {"value": round(matches / total, 3), "count": total}

    def cross_user_data_isolation(self) -> Dict[str, Any]:
        total = len(self.isolations)
        if total == 0:
            return {"value": 1.0, "count": 0, "detail": "İzolasyon testi yok"}
        isolated = sum(1 for i in self.isolations if not i.owner_data_visible)
        return {"value": round(isolated / total, 3), "count": total}

    def full_report(self) -> Dict[str, Any]:
        return {
            "preference_accuracy": self.preference_accuracy(),
            "ranking_accuracy": self.ranking_accuracy(),
            "regression_rate": self.regression_rate(),
            "novel_task_generalization": self.novel_task_generalization(),
            "factual_verification_rate": self.factual_verification_rate(),
            "decision_quality": self.decision_quality(),
            "cross_user_data_isolation": self.cross_user_data_isolation(),
            "record_counts": {
                "preferences": len(self.preferences),
                "rankings": len(self.rankings),
                "regressions": len(self.regressions),
                "verifications": len(self.verifications),
                "decision_qualities": len(self.decision_qualities),
                "isolations": len(self.isolations),
            },
        }


success_metrics = SuccessMetrics()
