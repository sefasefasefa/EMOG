"""
CK-0 - EMOG Bilişsel Çekirdek (Cognitive Kernel)

Yerel, küçük boyutlu bir karar ve tercih öğrenme modeli.
Ana NVIDIA modelinin ürettiği seçenekleri değerlendirir,
kullanıcı tercihlerini öğrenir ve tekrar etmemeyi sağlar.

Mimari (yaklaşık 43K parametre):
    Input (256) -> Hidden1 (128, ReLU) -> Hidden2 (64, ReLU)
    -> Preference Head (32) -> Decision Head (16, Softmax)

Girdi grupları:
    U: Kullanıcı tercihleri
    C: Mevcut görev ve bağlam
    E: Geçmiş deneyimler
    M: Ana model cevabı ve özellikleri
    R: Kurallar, öncelikler ve kısıtlamalar

X = concat(U, C, E, M, R) -> 256 boyutlu vektör

Y = Softmax(W3 * ReLU(W2 * ReLU(W1 * X + b1) + b2) + b3)
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from agent_app.cognitive_memory import ExperienceMemory, ExperienceRecord


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def _sigmoid_derivative(y: np.ndarray) -> np.ndarray:
    return y * (1.0 - y)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0, x)


def _relu_derivative(y: np.ndarray) -> np.ndarray:
    return (y > 0).astype(float)


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x, axis=-1, keepdims=True)
    ex = np.exp(x)
    return ex / np.sum(ex, axis=-1, keepdims=True)


class CK0NeuralNetwork:
    """Küçük çaplı karar ağı (CK-0)."""

    def __init__(
        self,
        input_dim: int = 256,
        hidden1: int = 128,
        hidden2: int = 64,
        pref_dim: int = 32,
        decision_dim: int = 16,
        seed: int = 42,
    ) -> None:
        self.input_dim = input_dim
        self.hidden1 = hidden1
        self.hidden2 = hidden2
        self.pref_dim = pref_dim
        self.decision_dim = decision_dim

        rng = np.random.default_rng(seed)
        limit1 = math.sqrt(2.0 / input_dim)
        limit2 = math.sqrt(2.0 / hidden1)
        limit3 = math.sqrt(2.0 / hidden2)
        limit4 = math.sqrt(2.0 / hidden2)
        limit5 = math.sqrt(2.0 / pref_dim)

        self.W1 = rng.normal(0, limit1, size=(input_dim, hidden1))
        self.b1 = np.zeros(hidden1, dtype=np.float64)
        self.W2 = rng.normal(0, limit2, size=(hidden1, hidden2))
        self.b2 = np.zeros(hidden2, dtype=np.float64)
        self.W3 = rng.normal(0, limit3, size=(hidden2, pref_dim))
        self.b3 = np.zeros(pref_dim, dtype=np.float64)
        self.W4 = rng.normal(0, limit4, size=(pref_dim, decision_dim))
        self.b4 = np.zeros(decision_dim, dtype=np.float64)

        # Eğitim parametreleri
        self.learning_rate = 0.01

    def forward(self, x: np.ndarray) -> Dict[str, np.ndarray]:
        """İleri yayılım."""
        h1 = _relu(x @ self.W1 + self.b1)
        h2 = _relu(h1 @ self.W2 + self.b2)
        p = _relu(h2 @ self.W3 + self.b3)
        logits = p @ self.W4 + self.b4
        y = _softmax(logits)

        return {
            "input": x,
            "h1": h1,
            "h2": h2,
            "pref": p,
            "logits": logits,
            "output": y,
        }

    def backward(
        self,
        cache: Dict[str, np.ndarray],
        target: np.ndarray,
        lr: float = 0.01,
    ) -> float:
        """Geri yayılım (tek örnek). 1D aktivasyonları 2D'ye şekillendirir."""
        # Tek örnekli 1D girdileri 2D satır vektörüne dönüştür (matmul uyumu için)
        x = cache["input"].reshape(1, -1)
        h1 = cache["h1"].reshape(1, -1)
        h2 = cache["h2"].reshape(1, -1)
        p = cache["pref"].reshape(1, -1)
        logits = cache["logits"].reshape(1, -1)
        y = cache["output"].reshape(1, -1)
        target = np.asarray(target).reshape(1, -1)

        # Output error (cross-entropy gradient simplifies to y - target)
        d_logits = (y - target) / y.shape[0]

        # W4, b4  -> W4: [32, 16]
        dW4 = p.T @ d_logits          # [32,1] @ [1,16] = [32,16]
        db4 = np.sum(d_logits, axis=0)  # [16]

        # Pref layer  -> W3: [64, 32]
        dp = d_logits @ self.W4.T     # [1,16] @ [16,32] = [1,32]
        dp = dp * _relu_derivative(p)  # [1,32]

        dW3 = h2.T @ dp               # [64,1] @ [1,32] = [64,32]
        db3 = np.sum(dp, axis=0)      # [32]

        # h2  -> W2: [128, 64]
        dh2 = dp @ self.W3.T          # [1,32] @ [32,64] = [1,64]
        dh2 = dh2 * _relu_derivative(h2)  # [1,64]

        dW2 = h1.T @ dh2              # [128,1] @ [1,64] = [128,64]
        db2 = np.sum(dh2, axis=0)     # [64]

        # h1  -> W1: [256, 128]
        dh1 = dh2 @ self.W2.T         # [1,64] @ [64,128] = [1,128]
        dh1 = dh1 * _relu_derivative(h1)  # [1,128]

        dW1 = x.T @ dh1               # [256,1] @ [1,128] = [256,128]
        db1 = np.sum(dh1, axis=0)     # [128]

        # Update
        self.W4 -= lr * dW4
        self.b4 -= lr * db4
        self.W3 -= lr * dW3
        self.b3 -= lr * db3
        self.W2 -= lr * dW2
        self.b2 -= lr * db2
        self.W1 -= lr * dW1
        self.b1 -= lr * db1

        # Loss
        loss = -float(np.sum(target * np.log(y + 1e-12)))
        return loss

    def save(self, path: str | Path) -> None:
        np.savez(
            path,
            W1=self.W1,
            b1=self.b1,
            W2=self.W2,
            b2=self.b2,
            W3=self.W3,
            b3=self.b3,
            W4=self.W4,
            b4=self.b4,
        )

    def load(self, path: str | Path) -> None:
        data = np.load(path)
        self.W1 = data["W1"]
        self.b1 = data["b1"]
        self.W2 = data["W2"]
        self.b2 = data["b2"]
        self.W3 = data["W3"]
        self.b3 = data["b3"]
        self.W4 = data["W4"]
        self.b4 = data["b4"]


