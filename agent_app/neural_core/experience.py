"""Deneyimden öğrenme: sonuç, puan ve tekrar kullanılabilir ders kaydı."""
from . import config as neural_config
from .models import NeuralExperience


def _default_lesson(goal, outcome, score):
    short = (goal or "").strip()[:140]
    if outcome == "success":
        return f"'{short}' hedefinde izlenen yol {score} puanla işe yaradı; benzer görevlerde tekrarla."
    if outcome == "failure":
        return f"'{short}' hedefi {score} puanla başarısız oldu; planı daha küçük adımlara böl ve varsayımları erken test et."
    return f"'{short}' hedefi kısmi sonuç verdi ({score}); eksik kalan doğrulama adımını güçlendir."


def record(user, goal, outcome="partial", score=0, lesson="", context=None, run=None):
    """Bir çalıştırmanın deneyimini kaydet."""
    if outcome not in dict(NeuralExperience.OUTCOME_CHOICES):
        outcome = "partial"
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 0
    lesson = (lesson or "").strip() or _default_lesson(goal, outcome, score)
    return NeuralExperience.objects.create(
        user=user,
        run=run,
        goal=(goal or "")[:4000],
        outcome=outcome,
        score=score,
        lesson=lesson[:2000],
        context=context if isinstance(context, dict) else {},
    )


def recent(user, limit=None):
    """Kullanıcının son deneyimleri (en yeni önce)."""
    limit = int(limit or neural_config.get_config().get("MAX_CONTEXT_EXPERIENCES", 20))
    return list(
        NeuralExperience.objects.filter(user=user).order_by("-created_at")[: max(1, limit)]
    )


def record_shared(user, goal, outcome="partial", score=0, lesson="", context=None, run=None):
    """Paylaşılan (cross-user) bir deneyim kaydı oluştur."""
    if outcome not in dict(NeuralExperience.OUTCOME_CHOICES):
        outcome = "partial"
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        score = 0
    lesson = (lesson or "").strip() or _default_lesson(goal, outcome, score)
    return NeuralExperience.objects.create(
        user=user,
        run=run,
        goal=(goal or "")[:4000],
        outcome=outcome,
        score=score,
        lesson=lesson[:2000],
        context=context if isinstance(context, dict) else {},
        shared=True,
    )


def shared_recent(limit=20):
    """Tüm kullanıcılara açık paylaşılan deneyimler (en yeni önce)."""
    limit = int(limit or 20)
    if limit < 1:
        limit = 1
    return list(
        NeuralExperience.objects.filter(shared=True).order_by("-created_at")[:limit]
    )


def shared_lessons(limit=10):
    """Paylaşılan deneyimlerden ders metinleri."""
    limit = int(limit or 10)
    return [
        item.lesson
        for item in shared_recent(limit=limit)
        if (item.lesson or "").strip()
    ]


def lessons(user, limit=None):
    """Son deneyimlerden ders metinleri."""
    limit = int(limit or neural_config.get_config().get("MAX_LESSONS", 10))
    return [
        item.lesson
        for item in recent(user, limit=limit)
        if (item.lesson or "").strip()
    ]


def stats(user):
    """Ortalama puan ve sonuç dağılımı."""
    rows = recent(user, limit=50)
    if not rows:
        return {"count": 0, "average": 0, "success": 0, "failure": 0, "partial": 0}
    counts = {"success": 0, "failure": 0, "partial": 0}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return {
        "count": len(rows),
        "average": round(sum(row.score for row in rows) / len(rows), 1),
        **counts,
    }
