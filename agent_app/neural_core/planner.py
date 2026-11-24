"""Zor problem çözme: hedefi yürütülebilir adımlara böler.

LLM varsa JSON plan ister; yoksa moda göre deterministik şablon üretir.
"""
from . import config as neural_config
from .jsonutil import parse_json


SYSTEM_PROMPT = (
    "Sen EMOG'un bilişsel planlayıcısısın. Hedefi en fazla {limit} adıma böl. "
    "Yalnızca JSON dizisi döndür: "
    '[{{"title": "kısa adım", "detail": "ne yapılacak"}}]. '
    "Adımlar sıralı, ölçülebilir ve birbirine bağımlı olmamalı."
)


BASE_STEPS = [
    ("define", "Hedefi ve başarı ölçütlerini netleştir", "Hedefi tek cümlelik bir sonuç tanımına indir; kabul kriterlerini yaz."),
    ("inspect", "Mevcut durumu ve kısıtları incele", "Var olan dosya, veri ve bağımlılıkları çıkar; riskleri not et."),
]

MODE_STEPS = {
    "full": [
        ("alternatives", "Alternatif yaklaşımları karşılaştır", "En az iki yaklaşımı artı/eksi ve maliyetle karşılaştır."),
        ("sources", "Güvenilir kaynakları tara", "Konuyla ilgili güncel kaynaklardan kanıt topla."),
    ],
    "solve": [
        ("root_cause", "Kök nedeni bul", "Belirtiyi değil, hatayı üreten koşulu tespit et."),
        ("smallest_fix", "En küçük güvenli düzeltmeyi uygula", "Davranışı düzelten minimal değişikliği yap."),
    ],
    "explore": [
        ("alternatives", "Alternatif yaklaşımları karşılaştır", "Farklı mimari ve ürün yaklaşımlarını puanla."),
        ("experiment", "En riskli varsayımı test et", "Küçük bir deneyle varsayımı doğrula veya çürüt."),
    ],
    "research": [
        ("sources", "Güvenilir kaynakları tara", "Birincil kaynakları ve karşılaştırmaları topla."),
        ("synthesize", "Bulguları sentezle", "Kaynaklar arası çelişkileri ve boşlukları işaretle."),
    ],
    "learn": [
        ("review", "Önceki deneyimleri gözden geçir", "Benzer hedeflerdeki başarı ve başarısızlıkları oku."),
        ("lesson", "Tekrarlanabilir ders çıkar", "Bir sonraki çalıştırmaya taşınacak kuralı yaz."),
    ],
}

CLOSING_STEPS = [
    ("execute", "Planı adım adım uygula", "Bağımlılık sırasına göre ilerle; her adımın çıktısını kaydet."),
    ("verify", "Sonucu doğrula ve test et", "Kabul kriterlerini somut bir kontrolle doğrula."),
    ("record", "Dersi deneyim defterine kaydet", "Ne işe yaradı, ne yaramadı ve neden."),
]


def _deterministic_plan(mode):
    steps = list(BASE_STEPS) + list(MODE_STEPS.get(mode, MODE_STEPS["full"])) + list(CLOSING_STEPS)
    return [
        {
            "key": key,
            "title": title,
            "detail": detail,
            "status": "pending",
        }
        for key, title, detail in steps
    ]


def build_plan(goal, mode="full", context=None, gateway=None):
    """Hedef için plan adımları döndür (en az 4, en fazla MAX_PLAN_STEPS)."""
    goal = (goal or "").strip()
    cfg = neural_config.get_config()
    limit = max(4, int(cfg.get("MAX_PLAN_STEPS", 10)))
    plan = []

    if gateway is not None and not neural_config.offline():
        prompt = SYSTEM_PROMPT.format(limit=limit)
        context_note = ""
        if context:
            context_note = "\nBağlam: " + str(context)[:1200]
        try:
            text = gateway.complete(prompt, f"Hedef: {goal}{context_note}", purpose="agent")
        except Exception:
            text = ""
        parsed = parse_json(text, list)
        if parsed:
            for index, item in enumerate(parsed[:limit], start=1):
                if isinstance(item, str):
                    title, detail = item, ""
                elif isinstance(item, dict):
                    title = str(item.get("title") or item.get("step") or "").strip()
                    detail = str(item.get("detail") or item.get("description") or "").strip()
                else:
                    continue
                if not title:
                    continue
                plan.append(
                    {
                        "key": f"llm-{index}",
                        "title": title[:200],
                        "detail": detail[:1000],
                        "status": "pending",
                    }
                )

    if len(plan) < 4:
        plan = _deterministic_plan(mode)
    return plan[:limit]
