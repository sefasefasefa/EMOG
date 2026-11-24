import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from .graph import ecosystem_graph, get_graph_visualization
from .schemas import EcosystemState


def print_banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


async def run_ecosystem(user_prompt: str) -> EcosystemState:
    """
    Ecosystem'ı async olarak çalıştırır.
    LangGraph'in astream veya ainvoke kullanarak state'i takip eder.
    """
    print_banner("MULTI-AGENT ECOSYSTEM BAŞLATILIYOR")
    print(f"Kullanıcı İsteği: {user_prompt}")
    
    initial_state: EcosystemState = {
        "user_prompt": user_prompt,
        "intent": None,
        "tasks": [],
        "artifacts": [],
        "final_output": "",
        "iteration_count": 0,
        "current_agent": "orchestrator",
        "all_approved": False
    }
    
    print_section("GRAPH YAPISI")
    print(get_graph_visualization())
    
    print_section("AKIŞ BAŞLIYOR")
    
    try:
        # Graph'ı async invoke et
        final_state = await ecosystem_graph.ainvoke(initial_state)
        
        return final_state
        
    except Exception as e:
        print(f"\n❌ HATA: {e}")
        import traceback
        traceback.print_exc()
        raise


async def run_test_scenario():
    """Test senaryosu: Müşteri siparişleri yönetim paneli."""
    
    user_prompt = (
        "Bana yerel veritabanında müşteri siparişlerini tutan ve anlık grafikle gösteren "
        "bir yönetim paneli yaz."
    )
    
    final_state = await run_ecosystem(user_prompt)
    
    print_banner("SONUÇ")
    
    # Intent sonucu
    intent = final_state.get("intent")
    if intent:
        print(f"\n📋 Niyet Analizi:")
        print(f"   Kod Gerekli: {intent.is_code_required}")
        print(f"   Özet: {intent.intent_summary}")
        print(f"   Bileşenler: {', '.join(intent.required_components)}")
        print(f"   Özel İstekler: {', '.join(getattr(intent, 'specific_requirements', []) or [])}")
        print(f"   Estetik: {getattr(intent, 'aesthetic_expectations', 'standard')}")
    
    # İterasyon bilgisi
    print(f"\n🔄 Toplam İterasyon: {final_state.get('iteration_count', 0)}")
    
    # Artifact durumları
    artifacts = final_state.get("artifacts", [])
    if artifacts:
        print_section("AJAN ÇIKTILARI DURUMU")
        for artifact in artifacts:
            status = "✅ ONAYLANDI" if artifact.is_approved else "❌ REDDEDİLDİ"
            print(f"   {artifact.agent_name}: {status} (İterasyon: {artifact.iteration})")
            if artifact.feedback:
                print(f"      → Geri Bildirim: {artifact.feedback[:100]}...")
    
    # Final output
    final_output = final_state.get("final_output", "")
    if final_output:
        print_section("NİHAİ PROJE PAKETİ")
        print(final_output)
    
    return final_state


async def run_custom_prompt(prompt: str):
    """Özel prompt ile test."""
    print_banner(f"ÖZEL TEST: {prompt[:50]}...")
    final_state = await run_ecosystem(prompt)
    return final_state


def main():
    """Ana giriş noktası."""
    # API Key kontrolü
    if not os.getenv("NVIDIA_API_KEY"):
        print("❌ HATA: NVIDIA_API_KEY environment variable ayarlanmamış!")
        print("   .env dosyası oluşturun ve NVIDIA_API_KEY=nvapi-... ekleyin")
        sys.exit(1)
    
    print("🤖 Multi-Agent Orchestration & Moderation Ecosystem")
    print("   Intent → Parallel Specialists → Critic Loop → Synthesizer")
    print("   LangGraph + GPT-4o")
    
    # Argüman kontrolü
    if len(sys.argv) > 1:
        # Komut satırından prompt verilmişse
        custom_prompt = " ".join(sys.argv[1:])
        asyncio.run(run_custom_prompt(custom_prompt))
    else:
        # Varsayılan test senaryosu
        asyncio.run(run_test_scenario())


if __name__ == "__main__":
    main()
