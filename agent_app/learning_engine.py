"""
EMOG Learning Engine — Öğrenme ve Model Geliştirme (Bölüm 10)

İki ayrı öğrenme mekanizması:
  10.1 Experience Learning -> Her deneyim kaydedilir, gruplanır, çelişki kontrolü yapılır,
                              eğitim verisi oluşturulur.
  10.2 Weight Learning     -> Sinir ağı ağırlıkları periodik yeniden eğitilir (gerişleme
                              testi ile), doğrudan her mesajdan sonra güncellenmez.

Eğitim kayıp fonksiyonu:
    L = L_preference + λ·L_ranking + μ·L_consistency
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from agent_app.cognitive_kernel import ck0, CK0NeuralNetwork
from agent_app.cognitive_memory import cognitive_memory, ExperienceRecord
from agent_app.personal_learning import personal_learning

logger = logging.getLogger(__name__)


@dataclass
class ExperienceEntry:
    experience_id: str
    user_id: str
    task: str
    method: str
    result: str
    reason: str
    learned: str
    category: str
    confidence: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class TrainingSnapshot:
    snapshot_id: str
    timestamp: float
    weights: Dict[str, List[float]]
    metrics: Dict[str, float]


class ExperienceLearner:
    def __init__(self):
        self.experiences: List[ExperienceEntry] = []

    def record_experience(
        self,
        user_id: str,
        task: str,
        method: str,
        result: str,
        reason: str,
        learned: str,
        category: str = "general",
        confidence: float = 0.0,
        sharing_consent: bool = False,
    ) -> str:
        exp_id = str(uuid.uuid4())
        entry = ExperienceEntry(
            experience_id=exp_id,
            user_id=user_id,
            task=task,
            method=method,
            result=result,
            reason=reason,
            learned=learned,
            category=category,
            confidence=confidence,
        )
        self.experiences.append(entry)

        cognitive_memory.add_experience(
            user_id=user_id,
            task_id=exp_id,
            category=category,
            context={"task": task, "method": method, "result": result},
            model_response={"reason": reason, "learned": learned},
            feedback={"result": result, "confidence": confidence},
            learned_rule={"method": method, "result": result, "learned": learned},
            confidence=confidence,
            verification="pending",
            sharing_consent=sharing_consent,
        )

        if result == "başarılı":
            personal_learning.add_successful_strategy(user_id, category, method)
        elif result == "başarısız":
            personal_learning.add_failed_method(user_id, category, method)
        personal_learning.add_conversation_learning(
            user_id, category, learned, {"task": task, "method": method, "result": result}
        )

        if sharing_consent:
            personal_learning.add_shared_learning(
                category=category,
                task_type=task,
                method=method,
                result=result,
                reason=reason,
                learned=learned,
                context={"task": task, "method": method},
                sharing_consent=True,
                user_id=user_id,
            )

        logger.info(
            "ExperienceLearner: deneyim kaydedildi id=%s result=%s method=%s",
            exp_id, result, method,
        )
        return exp_id

    def get_experiences_by_user(self, user_id: str) -> List[ExperienceEntry]:
        return [e for e in self.experiences if e.user_id == user_id]

    def get_experiences_by_category(self, category: str) -> List[ExperienceEntry]:
        return [e for e in self.experiences if e.category == category]

    def group_by_category(self) -> Dict[str, List[ExperienceEntry]]:
        groups: Dict[str, List[ExperienceEntry]] = {}
        for e in self.experiences:
            groups.setdefault(e.category, []).append(e)
        return groups

    def conflict_check(
        self, category: str
    ) -> Tuple[List[ExperienceEntry], List[ExperienceEntry]]:
        category_exps = self.get_experiences_by_category(category)
        method_results: Dict[str, set] = {}
        for e in category_exps:
            method_results.setdefault(e.method, set()).add(e.result)

        conflicting_methods = {
            m for m, results in method_results.items()
            if "başarılı" in results and "başarısız" in results
        }

        clean = [e for e in category_exps if e.method not in conflicting_methods]
        conflicts = [e for e in category_exps if e.method in conflicting_methods]
        return clean, conflicts

    def build_training_data(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        if category:
            clean, _ = self.conflict_check(category)
            exps = clean
        else:
            exps = []
            for cat in self.group_by_category().keys():
                c, _ = self.conflict_check(cat)
                exps.extend(c)

        training_data: List[Dict[str, Any]] = []
        for e in exps:
            input_vec = self._text_to_vector(e.task + " " + e.method)
            target_pref = 1.0 if e.result == "başarılı" else 0.0
            training_data.append({
                "experience_id": e.experience_id,
                "input": input_vec.tolist(),
                "target_preference": target_pref,
                "method": e.method,
                "result": e.result,
                "category": e.category,
            })
        return training_data

    @staticmethod
    def _text_to_vector(text: str, dim: int = 256) -> np.ndarray:
        vec = np.zeros(dim, dtype=np.float64)
        for i, ch in enumerate(text.lower()):
            vec[i % dim] += ord(ch) / 255.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec


class WeightLearner:
    def __init__(self, experience_learner: ExperienceLearner):
        self.experience_learner = experience_learner
        self.lambda_ranking = 0.5
        self.mu_consistency = 0.5
        self.snapshots_dir = Path.home() / ".emog" / "learning_engine" / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots: List[TrainingSnapshot] = self._load_snapshots()

    def _preference_loss(
        self, predictions: np.ndarray, targets: np.ndarray
    ) -> float:
        eps = 1e-12
        p = np.clip(predictions, eps, 1.0 - eps)
        return -float(np.mean(targets * np.log(p) + (1.0 - targets) * np.log(1.0 - p)))

    def _ranking_loss(
        self, predictions: np.ndarray, targets: np.ndarray, pairs: List[Tuple[int, int]]
    ) -> float:
        if not pairs:
            return 0.0
        losses = []
        for i, j in pairs:
            diff = predictions[j] - predictions[i]
            losses.append(math.log(1.0 + math.exp(diff)))
        return float(np.mean(losses))

    def _consistency_loss(
        self, predictions_a: np.ndarray, predictions_b: np.ndarray
    ) -> float:
        if len(predictions_a) == 0:
            return 0.0
        return float(np.mean((predictions_a - predictions_b) ** 2))

    def compute_total_loss(
        self,
        predictions: np.ndarray,
        targets: np.ndarray,
        pairs: List[Tuple[int, int]],
        consistency_pred_a: Optional[np.ndarray] = None,
        consistency_pred_b: Optional[np.ndarray] = None,
    ) -> float:
        l_pref = self._preference_loss(predictions, targets)
        l_rank = self._ranking_loss(predictions, targets, pairs)
        l_cons = 0.0
        if consistency_pred_a is not None and consistency_pred_b is not None:
            l_cons = self._consistency_loss(consistency_pred_a, consistency_pred_b)
        return l_pref + self.lambda_ranking * l_rank + self.mu_consistency * l_cons

    def _snapshot_weights(self) -> TrainingSnapshot:
        nn = ck0.nn
        weights = {
            "W1": nn.W1.tolist(),
            "b1": nn.b1.tolist(),
            "W2": nn.W2.tolist(),
            "b2": nn.b2.tolist(),
            "W3": nn.W3.tolist(),
            "b3": nn.b3.tolist(),
            "W4": nn.W4.tolist(),
            "b4": nn.b4.tolist(),
        }
        snapshot = TrainingSnapshot(
            snapshot_id=str(uuid.uuid4()),
            timestamp=time.time(),
            weights=weights,
            metrics={},
        )
        self.snapshots.append(snapshot)
        self._save_snapshots()
        return snapshot

    def _restore_weights(self, snapshot: TrainingSnapshot) -> None:
        nn = ck0.nn
        nn.W1 = np.array(snapshot.weights["W1"], dtype=np.float64)
        nn.b1 = np.array(snapshot.weights["b1"], dtype=np.float64)
        nn.W2 = np.array(snapshot.weights["W2"], dtype=np.float64)
        nn.b2 = np.array(snapshot.weights["b2"], dtype=np.float64)
        nn.W3 = np.array(snapshot.weights["W3"], dtype=np.float64)
        nn.b3 = np.array(snapshot.weights["b3"], dtype=np.float64)
        nn.W4 = np.array(snapshot.weights["W4"], dtype=np.float64)
        nn.b4 = np.array(snapshot.weights["b4"], dtype=np.float64)
        logger.info("WeightLearner: ağırlıklar geri yüklendi snapshot=%s", snapshot.snapshot_id)

    def _evaluate_performance(self, training_data: List[Dict[str, Any]]) -> float:
        if not training_data:
            return 1.0
        errors = []
        for item in training_data:
            x = np.array(item["input"], dtype=np.float64)
            y = float(item["target_preference"])
            out = ck0.nn.forward(x)
            pred = float(out["output"][0])
            errors.append((pred - y) ** 2)
        return float(np.mean(errors))

    def _retrain_once(self, training_data: List[Dict[str, Any]], lr: float = 0.01) -> float:
        if not training_data:
            return 0.0

        total_loss = 0.0
        pairs: List[Tuple[int, int]] = []

        by_category: Dict[str, List[Dict[str, Any]]] = {}
        for item in training_data:
            by_category.setdefault(item["category"], []).append(item)
        for cat_items in by_category.values():
            good = [i for i, it in enumerate(cat_items) if it["result"] == "başarılı"]
            bad = [i for i, it in enumerate(cat_items) if it["result"] == "başarısız"]
            for gi in good:
                for bi in bad:
                    pairs.append((gi, bi))

        for item in training_data:
            x = np.array(item["input"], dtype=np.float64)
            y = np.array([item["target_preference"]], dtype=np.float64)

            cache = ck0.nn.forward(x)
            pred = cache["output"]

            target = np.zeros(ck0.nn.decision_dim, dtype=np.float64)
            target[0] = item["target_preference"]

            loss = ck0.nn.backward(cache, target, lr=lr)
            total_loss += loss

        return total_loss / max(len(training_data), 1)

    def train_and_validate(
        self, category: Optional[str] = None, epochs: int = 5
    ) -> Dict[str, Any]:
        training_data = self.experience_learner.build_training_data(category=category)
        if not training_data:
            return {
                "status": "no_data",
                "message": "Eğitim verisi yok.",
                "trained": False,
            }

        before_error = self._evaluate_performance(training_data)
        snapshot = self._snapshot_weights()

        for epoch in range(epochs):
            self._retrain_once(training_data)

        after_error = self._evaluate_performance(training_data)
        improvement = before_error - after_error

        success_threshold = 0.05
        if improvement >= success_threshold:
            ck0.nn.save(ck0.model_path)
            ck0._save_all()
            status = "published"
            message = (
                f"Yeni model yayımlandı. Hata: {before_error:.4f} -> {after_error:.4f} "
                f"(gelişim: {improvement:.4f})"
            )
        else:
            self._restore_weights(snapshot)
            status = "rolled_back"
            message = (
                f"Geri alın. Eğitim iyileştirme yetersiz: {before_error:.4f} -> "
                f"{after_error:.4f} (gelişim: {improvement:.4f}, eşik: {success_threshold})"
            )

        report = {
            "status": status,
            "message": message,
            "trained": status == "published",
            "category": category or "all",
            "training_samples": len(training_data),
            "epochs": epochs,
            "before_error": round(before_error, 4),
            "after_error": round(after_error, 4),
            "improvement": round(improvement, 4),
            "snapshot_id": snapshot.snapshot_id,
            "loss_weights": {
                "preference": 1.0,
                "ranking": self.lambda_ranking,
                "consistency": self.mu_consistency,
            },
        }
        logger.info("WeightLearner: %s", message)
        return report

    def _save_snapshots(self) -> None:
        data = [asdict(s) for s in self.snapshots[-20:]]
        path = self.snapshots_dir / "snapshots.json"
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("WeightLearner: snapshot kaydetme hatası: %s", exc)

    def _load_snapshots(self) -> List[TrainingSnapshot]:
        path = self.snapshots_dir / "snapshots.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return [TrainingSnapshot(**d) for d in raw]
        except Exception:
            return []


experience_learner = ExperienceLearner()
weight_learner = WeightLearner(experience_learner)


def record_experience(
    user_id: str,
    task: str,
    method: str,
    result: str,
    reason: str,
    learned: str,
    category: str = "general",
    confidence: float = 0.0,
    sharing_consent: bool = False,
) -> str:
    return experience_learner.record_experience(
        user_id=user_id, task=task, method=method, result=result, reason=reason,
        learned=learned, category=category, confidence=confidence,
        sharing_consent=sharing_consent,
    )


def train_model(category: Optional[str] = None, epochs: int = 5) -> Dict[str, Any]:
    return weight_learner.train_and_validate(category=category, epochs=epochs)
