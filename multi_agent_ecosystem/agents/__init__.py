"""
EMOG Multi-Agent System - Yeni Ajans Modülleri

Sorumluluklara bölünmüş, birbirinden bağımsız ajanlar:
- Discovery: Yeni fikirler ve yöntemler
- Research: Bilgi ve kaynak araştırması
- Reasoning: Mantıksal problem çözme
- Strategy: Çözüm stratejileri
- Critic: Hataları ve eksikleri bulma (mevcut)
- Experiment: Hipotezleri test etme
- Learning: Deneyimlerden öğrenme
- Planner: Görev planlama (mevcut orchestrator)
- Verification: Sonuçları doğrulama
- Optimization: Performans iyileştirme

Her ajanın kendi sorumluluğu vardır; aynı cevabı üretmeye çalışmaz.
"""

from .base_agent import BaseAgent, AgentResult
from .coordinator import MultiAgentCoordinator, run_multi_agent_flow

__all__ = [
    "BaseAgent",
    "AgentResult",
    "MultiAgentCoordinator",
    "run_multi_agent_flow",
]
