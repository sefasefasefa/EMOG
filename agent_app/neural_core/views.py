"""NEURAL CORE sayfası ve JSON API uçları."""
import json
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from agent_app.models import User
from agent_app.permissions import current_user, require_auth, require_feature

from . import config as neural_config
from . import evolution, experience, preferences
from .gateway import ModelGateway
from .kernel import CognitiveKernel
from .models import (
    NeuralExperience,
    NeuralGoal,
    NeuralPreference,
    NeuralProposal,
    NeuralRun,
    NeuralToolApproval,
)
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


# ======================================================================
# Yardımcılar
# ======================================================================
def _json(payload, status=200):
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def _error(message, code="error", status=400):
    return _json({"ok": False, "error": message, "code": code}, status=status)


def _body(request):
    if request.content_type and "json" in request.content_type:
        try:
            data = json.loads((request.body or b"{}").decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}
    return {key: request.POST.get(key) for key in request.POST}


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _iso(value):
    return value.isoformat() if value else ""


def _subfeature(key):
    """Alt özellik bayrağı; hata durumunda kapalı kabul et (fail-closed)."""
    from audit.feature_store import is_enabled

    try:
        return bool(is_enabled(key))
    except Exception:
        logger.exception("NEURAL alt özellik kontrolü başarısız: %s", key)
        return False


def _feature_denied(key):
    return _error(
        f"'{key}' özelliği yönetici panelinden devre dışı bırakılmış.",
        "feature_disabled",
        403,
    )


def _serialize_run(run):
    return {
        "id": str(run.id),
        "goal": run.goal,
        "mode": run.mode,
        "status": run.status,
        "provider": run.provider,
        "plan": run.plan,
        "alternatives": run.alternatives,
        "research": run.research,
        "result": run.result,
        "evaluation": run.evaluation,
        "error": run.error,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at),
    }


def _serialize_preference(item):
    return {
        "key": item.key,
        "value": item.value,
        "confidence": item.confidence,
        "source": item.source,
        "updated_at": _iso(item.updated_at),
    }


def _serialize_proposal(item):
    return {
        "id": str(item.id),
        "title": item.title,
        "rationale": item.rationale,
        "changes": item.changes,
        "risk": item.risk,
        "status": item.status,
        "created_at": _iso(item.created_at),
        "applied_at": _iso(item.applied_at),
    }


def _serialize_approval(item):
    return {
        "id": str(item.id),
        "tool": item.tool,
        "args": item.args,
        "status": item.status,
        "output": item.output,
        "created_at": _iso(item.created_at),
    }


def _serialize_goal(item):
    return {
        "id": str(item.id),
        "title": item.title,
        "mode": item.mode,
        "interval_minutes": item.interval_minutes,
        "enabled": item.enabled,
        "last_run_at": _iso(item.last_run_at),
        "next_run_at": _iso(item.next_run_at),
        "last_result": item.last_result,
    }


def _run_background(user_id, run_id):
    close_old_connections()
    try:
        user = User.objects.filter(id=user_id).first()
        run = NeuralRun.objects.filter(id=run_id).first()
        if user is None or run is None:
            return
        CognitiveKernel(user).run(run.goal, run.mode, run=run)
    except Exception as exc:
        logger.exception("NEURAL arka plan çalıştırma hatası")
        try:
            NeuralRun.objects.filter(
                id=run_id, status__in=["pending", "running"]
            ).update(
                status="failed",
                error=str(exc)[:4000],
                finished_at=timezone.now(),
            )
        except Exception:
            logger.exception("NEURAL arka plan hata durumu kaydedilemedi")
    finally:
        close_old_connections()


# ======================================================================
# Sayfa
# ======================================================================
@require_auth
def neural_page(request):
    """Neural Core ayrı bölüm değil; yetenekler EMOG/chat içine entegredir."""
    return redirect("/chat/?neural=1")


# ======================================================================
# Durum ve çalıştırmalar
# ======================================================================
@require_feature("neural_core")
@require_http_methods(["GET"])
def neural_status(request):
    user = current_user(request)
    gateway = ModelGateway()
    runs = NeuralRun.objects.filter(user=user)
    return _json({
        "ok": True,
        "offline": neural_config.offline(),
        "providers": gateway.available(),
        "active_provider": gateway.active_provider(),
        "runs": {
            "total": runs.count(),
            "completed": runs.filter(status="completed").count(),
            "failed": runs.filter(status="failed").count(),
        },
        "experiences": NeuralExperience.objects.filter(user=user).count(),
        "proposals_pending": NeuralProposal.objects.filter(user=user, status="pending").count(),
        "goals_enabled": NeuralGoal.objects.filter(user=user, enabled=True).count(),
        "stats": experience.stats(user),
        "modes": [{"key": key, "label": label} for key, label in NeuralRun.MODE_CHOICES],
    })


