"""
buildkit - "Proje yap" dongusu.

Kullanici "haber sitesi yap", "3d oyun yap", "android icin forum sistemi yap"
dediginde devreye giren otonom uretim hatti:

    1. RESUME     Konuyu gercekten arastir (coklu saglayici, derin okuma, sentez)
    2. BENCHMARK  Ayni alandaki siteleri/uygulamalari incele, ozellik matrisini cikar
    3. BLUEPRINT  En gelismis hali ne olmali? Spesifikasyonu yaz
    4. PLAN       Sirali, bagimlilikli, dogrulanabilir is kalemleri uret
    5. BUILD      Her kalemi arac'larla uygula, dogrula, bozulursa onar
    6. CRITIC     Bosluk var mi? Yeni kalem uret ve donguye geri don
    7. POLISH     Son kontrol, kalite seridi, teslim

Her asamada calisma durumu iki yere yazilir:
  * task_runs/<run_id>.json  (dosya; worker cokse bile plan kaybolmaz)
  * BuildRun / BuildPlanItem tablolari (DB; sorgulanabilir ve raporlanabilir)
"""

__all__ = [
    "pipeline",
    "search",
    "extract",
    "research",
    "benchmark",
    "blueprint",
    "plan",
    "executor",
    "critic",
    "design_review",
    "subagents",
    "events",
]