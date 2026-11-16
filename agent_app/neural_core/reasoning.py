"""Akıl yürütme motoru: hedef için hipotezler üretir ve iz sürer.

LLM varsa JSON hipotez listesi ister; yoksa deterministik hipotezler üretir.
Çevrimdışı modda hiçbir ağ çağrısı yapılmaz.
"""
from . import config as neural_config
from .jsonutil import parse_json


SYSTEM_PROMPT = (
    "Sen EMOG'un akıl yürütme motorusun. Verilen hedef için test edilebilir "
    "hipotezler üret. Yalnızca JSON dizisi döndür: "
    '[{"statement": "hipotez", "evidence": "kanıt/temel", "confidence": 0.0-1.0}]. '
    "Hipotezler birbirinden farklı ve ölçülebilir olsun; güven değerini 0-1 arasında ver."
)


DEFAULT_HYPOTHESES = [
    {
        "statement": "Hedef mevcut kaynaklarla adım adım uygulanabilir; en kritik varsayımlar erken test edilmelidir.",
        "evidence": "Plan adımları ve önceki deneyim kayıtları",
        "confidence": 0.72,
    },
    {
        "statement": "En küçük çalışan sürüm (MVP) ile risk azaltılıp öğrenme hızlandırılabilir.",
        "evidence": "MVP yaklaşımı ve düşük başlangıç maliyeti",
        "confidence": 0.68,
    },
    {
        "statement": "Dış kaynaklardan ve benzer çözümlerden doğrulama gerekebilir.",
        "evidence": "Araştırma bulguları ve karşılaştırmalar",
        "confidence": 0.6,
    },
]


def _coerce_confidence(value, default=0.7):
    """Güven değerini toleranslı biçimde 0-1 aralığına indir."""
    if isinstance(value, bool):
        return default
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return max(0.0, min(1.0, number))


def _as_text(value, default=""):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _normalize_hypothesis(item):
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {
            "statement": text[:1000],
            "evidence": "",
            "confidence": 0.7,
        }
    if not isinstance(item, dict):
        return None
    statement = _as_text(
        item.get("statement")
        or item.get("hypothesis")
        or item.get("title")
        or item.get("claim")
    )
    if not statement:
        return None
    evidence = _as_text(
        item.get("evidence")
        or item.get("reason")
        or item.get("basis")
        or item.get("support")
        or item.get("source")
    )
    confidence = _coerce_confidence(
        item.get("confidence", item.get("score")), default=0.7
    )
    return {
        "statement": statement[:1000],
        "evidence": evidence[:1000],
        "confidence": confidence,
    }


def generate_hypotheses(goal, gateway=None, max_items=None):
    """Hedef için hipotez listesi döndür (statement, evidence, confidence).

    LLM varsa JSON hipotez listesi ister; yoksa deterministik hipotezler.
    """
    goal = (goal or "").strip()
    hypotheses = []

    if gateway is not None and not neural_config.offline():
        prompt = SYSTEM_PROMPT
        try:
            text = gateway.complete(prompt, f"Hedef: {goal}", purpose="agent")
        except Exception:
            text = ""
        parsed = parse_json(text, list)
        for item in parsed or []:
            normalized = _normalize_hypothesis(item)
            if normalized is not None:
                hypotheses.append(normalized)

    if not hypotheses:
        hypotheses = [dict(item) for item in DEFAULT_HYPOTHESES]

    # En az bir varsayılan hipotez güvencesi.
    if not hypotheses:
        hypotheses = [dict(DEFAULT_HYPOTHESES[0])]

    if max_items is not None:
        try:
            limit = int(max_items)
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit > 0:
            hypotheses = hypotheses[:limit]

    # Her hipoteze güvenli kopya.
    cleaned = []
    for item in hypotheses:
        cleaned.append({
            "statement": str(item.get("statement", "") or "")[:1000],
            "evidence": str(item.get("evidence", "") or "")[:1000],
            "confidence": _coerce_confidence(item.get("confidence", 0.7), 0.7),
        })
    if not cleaned:
        cleaned = [dict(DEFAULT_HYPOTHESES[0])]
    return cleaned


class ReasoningEngine:
    def __init__(self, user, gateway=None):
        self.user = user
        self.gateway = gateway if gateway is not None else None
        if self.gateway is None:
            from .gateway import ModelGateway

            self.gateway = ModelGateway()

    def generate_hypotheses(self, goal, gateway=None, max_items=None):
        gw = gateway if gateway is not None else self.gateway
        return generate_hypotheses(goal, gw, max_items=max_items)

    def reason(self, goal, mode="full", context=None, plan=None):
        goal = (goal or "").strip()
        context = context if isinstance(context, dict) else (context or {})
        plan = plan if isinstance(plan, list) else (plan or [])

        hypotheses = self.generate_hypotheses(goal, self.gateway)

        # Ortalama güven.
        if hypotheses:
            confidence = sum(
                _coerce_confidence(item.get("confidence", 0.7), 0.7) for item in hypotheses
            ) / len(hypotheses)
        else:
            confidence = 0.6
        confidence = max(0.0, min(1.0, float(confidence)))

        if hypotheses:
            lead = hypotheses[0]["statement"]
        else:
            lead = "Belirlenen hedef için varsayılan akıl yürütme sonucu."
        mode_text = (mode or "full").strip() or "full"
        conclusion = (
            f"{mode_text} modunda '{goal[:160]}' hedefi için "
            f"{len(hypotheses)} hipotez üretildi; öne çıkan: {lead[:200]}"
        )

        trace = [
            {
                "step": 1,
                "phase": "analysis",
                "title": "Analiz",
                "detail": (
                    f"Hedef analiz edildi: {goal[:300]}. "
                    f"Mod: {mode_text}; plan adımı: {len(plan)}; bağlam anahtarları: {list(context.keys())[:8]}."
                ),
            },
            {
                "step": 2,
                "phase": "hypothesis",
                "title": "Hipotez",
                "detail": f"{len(hypotheses)} hipotez üretildi ve kanıtlarıyla puanlandı.",
            },
            {
                "step": 3,
                "phase": "conclusion",
                "title": "Sonuç",
                "detail": conclusion,
            },
        ]

        # Plan varsa ek iz adımı ekle (en az 3 zaten var).
        if plan:
            trace.insert(
                1,
                {
                    "step": 2,
                    "phase": "plan",
                    "title": "Plan inceleme",
                    "detail": f"Plan {len(plan)} adım içeriyor; akıl yürütme buna göre şekillendi.",
                },
            )
            # step numaralarını yeniden sırala
            for index, item in enumerate(trace, start=1):
                item["step"] = index

        return {
            "hypotheses": hypotheses,
            "trace": trace,
            "conclusion": conclusion,
            "confidence": confidence,
        }
