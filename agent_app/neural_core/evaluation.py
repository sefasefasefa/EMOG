"""Çalıştırma çıktısını ölçen sezgisel değerlendirici."""


def score_run(plan, alternatives, findings, result):
    """0-100 arası puan, güçlü yönler ve boşluklar döndür."""
    plan = plan or []
    alternatives = alternatives or []
    findings = findings or []
    result = result or {}

    score = 25
    score += min(len(plan), 8) * 4
    score += min(len(alternatives), 4) * 4
    score += min(len(findings), 5) * 3
    if result.get("summary"):
        score += 10
    if result.get("lessons"):
        score += 3
    score = max(0, min(100, score))

    strengths = []
    gaps = []
    if len(plan) >= 5:
        strengths.append("Plan yeterince ayrıntılı")
    else:
        gaps.append("Plan adımları yetersiz")
    if len(alternatives) >= 2:
        strengths.append("Birden fazla yaklaşım karşılaştırıldı")
    else:
        gaps.append("Alternatif yaklaşım yok")
    if findings:
        strengths.append("Dış kaynaklardan kanıt toplandı")
    else:
        gaps.append("Araştırma bulgusu yok (çevrimdışı olabilir)")
    if result.get("summary"):
        strengths.append("Sonuç özeti üretildi")
    else:
        gaps.append("Sonuç özeti eksik")

    return {
        "score": score,
        "strengths": strengths,
        "gaps": gaps,
        "verdict": "güçlü" if score >= 75 else "yeterli" if score >= 50 else "zayıf",
    }
