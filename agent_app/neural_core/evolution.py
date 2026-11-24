"""Kontrollü kendini geliştirme.

Yalnızca güvenli politika anahtarları önerilir ve yalnızca kullanıcı onayıyla
uygulanır. Model ağırlıkları veya kaynak kodu bu modül tarafından değiştirilmez.
"""
import logging

from django.utils import timezone

from . import config as neural_config
from .experience import recent, stats
from .models import NeuralProposal
from .preferences import get_value, set_value

logger = logging.getLogger(__name__)


def _pending_duplicate(user, changes):
    return NeuralProposal.objects.filter(
        user=user, status="pending", changes=changes
    ).exists()


def _create(user, title, rationale, changes, risk="low"):
    if _pending_duplicate(user, changes):
        return None
    return NeuralProposal.objects.create(
        user=user,
        title=title[:300],
        rationale=rationale,
        changes=changes,
        risk=risk,
    )


def propose(user):
    """Son deneyimlere göre güvenli politika önerileri üret."""
    created = []
    rows = recent(user, limit=20)
    summary = stats(user)

    if rows and summary["average"] < 60:
        proposal = _create(
            user,
            "Plan derinliğini artır",
            f"Son {summary['count']} deneyimde ortalama puan {summary['average']}. "
            "Daha derin plan, eksik doğrulama adımlarını azaltabilir.",
            {"planner.depth": "deep"},
        )
        if proposal:
            created.append(proposal)

    failures = sum(1 for row in rows if row.outcome == "failure")
    if failures >= 2 and get_value(user, "research.enabled", None) is not True:
        proposal = _create(
            user,
            "Araştırmayı aç",
            f"Son deneyimlerde {failures} başarısızlık var. Dış kaynak taraması "
            "varsayımları erken doğrulayabilir.",
            {"research.enabled": True},
        )
        if proposal:
            created.append(proposal)

    if rows and get_value(user, "prompt.addendum", None) is None:
        lesson = next((row.lesson for row in rows if row.lesson), "")
        proposal = _create(
            user,
            "Deneyim dersini isteme ekle",
            "Geçmiş derslerin bir kısmı sonraki çalıştırmalara taşınmıyor. "
            "Kısa bir ek yönerge tekrar eden hataları azaltabilir.",
            {"prompt.addendum": lesson[:500]},
        )
        if proposal:
            created.append(proposal)

    return created


def apply(user, proposal):
    """Öneriyi yalnızca güvenli anahtarlarla ve sahibi tarafından uygula."""
    if proposal is None or proposal.user_id != user.id:
        return False
    if proposal.status != "pending":
        return False
    changes = proposal.changes if isinstance(proposal.changes, dict) else {}
    if not changes:
        return False
    safe_keys = set(neural_config.get_config().get("SAFE_POLICY_KEYS") or [])
    if not set(changes).issubset(safe_keys):
        logger.warning(
            "NEURAL evrim güvenli olmayan anahtar reddedildi: %s",
            sorted(set(changes) - safe_keys),
        )
        return False

    for key, value in changes.items():
        set_value(user, key, value, source="evolution", confidence=0.8)
    proposal.status = "applied"
    proposal.applied_at = timezone.now()
    proposal.save(update_fields=["status", "applied_at"])
    return True


def reject(user, proposal):
    if proposal is None or proposal.user_id != user.id:
        return False
    if proposal.status != "pending":
        return False
    proposal.status = "rejected"
    proposal.save(update_fields=["status"])
    return True
