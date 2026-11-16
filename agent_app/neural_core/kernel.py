"""Bilişsel çekirdek: plan → keşif → araştırma → değerlendirme → öğrenme."""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import config as neural_config
from . import discovery, evaluation, experience, learning, planner, preferences, reasoning, research
from .gateway import ModelGateway
from .models import NeuralDiscovery, NeuralGoal, NeuralRun

logger = logging.getLogger(__name__)


class CognitiveKernel:
    def __init__(self, user, gateway=None):
        self.user = user
        self.gateway = gateway or ModelGateway()
        self.config = neural_config.get_config()

    def run(self, goal, mode="full", run=None):
        goal = (goal or "").strip()
        max_goal = int(self.config.get("MAX_GOAL_CHARS", 4000))
        if not goal:
            raise ValueError("hedef boş olamaz")
        if len(goal) > max_goal:
            raise ValueError(f"hedef çok uzun (en fazla {max_goal} karakter)")
        valid_modes = {choice[0] for choice in NeuralRun.MODE_CHOICES}
        if mode not in valid_modes:
            mode = self.config.get("DEFAULT_MODE", "full")

        run = run or NeuralRun.objects.create(user=self.user, goal=goal, mode=mode)
        run.goal = goal
        run.mode = mode
        run.status = "running"
        run.provider = self.gateway.active_provider()
        run.started_at = timezone.now()
        run.save(update_fields=["goal", "mode", "status", "provider", "started_at"])

        try:
            context = {
                "preferences": preferences.all_values(self.user),
                "experiences": [
                    {
                        "goal": item.goal[:300],
                        "outcome": item.outcome,
                        "score": item.score,
                        "lesson": item.lesson[:500],
                    }
                    for item in experience.recent(self.user)
                ],
                "shared_lessons": experience.shared_lessons(limit=5),
            }
            plan = planner.build_plan(goal, mode, context, self.gateway)

            reasoning_engine = reasoning.ReasoningEngine(self.user, self.gateway)
            reasoning_result = reasoning_engine.reason(goal, mode, context, plan)

            hypotheses = discovery.hypotheses(goal, self.gateway, max_items=3)
            alternatives = []
            if mode in ("full", "explore"):
                alternatives = discovery.generate(
                    goal, mode, self.gateway,
                    max_items=self.config.get("MAX_ALTERNATIVES", 4),
                )
                if alternatives:
                    NeuralDiscovery.objects.create(
                        user=self.user,
                        run=run,
                        goal=goal,
                        candidates=alternatives,
                        selected=alternatives[0]["name"],
                    )

            research_enabled = bool(
                preferences.get_value(self.user, "research.enabled", True)
            ) and mode in ("full", "research")
            research_result = research.run(
                goal,
                enabled=research_enabled,
                gateway=self.gateway,
            )

            result = {
                "summary": (
                    f"'{goal[:160]}' hedefi {mode} modunda işlendi. "
                    f"{len(plan)} plan adımı, {len(alternatives)} alternatif ve "
                    f"{len(research_result.get('findings') or [])} araştırma bulgusu üretildi."
                ),
                "selected_approach": alternatives[0]["name"] if alternatives else "",
                "next_steps": [step["title"] for step in plan[:6]],
                "lessons": experience.lessons(self.user, limit=5),
                "reasoning": reasoning_result,
                "hypotheses": hypotheses,
                "offline": neural_config.offline(),
                "provider": run.provider,
                "mode": mode,
            }
            score = evaluation.score_run(
                plan, alternatives, research_result.get("findings") or [], result
            )
            if score["score"] >= 70:
                outcome = "success"
            elif score["score"] >= 45:
                outcome = "partial"
            else:
                outcome = "failure"

            exp = experience.record(
                self.user,
                goal,
                outcome,
                score["score"],
                context={
                    "run_id": str(run.id),
                    "mode": mode,
                    "gaps": score.get("gaps", []),
                },
                run=run,
            )

            learning_result = learning.LearningEngine(self.user, self.gateway).learn(
                run, score["score"], context={"mode": mode, "gaps": score.get("gaps", [])}
            )

            result["learning"] = learning_result

            run.plan = plan
            run.alternatives = alternatives
            run.research = research_result
            run.result = {**result, "outcome": outcome}
            run.evaluation = score
            run.status = "completed"
            run.finished_at = timezone.now()
            run.save(update_fields=[
                "plan", "alternatives", "research", "result", "evaluation",
                "status", "finished_at", "provider",
            ])
        except Exception as exc:
            logger.exception("NEURAL çalıştırma hatası: %s", exc)
            run.status = "failed"
            run.error = str(exc)[:4000]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at"])

        return run


def _autonomy_allowed():
    """Otonom döngü için üst ve alt özellik bayrakları açık olmalı (fail-closed)."""
    try:
        from audit.feature_store import is_enabled

        return bool(is_enabled("neural_core") and is_enabled("neural_autonomy"))
    except Exception:
        return False


def sweep_stale_runs(minutes=30):
    """Uzun süredir `running` kalan çalıştırmaları hata olarak kapat."""
    cutoff = timezone.now() - timedelta(minutes=max(1, int(minutes)))
    stale = NeuralRun.objects.filter(status="running", updated_at__lt=cutoff)
    return stale.update(
        status="failed",
        error="Çalıştırma zaman aşımına uğradı; arka plan işi tamamlanmadı.",
        finished_at=timezone.now(),
    )


def run_goal_cycle(limit=None, dry_run=False):
    """Zamanı gelen otonom hedefleri çalıştır."""
    if not _autonomy_allowed():
        return {
            "ran": 0,
            "failed": 0,
            "skipped": 0,
            "goals": [],
            "disabled": True,
        }

    sweep_stale_runs()

    cfg = neural_config.get_config()
    now = timezone.now()
    max_goals = int(limit or cfg.get("AUTONOMY_MAX_GOALS", 10))
    minimum = int(cfg.get("AUTONOMY_MIN_INTERVAL_MINUTES", 15))
    due = (
        NeuralGoal.objects.filter(enabled=True)
        .filter(Q(next_run_at__isnull=True) | Q(next_run_at__lte=now))
        .select_related("user")
        .order_by("next_run_at", "created_at")[:max_goals]
    )

    report = {"ran": 0, "failed": 0, "skipped": 0, "goals": []}
    for goal in due:
        if goal.user_id is None:
            report["skipped"] += 1
            continue
        if dry_run:
            report["skipped"] += 1
            report["goals"].append({"id": str(goal.id), "title": goal.title, "dry_run": True})
            continue
        run = None
        try:
            run = CognitiveKernel(goal.user).run(goal.title, goal.mode)
            report["ran"] += 1
            if run.status != "completed":
                report["failed"] += 1
        except Exception as exc:
            logger.exception("NEURAL otonom hedef hatası: %s", exc)
            report["failed"] += 1
        interval = max(minimum, int(goal.interval_minutes or minimum))
        goal.last_run_at = now
        goal.next_run_at = now + timedelta(minutes=interval)
        goal.last_result = {
            "run_id": str(run.id) if run else "",
            "status": run.status if run else "failed",
            "score": (run.evaluation or {}).get("score", 0) if run else 0,
        }
        goal.save(update_fields=["last_run_at", "next_run_at", "last_result"])
        report["goals"].append({
            "id": str(goal.id),
            "title": goal.title,
            "status": run.status if run else "failed",
        })
    return report