@require_feature("neural_core")
@require_http_methods(["GET"])
def neural_runs_list(request):
    user = current_user(request)
    runs = [_serialize_run(item) for item in NeuralRun.objects.filter(user=user)[:20]]
    return _json({"ok": True, "runs": runs})


@require_feature("neural_core")
@require_http_methods(["POST"])
def neural_run_create(request):
    user = current_user(request)
    body = _body(request)
    goal = str(body.get("goal") or "").strip()
    mode = str(body.get("mode") or "full").strip()
    if not goal:
        return _error("Hedef boş olamaz.", "goal_required")
    cfg = neural_config.get_config()
    if len(goal) > int(cfg.get("MAX_GOAL_CHARS", 4000)):
        return _error("Hedef çok uzun.", "goal_too_long")
    valid_modes = {choice[0] for choice in NeuralRun.MODE_CHOICES}
    if mode not in valid_modes:
        mode = "full"

    run = NeuralRun.objects.create(user=user, goal=goal, mode=mode)
    if cfg.get("SYNC_RUNS"):
        run = CognitiveKernel(user).run(goal, mode, run=run)
    else:
        thread = threading.Thread(
            target=_run_background, args=(user.id, run.id), daemon=True
        )
        thread.start()
    run.refresh_from_db()
    return _json({"ok": True, "run": _serialize_run(run)})


@require_feature("neural_core")
@require_http_methods(["GET"])
def neural_run_detail(request, run_id):
    user = current_user(request)
    run = NeuralRun.objects.filter(id=run_id, user=user).first()
    if run is None:
        return _error("Çalıştırma bulunamadı.", "run_not_found", 404)
    return _json({"ok": True, "run": _serialize_run(run)})


# ======================================================================
# Tercihler
# ======================================================================
@require_feature("neural_core")
@require_http_methods(["GET", "POST"])
def neural_preferences_api(request):
    user = current_user(request)
    if request.method == "GET":
        items = [
            _serialize_preference(item)
            for item in NeuralPreference.objects.filter(user=user)
        ]
        return _json({"ok": True, "preferences": items})

    body = _body(request)
    key = str(body.get("key") or "").strip()
    if not key:
        return _error("Tercih anahtarı gerekli.", "key_required")
    if "value" not in body:
        return _error("Tercih değeri gerekli.", "value_required")
    try:
        item = preferences.set_value(
            user, key, body.get("value"), source=str(body.get("source") or "user")
        )
    except ValueError as exc:
        return _error(str(exc), "invalid_preference")
    return _json({"ok": True, "preference": _serialize_preference(item)})


# ======================================================================
# Kendini geliştirme önerileri
# ======================================================================
@require_feature("neural_core")
@require_http_methods(["GET", "POST"])
def neural_proposals_api(request):
    user = current_user(request)
    if request.method == "POST":
        if not _subfeature("neural_evolution"):
            return _feature_denied("neural_evolution")
        try:
            created = evolution.propose(user)
        except Exception as exc:
            logger.exception("NEURAL öneri üretme hatası")
            return _error(str(exc), "proposal_failed", 500)
        return _json({"ok": True, "created": [_serialize_proposal(item) for item in created]})

    items = [
        _serialize_proposal(item)
        for item in NeuralProposal.objects.filter(user=user)[:50]
    ]
    return _json({"ok": True, "proposals": items})


@require_feature("neural_core")
@require_http_methods(["POST"])
def neural_proposal_decide(request, proposal_id, action):
    if not _subfeature("neural_evolution"):
        return _feature_denied("neural_evolution")
    user = current_user(request)
    proposal = NeuralProposal.objects.filter(id=proposal_id, user=user).first()
    if proposal is None:
        return _error("Öneri bulunamadı.", "proposal_not_found", 404)
    if action == "apply":
        changed = evolution.apply(user, proposal)
        message = "Öneri uygulandı." if changed else "Öneri uygulanamadı veya güvenli değil."
    elif action == "reject":
        changed = evolution.reject(user, proposal)
        message = "Öneri reddedildi." if changed else "Öneri reddedilemedi."
    else:
        return _error("Geçersiz işlem.", "invalid_action")
    if not changed:
        return _error(message, "proposal_not_applied")
    proposal.refresh_from_db()
    return _json({"ok": True, "proposal": _serialize_proposal(proposal), "message": message})


# ======================================================================
# Bilgisayar araçları (izin/onay kontrollü)
# ======================================================================
@require_feature("neural_core")
@require_http_methods(["GET"])
def neural_tools_api(request):
    if not _subfeature("neural_tools"):
        return _feature_denied("neural_tools")
    user = current_user(request)
    registry = ToolRegistry(user)
    approvals = [
        _serialize_approval(item)
        for item in NeuralToolApproval.objects.filter(user=user)[:30]
    ]
    return _json({
        "ok": True,
        "tools": registry.list_tools(),
        "approvals": approvals,
        "pending": sum(1 for item in approvals if item["status"] == "pending"),
    })


