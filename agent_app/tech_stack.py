"""
EMOG Teknik Altyapı Kaydı — Bölüm 13 (Matematiksel ve Teknik Altyapı)

Bu modül, paylaşılan teknik altyapı speksiyonunu kodla eşler:
- Her bileşen için kullanılan teknoloji,
- Mevcut kodda karşılık gelen uygulama,
- Çalışma zamanında kullanılabilirlik kontrolü.

Eksik bileşenler (faiss, mlflow, langfuse, cbor2, rust toolchain)
çalışmayı engellemez; sadece ilgili özellik devre dışı kalır.
"""

from __future__ import annotations

import importlib
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TechComponent:
    name: str
    key: str
    technology: str
    implementation: str
    status: str = "unknown"
    detail: str = ""
    installed_version: Optional[str] = None
    pkg_name: Optional[str] = None


class TechStack:
    _KEY_TO_PKG: Dict[str, str] = {
        "neural_core": "torch",
        "model_inference": "onnxruntime",
        "binary_protocol": "google.protobuf",
        "backend": "fastapi",
        "agent_orchestration": "langgraph",
        "model_gateway": "litellm",
        "advanced_memory": "psycopg2",
        "vector_search": "faiss-cpu",
        "training": "torch",
        "model_versioning": "mlflow",
        "monitoring": "opentelemetry",
    }

    def __init__(self):
        self.components: List[TechComponent] = []
        self._build()

    def _build(self) -> None:
        specs = [
            TechComponent(
                name="Nöral Çekirdek",
                key="neural_core",
                technology="Python + PyTorch",
                implementation="agent_app/cognitive_kernel.py (CK0 sinir ağı), agent_app/cognitive_memory.py",
                status=self._py_pkg_status("torch"),
                detail="CK-0 karar ağı, deneyim belleği ve tercih öğrenme",
                pkg_name="torch",
            ),
            TechComponent(
                name="Model Çıkarımı",
                key="model_inference",
                technology="ONNX Runtime",
                implementation="agent_app/model_gateway.py (model çağrıları), agent_app/views.py llm_create()",
                status=self._py_pkg_status("onnxruntime"),
                detail="NVIDIA / harici LLM çıkarma çağrıları",
                pkg_name="onnxruntime",
            ),
            TechComponent(
                name="İkili Çekirdek",
                key="binary_core",
                technology="Rust",
                implementation="agent_app/neural_core/binary_control.py (facade), multi_agent_ecosystem/ (kritik yapı)",
                status=self._exe_status("rustc"),
                detail="Rust derleyicisi yoksa Python facade devreye girer",
                pkg_name=None,
            ),
            TechComponent(
                name="İkili Protokol",
                key="binary_protocol",
                technology="CBOR / Protocol Buffers",
                implementation="agent_app (JSON tabanlı iletişim); protobuf python paketi mevcut",
                status=self._py_pkg_status("google.protobuf", "cbor2"),
                detail="protobuf yüklü; cbor2 eksikse JSON fallback kullanılır",
                pkg_name="google.protobuf",
            ),
            TechComponent(
                name="Arka Uç",
                key="backend",
                technology="Python + FastAPI",
                implementation="agent_app/views.py (Django views), fastapi paketi mevcut",
                status=self._py_pkg_status("fastapi"),
                detail="Django temel web çerçevesi; FastAPI alternatif endpoint için hazır",
                pkg_name="fastapi",
            ),
            TechComponent(
                name="Ajan Orkestrasyonu",
                key="agent_orchestration",
                technology="LangGraph + özel kontrol sistemi",
                implementation="multi_agent_ecosystem/graph.py (LangGraph StateGraph), multi_agent_ecosystem/orchestrator.py",
                status=self._py_pkg_status("langgraph"),
                detail="Çoklu ajan akışı, critic/senthez döngüsü",
                pkg_name="langgraph",
            ),
            TechComponent(
                name="Model Ağ Geçidi",
                key="model_gateway",
                technology="LiteLLM",
                implementation="agent_app/model_gateway.py (httpx/openai tabanlı kendi geçidi)",
                status=self._py_pkg_status("litellm"),
                detail="LiteLLM yüklü; mevcut geçidi LiteLLM'e geçirilebilir",
                pkg_name="litellm",
            ),
            TechComponent(
                name="Hafıza",
                key="memory",
                technology="SQLite",
                implementation="emog_project/db.sqlite3, agent_app/cognitive_memory.py (JSON depolama)",
                status="available",
                detail="SQLite built-in; veritabanı dosyası mevcut",
                pkg_name=None,
            ),
            TechComponent(
                name="İleri Hafıza",
                key="advanced_memory",
                technology="PostgreSQL + pgvector",
                implementation="emog_project/settings.py (DATABASES), agent_app/cognitive_memory.py",
                status=self._py_pkg_status("psycopg2", "pgvector"),
                detail="psycopg2 ve pgvector yüklü; veritabanı yapılandırması settings'te",
                pkg_name="psycopg2",
            ),
            TechComponent(
                name="Vektör Arama",
                key="vector_search",
                technology="FAISS",
                implementation="Henüz entegre edilmedi (faiss paketi eksik)",
                status=self._py_pkg_status("faiss-cpu", "faiss"),
                detail="faiss yüklü değil; vektör arama devre dışı",
                pkg_name="faiss-cpu",
            ),
            TechComponent(
                name="Eğitim",
                key="training",
                technology="PyTorch",
                implementation="agent_app/cognitive_kernel.py (CK0 geri yayılımı), agent_app/learning_engine.py (WeightLearner)",
                status=self._py_pkg_status("torch"),
                detail="CK0 neural network eğitimi ve Learning Engine eğitim döngüsü",
                pkg_name="torch",
            ),
            TechComponent(
                name="Model Sürümleme",
                key="model_versioning",
                technology="MLflow",
                implementation="Henüz entegre edilmedi (mlflow paketi eksik)",
                status=self._py_pkg_status("mlflow"),
                detail="mlflow yüklü değil; sürümleme devre dışı",
                pkg_name="mlflow",
            ),
            TechComponent(
                name="İzleme",
                key="monitoring",
                technology="OpenTelemetry / Langfuse",
                implementation="agent_app/sandbox/health_monitoring.py (Prometheus metrikleri, HealthMonitor), opentelemetry paketi",
                status=self._py_pkg_status("opentelemetry", "langfuse"),
                detail="OpenTelemetry yüklü; langfuse eksik",
                pkg_name="opentelemetry",
            ),
            TechComponent(
                name="İzole Deney Ortamı",
                key="sandbox",
                technology="Docker",
                implementation="agent_app/sandbox/security_pipeline.py (SecurityPatchPipeline, Docker testleri), agent_app/sandbox.py (yerel korumalı çalıştırma)",
                status=self._docker_status(),
                detail="Docker SDK ve CLI mevcut; Docker daemon yoksa fallback testi kullanılır",
                pkg_name=None,
            ),
        ]
        self.components = specs

    @staticmethod
    def _py_pkg_status(*names: str) -> str:
        for n in names:
            try:
                mod = importlib.import_module(n)
                ver = getattr(mod, "__version__", None)
                return "available" if ver else "partial"
            except ImportError:
                continue
        return "missing"

    @staticmethod
    def _exe_status(name: str) -> str:
        return "available" if shutil.which(name) else "missing"

    @staticmethod
    def _docker_status() -> str:
        has_cli = shutil.which("docker") is not None
        try:
            importlib.import_module("docker")
            has_sdk = True
        except ImportError:
            has_sdk = False
        if has_cli and has_sdk:
            return "available"
        if has_sdk:
            return "partial"
        return "missing"

    def report(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for c in self.components:
            out.append({
                "name": c.name,
                "key": c.key,
                "technology": c.technology,
                "implementation": c.implementation,
                "status": c.status,
                "detail": c.detail,
                "installed_version": c.installed_version or self._installed_version_for(c.key),
            })
        return out

    def _installed_version_for(self, key: str) -> Optional[str]:
        pkg = self._KEY_TO_PKG.get(key)
        if not pkg:
            return None
        try:
            mod = importlib.import_module(pkg)
            return getattr(mod, "__version__", None)
        except ImportError:
            return None

    def is_available(self, key: str) -> bool:
        comp = next((c for c in self.components if c.key == key), None)
        return comp is not None and comp.status == "available"

    def missing(self) -> List[str]:
        return [c.key for c in self.components if c.status == "missing"]

    def summary(self) -> Dict[str, Any]:
        total = len(self.components)
        available = sum(1 for c in self.components if c.status == "available")
        partial = sum(1 for c in self.components if c.status == "partial")
        missing = sum(1 for c in self.components if c.status == "missing")
        return {
            "total": total,
            "available": available,
            "partial": partial,
            "missing": missing,
            "missing_keys": self.missing(),
            "coverage_percent": round(100 * (available + partial * 0.5) / total, 1) if total else 0,
        }


tech_stack = TechStack()
