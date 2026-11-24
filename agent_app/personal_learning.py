"""
EMOG Personal Learning — Kişiye Özel Öğrenme (Bölüm 9)

İki ayrı öğrenme alanı:
  PRIVATE MEMORY  -> Kullanıcıya özel tercihler, önceki konuşmalar, başarılı/başarısız yöntemler
  SHARED LEARNING -> Genel problem çözme deneyimleri (sadece kullanıcı izniyle, anonimleştirilmiş)

Önemli kural: Yalnızca kullanıcı kimliğini silmek yeterli anonimleştirme değildir;
kişisel bilgiler (isim, e-posta, IP, telefon vb.) ayrıca temizlenir. Paylaşım için açık
kullanıcı izni (sharing_consent) gerekir.

Kullanıcıya özel profil vektörü ve karar parametreleri:
  θ_u = θ_global + Δθ_u
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    user_id: str
    preferences: Dict[str, Any] = field(default_factory=dict)
    successful_strategies: Dict[str, str] = field(default_factory=dict)
    failed_methods: Dict[str, List[str]] = field(default_factory=dict)
    conversation_learnings: List[Dict[str, Any]] = field(default_factory=list)
    decision_params: Dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    experience_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        return cls(
            user_id=data.get("user_id", ""),
            preferences=data.get("preferences", {}),
            successful_strategies=data.get("successful_strategies", {}),
            failed_methods=data.get("failed_methods", {}),
            conversation_learnings=data.get("conversation_learnings", []),
            decision_params=data.get("decision_params", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            experience_count=data.get("experience_count", 0),
        )


@dataclass
class SharedLearningRecord:
    record_id: str
    category: str
    task_type: str
    method: str
    result: str
    reason: str
    learned: str
    context_summary: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SharedLearningRecord":
        return cls(
            record_id=data.get("record_id", str(uuid.uuid4())),
            category=data.get("category", ""),
            task_type=data.get("task_type", ""),
            method=data.get("method", ""),
            result=data.get("result", ""),
            reason=data.get("reason", ""),
            learned=data.get("learned", ""),
            context_summary=data.get("context_summary", {}),
            created_at=data.get("created_at", time.time()),
            usage_count=data.get("usage_count", 0),
        )


class PersonalLearningManager:
    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or Path.home() / ".emog" / "personal_learning")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.profiles_path = self.storage_dir / "user_profiles.json"
        self.shared_path = self.storage_dir / "shared_learning.json"

        self.profiles: Dict[str, UserProfile] = self._load_profiles()
        self.shared_learning: List[SharedLearningRecord] = self._load_shared()

    def _load_profiles(self) -> Dict[str, UserProfile]:
        try:
            raw = json.loads(self.profiles_path.read_text(encoding="utf-8"))
            return {uid: UserProfile.from_dict(d) for uid, d in raw.items()}
        except Exception:
            return {}

    def _save_profiles(self) -> None:
        data = {uid: p.to_dict() for uid, p in self.profiles.items()}
        self.profiles_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def get_profile(self, user_id: str) -> UserProfile:
        if user_id not in self.profiles:
            self.profiles[user_id] = UserProfile(user_id=user_id)
            self._save_profiles()
        return self.profiles[user_id]

    def update_preference(self, user_id: str, key: str, value: Any) -> None:
        profile = self.get_profile(user_id)
        profile.preferences[key] = value
        profile.updated_at = time.time()
        self._save_profiles()
        logger.info("PersonalLearning: preference güncellendi user=%s key=%s", user_id, key)

    def add_successful_strategy(self, user_id: str, category: str, strategy: str) -> None:
        profile = self.get_profile(user_id)
        profile.successful_strategies[category] = strategy
        profile.experience_count += 1
        profile.updated_at = time.time()
        self._save_profiles()

    def add_failed_method(self, user_id: str, category: str, method: str) -> None:
        profile = self.get_profile(user_id)
        failed = profile.failed_methods.setdefault(category, [])
        if method not in failed:
            failed.append(method)
        profile.experience_count += 1
        profile.updated_at = time.time()
        self._save_profiles()

    def add_conversation_learning(
        self, user_id: str, category: str, learning: str, context: Dict[str, Any]
    ) -> None:
        profile = self.get_profile(user_id)
        profile.conversation_learnings.append(
            {
                "category": category,
                "learning": learning,
                "context": context,
                "timestamp": time.time(),
            }
        )
        profile.conversation_learnings = profile.conversation_learnings[-10:]
        profile.experience_count += 1
        profile.updated_at = time.time()
        self._save_profiles()

    def update_decision_param(self, user_id: str, param: str, value: float) -> None:
        profile = self.get_profile(user_id)
        profile.decision_params[param] = value
        profile.updated_at = time.time()
        self._save_profiles()

    def get_decision_params(self, user_id: str) -> Dict[str, float]:
        return self.get_profile(user_id).decision_params

    def get_private_summary(self, user_id: str) -> Dict[str, Any]:
        profile = self.get_profile(user_id)
        return {
            "user_id": user_id,
            "preferences": profile.preferences,
            "successful_strategies": profile.successful_strategies,
            "failed_methods": profile.failed_methods,
            "conversation_learnings_count": len(profile.conversation_learnings),
            "decision_params": profile.decision_params,
            "experience_count": profile.experience_count,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }

    _PII_PATTERNS = [
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        re.compile(r"\b(?:isim|ad|soyad|kullanıcı adı|username)\s*[:=]\s*(\S+)", re.I),
        re.compile(r"\b(?:tc|kimlik|tcno|identity)\s*[:=]\s*(\S+)", re.I),
    ]

    @staticmethod
    def _anonymize_context(context: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        for k, v in context.items():
            kl = k.lower()
            if any(p in kl for p in ("email", "mail", "phone", "tel", "ip", "address", "adres", "name", "isim", "username", "kullanıcı")):
                continue
            if isinstance(v, str):
                clean_value = v
                for pat in PersonalLearningManager._PII_PATTERNS:
                    clean_value = pat.sub("[REDACTED]", clean_value)
                clean[k] = clean_value
            else:
                clean[k] = v
        return clean

    def _load_shared(self) -> List[SharedLearningRecord]:
        try:
            raw = json.loads(self.shared_path.read_text(encoding="utf-8"))
            return [SharedLearningRecord.from_dict(d) for d in raw]
        except Exception:
            return []

    def _save_shared(self) -> None:
        data = [r.to_dict() for r in self.shared_learning]
        self.shared_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def add_shared_learning(
        self,
        category: str,
        task_type: str,
        method: str,
        result: str,
        reason: str,
        learned: str,
        context: Dict[str, Any],
        sharing_consent: bool = False,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        if not sharing_consent:
            logger.info("PersonalLearning: sharing_consent=False, paylaşılmayacak")
            return None

        clean_context = self._anonymize_context(context or {})
        record = SharedLearningRecord(
            record_id=str(uuid.uuid4()),
            category=category,
            task_type=task_type,
            method=method,
            result=result,
            reason=reason,
            learned=learned,
            context_summary=clean_context,
        )
        self.shared_learning.append(record)
        self._save_shared()
        logger.info("PersonalLearning: shared learning kaydedildi record=%s", record.record_id)
        return record.record_id

    def get_shared_learning(
        self, category: Optional[str] = None, task_type: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        results = self.shared_learning
        if category:
            results = [r for r in results if r.category == category]
        if task_type:
            results = [r for r in results if r.task_type == task_type]
        results = sorted(results, key=lambda r: r.usage_count, reverse=True)[:limit]
        for r in results:
            r.usage_count += 1
        self._save_shared()
        return [r.to_dict() for r in results]

    def get_shared_summary(self) -> Dict[str, Any]:
        categories = {}
        for r in self.shared_learning:
            categories[r.category] = categories.get(r.category, 0) + 1
        return {
            "total_records": len(self.shared_learning),
            "categories": categories,
        }

    def get_user_view(self, user_id: str) -> Dict[str, Any]:
        return {
            "private": self.get_private_summary(user_id),
            "shared_available": len(self.shared_learning),
        }


# Global instance
personal_learning = PersonalLearningManager()