@require_feature("neural_core")
@require_http_methods(["POST"])
def neural_tool_action(request, action):
    if not _subfeature("neural_tools"):
        return _feature_denied("neural_tools")
    user = current_user(request)
    registry = ToolRegistry(user)
    body = _body(request)
    try:
        if action == "list":
            result = registry.list_files(str(body.get("path") or "."))
        elif action == "read":
            result = registry.read_file(str(body.get("path") or ""))
        elif action == "write":
            approval = registry.request_write(
                str(body.get("path") or ""), body.get("content", "")
            )
            return _json({"ok": True, "approval": _serialize_approval(approval)})
        elif action == "command":
            approval = registry.request_command(
                str(body.get("command") or ""), cwd=str(body.get("cwd") or ".")
            )
            return _json({"ok": True, "approval": _serialize_approval(approval)})
        else:
            return _error("Geçersiz araç işlemi.", "invalid_action")
    except (ValueError, FileNotFoundError) as exc:
        return _error(str(exc), "tool_error")
    except Exception as exc:
        logger.exception("NEURAL araç hatası")
        return _error(str(exc), "tool_error", 500)
    return _json({"ok": True, "result": result})


@require_feature("neural_core")
@require_http_methods(["POST"])
def neural_tool_decide(request, approval_id):
    if not _subfeature("neural_tools"):
        return _feature_denied("neural_tools")
    user = current_user(request)
    body = _body(request)
    try:
        result = ToolRegistry(user).decide(
            approval_id, _truthy(body.get("approve")), user
        )
    except LookupError as exc:
        return _error(str(exc), "approval_not_found", 404)
    except Exception as exc:
        logger.exception("NEURAL onay hatası")
        return _error(str(exc), "approval_failed", 500)
    return _json({"ok": result["ok"], "status": result["status"], "output": result["output"]})


# ======================================================================
# Otonom hedefler
# ======================================================================
@require_feature("neural_core")
@require_http_methods(["GET", "POST"])
def neural_goals_api(request):
    if not _subfeature("neural_autonomy"):
        return _feature_denied("neural_autonomy")
    user = current_user(request)
    cfg = neural_config.get_config()
    if request.method == "GET":
        items = [_serialize_goal(item) for item in NeuralGoal.objects.filter(user=user)]
        return _json({"ok": True, "goals": items})

    body = _body(request)
    title = str(body.get("title") or "").strip()
    mode = str(body.get("mode") or "full").strip()
    if not title:
        return _error("Hedef başlığı gerekli.", "title_required")
    if len(title) > int(cfg.get("MAX_GOAL_CHARS", 4000)):
        return _error("Hedef çok uzun.", "goal_too_long")
    if NeuralGoal.objects.filter(user=user).count() >= int(cfg.get("AUTONOMY_MAX_GOALS", 10)):
        return _error("Otonom hedef sınırı aşıldı.", "goal_limit")
    minimum = int(cfg.get("AUTONOMY_MIN_INTERVAL_MINUTES", 15))
    try:
        interval = max(minimum, int(body.get("interval_minutes") or cfg.get(
            "AUTONOMY_DEFAULT_INTERVAL_MINUTES", 60
        )))
    except (TypeError, ValueError):
        interval = int(cfg.get("AUTONOMY_DEFAULT_INTERVAL_MINUTES", 60))
    valid_modes = {choice[0] for choice in NeuralRun.MODE_CHOICES}
    if mode not in valid_modes:
        mode = "full"
    from django.utils import timezone
    goal = NeuralGoal.objects.create(
        user=user,
        title=title,
        mode=mode,
        interval_minutes=interval,
        next_run_at=timezone.now() + timedelta(minutes=interval),
    )
    return _json({"ok": True, "goal": _serialize_goal(goal)})


@require_feature("neural_core")
@require_http_methods(["POST"])
def neural_goal_toggle(request, goal_id):
    if not _subfeature("neural_autonomy"):
        return _feature_denied("neural_autonomy")
    from django.utils import timezone

    user = current_user(request)
    goal = NeuralGoal.objects.filter(id=goal_id, user=user).first()
    if goal is None:
        return _error("Otonom hedef bulunamadı.", "goal_not_found", 404)
    body = _body(request)
    goal.enabled = _truthy(body["enabled"]) if "enabled" in body else not goal.enabled
    if goal.enabled and (goal.next_run_at is None or goal.next_run_at <= timezone.now()):
        goal.next_run_at = timezone.now()
    goal.save(update_fields=["enabled", "next_run_at"])
    return _json({"ok": True, "goal": _serialize_goal(goal)})
