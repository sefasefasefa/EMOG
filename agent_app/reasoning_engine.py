"""
EMOG Reasoning Engine - Akıl Yürütme Motoru

Karmaşık görevleri parçalayan ve her adımı doğrulayan motor.
Mantıksal çıkarımlar, hipotez doğrulama ve kanıt tabanlı karar verme sağlar.

Aşamalar:
1. Görev ayrıştırma (Task Decomposition)
2. Hipotez üretimi (Hypothesis Generation)
3. Kanıt toplayıcı (Evidence Collector)
4. Doğrulama motoru (Verification Engine)
5. Sonuç değerlendirme (Conclusion Engine)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid
import time
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SubTask:
    """Tek bir alt görev."""
    id: str
    name: str
    description: str
    status: str = "pending"  # pending, in_progress, completed, failed, skipped
    evidence: List[Dict[str, Any]] = None
    result: Any = None
    verified: bool = False
    verification_notes: str = ""
    confidence: float = 0.0
    parent_id: Optional[str] = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = []


@dataclass
class Evidence:
    """Kanıt kaydı."""
    id: str
    source: str  # "web_search", "api", "document", "user_input", "analysis"
    content: str
    confidence: float  # 0.0 - 1.0
    timestamp: float
    verified: bool = False


@dataclass
class TaskStep:
    """Tam görev akışı."""
    task_id: str
    task_name: str
    subtasks: List[Any] = None
    current_step: int = 0
    completed: bool = False
    final_conclusion: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Reasoning Engine
# ---------------------------------------------------------------------------

class ReasoningEngine:
    """
    EMOG Bilişsel Çekirdek - Akıl Yürütme Motoru.
    
    Karmaşık görevleri parçalar, her adımı doğrular ve
    kanıt tabanlı kararlar verir.
    """

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or Path.home() / ".emog" / "reasoning_engine")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.active_tasks: Dict[str, TaskStep] = {}
        self.completed_tasks: List[TaskStep] = []
        self.evidence_store: Dict[str, List[Evidence]] = {}

    # ------------------------------------------------------------------
    # Task Decomposition (Görev Ayrıştırma)
    # ---------------------------------------------------------------------------
    def decompose_task(self, task_description: str, task_type: str = "general") -> Dict[str, Any]:
        """
        Gerekli görevleri belirli kurallara göre parçalar.
        
        Args:
            task_description: Kullanıcının görevi
            context: Ek bağlam bilgileri
            
        Returns:
            Parçalanmış görev yapısı
        """
        task_id = str(uuid.uuid4())
        
        # Önceden tanımlanmış görev şablonları
        task_templates = {
            "build_app": {
                "name": "SaaS Ürün Geliştirme",
                "steps": [
                    {"name": "Pazar Araştırması", "description": "Hedef kitle, pazar boyutu ve rekabet analizi"},
                    {"name": "Müşteri Problemi", "description": "Müşterinin temel acısı ve ihtiyacının tanımlanması"},
                    {"name": "Alternatif Ürünler", "description": "Mevcut çözümlerin ve pazar boşluklarının incelenmesi"},
                    {"name": "Teknoloji Seçimi", "description": "Yığın (stack), altyapı ve araçların seçilmesi"},
                    {"name": "Maliyet Analizi", "description": "Geliştirme, operasyon ve pazarlama maliyetleri"},
                    {"name": "Ürün Geliştirme", "description": "MVP kodlama ve uygulama"},
                    {"name": "Test", "description": "Birim, entegrasyon ve kullanıcı testi"},
                    {"name": "Satış Stratejisi", "description": "İlk müşteri edinme ve satış planı"},
                    {"name": "Sonuç Değerlendirme", "description": "Performans, geri bildirim ve sonraki adımlar"}
                ]
            },
            "research": {
                "name": "Araştırma",
                "steps": [
                    {"name": "Konu Belirleme", "description": "Araştırma kapsamı ve hedefi"},
                    {"name": "Kaynak Toplama", "description": "Kaynakları toplayın ve sınıflandırın"},
                    {"name": "Veri Analizi", "description": "Toplanan verileri analiz edin"},
                    {"name": "Sonuç Çıkarımı", "description": "Sonuçları çıkarın ve sonuçlandırın"},
                    {"name": "Raporlama", "description": "Bulguları raporlayın"}
                ]
            },
            "general": {
                "name": "Genel Görev",
                "steps": [
                    {"name": "Hedef Belirleme", "description": "Neleri başarmak istediğinizi tanımlayın"},
                    {"name": "Planlama", "description": "Nasıl yapacağınızı planlayın"},
                    {"name": "Uygulama", "description": "Planı gerçekleştirin"},
                    {"name": "Değerlendirme", "description": "Sonuçları değerlendirin"}
                ]
            }
        }
        
        # En uygun şablonu seç
        template = task_templates.get(task_type, task_templates["general"])
        
        subtasks = []
        for i, step in enumerate(template["steps"]):
            subtask = {
                "id": str(uuid.uuid4()),
                "name": step["name"],
                "description": step["description"],
                "status": "pending",
                "order": i,
                "depends_on": [],  # Önceki görevler
                "result": None,
                "verified": False
            }
            subtasks.append(subtask)
        
        task_step = TaskStep(
            task_id=task_id,
            task_name=task_description,
            subtasks=subtasks,
            current_step=0,
            completed=False,
            final_conclusion="",
            confidence=0.0
        )
        
        self.active_tasks[task_id] = task_step
        
        return {
            "task_id": task_id,
            "task_name": task_description,
            "subtasks": subtasks,
            "total_steps": len(subtasks),
            "message": f"Görev '{task_description}' {len(subtasks)} adımına bölündü."
        }
    
    def add_evidence(self, task_id: str, source: str, content: str, confidence: float = 0.5) -> str:
        """
        Bir göreve kanıt ekler.
        """
        att_id = str(uuid.uuid4())
        evidence = Evidence(
            id=att_id,
            source=source,
            content=content,
            confidence=confidence,
            timestamp=time.time()
        )
        
        if task_id not in self.evidence_store:
            self.evidence_store[task_id] = []
        
        self.evidence_store[task_id].append(evidence)
        return att_id
    
    def verify_evidence(self, task_id: str, evidence_id: str, verified: bool, notes: str = "") -> bool:
        """Bir kanıtı doğrulanmış/reddedildi olarak işaretle."""
        if task_id not in self.evidence_store:
            return False
        
        for att in self.evidence_store[task_id]:
            if att.id == evidence_id:
                att.verified = verified
                return True
        return False
    
    def complete_task(self, task_id: str, conclusion: str, confidence: float = 0.8) -> bool:
        """Bir görevi tamamlandı olarak işaretle."""
        if task_id not in self.active_tasks:
            return False
        
        task = self.active_tasks[task_id]
        task.completed = True
        task.final_conclusion = conclusion
        task.confidence = confidence
        
        self.completed_tasks.append(task)
        del self.active_tasks[task_id]
        return True
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Bir görevin durumunu döndürür."""
        task = self.active_tasks.get(task_id)
        if not task:
            # Tamamlanmış görevlerde ara
            for t in self.completed_tasks:
                if t.task_id == task_id:
                    task = t
                    break
        
        if not task:
            return None
        
        return {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "completed": task.completed,
            "subtasks": len(task.subtasks),
            "completed_subtasks": sum(1 for s in task.subtasks if s.status == "completed"),
            "current_step": task.current_step,
            "final_conclusion": task.final_conclusion,
            "confidence": task.confidence
        }
    
    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Tüm görevleri döndürür."""
        all_tasks = list(self.active_tasks.values()) + self.completed_tasks
        return [
            {
                "task_id": t.task_id,
                "task_name": t.task_name,
                "completed": t.completed,
                "subtasks": len(t.subtasks),
                "completed_subtasks": sum(1 for s in t.subtasks if s.status == "completed"),
                "current_step": t.current_step,
                "final_conclusion": t.final_conclusion,
                "confidence": t.confidence
            }
            for t in all_tasks
        ]

    # ------------------------------------------------------------------
    # Complex task detection (Karmaşık görev algılama)
    # ------------------------------------------------------------------
    _COMPLEX_TRIGGERS = (
        "geliştir", "kur", "yap", "planla", "strategi", "saas", "ürün", "produkt",
        "müşteri bul", "musteri bul", "pazar", "araştır", "arastır", "raporla",
        "hesapla", "analiz et", "roadmap", "30 gün", "30 gun", "iki ay", "yıl içinde",
    )

    # Her alt görev türü için varsayılan (fallback) sağlayıcı önerisi.
    # Keşif motoru eşleme yapamazsa bu harita devreye girer.
    _FALLBACK_PROVIDERS = {
        "pazar araştırması": ("Web Arama Motoru", "web_search"),
        "müşteri problemi": ("Firma Rehberleri", "business_directory"),
        "alternatif ürünler": ("E-ticaret Platformları", "ecommerce_platforms"),
        "teknoloji seçimi": ("Bulut Hizmet Sağlayıcıları", "cloud_providers"),
        "maliyet analizi": ("Finansal ve Yatırım Verileri", "financial_data"),
        "ürün geliştirme": ("API Entegrasyonu", "api_integration"),
        "test": ("Web Sayfası Yapılandırılmış Veri", "structured_web_data"),
        "satış stratejisi": ("Reklam ve Pazarlama Verileri", "advertising_data"),
        "sonuç değerlendirme": ("Kullanıcı Geri Bildirim ve İnceleme", "user_reviews"),
    }

    def _fallback_for_subtask(self, subtask_name: str) -> Dict[str, Any]:
        """Alt görevin ismine göre varsayılan sağlayıcı döndürür."""
        key = subtask_name.strip().lower()
        for template_key, (name, source) in self._FALLBACK_PROVIDERS.items():
            if key == template_key or template_key in key:
                return {
                    "best_method": name,
                    "best_score": 0.25,
                    "discovery_score": 0.25,
                    "data_source": source,
                    "alternatives": [],
                    "reasoning": "Keşif motoru eşleme yapamadığı için varsayılan sağlayıcı atandı.",
                }
        # Son çare: web arama
        return {
            "best_method": "Web Arama Motoru",
            "best_score": 0.2,
            "discovery_score": 0.2,
            "data_source": "web_search",
            "alternatives": [],
            "reasoning": "Hiçbir sağlayıcıyla eşleştirilemedi; varsayılan web arama atandı.",
        }

    def _is_complex_task(self, message: str) -> bool:
        """Mesajın karmaşık, çok adımlı bir görev olup olmadığını tahmin eder."""
        if not message:
            return False
        low = message.lower()
        return any(t in low for t in self._COMPLEX_TRIGGERS)

    def _discover_for_subtask(
        self, subtask_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Bir alt görev için en uygun keşif yöntemini ve discovery skorunu döndürür."""
        result = None
        try:
            from agent_app.discovery_engine import discovery_engine

            result = discovery_engine.discover(
                subtask_description, context=context, max_hypotheses=3
            )
        except Exception as exc:
            logger.warning("discovery for subtask error: %s", exc)
        if result and result.best_hypothesis:
            h = result.best_hypothesis
            return {
                "best_method": h.name,
                "best_score": round(float(h.applicability_score or 0.0), 2),
                "discovery_score": round(float(h.discovery_score or 0.0), 2),
                "data_source": h.data_source,
                "alternatives": [x.name for x in (result.hypotheses or [])],
                "reasoning": result.reasoning or "",
            }
        # Keşif motoru hiçbir yöntem bulamadıysa varsayılan sağlayıcı atanır
        return self._fallback_for_subtask(subtask_description)

    def process_message(
        self,
        conversation: Any,
        user: Any,
        message: str,
        saved_attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Gelen mesaji analiz eder.

        Karmaşık bir görev tespit edilirse: görevi parçalar, her alt görev için
        keşif/discovery skoru hesaplar ve kanıt beklemesiyle döner. Sonuç
        otomatik doğru kabul edilmez (doğrulama beklenir).

        Returns:
            {"intent": "chat_analysis", "reply": str, "attachments": [], "media": [],
             "analysis_result": {...}}  veya None (basit sohbet için).
        """
        if not message or not message.strip():
            return None

        raw = message.strip()
        if not self._is_complex_task(raw):
            return None  # Basit sohbet; normal akışa devam

        # 1. Görevi parçala
        task_type = (
            "build_app"
            if any(k in raw.lower() for k in ("saas", "ürün", "product", "uygulama", "geliştir", "kur"))
            else "general"
        )
        decomp = self.decompose_task(raw, task_type=task_type)
        task_id = decomp["task_id"]
        subtasks = list(decomp["subtasks"])

        # 2. Her alt görev için keşif yöntemi ve discovery skoru (otomatik onay YOK)
        context = {
            "user_id": getattr(user, "id", None) or getattr(user, "pk", None),
            "conversation_id": getattr(conversation, "id", None) or getattr(conversation, "pk", None),
        }
        analysis_subtasks = []
        for st in subtasks:
            discovery = self._discover_for_subtask(
                f'{st["name"]}: {st["description"]}', context
            )
            analysis_subtasks.append(
                {
                    "id": st["id"],
                    "name": st["name"],
                    "description": st["description"],
                    "status": "pending",
                    "verified": False,
                    "verification_notes": "Henüz doğrulanmadı; kaynak/kanıt gerekli.",
                    "confidence": 0.0,
                    "discovery": discovery,
                }
            )

        # 3. Genel güven: doğrulanmamış adım olduğu için düşük başlangıç değeri
        overall_confidence = 0.45

        analysis_result = {
            "task_id": task_id,
            "task_name": decomp["task_name"],
            "total_steps": len(analysis_subtasks),
            "subtasks": analysis_subtasks,
            "completed": False,
            "confidence": overall_confidence,
            "verification_required": True,
            "note": (
                "Her alt görev ayrı ayrı araştırılır; sonuç kaynak, kanıt ve test "
                "sonuçlarıyla doğrulanmadan doğru kabul edilmez."
            ),
        }

        reply_lines = [
            f"🧠 Akıl Yürütme Motoru: '{decomp['task_name']}' görevi {len(analysis_subtasks)} alt göreve bölündü.",
            "",
            "Alt görevler ve keşif önerileri:",
        ]
        for i, st in enumerate(analysis_subtasks, 1):
            disc = st["discovery"]
            reply_lines.append(f"{i}. {st['name']} — {st['description']}")
            if disc.get("best_method"):
                reply_lines.append(
                    f"   Öneri: {disc['best_method']} "
                    f"(uyguluvabilirlik {disc['best_score']:.2f}, discovery {disc['discovery_score']:.2f}, "
                    f"kaynak: {disc['data_source']})"
                )
            else:
                reply_lines.append("   Öneri: uygun keşif yöntemi bulunamadı.")
            reply_lines.append("   Durum: doğrulanmadı (kanıt/test bekleniyor)")
        reply_lines.append("")
        reply_lines.append(
            "Not: Her alt görev ayrı ayrı araştırılır; kaynak, kanıt ve test sonuçlarıyla doğrulanmadan doğru kabul edilmez."
        )
        reply_text = "\n".join(reply_lines)

        return {
            "intent": "chat_analysis",
            "reply": reply_text,
            "attachments": [],
            "media": [],
            "analysis_result": analysis_result,
        }


# Global instance
reasoning_engine = ReasoningEngine()
