"""Alternatif düşünme: hedef için farklı yaklaşımlar üretip puanlar."""
from . import config as neural_config
from .jsonutil import parse_json


SYSTEM_PROMPT = (
    "Sen EMOG'un keşif motorusun. Aynı hedefe ulaşan farklı yaklaşımlar üret. "
    "Yalnızca JSON dizisi döndür: "
    '[{"name": "...", "summary": "...", "pros": ["..."], "cons": ["..."], "score": 0-100}]. '
    "Yaklaşımlar birbirinden gerçekten farklı olsun."
)


def _coerce_score(value, default=60):
    """LLM'den gelen puanı toleranslı biçimde 0-100 aralığına indir."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(0, min(100, int(number)))


def _as_list(value):
    """String/None/karışık değerleri güvenli bir metin listesine çevir."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text[:300]] if text else []
    if isinstance(value, (list, tuple, set)):
        items = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text[:300])
        return items
    text = str(value).strip()
    return [text[:300]] if text else []


def _candidate(name, summary, pros, cons, score):
    return {
        "name": str(name or "")[:200],
        "summary": str(summary or "")[:1000],
        "pros": _as_list(pros),
        "cons": _as_list(cons),
        "score": _coerce_score(score),
    }


def _templates(goal):
    text = (goal or "").lower()
    candidates = []
    if any(word in text for word in ("api", "servis", "backend", "sunucu")):
        candidates.append(_candidate(
            "Servis odaklı tasarım",
            "İş kurallarını ayrı bir servis katmanında topla; arayüzü ince tut.",
            ["Bağımsız test edilebilir", "Farklı istemcilerle paylaşılabilir"],
            ["Ek altyapı ve sözleşme yönetimi gerektirir"],
            84,
        ))
    if any(word in text for word in ("arayüz", "ui", "tasarım", "mobil", "web", "ekran")):
        candidates.append(_candidate(
            "Kullanıcı deneyimi önce",
            "Önce en kritik kullanıcı akışını uçtan uca çalıştır, sonra genişlet.",
            ["Erken geri bildirim", "Boşa harcanan özellik riski düşük"],
            ["Altyapı borcu birikebilir"],
            82,
        ))
    if any(word in text for word in ("veri", "db", "veritabanı", "model", "rapor")):
        candidates.append(_candidate(
            "Veri modeli önce",
            "Şemayı ve veri akışını netleştir; uygulamayı bunun üzerine kur.",
            ["Tutarlı veri", "İleride migration maliyeti düşük"],
            ["Kullanıcı ihtiyacı geç doğrulanır"],
            80,
        ))
    if any(word in text for word in ("performans", "hız", "ölçek", "yük")):
        candidates.append(_candidate(
            "Performans odaklı",
            "Darboğazı ölç, yalnızca kanıtlanmış noktayı optimize et.",
            ["Ölçülebilir kazanç", "Gereksiz karmaşıklıktan kaçınır"],
            ["Erken ölçüm altyapısı gerektirir"],
            78,
        ))

    candidates.extend([
        _candidate(
            "En küçük çalışan sürüm (MVP)",
            "Tek bir değerli akışı en az kodla çalıştır; öğrenmeyi hızlandır.",
            ["Hızlı geri bildirim", "Düşük başlangıç maliyeti"],
            ["Kapsam dışı ihtiyaçlar sonraya kalır"],
            86,
        ),
        _candidate(
            "Araştırma destekli",
            "Önce kaynakları ve benzer çözümleri tara, sonra tasarla.",
            ["Kanıta dayalı karar", "Bilinen tuzaklardan kaçınır"],
            ["Başlangıç daha yavaş"],
            74,
        ),
        _candidate(
            "Hızlı deneme (riskli)",
            "Varsayımı en kısa yoldan test et; başarısızsa geri dön.",
            ["Çok hızlı öğrenme", "Düşük planlama maliyeti"],
            ["Teknik borç ve yeniden yazma riski yüksek"],
            58,
        ),
    ])
    return candidates


def generate(goal, mode="explore", gateway=None, max_items=None):
    """Puanlanmış alternatifleri büyükten küçüğe döndür."""
    cfg = neural_config.get_config()
    limit = max(2, int(max_items or cfg.get("MAX_ALTERNATIVES", 4)))
    candidates = []

    if gateway is not None and not neural_config.offline():
        try:
            text = gateway.complete(SYSTEM_PROMPT, f"Hedef: {goal}", purpose="research")
        except Exception:
            text = ""
        parsed = parse_json(text, list)
        for item in parsed or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or "").strip()
            if not name:
                continue
            candidates.append(_candidate(
                name[:200],
                str(item.get("summary") or item.get("description") or "")[:1000],
                _as_list(item.get("pros")),
                _as_list(item.get("cons")),
                item.get("score", 60),
            ))

    if len(candidates) < 2:
        candidates = _templates(goal)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:limit]


def hypotheses(goal, gateway=None, max_items=3):
    """Hedef için test edilebilir hipotezler üret."""
    limit = max(1, int(max_items or 3))
    goal = (goal or "").strip()
    result = []
    if gateway is not None and not neural_config.offline():
        try:
            text = gateway.complete(
                "Hipotez üret. JSON dizisi: [{\"statement\": \"...\", \"confidence\": 0-1}]",
                f"Hedef: {goal}",
                purpose="research",
            )
        except Exception:
            text = ""
        parsed = parse_json(text, list)
        for item in parsed or []:
            if isinstance(item, str):
                stmt = item.strip()
                conf = 0.5
            elif isinstance(item, dict):
                stmt = str(item.get("statement") or item.get("hypothesis") or "").strip()
                conf = item.get("confidence", 0.5)
            else:
                continue
            if not stmt:
                continue
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                conf = 0.5
            result.append({"statement": stmt[:500], "confidence": max(0.0, min(1.0, conf))})
            if len(result) >= limit:
                break
    if not result:
        result = [
            {"statement": f"'{goal[:120]}' hedefi için en küçük çalışan çözüm yeterlidir.", "confidence": 0.6},
            {"statement": "Mevcut veri ve bağımlılıklar planı destekliyor.", "confidence": 0.5},
        ]
    return result[:limit]


def merge_technologies(candidates):
    """Aday yaklaşımları teknoloji birleşimleri olarak birleştir."""
    if not candidates:
        candidates = [{"name": "Varsayılan", "summary": "", "score": 60}]
    merged = []
    for idx, cand in enumerate(candidates):
        name = cand.get("name") if isinstance(cand, dict) else str(cand)
        merged.append({
            "name": str(name or f"Yaklaşım {idx + 1}")[:200],
            "combined": [str(name or f"Yaklaşım {idx + 1}")],
            "score": cand.get("score", 60) if isinstance(cand, dict) else 60,
        })
    if len(merged) >= 2:
        merged.append({
            "name": "Birleşik yaklaşım",
            "combined": [m["name"] for m in merged[:2]],
            "score": max(m.get("score", 60) for m in merged[:2]),
        })
    return merged


def experiment_plan(candidate):
    """Bir aday için deney ve test planı üret."""
    name = ""
    if isinstance(candidate, dict):
        name = str(candidate.get("name") or candidate.get("title") or "")
    if not name:
        name = str(candidate or "aday")
    return {
        "candidate": name[:200],
        "steps": [
            f"'{name}' için minimal prototip oluştur",
            "Kabul kriterlerini tanımla",
            "Ölçüm al ve sonucu kaydet",
        ],
        "success_criteria": [
            "Prototip kabul kriterlerini karşılar",
            "Ölçülen sonuç temel çizginin üzerinde",
        ],
    }