class CognitiveKernel:
    """
    EMOG CK-0 Bilişsel Çekirdek.
    Ana LLM yanıtlarını değerlendirir, kullanıcı tercihlerini öğrenir
    ve en iyi eylemi seçer.
    """

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self.storage_dir = Path(storage_dir or Path.home() / ".emog" / "cognitive")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = self.storage_dir / "ck0_model.npz"
        self.pref_path = self.storage_dir / "preferences.json"

        self.nn = CK0NeuralNetwork()
        if self.model_path.exists():
            self.nn.load(self.model_path)

        self.memory = ExperienceMemory(self.storage_dir / "cognitive_memory")
        self.preferences: Dict[str, Any] = self._load_json(
            self.pref_path, {}
        )

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------
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

    def _save_all(self) -> None:
        self.nn.save(self.model_path)
        self.memory._save_experiences()
        self._save_json(self.pref_path, self.preferences)

    # ------------------------------------------------------------------
    # Feature encoding
    # ------------------------------------------------------------------
    def encode_input(
        self,
        user_prefs: Dict[str, Any],
        context: Dict[str, Any],
        experience: Dict[str, Any],
        model_response: Dict[str, Any],
        rules: Dict[str, Any],
    ) -> np.ndarray:
        """
        Girdi vektörünü (256 boyutlu) oluşturur.
        Her kaynak için 51 boyut ayrılmıştır (5 * 51 = 255 + 1 bias bias gibi).
        Gerçekte daha basit: her kategoriden özellik çıkarılıp 256'ye padlenir.
        """
        parts: List[float] = []

        def add_feature(val: float) -> None:
            parts.append(float(val))

        def encode_dict(d: Dict[str, Any], size: int) -> List[float]:
            vals: List[float] = []
            for k, v in d.items():
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                elif isinstance(v, bool):
                    vals.append(1.0 if v else 0.0)
                elif isinstance(v, str):
                    # Basit hash tabanlı sayısal représente
                    vals.append(float(hash(v) % 1000) / 1000.0)
                elif isinstance(v, list):
                    vals.append(float(len(v)) / 100.0)
                else:
                    vals.append(0.0)
            # Pad or truncate
            if len(vals) < size:
                vals.extend([0.0] * (size - len(vals)))
            return vals[:size]

        # Kategori başına 50 boyut = 5 * 50 = 250, geri kalan 6 bias/ekstra
        u = encode_dict(user_prefs, 50)
        c = encode_dict(context, 50)
        e = encode_dict(experience, 50)
        m = encode_dict(model_response, 50)
        r = encode_dict(rules, 50)

        x = u + c + e + m + r
        # Tam 256'ye tamamla
        if len(x) < 256:
            x.extend([0.0] * (256 - len(x)))
        return np.array(x[:256], dtype=np.float64)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def decide(
        self,
        user_prefs: Dict[str, Any],
        context: Dict[str, Any],
        experience: Dict[str, Any],
        model_response: Dict[str, Any],
        rules: Dict[str, Any],
        options: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Verilen seçeneklerden en iyi olanı seçer.
        options: [{"id":..., "text":..., "features":{...}}, ...]
        """
        if not options:
            return {"selected": None, "reason": "no_options", "scores": []}

        # Geçmiş deneyimleri memory'den al ve経騆 olarak kullan
        past_experiences = self.memory.get_experiences_by_category(
            context.get("category", "general")
        )
        past_exp_list = [e.to_dict() for e in past_experiences[-5:]]  # Son 5 deneyim

        # Seçenek özelliklerini girdiye ekle
        for opt in options:
            if "features" not in opt:
                opt["features"] = {}

        # Tüm seçenekler için skor hesapla
        scores: List[float] = []
        for opt in options:
            merged_ctx = dict(context)
            merged_ctx.update(opt.get("features", {}))
            x = self.encode_input(
                user_prefs, merged_ctx, past_experiences, model_response, rules
            )
            forward = self.nn.forward(x)
            score = float(forward["output"][0])  # İlk sınıf = seçilme olasılığı
            scores.append(score)

        # En yüksek skora sahip seçenek
        best_idx = int(np.argmax(scores))
        best = options[best_idx]

        # Deneyim kaydı - CognitiveMemory'ye kaydet
        exp_record = {
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "user_prefs": user_prefs,
            "context": context,
            "experience": past_experiences,
            "model_response": model_response,
            "rules": rules,
            "selected_option": {
                "id": best.get("id"),
                "text": best.get("text", ""),
                "score": scores[best_idx],
            },
            "all_scores": [float(s) for s in scores],
        }
        self.memory.add_experience(
            user_id=user_prefs.get("user_id", "anonymous"),
            task_id=context.get("task_id", str(uuid.uuid4())),
            category=context.get("category", "general"),
            context=context,
            model_response=model_response,
            feedback={"status": "pending", "score": scores[best_idx]},
            learned_rule={"selected_option": best.get("id"), "score": scores[best_idx]},
            confidence=scores[best_idx],
        )

        return {
            "selected": best,
            "selected_index": best_idx,
            "score": scores[best_idx],
            "scores": scores,
            "all_options": options,
            "reason": f"Seçilen seçenek skoru: {scores[best_idx]:.3f}",
        }

    def learn_from_feedback(
        self,
        selected_index: int,
        reward: float,  # 0.0 (kötü) ile 1.0 (iyi) arasında
        scores: List[float],
    ) -> float:
        """
        Kullanıcı geri bildirimiyle modeli günceller.
        reward: 1.0 = çok iyi, 0.0 = kötü, 0.5 = nötr
        """
        if not self.memory.experiences:
            return 0.0

        last_exp = self.memory.experiences[-1]
        # One-hot hedef vektörü
        target = np.zeros(self.nn.decision_dim, dtype=np.float64)
        target[selected_index % self.nn.decision_dim] = reward

        # Son forward'ı yeniden hesaplamak için son deneyimi tekrar kodlayalım
        x = self.encode_input(
            last_exp["user_prefs"],
            last_exp["context"],
            last_exp["experience"],
            last_exp["model_response"],
            last_exp["rules"],
        )
        cache = self.nn.forward(x)
        loss = self.nn.backward(cache, target, lr=self.nn.learning_rate)

        # Deneyim kaydına güncelle
        last_exp["feedback_reward"] = reward
        last_exp["feedback_time"] = time.time()
        self.memory._save_experiences()

        return float(loss)

    def learn_preference_from_text(
        self, message: str
    ) -> Dict[str, Any]:
        """
        Kullanıcının mesajından tercihleri çıkarır ve saklar.
        Örneğin: 'benimle uzun konuşma' -> {'prefers_long_response': True}
                 'kısa yanıt ver' -> {'prefers_short_response': True}
                 'benimle İngilizce konuşma' -> {'prefers_language': 'english'}
        """
        msg_lower = (message or "").strip().lower()
        learned: Dict[str, Any] = {}

        # Tercih kalıpları
        preference_patterns: List[Dict[str, Any]] = [
            # Dil tercihi
            {
                "pattern": ["türkçe konuş", "türkçe cevapla", "türkçe yaz"],
                "key": "preferred_language",
                "value": "turkish",
            },
            {
                "pattern": ["english konuş", "english cevapla", "english yaz"],
                "key": "preferred_language",
                "value": "english",
            },
            # Uzunluk tercihi
            {
                "pattern": ["kısa cevap", "kısa konuş", "özetle", "kısaca"],
                "key": "preferred_response_length",
                "value": "short",
            },
            {
                "pattern": ["uzun cevap", "uzun konuş", "detaylı", "ayrıntılı"],
                "key": "preferred_response_length",
                "value": "long",
            },
            # Stil tercihi
            {
                "pattern": ["samimi konuş", "arkadaşlık tonunda"],
                "key": "preferred_style",
                "value": "friendly",
            },
            {
                "pattern": ["resmi konuş", "profesyonel"],
                "key": "preferred_style",
                "value": "formal",
            },
            # İçerik tercihi
            {
                "pattern": ["sadece gerçek", "gerçek bilgi", "doğru bilgi"],
                "key": "prefers_factual_info",
                "value": True,
            },
            {
                "pattern": ["yorum yap", "fikir ver", "analiz et"],
                "key": "prefers_analysis",
                "value": True,
            },
            # Reddedilen/istemediği şeyler
            {
                "pattern": ["kötü yanıt", "kötü cevap", "istemiyorum", "istemem"],
                "key": "disliked_response_style",
                "value": "generic",
            },
        ]

        for pattern_info in preference_patterns:
            patterns = pattern_info["pattern"]
            key = pattern_info["key"]
            value = pattern_info["value"]

            for p in patterns:
                if p in msg_lower:
                    self.preferences[key] = value
                    self._save_all()
                    learned[key] = value
                    break  # Bu kategoride bir kez sadece

        # Genel "istiyorum / istemiyorum" kalıpları
        want_patterns = [
            ("benimle", "wants_interaction_with_me"),
            ("benim için", "wants_for_me"),
            ("bana", "wants_for_me"),
        ]

        for p in want_patterns:
            pattern_str = p[0] if isinstance(p, tuple) else p
            if pattern_str in msg_lower:
                key = "prefers_interaction"
                self.preferences[key] = True
                self._save_all()
                learned[key] = True

        return learned

    def get_preference_summary(self) -> Dict[str, Any]:
        return {
            "total_experiences": len(self.memory.experiences),
            "model_params": int(
                self.nn.W1.size
                + self.nn.b1.size
                + self.nn.W2.size
                + self.nn.b2.size
                + self.nn.W3.size
                + self.nn.b3.size
                + self.nn.W4.size
                + self.nn.b4.size
            ),
            "recent_rewards": [
                e.get("feedback_reward")
                for e in self.memory.experiences[-10:]
                if e.get("feedback_reward") is not None
            ],
            "stored_preferences": dict(self.preferences),
        }


# Global instance (kolay erişim için)

    def learn_preference_from_text(
        self, message: str
    ) -> Dict[str, Any]:
        """
        Kullanıcının mesajından tercihleri çıkarır ve saklar.
        Örneğin: 'benimle uzun konuşma' -> {'prefers_long_response': True}
                 'kısa yanıt ver' -> {'prefers_short_response': True}
                 'benimle İngilizce konuşma' -> {'prefers_language': 'english'}
        """
        msg_lower = (message or "").strip().lower()
        learned: Dict[str, Any] = {}

        # Tercih kalıpları
        preference_patterns: List[Dict[str, Any]] = [
            # Dil tercihi
            {
                "pattern": ["türkçe konuş", "türkçe cevapla", "türkçe yaz"],
                "key": "preferred_language",
                "value": "turkish",
            },
            {
                "pattern": ["english konuş", "english cevapla", "english yaz"],
                "key": "preferred_language",
                "value": "english",
            },
            # Uzunluk tercihi
            {
                "pattern": ["kısa cevap", "kısa konuş", "özetle", "kısaca"],
                "key": "preferred_response_length",
                "value": "short",
            },
            {
                "pattern": ["uzun cevap", "uzun konuş", "detaylı", "ayrıntılı"],
                "key": "preferred_response_length",
                "value": "long",
            },
            # Stil tercihi
            {
                "pattern": ["samimi konuş", "arkadaşlık tonunda"],
                "key": "preferred_style",
                "value": "friendly",
            },
            {
                "pattern": ["resmi konuş", "profesyonel"],
                "key": "preferred_style",
                "value": "formal",
            },
            # İçerik tercihi
            {
                "pattern": ["sadece gerçek", "gerçek bilgi", "doğru bilgi"],
                "key": "prefers_factual_info",
                "value": True,
            },
            {
                "pattern": ["yorum yap", "fikir ver", "analiz et"],
                "key": "prefers_analysis",
                "value": True,
            },
            # Reddedilen/istemediği şeyler
            {
                "pattern": ["kötü yanıt", "kötü cevap", "istemiyorum", "istemem"],
                "key": "disliked_response_style",
                "value": "generic",
            },
        ]

        for pattern_info in preference_patterns:
            patterns = pattern_info["pattern"]
            key = pattern_info["key"]
            value = pattern_info["value"]

            for p in patterns:
                if p in msg_lower:
                    self.preferences[key] = value
                    self._save_all()
                    learned[key] = value
                    break  # Bu kategoride bir kez sadece

        # Genel "istiyorum / istemiyorum" kalıpları
        want_patterns = [
            ("benimle", "wants_interaction_with_me"),
            ("benim için", "wants_for_me"),
            ("bana", "wants_for_me"),
        ]

        for p in want_patterns:
            pattern_str = p[0] if isinstance(p, tuple) else p
            if pattern_str in msg_lower:
                key = "prefers_interaction"
                self.preferences[key] = True
                self._save_all()
                learned[key] = True

        return learned


    def learn_preference_from_text(
        self, message: str
    ) -> Dict[str, Any]:
        """
        Kullanıcının mesajından tercihleri çıkarır ve saklar.
        Örneğin: 'benimle uzun konuşma' -> {'prefers_long_response': True}
                 'kısa yanıt ver' -> {'prefers_short_response': True}
                 'benimle İngilizce konuşma' -> {'prefers_language': 'english'}
        """
        msg_lower = (message or "").strip().lower()
        learned: Dict[str, Any] = {}

        # Tercih kalıpları
        preference_patterns: List[Dict[str, Any]] = [
            # Dil tercihi
            {
                "pattern": ["türkçe konuş", "türkçe cevapla", "türkçe yaz"],
                "key": "preferred_language",
                "value": "turkish",
            },
            {
                "pattern": ["english konuş", "english cevapla", "english yaz"],
                "key": "preferred_language",
                "value": "english",
            },
            # Uzunluk tercihi
            {
                "pattern": ["kısa cevap", "kısa konuş", "özetle", "kısaca"],
                "key": "preferred_response_length",
                "value": "short",
            },
            {
                "pattern": ["uzun cevap", "uzun konuş", "detaylı", "ayrıntılı"],
                "key": "preferred_response_length",
                "value": "long",
            },
            # Stil tercihi
            {
                "pattern": ["samimi konuş", "arkadaşlık tonunda"],
                "key": "preferred_style",
                "value": "friendly",
            },
            {
                "pattern": ["resmi konuş", "profesyonel"],
                "key": "preferred_style",
                "value": "formal",
            },
            # İçerik tercihi
            {
                "pattern": ["sadece gerçek", "gerçek bilgi", "doğru bilgi"],
                "key": "prefers_factual_info",
                "value": True,
            },
            {
                "pattern": ["yorum yap", "fikir ver", "analiz et"],
                "key": "prefers_analysis",
                "value": True,
            },
            # Reddedilen/istemediği şeyler
            {
                "pattern": ["kötü yanıt", "kötü cevap", "istemiyorum", "istemem"],
                "key": "disliked_response_style",
                "value": "generic",
            },
        ]

        for pattern_info in preference_patterns:
            patterns = pattern_info["pattern"]
            key = pattern_info["key"]
            value = pattern_info["value"]

            for p in patterns:
                if p in msg_lower:
                    self.preferences[key] = value
                    self._save_all()
                    learned[key] = value
                    break  # Bu kategoride bir kez sadece

        # Genel "istiyorum / istemiyorum" kalıpları
        want_patterns = [
            ("benimle", "wants_interaction_with_me"),
            ("benim için", "wants_for_me"),
            ("bana", "wants_for_me"),
        ]

        for p in want_patterns:
            pattern_str = p[0] if isinstance(p, tuple) else p
            if pattern_str in msg_lower:
                key = "prefers_interaction"
                self.preferences[key] = True
                self._save_all()
                learned[key] = True

        return learned


    def learn_preference_from_text(
        self, message: str
    ) -> Dict[str, Any]:
        """
        Kullanıcının mesajından tercihleri çıkarır ve saklar.
        Örneğin: 'benimle uzun konuşma' -> {'prefers_long_response': True}
                 'kısa yanıt ver' -> {'prefers_short_response': True}
                 'benimle İngilizce konuşma' -> {'prefers_language': 'english'}
        """
        msg_lower = (message or "").strip().lower()
        learned: Dict[str, Any] = {}

        # Tercih kalıpları
        preference_patterns: List[Dict[str, Any]] = [
            # Dil tercihi
            {
                "pattern": ["türkçe konuş", "türkçe cevapla", "türkçe yaz"],
                "key": "preferred_language",
                "value": "turkish",
            },
            {
                "pattern": ["english konuş", "english cevapla", "english yaz"],
                "key": "preferred_language",
                "value": "english",
            },
            # Uzunluk tercihi
            {
                "pattern": ["kısa cevap", "kısa konuş", "özetle", "kısaca"],
                "key": "preferred_response_length",
                "value": "short",
            },
            {
                "pattern": ["uzun cevap", "uzun konuş", "detaylı", "ayrıntılı"],
                "key": "preferred_response_length",
                "value": "long",
            },
            # Stil tercihi
            {
                "pattern": ["samimi konuş", "arkadaşlık tonunda"],
                "key": "preferred_style",
                "value": "friendly",
            },
            {
                "pattern": ["resmi konuş", "profesyonel"],
                "key": "preferred_style",
                "value": "formal",
            },
            # İçerik tercihi
            {
                "pattern": ["sadece gerçek", "gerçek bilgi", "doğru bilgi"],
                "key": "prefers_factual_info",
                "value": True,
            },
            {
                "pattern": ["yorum yap", "fikir ver", "analiz et"],
                "key": "prefers_analysis",
                "value": True,
            },
            # Reddedilen/istemediği şeyler
            {
                "pattern": ["kötü yanıt", "kötü cevap", "istemiyorum", "istemem"],
                "key": "disliked_response_style",
                "value": "generic",
            },
        ]

        for pattern_info in preference_patterns:
            patterns = pattern_info["pattern"]
            key = pattern_info["key"]
            value = pattern_info["value"]

            for p in patterns:
                if p in msg_lower:
                    self.preferences[key] = value
                    self._save_all()
                    learned[key] = value
                    break  # Bu kategoride bir kez sadece

        # Genel "istiyorum / istemiyorum" kalıpları
        want_patterns = [
            ("benimle", "wants_interaction_with_me"),
            ("benim için", "wants_for_me"),
            ("bana", "wants_for_me"),
        ]

        for p in want_patterns:
            pattern_str = p[0] if isinstance(p, tuple) else p
            if pattern_str in msg_lower:
                key = "prefers_interaction"
                self.preferences[key] = True
                self._save_all()
                learned[key] = True

        return learned

ck0 = CognitiveKernel()
