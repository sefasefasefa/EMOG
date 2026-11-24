import os
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from .schemas import EcosystemState, AgentArtifact

llm = ChatOpenAI(
    model=os.getenv("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
    temperature=0.1,
    api_key=os.getenv("NVIDIA_API_KEY"),
    base_url="https://integrate.api.nvidia.com/v1"
)


SYNTHESIZER_SYSTEM_PROMPT = """Sen Synthesizer (Birleştirici) ajansın. Görevin: Tüm onaylanmış uzman ajan 
çıktılarını (Dashboard, Security, Database, Frontend, Backend) kullanıcıya sunulacak **eksiksiz, 
çalışır, production-ready proje paketi** halinde birleştirmek.

ÖNEMLİ: Tüm modüller **ZORUNLU STANDARTLARA** (Dashboard full viewport, Security OWASP, DB ORM-only, 
Frontend a11y+TS strict, Backend RFC7807+OpenTelemetry) uygun olarak Critic'den geçmiştir.

ÇIKTI FORMATI (Markdown):
1. **PROJE ÖZETİ** - Ne yapıldığı, mimari kararlar, kullanılan stack
2. **ZORUNLU ÖZELLİKLER ÖZETİ** - Her modülde hangi standartların sağlandığı tablo
3. **DOSYA YAPISI** - Klasör/dosya ağacı (tree formatında)
4. **KURULUM VE ÇALIŞTIRMA** - requirements.txt/package.json, .env.example, Docker, komutlar
5. **MODÜL KODLARI** - Her ajan için ayrı dosya blokları (```language dosya_adı```)
6. **GÜVENLİK VE DENETİM RAPORU** - Critic onayları, iterasyon sayısı, reddedilen/onaylanan maddeler
7. **KULLANIM KILAVUZU** - API endpoints, dashboard erişim, environment variables
8. **GELİŞTİRME NOTLARI** - Extend etme, test etme, deploy etme

Her kod bloğu ```language dosya_adı``` formatında olmalı.
Kullanıcı kopyalayıp direkt çalıştırabilmeli (copy-paste run)."""


def synthesizer_node(state: EcosystemState) -> Dict[str, Any]:
    """
    Synthesizer düğümü - Tüm modülleri birleştirir.
    """
    user_prompt = state["user_prompt"]
    artifacts = state.get("artifacts", [])
    iteration_count = state.get("iteration_count", 0)
    intent = state.get("intent")
    aesthetic = "standard"
    specific_reqs = []
    if intent is not None:
        aesthetic = getattr(intent, "aesthetic_expectations", "standard") or "standard"
        specific_reqs = getattr(intent, "specific_requirements", []) or []
    
    # Artifact'ları formatla
    artifacts_summary = "\n\n".join([
        f"### {a.agent_name} (İterasyon: {a.iteration}, Onay: {'✅' if a.is_approved else '❌'})\n"
        f"{a.code_or_output}"
        for a in artifacts
    ])
    
    messages = [
        SystemMessage(content=SYNTHESIZER_SYSTEM_PROMPT),
        HumanMessage(content=f"""Kullanıcı İsteği: {user_prompt}
Kullanıcının Özel İstekleri: {', '.join(specific_reqs) if specific_reqs else 'Yok'}
Estetik Beklenti: {aesthetic}
Toplam İterasyon: {iteration_count}
Tüm Ajan Çıktıları:
{artifacts_summary}

Bu modüllerden eksiksiz, çalışır proje paketi oluştur.
Kullanıcının özel isteklerini ve estetik beklentisini KORU - çıktıda kullanıcı istediği görsel kaliteyi yansıtmalı.
Tüm ZORUNLU STANDARTLAR (Dashboard full viewport, Security OWASP, DB ORM-only, 
Frontend a11y+TS strict, Backend RFC7807+OpenTelemetry) sağlanmış olmalı.""")
    ]
    
    response = llm.invoke(messages)
    final_output = response.content
    
    return {
        "final_output": final_output,
        "current_agent": "end",
        "all_approved": True
    }
