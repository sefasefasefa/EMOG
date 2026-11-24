#!/usr/bin/env python3
"""
EMOG Discovery Engine
Discovers, evaluates, and ranks analysis methods/tools for given tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid
import time
import json


@dataclass
class Hypothesis:
    """Represents a discovery hypothesis/method."""
    id: str
    name: str
    description: str
    data_source: str
    confidence: float
    cost: float
    data_quality: float
    legal_risk: float
    applicability_score: float = 0.0

    # Discovery Score components (4.5)
    novelty: float = 0.0
    feasibility: float = 0.0
    cost_advantage: float = 0.0
    verifiability: float = 0.0
    risk: float = 0.0
    discovery_score: float = 0.0

    def score(self) -> float:
        """Overall applicability score."""
        weights = {
            "confidence": 0.3,
            "data_quality": 0.3,
            "cost": 0.2,
            "legal_risk": 0.2,
        }
        cost_score = 1.0 - self.cost
        risk_score = 1.0 - self.legal_risk
        return (
            self.confidence * weights["confidence"] +
            self.data_quality * weights["data_quality"] +
            cost_score * weights["cost"] +
            risk_score * weights["legal_risk"]
        )

    def compute_discovery_score(self, weights: Dict[str, float]) -> float:
        """Compute discovery score: w1*N + w2*F + w3*C + w4*V - w5*R"""
        w1 = weights.get("novelty", 0.2)
        w2 = weights.get("feasibility", 0.2)
        w3 = weights.get("cost_advantage", 0.2)
        w4 = weights.get("verifiability", 0.2)
        w5 = weights.get("risk", 0.2)

        score = (
            w1 * self.novelty +
            w2 * self.feasibility +
            w3 * self.cost_advantage +
            w4 * self.verifiability -
            w5 * self.risk
        )
        return max(0.0, min(1.0, score))


@dataclass
class DiscoveryResult:
    """Discovery analysis result."""
    task_id: str
    query: str
    hypotheses: List[Hypothesis]
    best_hypothesis: Optional[Hypothesis]
    reasoning: str
    timestamp: float


class HypothesisGenerator:
    """Generates hypotheses/methods for a given task."""

    DEFAULT_METHODS: List[Dict[str, Any]] = [
        {
            "name": "Web Arama Motoru",
            "description": "Arama motoru indekslerinden veri toplama",
            "data_source": "web_search",
            "confidence": 0.7,
            "cost": 0.2,
            "data_quality": 0.6,
            "legal_risk": 0.2,
        },
        {
            "name": "Açık Veri Kaynakları",
            "description": "Açık veri portalları ve kamu veri setleri",
            "data_source": "open_data",
            "confidence": 0.8,
            "cost": 0.1,
            "data_quality": 0.8,
            "legal_risk": 0.1,
        },
        {
            "name": "Firma Rehberleri",
            "description": "Ticaret odası, firma rehberleri ve şirket kayıtları",
            "data_source": "business_directory",
            "confidence": 0.85,
            "cost": 0.3,
            "data_quality": 0.85,
            "legal_risk": 0.1,
        },
        {
            "name": "Kamu İşletme Kayıtları",
            "description": "Resmi kurum kayıtları ve lisans bilgileri",
            "data_source": "public_records",
            "confidence": 0.9,
            "cost": 0.4,
            "data_quality": 0.9,
            "legal_risk": 0.05,
        },
        {
            "name": "Web Sayfası Yapılandırılmış Veri",
            "description": "Schema.org, JSON-LD gibi yapılandırılmış web verileri",
            "data_source": "structured_web_data",
            "confidence": 0.75,
            "cost": 0.4,
            "data_quality": 0.7,
            "legal_risk": 0.2,
        },
        {
            "name": "Sektörel Veri Tabanı",
            "description": "Endüstriyel veri kaynakları ve sektör raporları",
            "data_source": "sector_database",
            "confidence": 0.8,
            "cost": 0.5,
            "data_quality": 0.85,
            "legal_risk": 0.1,
        },
        {
            "name": "API Entegrasyonu",
            "description": "Resmi API'ler üzerinden veri çekme",
            "data_source": "api_integration",
            "confidence": 0.85,
            "cost": 0.4,
            "data_quality": 0.9,
            "legal_risk": 0.1,
        },
        {
            "name": "Coğrafi Bilgi Sistemi (GIS)",
            "description": "Harita ve coğrafi veri kaynakları",
            "data_source": "gis",
            "confidence": 0.7,
            "cost": 0.6,
            "data_quality": 0.75,
            "legal_risk": 0.1,
        },
        {
            "name": "Otomatik Sınıflandırma",
            "description": "ML ile otomatik veri sınıflandırması",
            "data_source": "auto_classification",
            "confidence": 0.6,
            "cost": 0.7,
            "data_quality": 0.6,
            "legal_risk": 0.2,
        },
        {
            "name": "Sosyal Medya ve İnfluencer Analizi",
            "description": "Sosyal platformlarda trend, müşteri duygusu ve influencer takibi",
            "data_source": "social_media",
            "confidence": 0.65,
            "cost": 0.3,
            "data_quality": 0.6,
            "legal_risk": 0.25,
        },
        {
            "name": "E-ticaret Platformları",
            "description": "Amazon, Trendyol, Hepsiburada gibi satış ve ürün verileri",
            "data_source": "ecommerce_platforms",
            "confidence": 0.75,
            "cost": 0.35,
            "data_quality": 0.7,
            "legal_risk": 0.15,
        },
        {
            "name": "Kullanıcı Geri Bildirim ve İnceleme",
            "description": "Uygulama mağazaları, inceleme siteleri ve müşteri yorumları",
            "data_source": "user_reviews",
            "confidence": 0.7,
            "cost": 0.2,
            "data_quality": 0.65,
            "legal_risk": 0.1,
        },
        {
            "name": "Finansal ve Yatırım Verileri",
            "description": "Borsa, yatırım raporları ve finansal performans verileri",
            "data_source": "financial_data",
            "confidence": 0.8,
            "cost": 0.5,
            "data_quality": 0.85,
            "legal_risk": 0.1,
        },
        {
            "name": "Hukuki ve Uygunluk Kaynakları",
            "description": "KVKK, telif hakları, teslimat şartları ve mevzuat takibi",
            "data_source": "legal_compliance",
            "confidence": 0.85,
            "cost": 0.4,
            "data_quality": 0.9,
            "legal_risk": 0.05,
        },
        {
            "name": "Bulut Hizmet Sağlayıcıları",
            "description": "AWS, Azure, Google Cloud gibi altyapı ve barındırma fiyatları",
            "data_source": "cloud_providers",
            "confidence": 0.8,
            "cost": 0.45,
            "data_quality": 0.85,
            "legal_risk": 0.1,
        },
        {
            "name": "İlan ve İşveren Kaynakları",
            "description": "İş ilanları, yetenek pazarı ve personel maliyeti verileri",
            "data_source": "job_boards",
            "confidence": 0.75,
            "cost": 0.25,
            "data_quality": 0.75,
            "legal_risk": 0.1,
        },
        {
            "name": "Reklam ve Pazarlama Verileri",
            "description": "Google Ads, Meta Ads gibi reklam performansı ve müşteri edinme maliyeti",
            "data_source": "advertising_data",
            "confidence": 0.7,
            "cost": 0.4,
            "data_quality": 0.75,
            "legal_risk": 0.15,
        },
    ]

    def generate_hypotheses(
        self,
        task_description: str,
        context: Dict[str, Any],
        max_hypotheses: int = 5,
        excluded_hypotheses: Optional[List[str]] = None,
    ) -> List[Hypothesis]:
        """Generate hypotheses for the given task."""
        task_lower = task_description.lower()
        scored_methods: List[Hypothesis] = []

        for method in self.DEFAULT_METHODS:
            if method["data_source"] in (excluded_hypotheses or []):
                continue

            score = 0.0
            method_name = method["name"].lower()
            method_desc = method.get("description", "").lower()

            for word in task_lower.split():
                if word in method_name or word in method_desc:
                    score += 0.1

            # Genişletilmiş anahtar kelime eşleştirmesi: her alt görev türü için sağlayıcı önerisi
            if any(kw in task_lower for kw in ["müşteri", "musteri", "firma", "şirket", "company", "işletme"]):
                if method["data_source"] in ["business_directory", "public_records", "user_reviews"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["pazar", "market", "rekabet", "raki̇p", "rakipler", "competitor"]):
                if method["data_source"] in ["web_search", "sector_database", "social_media", "advertising_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["alternatif", "alternative", "mevcut çözüm", "pazar boşluğu"]):
                if method["data_source"] in ["web_search", "ecommerce_platforms", "sector_database"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["teknoloji", "technology", "yığın", "stack", "altyapı", "araç", "bulut", "cloud", "aws", "azure"]):
                if method["data_source"] in ["api_integration", "cloud_providers", "web_search", "structured_web_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["maliyet", "cost", "fiyat", "ütelik", "bütçe", "harcama", "finansal", "yatırım"]):
                if method["data_source"] in ["financial_data", "cloud_providers", "job_boards", "advertising_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["geliştir", "development", "mvp", "kodlama", "uygulama", "saas", "ürün"]):
                if method["data_source"] in ["api_integration", "cloud_providers", "job_boards", "web_search"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["test", "testi", "kalite", "hata", "bug"]):
                if method["data_source"] in ["api_integration", "web_search", "structured_web_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["satış", "sales", "müşteri edinme", "reklam", "pazarlama", "bul"]):
                if method["data_source"] in ["advertising_data", "social_media", "ecommerce_platforms", "business_directory"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["değerlendirme", "evaluation", "performans", "geri bildirim", "inceleme", "yorum"]):
                if method["data_source"] in ["user_reviews", "web_search", "financial_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["coğrafi", "harita", "konum", "yer", "map", "location", "gis"]):
                if method["data_source"] == "gis":
                    score += 0.3
            if any(kw in task_lower for kw in ["web", "site", "internet", "online"]):
                if method["data_source"] in ["web_search", "structured_web_data"]:
                    score += 0.3
            if any(kw in task_lower for kw in ["hukuki", "legal", "kvkk", "mevzuat", "uygunluk", "teslimat şartları"]):
                if method["data_source"] == "legal_compliance":
                    score += 0.3
            if any(kw in task_lower for kw in ["sosyal", "social", "trend", "influencer", "duygu"]):
                if method["data_source"] == "social_media":
                    score += 0.3

            score = min(score, 1.0)

            if score > 0.1:
                hypothesis = Hypothesis(
                    id=str(uuid.uuid4()),
                    name=method["name"],
                    description=method["description"],
                    data_source=method["data_source"],
                    confidence=method["confidence"],
                    cost=method["cost"],
                    data_quality=method["data_quality"],
                    legal_risk=method["legal_risk"],
                    # Discovery Score bileşenleri (4.5)
                    novelty=method.get("novelty", 0.5),
                    feasibility=1.0 - method["cost"],
                    cost_advantage=1.0 - method["cost"],
                    verifiability=method["data_quality"],
                    risk=method["legal_risk"],
                )
                hypothesis.applicability_score = score
                scored_methods.append(hypothesis)

        scored_methods.sort(key=lambda x: x.applicability_score, reverse=True)

        # Compute discovery score for each hypothesis
        discovery_weights = {
            "novelty": 0.2,
            "feasibility": 0.2,
            "cost_advantage": 0.2,
            "verifiability": 0.2,
            "risk": 0.2,
        }
        for h in scored_methods:
            h.discovery_score = h.compute_discovery_score(discovery_weights)

        return scored_methods[:max_hypotheses]


class DiscoveryEngine:
    """EMOG Discovery Engine - finds and ranks analysis methods."""

    def __init__(self) -> None:
        self.generator = HypothesisGenerator()
        self.history: List[DiscoveryResult] = []
        self.failed_hypotheses: List[str] = []

    def discover(
        self,
        task_description: str,
        context: Dict[str, Any] = None,
        max_hypotheses: int = 5,
    ) -> DiscoveryResult:
        context = context or {}
        hypotheses = self.generator.generate_hypotheses(
            task_description, context, max_hypotheses=max_hypotheses,
            excluded_hypotheses=self.failed_hypotheses
        )
        best = hypotheses[0] if hypotheses else None

        if hypotheses:
            reasoning = (
                f"'{task_description}' görevine en uygun yöntem: "
                f"{best.name} (skor: {best.applicability_score:.2f}). "
                f"Toplam {len(hypotheses)} alternatif değerlendirildi."
            )
        else:
            reasoning = "Uygun yöntem bulunamadı."

        result = DiscoveryResult(
            task_id=str(uuid.uuid4()),
            query=task_description,
            hypotheses=hypotheses,
            best_hypothesis=best,
            reasoning=reasoning,
            timestamp=time.time(),
        )
        self.history.append(result)
        return result

    def mark_as_failed(self, tool_name: str) -> None:
        if tool_name not in self.failed_hypotheses:
            self.failed_hypotheses.append(tool_name)

    def get_failed_hypotheses(self) -> List[str]:
        return self.failed_hypotheses.copy()


discovery_engine = DiscoveryEngine()