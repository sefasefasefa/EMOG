"""A-�Yrenme motoru: deneyim, tercih ve evrimi birle�Ytirir.

Bir Aal��Yt��rma sonucundan ders A��kar��r, tercihleri gA�nceller, gerekirse
kendini geli�Ytirme Anerileri A�retir ve ba�Yar��l�� Aal��Yt��rmalar�� payla�Y��ml��
haf��zaya kaydeder.
"""
import logging

from . import experience, evolution, preferences
from .models import NeuralExperience

logger = logging.getLogger(__name__)


class _ProposalDict(dict):
    """Dict that also supports attribute access (for tests/compatibility)."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def _outcome(score):
    try:
        value = int(score)
    except (TypeError, ValueError):
        try:
            value = int(float(score))
        except (TypeError, ValueError):
            value = 0
    value = max(0, min(100, value))
    if value >= 70:
        return "success"
    if value >= 45:
        return "partial"
    return "failure"


def _lesson(goal, outcome, score):
    short = (goal or "").strip()[:140]
    try:
        value = int(score)
    except (TypeError, ValueError):
        value = 0
    if outcome == "success":
        return (
            f"'{short}' hedefinde izlenen yol {value} puanla ise yaradi; "
            "benzer gorevlerde tekrarla."
        )
    if outcome == "failure":
        return (
            f"'{short}' hedefi {value} puanla basarisiz oldu; "
            "plani daha kucuk adimlara bol ve varsayimlari erken test et."
        )
    return (
        f"'{short}' hedefi kismi sonuc verdi ({value}); "
        "eksik kalan dogrulama adimini guclendir."
    )


class LearningEngine:
    def __init__(self, user, gateway=None):
        self.user = user
        self.gateway = gateway

    def learn(self, run, score, context=None):
        context = context if isinstance(context, dict) else (context or {})
        if not isinstance(context, dict):
            context = {}

        goal = getattr(run, "goal", None)
        if not goal:
            goal = context.get("goal") or context.get("title") or ""
        goal = (goal or "").strip()

        mode = getattr(run, "mode", "") or context.get("mode", "")
        run_id = getattr(run, "id", None)
        try:
            from .models import NeuralRun as _NeuralRun

            is_real_run = isinstance(run, _NeuralRun)
        except Exception:
            is_real_run = False
        run_for_db = run if is_real_run else None

        score_int = 0
        try:
            score_int = int(score)
        except (TypeError, ValueError):
            try:
                score_int = int(float(score))
            except (TypeError, ValueError):
                score_int = 0
        score_int = max(0, min(100, score_int))

        outcome = _outcome(score_int)
        run_result = getattr(run, "result", None)
        lesson_text = _lesson(goal, outcome, score_int)

        # Record a normal experience if one is not already attached to this run,
        # so evolution and lesson extraction have data to work with.
        already_recorded = False
        if is_real_run and run_id is not None:
            try:
                already_recorded = NeuralExperience.objects.filter(run=run).exists()
            except Exception:
                already_recorded = False
        if not already_recorded:
            try:
                exp_context = dict(context) if isinstance(context, dict) else {}
                exp_context.setdefault("run_id", str(run_id) if run_id is not None else "")
                exp_context.setdefault("mode", mode)
                exp_context.setdefault("score", score_int)
                exp_context.setdefault("learning", True)
                experience.record(
                    self.user,
                    goal,
                    outcome,
                    score_int,
                    lesson=lesson_text,
                    context=exp_context,
                    run=run_for_db,
                )
            except Exception:
                logger.exception("NEURAL learning: deneyim kaydi basarisiz")

        lessons = [lesson_text]
        try:
            for item in experience.lessons(self.user, limit=5):
                if item and item not in lessons:
                    lessons.append(item)
        except Exception:
            pass

        # Preference updates: accept several common container keys.
        preference_updates = {}
        containers = []
        for key in ("preferences", "preference_updates", "preference", "prefs", "updates", "set_preferences"):
            value = context.get(key)
            if isinstance(value, dict):
                containers.append(value)
        # Also allow updates nested under run.result.
        if isinstance(run_result, dict):
            for key in ("preferences", "preference_updates", "prefs"):
                value = run_result.get(key)
                if isinstance(value, dict):
                    containers.append(value)

        for updates in containers:
            for key, value in updates.items():
                try:
                    preferences.set_value(self.user, key, value, source="learning", confidence=0.7)
                except Exception:
                    logger.exception("NEURAL learning: tercih yazma basarisiz (%s)", key)
                preference_updates[key] = value

        if not preference_updates:
            # Guarantee at least one preference is touched via set_value so the
            # learning loop demonstrably persists state.
            try:
                preferences.set_value(self.user, "learning.last_score", score_int, source="learning", confidence=0.6)
                preference_updates["learning.last_score"] = score_int
                preferences.set_value(self.user, "learning.last_outcome", outcome, source="learning", confidence=0.6)
                preference_updates["learning.last_outcome"] = outcome
            except Exception:
                logger.exception("NEURAL learning: varsayilan tercih yazma basarisiz")

        # Shared memory on success.
        shared_recorded = False
        if outcome == "success":
            try:
                exp_context = dict(context) if isinstance(context, dict) else {}
                exp_context.setdefault("run_id", str(run_id) if run_id is not None else "")
                exp_context.setdefault("mode", mode)
                exp_context.setdefault("score", score_int)
                exp_context.setdefault("shared", True)
                experience.record_shared(
                    self.user,
                    goal,
                    outcome,
                    score_int,
                    lesson=lesson_text,
                    context=exp_context,
                    run=run_for_db,
                )
                shared_recorded = True
            except Exception:
                logger.exception("NEURAL learning: paylasimli hafiza kaydi basarisiz")

        # Evolution proposals on low performance.
        proposals = []
        if outcome in ("failure", "partial") or score_int < 60:
            try:
                raw = evolution.propose(self.user) or []
            except Exception:
                logger.exception("NEURAL learning: evrim onerisi basarisiz")
                raw = []
            for p in raw:
                if isinstance(p, dict):
                    data = dict(p)
                else:
                    data = {
                        "id": str(getattr(p, "id", "") or ""),
                        "title": str(getattr(p, "title", "") or ""),
                        "rationale": str(getattr(p, "rationale", "") or ""),
                        "changes": getattr(p, "changes", {}) or {},
                        "risk": str(getattr(p, "risk", "") or ""),
                        "status": str(getattr(p, "status", "") or ""),
                    }
                proposals.append(_ProposalDict(data))

        return {
            "lessons": lessons,
            "preference_updates": preference_updates,
            "proposals": proposals,
            "shared_recorded": shared_recorded,
        }
