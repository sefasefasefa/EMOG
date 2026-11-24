"""
EMOG Deneyim Motoru (Experience Memory Module)

Üç tür bellek sistemi:
1. Episodic Memory: Yaşanan olaylar ve geçmiş görevler
2. Semantic Memory: Öğrenilen kavramlar ve ilişkiler  
3. Procedural Memory: Başarılı yöntemler, iş akışları ve stratejiler

Tüm veriler JSON formatında saklanır ve sürümleme ile korunur.
"""

from __future__ import annotations

import json
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExperienceRecord:
    """Tek bir deneyim kaydı (Experience Record)."""

    def __init__(
        self,
        user_id: str,
        task_id: str,
        category: str,
        context: Dict[str, Any],
        model_response: Dict[str, Any],
        feedback: Dict[str, Any],
        learned_rule: Dict[str, Any],
        confidence: float = 0.0,
        verification: str = "pending",
        sharing_consent: bool = False,
        experience_id: Optional[str] = None,
    ) -> None:
        self.experience_id = experience_id or str(uuid.uuid4())
        self.user_id = user_id
        self.task_id = task_id
        self.category = category
        self.context = context
        self.model_response = model_response
        self.feedback = feedback
        self.learned_rule = learned_rule
        self.confidence = confidence
        self.verification = verification
        self.sharing_consent = sharing_consent
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "user_id": self.user_id,
            "task_id": self.task_id,
            "category": self.category,
            "context": self.context,
            "model_response": self.model_response,
            "feedback": self.feedback,
            "learned_rule": self.learned_rule,
            "confidence": self.confidence,
            "verification": self.verification,
            "sharing_consent": self.sharing_consent,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperienceRecord":
        return cls(
            experience_id=data.get("experience_id", str(uuid.uuid4())),
            user_id=data.get("user_id", ""),
            task_id=data.get("task_id", ""),
            category=data.get("category", ""),
            context=data.get("context", {}),
            model_response=data.get("model_response", {}),
            feedback=data.get("feedback", {}),
            learned_rule=data.get("learned_rule", {}),
            confidence=data.get("confidence", 0.0),
            verification=data.get("verification", "pending"),
            sharing_consent=data.get("sharing_consent", False),
        )


class ExperienceMemory:
    """
    EMOG Deneyim Belleği.
    Üç tip bellek: Episodic, Semantic, Procedural.
    """

    def __init__(self, storage_dir: str | None = None) -> None:
        self.storage_dir = Path(storage_dir or Path.home() / ".emog" / "cognitive_memory")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.experiences_path = self.storage_dir / "experiences.json"
        self.semantic_path = self.storage_dir / "semantic.json"
        self.procedural_path = self.storage_dir / "procedural.json"

        # Bellek depoları
        self.experiences: List[ExperienceRecord] = self._load_experiences()
        self.semantic_memory: Dict[str, Any] = self._load_json(self.semantic_path, {})
        self.procedural_memory: Dict[str, Any] = self._load_json(self.procedural_path, {})

    def _load_json(self, path: Path, default: Any) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, path: Path, data: Any) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_experiences(self) -> List[ExperienceRecord]:
        raw = self._load_json(self.experiences_path, [])
        return [ExperienceRecord.from_dict(item) for item in raw]

    def _save_experiences(self) -> None:
        data = [exp.to_dict() for exp in self.experiences]
        self._save_json(self.experiences_path, data)

    def add_experience(
        self,
        user_id: str,
        task_id: str,
        category: str,
        context: Dict[str, Any],
        model_response: Dict[str, Any],
        feedback: Dict[str, Any],
        learned_rule: Dict[str, Any],
        confidence: float = 0.0,
        verification: str = "pending",
        sharing_consent: bool = False,
    ) -> str:
        """Yeni deneyim kaydı ekler ve döndürür."""
        record = ExperienceRecord(
            user_id=user_id,
            task_id=task_id,
            category=category,
            context=context,
            model_response=model_response,
            feedback=feedback,
            learned_rule=learned_rule,
            confidence=confidence,
            verification=verification,
            sharing_consent=sharing_consent,
        )
        self.experiences.append(record)
        self._save_experiences()
        return record.experience_id

    def get_experience(self, experience_id: str) -> Optional[ExperienceRecord]:
        for exp in self.experiences:
            if exp.experience_id == experience_id:
                return exp
        return None

    def get_experiences_by_user(self, user_id: str) -> List[ExperienceRecord]:
        return [exp for exp in self.experiences if exp.user_id == user_id]

    def get_experiences_by_category(self, category: str) -> List[ExperienceRecord]:
        return [exp for exp in self.experiences if exp.category == category]

    def add_semantic_fact(
        self,
        concept: str,
        definition: str,
        source: str = "system",
        confidence: float = 1.0,
    ) -> None:
        """Semantic belleğe yeni bir kavram ekler."""
        if "concepts" not in self.semantic_memory:
            self.semantic_memory["concepts"] = {}
        self.semantic_memory["concepts"][concept] = {
            "definition": definition,
            "source": source,
            "confidence": confidence,
            "timestamp": time.time(),
        }
        self._save_json(self.semantic_path, self.semantic_memory)

    def add_procedural_rule(
        self,
        rule_id: str,
        description: str,
        steps: List[str],
        confidence: float = 1.0,
    ) -> None:
        """Procedural belleğe yeni bir iş akışı kuralı ekler."""
        if "procedures" not in self.procedural_memory:
            self.procedural_memory["procedures"] = {}
        self.procedural_memory["procedures"][rule_id] = {
            "description": description,
            "steps": steps,
            "confidence": confidence,
            "timestamp": time.time(),
        }
        self._save_json(self.procedural_path, self.procedural_memory)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_experiences": len(self.experiences),
            "semantic_concepts": len(self.semantic_memory.get("concepts", {})),
            "procedural_rules": len(self.procedural_memory.get("procedures", {})),
            "categories": list(set(exp.category for exp in self.experiences)),
            "users": list(set(exp.user_id for exp in self.experiences)),
        }


# Global instance
cognitive_memory = ExperienceMemory()
