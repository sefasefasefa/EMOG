import subprocess
import sys
import threading
import uuid
import time
import asyncio
import re
from datetime import datetime
import os
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from django import forms
from django.shortcuts import render, redirect
from django.conf import settings
from openai import OpenAI
from . import nvidia_net
from . import project_store
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .project_scanner import (
    get_project_context, FILE_TOOLS, TOOL_HANDLERS, execute_tool,
    scan_project, tool_read_file, tool_write_file, tool_list_files,
    tool_grep_files, tool_edit_file, tool_run_command
)
from .permissions import require_auth, current_user
from .models import Conversation, Node, Checkpoint
from .conversation_history import ConversationHistoryService, ConversationManager
from .model_router import get_model_router, select_model_for_task, escalate_model_tier, select_purpose_model

logger = logging.getLogger(__name__)

# Simple in-memory task queue (FIFO) and worker - PER PROJECT QUEUES
# Structure: {project_id: {user_id: [tasks]}}
TASK_QUEUES = {}  # project_id -> {user_id -> [tasks]}
TASK_QUEUE_LOCK = threading.Lock()
TASKS_LOCK = threading.Lock()
_QUEUE_WORKER_STARTED = False
ACTIVE_USERS = set()  # user_ids currently connected
USER_PAUSED = set()   # user_ids with paused queues
PRESENCE_LAST_SEEN = {}
PRESENCE_LOCK = threading.Lock()
_PRESENCE_MONITOR_STARTED = False
PRESENCE_TIMEOUT_SECONDS = 180


def _touch_presence(user_id):
    if not user_id:
        return
    with PRESENCE_LOCK:
        PRESENCE_LAST_SEEN[str(user_id)] = time.time()


def _background_preference(user_id):
    try:
        from .models import User as DBUser
        user = DBUser.objects.filter(id=user_id).only("continue_in_background").first()
        return bool(user and user.continue_in_background)
    except Exception:
        return False


def cancel_user_tasks(user_id, reason):
    """Kullanıcının bekleyen ve çalışan görevlerini iptal et."""
    run_ids = []
    with TASK_QUEUE_LOCK:
        for project_queue in TASK_QUEUES.values():
            if user_id in project_queue:
                project_queue[user_id].clear()
    with TASKS_LOCK:
        try:
            tasks = _load_tasks()
        except Exception:
            tasks = []
        changed = False
        now = datetime.utcnow().isoformat() + "Z"
        for rec in tasks:
            if rec.get("user_id") != user_id:
                continue
            if rec.get("status") not in ("queued", "running"):
                continue
            rec["status"] = "cancelled"
            rec["completed_at"] = now
            rec["error"] = reason
            changed = True
            if rec.get("run_id"):
                run_ids.append(str(rec["run_id"]))
        if changed:
            _save_tasks(tasks)
    if run_ids:
        try:
            from . import task_control
            for run_id in run_ids:
                task_control.cancel_run(run_id)
        except Exception:
            pass
    return run_ids


def set_user_active(user_id, active: bool, continue_in_background=None):
    """Mark user as active/inactive; honour the user's background preference."""
    if continue_in_background is None:
        continue_in_background = _background_preference(user_id)
    with PRESENCE_LOCK:
        if active:
            PRESENCE_LAST_SEEN[str(user_id)] = time.time()
        else:
            PRESENCE_LAST_SEEN.pop(str(user_id), None)
    with TASK_QUEUE_LOCK:
        if active:
            ACTIVE_USERS.add(user_id)
            USER_PAUSED.discard(user_id)
        else:
            ACTIVE_USERS.discard(user_id)
            if continue_in_background:
                # Kuyruk devam etsin: tarayıcı kapalıyken çalışmaya izinli.
                USER_PAUSED.discard(user_id)
            else:
                USER_PAUSED.add(user_id)
    if not active and not continue_in_background:
        cancel_user_tasks(
            user_id,
            "Tarayıcı kapandı ve bu hesap için arka planda devam etme kapalı.",
        )


def _presence_monitor():
    """Sekme çökerse/keepalive gelmezse presence'ı düşür."""
    while True:
        time.sleep(30)
        now = time.time()
        stale = []
        with PRESENCE_LOCK:
            for user_id, seen in list(PRESENCE_LAST_SEEN.items()):
                if now - seen > PRESENCE_TIMEOUT_SECONDS:
                    stale.append(user_id)
                    PRESENCE_LAST_SEEN.pop(user_id, None)
        for user_id in stale:
            try:
                set_user_active(user_id, False, _background_preference(user_id))
            except Exception as exc:
                logger.debug("presence monitor hatasi: %s", exc)


def _get_project_queue(project_id, user_id):
    """Get or create queue for a specific project and user."""
    if project_id not in TASK_QUEUES:
        TASK_QUEUES[project_id] = {}
    if user_id not in TASK_QUEUES[project_id]:
        TASK_QUEUES[project_id][user_id] = []
    return TASK_QUEUES[project_id][user_id]


def clear_project_queue(project_id, user_id):
    """Clear queued tasks for a project without touching the running one."""
    with TASK_QUEUE_LOCK:
        if project_id in TASK_QUEUES and user_id in TASK_QUEUES[project_id]:
            TASK_QUEUES[project_id][user_id].clear()


def cancel_project_tasks(project_id, user_id):
    """Cancel queued/running tasks for one project and signal the runner."""
    with TASK_QUEUE_LOCK:
        if project_id in TASK_QUEUES and user_id in TASK_QUEUES[project_id]:
            TASK_QUEUES[project_id][user_id].clear()
    run_ids = []
    with TASKS_LOCK:
        tasks = _load_tasks()
        now = datetime.utcnow().isoformat() + "Z"
        for task in tasks:
            if (
                task.get("user_id") == user_id
                and task.get("project_id") == project_id
                and task.get("status") in ("queued", "running")
            ):
                task["status"] = "cancelled"
                task["completed_at"] = now
                task["error"] = "Görev iptal edildi."
                if task.get("run_id"):
                    run_ids.append(str(task["run_id"]))
        _save_tasks(tasks)
    if run_ids:
        try:
            from . import task_control
            for run_id in run_ids:
                task_control.cancel_run(run_id)
        except Exception:
            pass


def is_user_paused(user_id):
    """Check if user's queue should be paused."""
    return user_id in USER_PAUSED


def _tasks_path():
    return os.path.join(settings.BASE_DIR, "tasks.json")


def _load_tasks():
    try:
        with open(_tasks_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_tasks(tasks):
    temp_path = _tasks_path() + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, _tasks_path())


def _update_task(run_id, **changes):
    with TASKS_LOCK:
        tasks = _load_tasks()
        for task in tasks:
            if task.get("run_id") == run_id:
                task.update(changes)
                _save_tasks(tasks)
                return


def _task_status(run_id):
    with TASKS_LOCK:
        for task in _load_tasks():
            if task.get("run_id") == run_id:
                return task.get("status")
    return None


def _queue_project_whatsapp_report(project_id, user_id, task_text, status,
                                   result=None, error_text=""):
    """Raporu görev bitişini bloklamadan WhatsApp'a gönder."""
    if not project_id or not user_id:
        return

    def _send():
        try:
            from .project_integrations import send_project_task_report
            output = ""
            stats = None
            if isinstance(result, dict):
                output = str(result.get("output") or result.get("summary") or "")
                stats = result.get("plan") if isinstance(result.get("plan"), dict) else None
            send_project_task_report(
                project_id, user_id, task_text, status,
                output=output, error=error_text, stats=stats,
            )
        except Exception as exc:
            logger.warning("WhatsApp proje raporu kuyruğa alınamadı: %s", exc)

    threading.Thread(target=_send, daemon=True, name="emog-wa-report").start()



def _ensure_build_project(user_id, task_text="", force_new=False):
    """Return a persistent project id, creating one when needed."""
    from . import project_store

    if not force_new:
        existing = project_store.find_latest_project(user_id=user_id)
        if existing:
            if not existing.get("description"):
                project_store.update_project(
                    existing["id"],
                    description=project_store.project_title(task_text)[:400],
                )
            return existing["id"]

    created = project_store.create_project(
        user_id,
        task_text=task_text,
        status="ready",
    )
    return created["id"]


def enqueue_task(task_text, model=None, priority="medium", project_id=None, user_id=None, mode="execute"):
    """Gorevi kuyruga al.

    mode="build"  -> otonom proje uretim dongusu (arastirma -> plan -> yap -> elestir)
    mode="execute"-> mevcut hizli arac dongusu
    """
    run_id = uuid.uuid4().hex
    if priority not in {"low", "medium", "high", "urgent"}:
        priority = "medium"
    if mode not in {"execute", "build"}:
        mode = "execute"
    now = datetime.utcnow().isoformat() + "Z"
    item = {
        "run_id": run_id,
        "task": task_text,
        "model": model,
        "priority": priority,
        "mode": mode,
        "enqueued_at": now,
        "project_id": project_id,
        "user_id": user_id,
    }
    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    try:
        os.makedirs(dirp, exist_ok=True)
        open(os.path.join(dirp, f"{run_id}.log"), "a", encoding="utf-8").close()
    except Exception:
        pass
    with TASK_QUEUE_LOCK:
        # Project-scoped queue
        if project_id:
            project_queue = _get_project_queue(project_id, user_id or "anonymous")
            project_queue.append(item)
            position = len(project_queue)
        else:
            # Fallback for tasks without project_id (backward compatibility)
            # Use a global anonymous queue
            if "anonymous" not in TASK_QUEUES:
                TASK_QUEUES["anonymous"] = {}
            if "anonymous" not in TASK_QUEUES["anonymous"]:
                TASK_QUEUES["anonymous"]["anonymous"] = []
            TASK_QUEUES["anonymous"]["anonymous"].append(item)
            position = len(TASK_QUEUES["anonymous"]["anonymous"])
    with TASKS_LOCK:
        tasks = _load_tasks()
        tasks.append(
            {
                "run_id": run_id,
                "title": task_text[:120],
                "task": task_text,
                "priority": priority,
                "mode": mode,
                "status": "queued",
                "enqueued_at": now,
                "completed_at": None,
                "project_id": project_id,
                "user_id": user_id,
            }
        )
        _save_tasks(tasks)
    return run_id, position


def _parse_ts(value):
    """ISO zaman damgasini (Z dahil) bilincli datetime'a cevir; None -> None."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = _dt.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        return d
    except Exception:
        return None


def _requeue_recent_persisted_tasks(max_age_hours: float = 3.0) -> int:
    """tasks.json'da 'queued' kalan YENI gorevleri bellek ici kuyruga geri al.

    Calistirma kuyrugu bellekte tutulur; sunucu yeniden basladiginda sifirlanir ve
    diske yazilmis 'queued' kayitlar hic calismazdi. Gunler once birakilmis eski
    kayitlari istenmeyen build'ler baslatmasin diye yalnizca taze olanlar alinir.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    try:
        tasks = _load_tasks()
    except Exception:
        return 0
    cutoff = _dt.now(_tz.utc) - _td(hours=max_age_hours)
    added = 0
    with TASK_QUEUE_LOCK:
        already = set()
        for project_queue in TASK_QUEUES.values():
            for user_queue in project_queue.values():
                for pending in user_queue:
                    already.add(pending.get("run_id"))
        for rec in tasks:
            if rec.get("status") != "queued":
                continue
            rid = rec.get("run_id")
            if not rid or rid in already:
                continue
            enq = _parse_ts(rec.get("enqueued_at"))
            if enq is None or enq < cutoff:
                continue
            item = {
                "run_id": rid,
                "task": rec.get("task") or "",
                "model": rec.get("model"),
                "priority": rec.get("priority") or "medium",
                "mode": rec.get("mode") or "execute",
                "enqueued_at": rec.get("enqueued_at"),
                "project_id": rec.get("project_id"),
                "user_id": rec.get("user_id"),
                "conversation_id": rec.get("conversation_id"),
            }
            if item["project_id"]:
                _get_project_queue(item["project_id"], item["user_id"] or "anonymous").append(item)
            else:
                if "anonymous" not in TASK_QUEUES:
                    TASK_QUEUES["anonymous"] = {}
                if "anonymous" not in TASK_QUEUES["anonymous"]:
                    TASK_QUEUES["anonymous"]["anonymous"] = []
                TASK_QUEUES["anonymous"]["anonymous"].append(item)
            already.add(rid)
            added += 1
    return added


def _recover_interrupted_running_tasks() -> int:
    """Sunucu yeniden baslarken 'running' kalmis gorevleri kurtar.

    Build gorevlerinde plan dosyasi varsa gorev failed yapilmaz; yeniden
    kuyruga alinir ve ayni run_id/plan ile kaldigi yerden devam eder.
    Plan dosyasi olmayan eski gorevler failed olarak isaretlenir.
    """
    try:
        tasks = _load_tasks()
    except Exception:
        return 0
    changed = 0
    now = datetime.utcnow().isoformat() + "Z"
    for rec in tasks:
        if rec.get("status") != "running":
            continue
        run_id = str(rec.get("run_id") or "")
        plan_path = os.path.join(settings.BASE_DIR, "task_runs", f"{run_id}.plan.json")
        is_build = (rec.get("mode") or "execute") == "build"
        allow_background = _background_preference(rec.get("user_id"))
        if is_build and run_id and os.path.exists(plan_path) and allow_background:
            rec["status"] = "queued"
            rec["enqueued_at"] = now
            rec["completed_at"] = None
            rec["resumed_at"] = now
            rec["error"] = (
                "Sunucu yeniden başlatıldı; görev plan dosyasından "
                "kaldığı yerden devam edecek."
            )
            changed += 1
            project_id = rec.get("project_id")
            if project_id:
                try:
                    from . import project_store
                    project_store.update_project(
                        project_id,
                        status="building",
                        last_run_status="queued",
                        last_error="",
                    )
                except Exception:
                    pass
            continue

        message = (
            "Sunucu yeniden başlatıldığı için görev kesildi. "
            "Lütfen görevi tekrar çalıştırın."
        )
        rec["status"] = "failed"
        rec["completed_at"] = now
        rec["error"] = message
        changed += 1
        project_id = rec.get("project_id")
        if project_id:
            try:
                from . import project_store
                project_store.update_project(
                    project_id,
                    last_run_status="failed",
                    last_error=message,
                )
            except Exception:
                pass
    if changed:
        with TASKS_LOCK:
            _save_tasks(tasks)
    return changed


def _start_queue_worker_once():
    global _QUEUE_WORKER_STARTED
    if _QUEUE_WORKER_STARTED:
        return
    _QUEUE_WORKER_STARTED = True

    # Onceki process'te yarim kalan gorevleri temizle.
    try:
        interrupted = _recover_interrupted_running_tasks()
        if interrupted:
            logger.warning("%s yarim kalmis gorev kurtarildi/kuyruga alindi", interrupted)
    except Exception as _exc:
        logger.warning("yarim kalmis gorevler temizlenemedi: %s", _exc)

    # Sunucu acilisinda 'queued' kalan taze gorevleri geri al (kaybolmasinlar).
    try:
        restored = _requeue_recent_persisted_tasks()
        if restored:
            logger.info("tasks.json'dan %s queued gorev kuyruga geri alindi", restored)
    except Exception as _exc:
        logger.warning("queued gorevler geri alinamadi: %s", _exc)

    def worker():
        while True:
            item = None
            processed_project = None
            processed_user = None
            with TASK_QUEUE_LOCK:
                # Round-robin: pick first project with pending tasks for a user who is not paused
                for project_id, project_queue in TASK_QUEUES.items():
                    if not project_queue:
                        continue
                    for user_id, user_queue in project_queue.items():
                        if user_queue and not is_user_paused(user_id):
                            item = user_queue.pop(0)
                            processed_project = project_id
                            processed_user = user_id
                            break
                    if item is not None:
                        break
            if item is None:
                time.sleep(0.5)
                continue
            _update_task(item["run_id"], status="running", started_at=datetime.utcnow().isoformat() + "Z")
            try:
                project_id = item.get("project_id")
                user_id = item.get("user_id")
                # Build gorevlerinde proje kimligi garanti altina alinir:
                # project_id yoksa kullaniciya ait son proje bulunur, yoksa
                # yeni bir tane olusturulur (yoksa "No project context set").
                mode = item.get("mode") or "execute"
                if not project_id and user_id:
                    try:
                        project_id = _ensure_build_project(user_id, item.get("task") or "")
                        item["project_id"] = project_id
                        _update_task(item["run_id"], project_id=project_id)
                    except Exception as _proj_exc:
                        logger.warning("proje kimligi cozulemedi: %s", _proj_exc)
                # Set project context for this thread before running agent
                from agent_app.project_scanner import set_project_context, clear_project_context, get_project_root
                if project_id:
                    # user_id None olsa bile baglami kur; sahiplik user_guid varsa
                    # set_project_context icinde dogrulanir (request'siz yolda).
                    set_project_context(project_id, user_id)
                    if get_project_root() is None:
                        # Sahiplik eslesmediyse kullaniciya ozel yeni proje ile tekrar dene
                        fresh = _ensure_build_project(user_id, item.get("task") or "", force_new=True)
                        if fresh:
                            project_id = fresh
                            item["project_id"] = project_id
                            _update_task(item["run_id"], project_id=project_id)
                            set_project_context(project_id, user_id)
                wrapper = {"__meta__": {"run_id": item["run_id"], "console": False, "project_id": project_id, "user_id": user_id}, "prompt": item["task"]}
                dirp = os.path.join(settings.BASE_DIR, "task_runs")
                path = os.path.join(dirp, f"{item['run_id']}.log")
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": "queued", "text": "Started processing"}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                mode = item.get("mode") or "execute"
                _update_task(item["run_id"], mode=mode)
                from . import project_store as _project_store
                if project_id:
                    _changes = {
                        "last_run_id": item["run_id"],
                        "last_run_status": "running",
                        "last_error": "",
                    }
                    if mode == "build":
                        _changes["status"] = "building"
                    try:
                        _project_store.update_project(project_id, **_changes)
                    except Exception:
                        pass
                _queue_project_whatsapp_report(
                    project_id,
                    item.get("user_id"),
                    item.get("task") or "",
                    "started",
                    None,
                    "",
                )
                if mode == "build":
                    # Otonom proje uretimi: arastirma -> kiyas -> plan -> yap -> elestir
                    from agent_app.buildkit.pipeline import run_build_pipeline, enabled as build_enabled
                    if not build_enabled():
                        message = "Otonom proje uretimi kapali (BUILD_PIPELINE.enabled=False)"
                        _update_task(item["run_id"], status="failed", completed_at=datetime.utcnow().isoformat() + "Z",
                                     error=message)
                        if project_id:
                            try:
                                _project_store.update_project(
                                    project_id, status="error", last_run_status="failed", last_error=message
                                )
                            except Exception:
                                pass
                        continue
                    from . import task_control
                    if _task_status(item["run_id"]) == "cancelled":
                        result = {"success": False, "error": "Görev iptal edildi."}
                    else:
                        task_control.clear_run(item["run_id"])
                        result = run_build_pipeline(wrapper, model_name=item.get("model"),
                                                    project_id=project_id, user_id=user_id)
                else:
                    from . import task_control
                    if _task_status(item["run_id"]) == "cancelled":
                        result = {"success": False, "error": "Görev iptal edildi."}
                    else:
                        task_control.clear_run(item["run_id"])
                        result = run_self_healing_loop(
                            wrapper,
                            model_name=item.get("model"),
                            max_iterations=int(getattr(settings, "AGENT_MAX_ITERATIONS", 5000)),
                        )
                succeeded = isinstance(result, dict) and result.get("success") is True
                cancelled = _task_status(item["run_id"]) == "cancelled"
                error_text = ""
                if not succeeded:
                    if isinstance(result, dict):
                        output = result.get("output")
                        if isinstance(output, dict):
                            output = json.dumps(output, ensure_ascii=False)[:2000]
                        error_text = str(result.get("error") or output or "Gorev basarisiz oldu")
                    else:
                        error_text = "Gorev basarisiz oldu"
                if cancelled:
                    error_text = "Görev iptal edildi (tarayıcı kapandı / arka plan izni kapalı)."
                _update_task(
                    item["run_id"],
                    status="cancelled" if cancelled else ("completed" if succeeded else "failed"),
                    completed_at=datetime.utcnow().isoformat() + "Z",
                    error="" if (succeeded or cancelled) else error_text[:4000],
                )
                _queue_project_whatsapp_report(
                    project_id,
                    item.get("user_id"),
                    item.get("task") or "",
                    "cancelled" if cancelled else ("completed" if succeeded else "failed"),
                    result,
                    error_text,
                )
                try:
                    from .whatsapp_remote import queue_task_report
                    queue_task_report(
                        item["run_id"],
                        "cancelled" if cancelled else ("completed" if succeeded else "failed"),
                        result,
                        error_text,
                    )
                except Exception as exc:
                    logger.debug("WhatsApp remote görev raporu kuyruğa alınamadı: %s", exc)
                try:
                    conv_id = item.get("conversation_id")
                    if conv_id:
                        conv = Conversation.objects.filter(id=conv_id).first()
                        if conv:
                            if succeeded:
                                summary = ""
                                if isinstance(result, dict):
                                    summary = str(result.get("output") or result.get("summary") or "")
                                if summary:
                                    _append_chat_message(
                                        conv, "assistant", summary[:4000],
                                        model=item.get("model") or "",
                                    )
                                _remember_conversation(
                                    conv,
                                    last_run_status="completed",
                                    last_summary=summary[:2000] or None,
                                )
                            else:
                                _append_chat_message(
                                    conv, "assistant", error_text[:4000],
                                    model=item.get("model") or "",
                                )
                                _remember_conversation(
                                    conv,
                                    last_run_status="failed",
                                    last_error=error_text[:2000],
                                )
                except Exception as _conv_exc:
                    logger.debug("gorev sonucu sohbete yazilamadi: %s", _conv_exc)
                if project_id:
                    try:
                        current = _project_store.read_project(project_id) or {}
                        changes = {
                            "last_run_id": item["run_id"],
                            "last_run_status": "completed" if succeeded else "failed",
                            "last_error": "" if succeeded else error_text[:500],
                        }
                        if mode == "build":
                            changes["status"] = "ready" if succeeded else "error"
                        _project_store.update_project(project_id, **changes)
                    except Exception:
                        pass
            except Exception as exc:
                _update_task(
                    item["run_id"],
                    status="failed",
                    completed_at=datetime.utcnow().isoformat() + "Z",
                    error=str(exc),
                )
                try:
                    from . import project_store as _project_store
                    if item.get("project_id"):
                        _project_store.update_project(
                            item["project_id"], status="error",
                            last_run_id=item["run_id"], last_run_status="failed", last_error=str(exc)[:500],
                        )
                except Exception:
                    pass
                try:
                    dirp = os.path.join(settings.BASE_DIR, "task_runs")
                    path = os.path.join(dirp, f"{item['run_id']}.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "type": "exception", "text": f"Worker exception: {exc}"}, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                try:
                    from .whatsapp_remote import queue_task_report
                    queue_task_report(item["run_id"], "failed", None, str(exc))
                except Exception:
                    pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    global _PRESENCE_MONITOR_STARTED
    if not _PRESENCE_MONITOR_STARTED:
        _PRESENCE_MONITOR_STARTED = True
        threading.Thread(target=_presence_monitor, daemon=True).start()

# Configure OpenAI client using NVIDIA settings from Django settings
omni_cfg = getattr(settings, "OMNIROUTE", {})
base_url = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1") or "https://integrate.api.nvidia.com/v1"
base_url = base_url.rstrip('/')
# For NVIDIA API (integrate.api.nvidia.com/v1), keep /v1 in base_url.
if base_url.endswith('/v1') and 'nvidia' not in base_url:
    base_url = base_url[:-3]

# Yedek endpoint'ler: bazi datacenter IP'lerinde NVIDIA ana hostu TLS
# handshake'te engellenir. NVIDIA_BASE_URLS virgulle ayrilmis liste verilirse
# her biri sirayla denenir.
_alt_raw = os.environ.get("NVIDIA_BASE_URLS", "") or ""
_alt_urls = [u.strip().rstrip('/') for u in _alt_raw.split(',') if u.strip()]
BASE_URLS = [base_url] + [u for u in _alt_urls if u != base_url]

_client_cache = {}


def _get_client(b_url):
    """Bir base_url icin OpenAI client (cache'li).

    HTTP katmaninda IP failover kullanilir: bu makinede NVIDIA'nin DNS'inin
    dondurdugu IP'lerden biri TLS'te engellenirken digeri calisabiliyor.
    """
    if b_url not in _client_cache:
        _client_cache[b_url] = OpenAI(
            base_url=b_url,
            api_key=omni_cfg.get("API_KEY", os.environ.get("NVIDIA_API_KEY", "")),
            timeout=omni_cfg.get("TIMEOUT", 120),
            max_retries=2,
            http_client=nvidia_net.make_http_client(),
        )
    return _client_cache[b_url]


client = _get_client(base_url)


def _is_model_unavailable(exc):
    """404 / 'Function ... Not found for account' -> model bu hesapta cagrilabilir degil."""
    msg = str(exc).lower()
    return (
        "404" in msg
        or "not found for account" in msg
        or "model_not_found" in msg
        or "does not exist" in msg
    )


def _model_fallbacks(model_id):
    """Verilen model calisamiyorsa denenecek sirali alternatifler."""
    candidates = _configured_tier_models()
    ordered = [m for m in candidates if m != model_id]
    ordered.append(getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "") or "")
    seen = set()
    return [m for m in ordered if m and not (m in seen or seen.add(m))]


def _is_rate_limited(exc) -> bool:
    """429 / Too Many Requests / rate limit -> geri cekilip tekrar denenebilir."""
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    return (
        "429" in msg
        or "too many requests" in msg
        or "rate limit" in msg
        or "rate_limit" in msg
        or "ratelimit" in msg
    )


def _retry_after_seconds(exc) -> float:
    """Yanittaki Retry-After degerini (varsa) saniye cinsinden dondurur."""
    try:
        resp = getattr(exc, "response", None)
        headers = getattr(resp, "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            return max(0.0, float(raw))
    except Exception:
        pass
    return 0.0


def llm_create(**kwargs):
    """LLM cagrisi.

    - reasoning_effort=none: reasoning modelleri token'i ic muhakemeye harcamasın,
      hizli ve dolu cevap donsun (0.5s).
    - timeout/connection hatasinda bir kez daha dener
    - 429 (Too Many Requests) durumunda ustel backoff ile 5 denemeye kadar tekrar dener
    - model hesapta deploy degilse (404) sirali olarak calisan modelleri dener
    - ag/TLS engelinde (timeout, handshake) yedek endpoint'leri dener
    """
    requested_model = kwargs.get("model")
    # NVIDIA reasoning modelleri ic muhakemede token harcayip bos/gec cevap donduruyor.
    # reasoning_effort=none ile tamamen kisaltildi; caller zaten belirttiyse ezme.
    kwargs.setdefault("reasoning_effort", "none")
    last_error = None
    total_attempts = 6
    for attempt in range(total_attempts):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            rate_limited = _is_rate_limited(exc)
            retryable = rate_limited or (
                "timeout" in message
                or "timed out" in message
                or "connection" in message
                or "handshake" in message
            )
            if attempt < total_attempts - 1 and retryable:
                # 429 icin ustel bekleme (2,4,8,16,32 sn) + az miktar jitter;
                # NVIDIA limiti dakika penceresinde kirilabilir, bu yuzden
                # eski 5 deneme/30 sn sabri yetersiz kaliyordu.
                if rate_limited:
                    base = min(2.0 * (2 ** attempt), 45.0)
                    delay = base + (time.time() % 1.0)
                else:
                    delay = 0.8
                delay = max(delay, min(_retry_after_seconds(exc), 120.0))
                if rate_limited:
                    logger.warning("LLM 429 (Too Many Requests); %.1f sn sonra tekrar deneniyor (deneme %d/%d): %s",
                                   delay, attempt + 2, total_attempts, str(exc)[:160])
                else:
                    logger.warning("LLM istegi ag hatasi, %.1f sn sonra tekrar deneniyor (deneme %d/%d): %s",
                                   delay, attempt + 2, total_attempts, str(exc)[:160])
                time.sleep(delay)
                continue
            raise
    raise last_error


def _is_network_error(exc):
    """TLS/timeout/connection hatasi -> endpoint degistirilebilir."""
    msg = str(exc).lower()
    return (
        "timeout" in msg
        or "timed out" in msg
        or "handshake" in msg
        or "connection" in msg
        or "connectionreset" in msg
        or "ssl" in msg
        or "network" in msg
    )


_NET_STATE = {"at": 0.0, "reachable": None, "detail": ""}
_NET_STATE_POS_TTL = 60.0   # erisilebilirken sik sik yoklamayip maliyet olusturma
_NET_STATE_NEG_TTL = 3.0    # erisilemiyorken hizli toparlansin (kisa sureli kesinti)


def provider_reachable(budget=4.0, attempts=2, gap=1.0):
    """NVIDIA'ya kisa bir yoklama yapar. (reachable, aciklama) doner.

    TLS el sikismasini paralel olarak tum aday IP'lerde dener; bu makinede
    DNS'in dondurdugu IP'lerden biri engellenirken digeri calisabildigi icin
    "tek IP'ye takil" davranisi birakildi.

    Kisa sureli kesintilerde birkac kez tekrar deneyerek kendini toparlar.
    """
    now = time.monotonic()
    ttl = _NET_STATE_POS_TTL if _NET_STATE["reachable"] else _NET_STATE_NEG_TTL
    if _NET_STATE["reachable"] is not None and (now - _NET_STATE["at"]) < ttl:
        return _NET_STATE["reachable"], _NET_STATE["detail"]

    omni_cfg = getattr(settings, "OMNIROUTE", {}) or {}
    endpoints = [b for b in BASE_URLS if b]
    if not endpoints:
        detail = "BASE_URL boş."
        _NET_STATE.update({"at": now, "reachable": False, "detail": detail})
        return False, detail

    reachable, detail = nvidia_net.probe_endpoints(
        endpoints, timeout=budget, attempts=attempts, gap=gap
    )
    _NET_STATE.update({"at": time.monotonic(), "reachable": reachable, "detail": detail})
    return reachable, detail


def _is_empty_completion(response):
    """Reasoning modelleri token butcesini ic muhakemeye harciyor; icerik bos kaliyor."""
    try:
        choice = response.choices[0]
    except Exception:
        return False
    content = (getattr(choice.message, "content", "") or "").strip()
    if content:
        return False
    return getattr(choice, "finish_reason", "") in ("length", "max_tokens")


def llm_create_resilient(**kwargs):
    """llm_create + model calisamiyorsa/ bos cevap donerse yedek modele gecis."""
    requested_model = kwargs.get("model")
    budget = kwargs.get("max_tokens")

    def _call(model, max_tokens, base=None):
        payload = dict(kwargs)
        payload["model"] = model
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if base:
            return _get_client(base).chat.completions.create(**payload)
        return llm_create(**payload)

    try:
        response = _call(requested_model, budget)
        if not _is_empty_completion(response):
            return response

        # Reasoning modeli: butceyi buyutup tekrar dene.
        if budget:
            bigger = min(int(budget) * 6, 8192)
            logger.warning(
                "%s bos icerik dondu (reasoning modeli); max_tokens %s -> %s ile tekrar deneniyor.",
                requested_model, budget, bigger,
            )
            response = _call(requested_model, bigger)
            if not _is_empty_completion(response):
                return response
        raise RuntimeError(
            f"{requested_model} icerik uretmedi (reasoning modeli olabilir, token butcesi yetersiz)."
        )
    except Exception as exc:
        # Ag/TLS engeli: yedek endpoint'leri dene.
        if _is_network_error(exc) and len(BASE_URLS) > 1:
            for alt in BASE_URLS[1:]:
                logger.warning("Endpoint %s engellendi, %s deneniyor.", base_url, alt)
                try:
                    response = _call(requested_model, budget, base=alt)
                    if not _is_empty_completion(response):
                        return response
                except Exception as alt_exc:
                    last_exc = alt_exc
                    if not _is_network_error(alt_exc):
                        raise

        model_unavailable = _is_model_unavailable(exc)
        rate_limited = _is_rate_limited(exc)
        empty_result = isinstance(exc, RuntimeError)
        if not requested_model or not (model_unavailable or rate_limited or empty_result):
            raise

        if rate_limited:
            # llm_create'in kisa backoff'u (<=~62 sn) yetmediyse uzun serinleme yap.
            # NVIDIA limiti dakika penceresinde kirilir; plan kalemleri burada
            # 429 ile dusup "0/25 tamam" oluyordu.
            cooldown = 45.0
            logger.warning("LLM 429 surecinde; %.0f sn serinleme sonrasi ayni model tekrar denenecek: %s",
                           cooldown, requested_model)
            time.sleep(cooldown)
            try:
                response = _call(requested_model, budget)
                if not _is_empty_completion(response):
                    return response
                raise RuntimeError(f"{requested_model} bos icerik dondu.")
            except Exception as cool_exc:
                if not (_is_rate_limited(cool_exc)
                        or _is_model_unavailable(cool_exc)
                        or isinstance(cool_exc, RuntimeError)):
                    raise
                exc = cool_exc
                model_unavailable = _is_model_unavailable(cool_exc)
                empty_result = True

        logger.warning(
            "Model %s kullanilamiyor (%s); yedek modeller deneniyor.",
            requested_model, str(exc)[:120],
        )
        last = exc
        for candidate in _model_fallbacks(requested_model):
            try:
                response = _call(candidate, budget)
                if _is_empty_completion(response):
                    last = RuntimeError(f"{candidate} bos icerik dondu.")
                    continue
                logger.info("Yedek model kullanildi: %s", candidate)
                return response
            except Exception as retry_exc:
                last = retry_exc
                if not (_is_model_unavailable(retry_exc) or _is_rate_limited(retry_exc)
                        or isinstance(retry_exc, RuntimeError)):
                    raise
        raise last



# Default max_tokens for chat completions
DEFAULT_MAX_TOKENS = omni_cfg.get("MAX_TOKENS", 2048)


class TaskForm(forms.Form):
    task = forms.CharField(
        label="Görev",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Örnek: 1'den 100'e kadar asal sayıları listeleyen Python fonksiyonunu yaz.",
            }
        ),
    )
    model = forms.ChoiceField(
        label="Model",
        required=False,
        help_text="OmniRoute modeli; 'Auto' seçili ise ayarlardaki varsayılan kullanılır.",
    )

    def __init__(self, *args, model_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if model_choices is None:
            model_choices = []
        # ensure 'auto' is the first/top option
        auto_label = f"Auto (use default: {getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto')})"
        choices = [("auto", auto_label)] + [(m, m) for m in model_choices]
        self.fields["model"].choices = choices
        # default to 'auto'
        self.fields["model"].initial = "auto"


def extract_code_block(text):
    if "```python" in text:
        parts = text.split("```python")
        if len(parts) > 1:
            return parts[1].split("```")[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            return parts[1].strip()
    return None


def run_self_healing_loop(initial_prompt, model_name=None, max_iterations=None, preferred_tier=None):
    """
    Tool-calling agent loop: LLM can read/write/list/grep/edit files and run commands.
    """
    if max_iterations is None:
        max_iterations = int(getattr(settings, "AGENT_MAX_ITERATIONS", 5000))
    # Initialize escalation counter for model tier escalation
    if not hasattr(run_self_healing_loop, '_escalation_count'):
        run_self_healing_loop._escalation_count = 0

    # model name may be provided in the prompt wrapper (we set default later)
    # allow callers to pass a wrapper dict: {"__meta__": {...}, "prompt": "..."}
    prompt_text = None
    if isinstance(initial_prompt, dict) and isinstance(initial_prompt.get("__meta__"), dict):
        prompt_text = initial_prompt.get("prompt") or initial_prompt.get("task") or ""
    else:
        prompt_text = initial_prompt

    # Get project context - use project root if available for isolation
    from agent_app.project_scanner import get_project_root
    project_root = get_project_root()
    project_ctx = get_project_context(project_root)

    # Build system prompt with project context and tool definitions
    system_prompt = f"""You are an autonomous senior developer agent working on a real project.
You have access to the project files and can use tools to read, write, edit, search, and run commands.

=== PROJECT CONTEXT ===
{project_ctx}

=== AVAILABLE TOOLS ===
You can call these functions by responding with a tool_calls block:
- read_file(path): Read a file's content
- code_outline(path): AST outline (classes/functions) for a Python file or directory
- write_file(path, content): Write content to a file (creates dirs)
- list_files(path): List files in a directory
- grep_files(pattern, path, include): Search for regex pattern in files
- edit_file(path, old_string, new_string): Replace exact string in file
- run_command(command, timeout): Run shell command in project root

IMPORTANT: The PROJECT CONTEXT above already contains the file tree and the contents/skeletons of key files.
Do NOT spend iterations re-reading files that are already included there unless you truly need the full content.

WORKFLOW (follow this order):
1. Identify concrete broken/missing items from the context (make a short internal list).
2. Fix them by editing/writing files. For "analyze the whole project and fix all issues" requests,
   reading-only is NOT enough: you must make changes in this run.
3. Run a verification command or re-read the edited file.
4. Finish with a final summary message describing what you changed.

When you need to use a tool, respond with a JSON tool_calls array. Example:
{{
  "tool_calls": [
    {{"name": "read_file", "arguments": {{"path": "agent_app/views.py"}}}}
  ]
}}

The system will execute the tool and return results. Continue until the task is complete.
Then respond with a final message summarizing what you did.

IMPORTANT:
- Always use tools to explore the codebase before making changes
- Make minimal, focused changes
- Run tests/commands to verify your changes work
- Never assume file contents - always read first
- Use edit_file for precise changes, write_file for new files
"""

    conversation_history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]

    # Use model router for intelligent model selection based on task complexity
    router = get_model_router()
    
    # Determine model tier from task complexity
    if not model_name:
        # Once amaca gore sec (kodlama / arastirma / hizli yurutme), sonra karmaşıklık tier'ı
        purpose_model, purpose_key = select_purpose_model(prompt_text)
        current_tier, model_config = select_model_for_task(prompt_text)
        complexity_model = model_config.get("model_id", getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', ''))
        # Amaca uyan modeli tercih et, ama karmaşıklık tier'ı cok yuksekse guclu modele gec
        if purpose_key in ("research",) or current_tier in ("ultra", "high"):
            model_name = complexity_model or purpose_model
            chosen_purpose = "research"
        else:
            model_name = purpose_model or complexity_model
            chosen_purpose = purpose_key
        logger.info("Amaca gore model: %s (%s) tier=%s", model_name, chosen_purpose, current_tier)
    else:
        # User explicitly specified a model, use it but get its tier info
        model_name = model_name
        # Find which tier this model belongs to
        router = get_model_router()
        current_tier = "standard"
        for tier_name, tier_info in router.tiers.items():
            if tier_info.get("model_id") == model_name:
                current_tier = tier_name
                break
        if not current_tier:
            current_tier = getattr(settings, "FASTEST_TIER", "") or getattr(settings, "DEFAULT_TASK_TIER", "standard")

    # logging: optional run_id may be set in the outer scope by callers.
    run_id = None
    console = False
    project_id = None
    user_id = None
    if isinstance(initial_prompt, dict) and initial_prompt.get("__meta__"):
        meta = initial_prompt.pop("__meta__")
        run_id = meta.get("run_id")
        console = meta.get("console", False)
        project_id = meta.get("project_id")
        user_id = meta.get("user_id")
    else:
        run_id = None
        console = False

    # Set project context for file tool isolation
    if project_id:
        from agent_app.project_scanner import set_project_context
        set_project_context(project_id, user_id)

    def _get_run_dir():
        dirp = os.path.join(settings.BASE_DIR, "task_runs")
        try:
            os.makedirs(dirp, exist_ok=True)
        except Exception:
            pass
        return dirp

    def _ensure_run_id():
        nonlocal run_id
        if not run_id:
            run_id = uuid.uuid4().hex
        open(os.path.join(_get_run_dir(), f"{run_id}.log"), "a", encoding="utf-8").close()
        return run_id

    def _append_log(entry_type, text):
        try:
            rid = _ensure_run_id()
            path = os.path.join(_get_run_dir(), f"{rid}.log")
            obj = {"ts": datetime.utcnow().isoformat() + "Z", "type": entry_type, "text": str(text)}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            if console:
                print(f"[{entry_type}] {text}", flush=True)
        except Exception:
            pass

    # Tool-calling loop
    read_only_streak = 0
    nudge_count = 0
    read_only_tools = {
        "read_file", "list_files", "grep_files", "code_outline",
        "analyze_dependencies", "analyze_image", "web_search",
    }
    for i in range(max_iterations):
        try:
            from . import task_control
            if run_id and (task_control.is_cancelled(run_id) or _task_status(run_id) == "cancelled"):
                return {
                    "success": False,
                    "output": "Görev iptal edildi (tarayıcı kapandı / arka plan izni kapalı).",
                    "cancelled": True,
                    "run_id": run_id,
                }
            _append_log("info", f"Iteration {i+1}/{max_iterations}")
            _append_log("status", "Düşünüyor...")
            
            # Build messages with tools
            response = llm_create_resilient(
                model=model_name,
                messages=conversation_history,
                temperature=0.2,
                tools=FILE_TOOLS,
                tool_choice="auto",
                max_tokens=DEFAULT_MAX_TOKENS,
            )
            
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = message.tool_calls or []
            
            _append_log("chat_response", content)
            if tool_calls:
                _append_log("tool_calls", json.dumps([{"name": tc.function.name, "args": tc.function.arguments} for tc in tool_calls], ensure_ascii=False))
            
            # If no tool calls, treat as final response
            if not tool_calls:
                if content.strip():
                    _append_log("final_response", content)
                    _append_log("status", "Tamamlandı")
                    return {"success": True, "output": content.strip(), "run_id": run_id}
                # Empty response, continue
                continue
            
            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                func_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                
                # Human-readable status message per tool
                tool_status_map = {
                    "read_file": "Dosya okunuyor...",
                    "write_file": "Dosya yazılıyor...",
                    "list_files": "Dosya ağacı taranıyor...",
                    "run_command": "Komut çalıştırılıyor...",
                    "grep_search": "Kodda arama yapılıyor...",
                    "analyze_dependencies": "Bağımlılıklar analiz ediliyor...",
                    "analyze_image": "Görsel analiz ediliyor...",
                    "web_search": "İnternette araştırılıyor...",
                }
                status_msg = tool_status_map.get(func_name, f"{func_name} çalıştırılıyor...")
                _append_log("status", status_msg)
                
                _append_log("tool_call", f"{func_name}({json.dumps(args, ensure_ascii=False)})")
                result = execute_tool(func_name, args)
                _append_log("tool_result", json.dumps(result, ensure_ascii=False))
                
                tool_results.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "name": func_name,
                    "content": json.dumps(result, ensure_ascii=False)
                })
            
            # Add assistant message with tool calls to history
            conversation_history.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in tool_calls
                ]
            })
            
            # Add tool results to history
            conversation_history.extend(tool_results)

            # Salt-okuma dongusune karsi: ajan yeterince baglam topladiysa
            # (6 ardisik tur yalnizca okuma/arama) duzeltme yapmaya yonlendir.
            tool_names = [tc.function.name for tc in tool_calls]
            if tool_names and all(name in read_only_tools for name in tool_names):
                read_only_streak += 1
            else:
                read_only_streak = 0
            if read_only_streak >= 6 and nudge_count < 2:
                nudge_count += 1
                read_only_streak = 0
                conversation_history.append({
                    "role": "user",
                    "content": (
                        "You have gathered enough context. Stop reading more files and start applying "
                        "concrete fixes now using edit_file/write_file. After editing, run a quick "
                        "verification and return a final summary. Do not read more files unless strictly necessary."
                    ),
                })
                _append_log("info", "Baglam yeterli: ajan duzeltme yapmaya yonlendirildi")

            _append_log("status", "Sonuçlar işleniyor...")
        
        except Exception as exc:
            err_text = str(exc)
            _append_log("exception", err_text)
            if "not a valid" in err_text or "Unknown built-in" in err_text or "invalid_request_error" in err_text:
                try:
                    omni_cfg = getattr(settings, "OMNIROUTE", {})
                    base = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
                    timeout = omni_cfg.get("TIMEOUT", 5)
                    candidates = [base + "/models", base, base + "/v1/models"]
                    model_ids = []
                    for url in candidates:
                        try:
                            with urllib.request.urlopen(url, timeout=timeout) as resp:
                                body = resp.read(65536).decode(errors="replace")
                                parsed = None
                                try:
                                    parsed = json.loads(body)
                                except Exception:
                                    try:
                                        parsed = json.loads(json.loads(body))
                                    except Exception:
                                        parsed = None
                                if isinstance(parsed, dict):
                                    data = parsed.get("data") or parsed.get("models") or parsed
                                    if isinstance(data, list):
                                        for it in data:
                                            if isinstance(it, dict) and it.get("id"):
                                                model_ids.append(it.get("id"))
                                    elif isinstance(parsed.get("data"), str):
                                        try:
                                            dd = json.loads(parsed.get("data"))
                                            if isinstance(dd, list):
                                                for it in dd:
                                                    if isinstance(it, dict) and it.get("id"):
                                                        model_ids.append(it.get("id"))
                                        except Exception:
                                            pass
                                    elif isinstance(parsed, list):
                                        for it in parsed:
                                            if isinstance(it, dict) and it.get("id"):
                                                model_ids.append(it.get("id"))
                                                if model_ids:
                                                    break
                        except Exception:
                            continue
                    suggestion_text = ""
                    if model_ids:
                        top = model_ids[:8]
                        suggestion_text = "\nÖnerilen modeller: " + ", ".join(top)
                    else:
                        suggestion_text = "\nModel listesi alınamadı; lütfen `/omni/models/` endpoint'ini kontrol edin."
                    return {"success": False, "output": f"Hata oluştu: {err_text}\n{suggestion_text}"}
                except Exception:
                    return {"success": False, "output": f"Hata oluştu: {err_text}"}
            
            # Auto-escalate model tier on model-related errors
            if current_tier and settings.MODEL_ROUTING.get("auto_escalate", True):
                max_escalations = settings.MODEL_ROUTING.get("max_escalations", 2)
                escalation_count = getattr(run_self_healing_loop, '_escalation_count', 0)
                if escalation_count < max_escalations:
                    new_tier = escalate_model_tier(current_tier, f"Model error: {err_text}")
                    if new_tier != current_tier:
                        current_tier = new_tier
                        model_config = router.tiers.get(current_tier, {})
                        model_name = model_config.get("model_id")
                        _append_log("info", f"Escalated due to: {err_text[:100]}")
                        run_self_healing_loop._escalation_count = escalation_count + 1
                        continue  # Retry with higher tier model
            return {"success": False, "output": f"Hata oluştu: {err_text}"}
    
    return {
        "success": False,
        "output": (
            f"Maksimum iterasyon sayısına ulaşıldı ({max_iterations} tur) ancak görev tam olarak "
            "çözülemedi. Daha karmaşık görevler için AGENT_MAX_ITERATIONS değerini artırın."
        ),
    }


@require_auth
def index(request):
    # Do NOT fetch model list during page render to avoid blocking the UI.
    # The frontend will populate model choices via AJAX using `/omni/models/`.
    # Keep a local placeholder; only fetch cached model ids when handling POST to
    # avoid blocking the initial GET render.
    form = TaskForm(model_choices=[])
    model_choices = []
    result = None

    user = current_user(request)
    user_id = str(user.id) if user else None
    requested_project = request.GET.get("project") or request.GET.get("project_id")
    active_project = None
    if requested_project:
        candidate = project_store.read_project(requested_project)
        if candidate and project_store.owner_matches(
            candidate.get("owner_guid") or candidate.get("user_id"),
            user_id=user_id,
            request=request,
        ):
            active_project = candidate
            request.session["active_project_explicit"] = True
        else:
            active_project = project_store.find_latest_project(user_id=user_id)
            request.session["active_project_explicit"] = False
    else:
        session_id = request.session.get("current_project_id") or request.session.get("current_project_guid")
        if session_id:
            candidate = project_store.read_project(session_id)
            if candidate and project_store.owner_matches(
                candidate.get("owner_guid") or candidate.get("user_id"),
                user_id=user_id,
                request=request,
            ):
                active_project = candidate
        if not active_project:
            active_project = project_store.find_latest_project(user_id=user_id)
        request.session["active_project_explicit"] = False

    if active_project:
        project_id = active_project["id"]
        request.session["current_project_id"] = project_id
        request.session["current_project_guid"] = project_id
        request.session.modified = True
        if not active_project.get("last_run_id") or active_project.get("last_run_status") == "running":
            related = [
                task for task in _load_tasks()
                if str(task.get("project_id") or "") == str(project_id)
            ]
            if related:
                latest_task = max(related, key=lambda t: str(t.get("enqueued_at") or ""))
                if (not active_project.get("last_run_id")
                        or latest_task.get("status") in ("completed", "failed", "cancelled")):
                    active_project["last_run_id"] = latest_task.get("run_id") or ""
                    active_project["last_run_status"] = latest_task.get("status") or ""
                    active_project["last_error"] = latest_task.get("error") or ""
    else:
        request.session.pop("current_project_id", None)
        request.session.pop("current_project_guid", None)
        request.session.modified = True

    if request.method == "POST":
        # try to fetch a short-lived cached model list for the POST handling
        try:
            model_choices = get_model_ids_cached() or []
        except Exception:
            model_choices = []
        form = TaskForm(request.POST, model_choices=model_choices)
        if form.is_valid():
            task = form.cleaned_data["task"]
            model = form.cleaned_data.get("model")
            # interpret 'auto' as None to use settings default
            if model == "auto" or not model:
                model = None
            result = run_self_healing_loop(task, model_name=model)
    
    # Determine provider type
    use_nvidia = getattr(settings, "OMNIROUTE", {}).get("BASE_URL", "").startswith("https://integrate.api.nvidia.com")
    
    return render(
        request,
        "agent_app/chat.html",
        {
            "form": form, 
            "result": result, 
            "default_model": getattr(settings, "OMNIROUTE_DEFAULT_MODEL", ""),
            "fastest_model": getattr(settings, "FASTEST_MODEL", "") or getattr(settings, "OMNIROUTE_DEFAULT_MODEL", ""),
            "fastest_tier": getattr(settings, "FASTEST_TIER", "") or "",
            "task_groups_json": json.dumps(_task_groups(), ensure_ascii=False),
            "provider": "nvidia" if use_nvidia else "omniroute",
            "active_project": active_project,
        },
    )


def landing_page(request):
    """Render the landing page with Replit-style layout.
    Redirect authenticated users to chat."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if user:
        return redirect('/chat/')
    return render(request, "agent_app/landing.html")


def runs_page(request):
    """Render the full recent-runs history screen."""
    return render(request, "agent_app/runs.html")


def projects_page(request):
    """Render the projects management page."""
    return render(request, "agent_app/projects.html")


def omni_health(request):
    """Simple health check for the configured OmniRoute provider.

    Tries a few sensible endpoints derived from the configured BASE_URL and
    returns a JSON summary indicating if the provider is reachable.
    """
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
    timeout = omni_cfg.get("TIMEOUT", 3)

    candidates = [
        base,
        base.rstrip("/") + "/health",
        base.rstrip("/") + "/v1/health",
        base.rstrip("/") + "/models",
        base.rstrip("/") + "/v1/models",
    ]

    attempts = []
    # try each candidate with a small retry/backoff
    for url in candidates:
        last_err = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    status = resp.getcode()
                    body = resp.read(4096).decode(errors="replace")
                    # try parse JSON
                    parsed = None
                    try:
                        parsed = json.loads(body)
                    except Exception:
                        parsed = body

                    attempts.append({"url": url, "ok": True, "status": status, "data": parsed})
                    return JsonResponse(
                        {
                            "ok": True,
                            "message": "OmniRoute reachable",
                            "url": url,
                            "status": status,
                            "data": parsed,
                            "attempts": attempts,
                        }
                    )
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {getattr(e, 'reason', str(e))}"
                attempts.append({"url": url, "ok": False, "status": e.code, "error": last_err})
            except Exception as e:
                last_err = str(e)
                attempts.append({"url": url, "ok": False, "error": last_err})
            # small backoff between attempts
            time.sleep(0.3)

    guidance = (
        "NVIDIA API'sine ulaşılamadı. İnternet bağlantınızı, VPN/firewall ayarlarınızı ve "
        ".env içindeki NVIDIA_API_KEY değerini kontrol edin. "
        "`python manage.py check_provider` komutu ayrıntılı teşhis verir."
    )
    return JsonResponse({"ok": False, "message": guidance, "attempts": attempts}, status=502)


def _normalize_model_ids(parsed):
    """Extract model ids from the various provider response shapes."""
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            parsed = None

    items = None
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        for key in ("data", "models", "result", "items"):
            value = parsed.get(key)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    continue
            if isinstance(value, list):
                items = value
                break
        else:
            if parsed.get("id"):
                items = [parsed]
    if not isinstance(items, list):
        return []

    model_ids = []
    for item in items:
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("model") or item.get("name")
        else:
            model_id = None
        if model_id and model_id not in model_ids:
            model_ids.append(model_id)
    return model_ids


def _configured_tier_models():
    """Model ids from NVIDIA_MODEL_TIERS, fastest first (used as fallback)."""
    tiers = getattr(settings, "NVIDIA_MODEL_TIERS", {}) or {}
    ranked = sorted(
        tiers.items(),
        key=lambda kv: kv[1].get("speed", 0),
        reverse=True,
    )
    ids = []
    for _tier, info in ranked:
        model_id = (info or {}).get("model_id")
        if model_id and model_id not in ids:
            ids.append(model_id)
    return ids


_MODELS_CACHE_TTL = int(os.environ.get("MODELS_CACHE_TTL", "600") or 600)
_models_cache = {"at": 0.0, "payload": None}
_models_cache_lock = threading.Lock()

# Yalnizca NVIDIA modelleri listelenir (katalogdaki meta/, google/, deepseek/ vb. gizlenir).
NVIDIA_MODEL_PREFIXES = ("nvidia/", "nemotron/", "nv-", "llama-3.1-nemotron", "nemoguard")


def _is_nvidia_model(model_id):
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if mid.startswith("nvidia/"):
        return True
    return any(mid.startswith(p) or ("/" + p) in mid for p in NVIDIA_MODEL_PREFIXES)



def _fetch_provider_model_ids():
    """(model_ids, error) - model_ids provider'dan geldiyse liste, aksi halde bos."""
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
    api_key = omni_cfg.get("API_KEY", "")
    budget = max(3.0, min(float(omni_cfg.get("MODELS_TIMEOUT", 8) or 8), 20.0))
    deadline = time.monotonic() + budget

    if not api_key:
        return [], "NVIDIA_API_KEY tanımlı değil (.env dosyasını kontrol edin)."

    candidates = [
        base.rstrip("/") + "/models",
        base,
        base.rstrip("/") + "/v1/models",
    ]
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}

    last_err = "Bilinmeyen hata"
    for url in candidates:
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            last_err += " | zaman asimi"
            break
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=remaining) as resp:
                body = resp.read(524288).decode(errors="replace")
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                model_ids = _normalize_model_ids(parsed)
                if model_ids:
                    return model_ids, None
                last_err = "Provider boş model listesi döndürdü."
        except urllib.error.HTTPError as e:
            detail = getattr(e, "reason", "") or ""
            try:
                detail = e.read(512).decode(errors="replace").strip()
            except Exception:
                pass
            last_err = f"HTTP {e.code} {detail or ''}".strip()
            if e.code in (401, 403):
                last_err += " (API anahtarı geçersiz)"
                break
        except Exception as e:
            # DNS/SSL/zaman asimi ağ seviyesindedir; diger URL'leri denemek anlamsiz.
            last_err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            break

    # urllib basarisiz olduysa SDK'yi dene (farkli HTTP yigini / proxy / CA destegi).
    remaining = deadline - time.monotonic()
    if remaining >= 2.0:
        try:
            probe_client = OpenAI(
                base_url=base_url,
                api_key=api_key or os.environ.get("NVIDIA_API_KEY", ""),
                timeout=remaining,
                max_retries=0,
            )
            sdk_ids = [m.id for m in probe_client.models.list().data if getattr(m, "id", None)]
            if sdk_ids:
                return sdk_ids, None
            last_err += " | SDK: boş liste"
        except Exception as e:
            last_err += f" | SDK: {type(e).__name__}: {e}" if str(e) else f" | SDK: {type(e).__name__}"

    return [], last_err


_MODEL_IDS_CACHE = {"at": 0.0, "ids": None}
_MODEL_IDS_CACHE_LOCK = threading.Lock()


def get_model_ids_cached(force=False):
    """Return a cached list of provider model ids (best-effort, never raises)."""
    try:
        with _MODEL_IDS_CACHE_LOCK:
            cached_ids = _MODEL_IDS_CACHE.get("ids")
            cached_at = _MODEL_IDS_CACHE.get("at", 0.0)
            if (not force) and cached_ids is not None and (time.time() - cached_at) < _MODELS_CACHE_TTL:
                return list(cached_ids)
        try:
            ids, _err = _fetch_provider_model_ids()
        except Exception:
            ids = []
        if not ids:
            ids = _configured_tier_models()
        with _MODEL_IDS_CACHE_LOCK:
            _MODEL_IDS_CACHE["at"] = time.time()
            _MODEL_IDS_CACHE["ids"] = list(ids) if ids else []
        return list(ids) if ids else []
    except Exception:
        return []


def _task_groups():
    """Amaca gore hazir model gruplari (yalnizca ayarlardan; canli baglanti gerekmez)."""
    task_map = getattr(settings, "NVIDIA_TASK_MODELS", {}) or {}
    labels = getattr(settings, "NVIDIA_TASK_LABELS", {}) or {}
    bench = getattr(settings, "NVIDIA_MODEL_BENCH", {}) or {}
    groups = []
    for key, model_id in task_map.items():
        if not model_id:
            continue
        label, description = labels.get(key, (key, ""))
        groups.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "model": model_id,
                "bench": bench.get(model_id, {}),
            }
        )
    return groups


def omni_models(request):
    """Return the available model list for the chat model selector."""
    force = str(request.GET.get("refresh", "")).lower() in ("1", "true", "yes")

    cached = _models_cache.get("payload")
    if cached and not force and (time.time() - _models_cache["at"]) < _MODELS_CACHE_TTL:
        return JsonResponse(cached)

    with _models_cache_lock:
        cached = _models_cache.get("payload")
        if cached and not force and (time.time() - _models_cache["at"]) < _MODELS_CACHE_TTL:
            return JsonResponse(cached)

        provider_ids, provider_err = _fetch_provider_model_ids()
        # Yalnizca NVIDIA modelleri; NVIDIA'dan baska hicbir sey gosterilmez.
        nvidia_ids = [m for m in provider_ids if _is_nvidia_model(m)]
        tier_ids = _configured_tier_models()

        if nvidia_ids:
            # Canli baglanti var: katalogdaki tum NVIDIA modelleri + ayarli tier'lar.
            merged = list(nvidia_ids)
            for model_id in tier_ids:
                if model_id not in merged:
                    merged.append(model_id)

            task_map = getattr(settings, "NVIDIA_TASK_MODELS", {}) or {}
            labels = getattr(settings, "NVIDIA_TASK_LABELS", {}) or {}
            bench = getattr(settings, "NVIDIA_MODEL_BENCH", {}) or {}
            groups = []
            for key, model_id in task_map.items():
                if not model_id or model_id not in merged:
                    continue
                label, description = labels.get(key, (key, ""))
                groups.append(
                    {
                        "key": key,
                        "label": label,
                        "description": description,
                        "model": model_id,
                        "bench": bench.get(model_id, {}),
                    }
                )
            payload = {
                "ok": True,
                "models": merged,
                "groups": groups,
                "provider_count": len(nvidia_ids),
                "total_count": len(merged),
                "source": "nvidia",
                "checked_at": int(time.time()),
                "data": {"data": [{"id": m} for m in merged]},
            }
        else:
            # Baglanti yok: liste BOS doner, sahte/kayitli model gosterilmez.
            payload = {
                "ok": False,
                "models": [],
                "groups": [],
                "provider_count": 0,
                "total_count": 0,
                "source": "offline",
                "error": provider_err or "NVIDIA'ya ulaşılamadı",
                "data": {"data": []},
            }

        _models_cache["at"] = time.time()
        _models_cache["payload"] = payload

    return JsonResponse(payload)


def nvidia_models(request):
    """Fetch model list from NVIDIA API."""
    omni_cfg = getattr(settings, "OMNIROUTE", {})
    base = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1")
    api_key = omni_cfg.get("API_KEY", "")
    timeout = omni_cfg.get("TIMEOUT", 30)

    if not api_key:
        return JsonResponse({"ok": False, "error": "NVIDIA_API_KEY not configured"}, status=500)

    candidates = [
        base.rstrip("/") + "/models",
    ]

    for url in candidates:
        try:
            headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.getcode()
                body = resp.read(65536).decode(errors="replace")
                parsed = None
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                return JsonResponse({"ok": True, "url": url, "status": status, "data": parsed})
        except urllib.error.HTTPError as e:
            return JsonResponse({"ok": False, "error": f"HTTP {e.code}: {getattr(e, 'reason', str(e))}"}, status=502)
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=502)

    return JsonResponse({"ok": False, "error": "Failed to fetch models"}, status=502)


def start_task(request):
    """Start a background task run and return a run_id for polling logs.

    Accepts POST form fields: 'task' and optional 'model'. Returns JSON {ok: True, run_id: '...'}.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    from agent_app.permissions import current_user
    user = current_user(request)

    task = (request.POST.get("task") or request.POST.get("task") or "").strip()
    if not task:
        return JsonResponse({"ok": False, "error": "Task is required"}, status=400)
    model = request.POST.get("model") or None
    if model == "auto" or not model:
        model = None

    user_id = str(user.id) if user else None

    project_id = request.session.get("current_project_id") or request.session.get("current_project_guid")
    current_meta = project_store.read_project(project_id) if project_id else None
    if current_meta and not project_store.owner_matches(
        current_meta.get("owner_guid") or current_meta.get("user_id"),
        user_id=user_id,
        request=request,
    ):
        current_meta = None
    if not current_meta:
        project_id = None

    if not project_id:
        latest = project_store.find_latest_project(user_id=user_id)
        if latest:
            project_id = latest["id"]
            request.session["current_project_id"] = project_id
            request.session["current_project_guid"] = project_id
            request.session.modified = True

    # Cancel any existing running/queued tasks for this user in this project before starting new one
    if user_id and project_id:
        cancel_project_tasks(project_id, user_id)

    _start_queue_worker_once()
    run_id, position = enqueue_task(task, model=model, project_id=project_id, user_id=user_id)

    # Auto-start sandbox container for preview
    if user_id and getattr(settings, 'EMOG_MULTI_TENANT', False):
        try:
            from agent_app.sandbox.user_sandbox import get_sandbox_manager
            manager = get_sandbox_manager()
            manager.ensure_running(user_id, project_id)
        except Exception:
            pass

    return JsonResponse({"ok": True, "run_id": run_id, "position": position})


VALID_INTENTS = {"chat", "execute", "build", "health", "models"}

# "Tam bir urun yap" isteklerini deterministik olarak yakalayan yedek siniflayici.
# Niyet router'i execute dese bile bu kaliplar eslesirse is "build" sayilir.
_BUILD_VERBS = (
    "yap", "yapman", "yapsin", "kurar", "kur", "kursun", "geliştir", "gelistir",
    "oluştur", "olustur", "yaz", "hazırla", "hazirla", "koda", "programla",
    "tasarla", "tasarla", "create", "build", "make", "develop", "construct", "scaffold",
)
_BUILD_NOUNS = (
    "site", "situsu", "web sitesi", "websitesi", "uygulama", "uygulaması", "app",
    "oyun", "oyunu", "forum", "sosyal medya", "e-ticaret", "eticaret", "haber",
    "panel", "dashboard", "crm", "erp", "blog", "pazar yeri", "marketplace",
    "platform", "sistem", "sistemi", "portal", "mobil uygulama", "web uygulaması",
    "reklam", "pazarlama", "randevu", "rehber", "kütüphane", "müzik", "video",
    "oyun motoru", "sağlık", "fitness", "yemek", "seyahat", "haber sitesi",
    "forum sistemi", "yönetim paneli", "yonetim paneli", "sohbet", "chat uygulaması",
)
_BUILD_PLATFORM_HINTS = ("android", "ios", "masaüstü", "desktop", "3d", "2d", "mobil", "web")
# Soru kalibi: "nasıl yapılır?" bir istek degil, sorudur.
_BUILD_QUESTION = re.compile(
    r"\b(nasıl|nedir|niye|hangi|kaç|kac|ne\s+yapılır|mı|mıdır|mi|midir)\b", re.I
)

# Fiil alternatifi: sinif tanimiyla ayni listeyi kullansin diye uretilir.
_BUILD_VERB_PATTERN = "|".join(
    sorted((re.escape(v) for v in _BUILD_VERBS), key=len, reverse=True)
)


def looks_like_project_build(message: str) -> bool:
    """Kullanici gercekten yeni bir urun mu istiyor?

    Niyet router'i ikna edici olabilir ama bazen "execute" der. Bu siniflayici
    tarz degil, *icerik* kontrolu yapar: yaratma fiili + urun turu kelimesi.
    "bir fonksiyon yaz" gibi dar istekler buraya girmemeli, "haber sitesi nasil
    yapilir?" gibi sorular da isteme sayilmamali.
    """
    text = (message or "").lower()
    if len(text) < 6:
        return False
    if "?" in text or _BUILD_QUESTION.search(text):
        return False
    if not any(verb in text for verb in _BUILD_VERBS):
        return False

    has_noun = any(noun in text for noun in _BUILD_NOUNS)
    platform_hint = any(hint in text for hint in _BUILD_PLATFORM_HINTS)
    # "X yap/kur/..." seklinde net bir urun adammi var mi?
    # Fiilin sonundaki "-ar/-er/-sın" ekleri de kabul edilir ("kurar mısın").
    pattern = re.search(
        r"(?:bana\s+)?(?:bir\s+)?([\wÇĞİÖŞÜçğıöşü\s'-]{2,40}?)\s+"
        r"(?:" + _BUILD_VERB_PATTERN + r")[a-zçğıöşü]{0,6}\b",
        text,
    )
    looks_concrete = bool(pattern and pattern.group(1).strip())
    return has_noun and (looks_concrete or platform_hint)


def _parse_assistant_decision(content):
    """Parse the model's intent decision without requiring provider JSON mode."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            decision = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(decision, dict):
        return None
    intent = str(decision.get("intent", "")).strip().lower()
    if intent not in VALID_INTENTS:
        return None
    return {
        "intent": intent,
        "message": str(decision.get("message") or "").strip(),
        "task": str(decision.get("task") or "").strip(),
        "model": str(decision.get("model") or "").strip(),
        "priority": str(decision.get("priority") or "medium").strip().lower(),
    }


_CONTINUATION_RE = re.compile(
    r"^\s*(?:lütfen|lutfen|hadi|şimdi|simdi|hemen)?\s*"
    r"(?:devam(?:\s+et)?|kald[ıi]ğ[ıi]n yerden devam(?:\s+et)?|devam edelim|"
    r"continue|resume|go on|keep going|tamamla|bitir|tekrar dene|yeniden dene)"
    r"\s*(?:lütfen|lutfen)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)


def _is_continuation_message(message: str) -> bool:
    return bool(_CONTINUATION_RE.match(message or ""))


def _task_display_text(task: dict) -> str:
    text = str(task.get("task") or "")
    marker = "Kullanıcı isteği:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    return (text or str(task.get("title") or "görev")).strip()


def _find_resumable_task(user_id: str, project_id: str = None):
    """Kullanicinin son basarisiz/iptal edilmis gorevini bul."""
    tasks = _load_tasks()
    rows = [t for t in tasks if str(t.get("user_id")) == str(user_id)]
    if project_id:
        scoped = [t for t in rows if str(t.get("project_id")) == str(project_id)]
        if scoped:
            rows = scoped
    rows.sort(key=lambda t: str(t.get("enqueued_at") or ""), reverse=True)
    for task in rows:
        if str(task.get("status") or "") in ("failed", "cancelled"):
            return task
    return None


def _find_active_task(user_id: str, project_id: str = None):
    """Su anda calisan/kuyrukta bekleyen gorevi bul."""
    tasks = _load_tasks()
    rows = [t for t in tasks if str(t.get("user_id")) == str(user_id)]
    if project_id:
        scoped = [t for t in rows if str(t.get("project_id")) == str(project_id)]
        if scoped:
            rows = scoped
    rows.sort(key=lambda t: str(t.get("enqueued_at") or ""), reverse=True)
    for task in rows:
        if str(task.get("status") or "") in ("running", "queued"):
            return task
    return None


def _latest_task(user_id: str, project_id: str = None):
    """Kullanicinin en son gorevini (durumu ne olursa olsun) dondur."""
    tasks = _load_tasks()
    rows = [t for t in tasks if str(t.get("user_id")) == str(user_id)]
    if project_id:
        scoped = [t for t in rows if str(t.get("project_id")) == str(project_id)]
        if scoped:
            rows = scoped
    rows.sort(key=lambda t: str(t.get("enqueued_at") or ""), reverse=True)
    return rows[0] if rows else None


def _get_chat_conversation(request, user, project_id: str = None):
    """Kullanici + proje icin kalici sohbet kaydi (Conversation/Node)."""
    conv = None
    conv_id = request.session.get("chat_conversation_id")
    if conv_id:
        try:
            conv = Conversation.objects.filter(id=conv_id, app_user=user).first()
        except Exception:
            conv = None
    if conv is not None:
        stored_project = str((conv.meta_data or {}).get("project_id") or "")
        if project_id and stored_project and stored_project != str(project_id):
            conv = None
    if conv is None:
        meta = {"project_id": project_id} if project_id else {}
        conv = Conversation.objects.create(app_user=user, title="EMOG Chat", meta_data=meta)
        request.session["chat_conversation_id"] = str(conv.id)
        request.session.modified = True
    return conv


def _chat_history_for_llm(conversation, limit: int = 10):
    """Son sohbet mesajlarini LLM mesaj formatinda dondur."""
    try:
        nodes = list(conversation.nodes.order_by("-timestamp")[:limit])
    except Exception:
        return []
    nodes.reverse()
    messages = []
    for node in nodes:
        if node.role not in ("user", "assistant"):
            continue
        content = str(node.content or "").strip()
        if not content:
            continue
        messages.append({"role": node.role, "content": content[:1400]})
    return messages


def _append_chat_message(conversation, role: str, content: str, model: str = "", meta_data: dict | None = None):
    if conversation is None:
        return None
    content = str(content or "").strip()
    if not content:
        return None
    try:
        last = conversation.nodes.order_by("-timestamp").first()
        return ConversationManager.add_message(
            str(conversation.id),
            str(last.id) if last else None,
            role,
            content[:8000],
            model=model or "",
            meta_data=meta_data or None,
        )
    except Exception as exc:
        logger.debug("chat mesaji kaydedilemedi: %s", exc)
        return None


def _remember_conversation(conversation, **values) -> None:
    if conversation is None:
        return
    try:
        meta = dict(conversation.meta_data or {})
        meta.update({k: v for k, v in values.items() if v is not None})
        conversation.meta_data = meta
        conversation.save()
    except Exception as exc:
        logger.debug("sohbet meta guncellenemedi: %s", exc)


@csrf_exempt
def assistant_message(request):
    """Understand a natural-language message and chat or start the right action."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    
    # Check authentication - if not authenticated, store message and return 401
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        # Get message early to store it
        if request.content_type and 'multipart/form-data' in request.content_type:
            message = (request.POST.get("message") or request.POST.get("task") or "").strip()
        else:
            message = (request.POST.get("message") or request.POST.get("task") or "").strip()
        
        if message:
            request.session['pending_chat_message'] = message
        return JsonResponse({
            "ok": False,
            "error": "Authentication required",
            "code": "auth_required",
            "login_url": "/auth/login/?next=/chat/",
        }, status=401)

    # Handle both form-data (multipart) and urlencoded
    if request.content_type and 'multipart/form-data' in request.content_type:
        message = (request.POST.get("message") or request.POST.get("task") or "").strip()
        uploaded_files_present = bool(getattr(request, "FILES", None)) and bool(
            request.FILES.getlist('attachments') or request.FILES.getlist('attachments[]')
        )
    else:
        message = (request.POST.get("message") or request.POST.get("task") or "").strip()
        uploaded_files_present = False
    
    if not message:
        return JsonResponse({"ok": False, "error": "Message is required"}, status=400)

    user_id = str(user.id)
    session_project = (
        request.session.get("current_project_id")
        or request.session.get("current_project_guid")
    )
    conversation = _get_chat_conversation(request, user, session_project)

    raw_message = message
    saved_chat_attachments = []
    if uploaded_files_present:
        try:
            from agent_app.chat_media import save_chat_attachments
            saved_chat_attachments = save_chat_attachments(request, user, conversation, node=None)
        except Exception as exc:
            logger.warning("chat ekleri kaydedilemedi: %s", exc)
            saved_chat_attachments = []

    message_for_store = message
    if saved_chat_attachments:
        names = ", ".join([str(a.get("name") or "dosya") for a in saved_chat_attachments])
        summary = f"[Ekler: {names}]"
        message_for_store = f"{message}\n\n{summary}" if message else summary
    message = message_for_store

    chat_history = _chat_history_for_llm(conversation, limit=10)
    latest_task = _latest_task(user_id, session_project)
    user_node = _append_chat_message(
        conversation,
        "user",
        message_for_store,
        meta_data={"attachments": saved_chat_attachments} if saved_chat_attachments else None,
    )
    if user_node and saved_chat_attachments:
        try:
            from agent_app.models import ChatAttachment
            att_ids = [a.get("id") for a in saved_chat_attachments if a.get("id")]
            if att_ids:
                ChatAttachment.objects.filter(id__in=att_ids, user=user, conversation=conversation).update(node=user_node)
        except Exception as exc:
            logger.debug("chat eki node baglantisi guncellenemedi: %s", exc)

    # Pending action (mandatory user choice) resolution / clarification.
    # Must run before intent routing and without guessing (no auto scene/target selection).
    try:
        from agent_app import chat_media as chat_media_mod
        pending_handled = chat_media_mod.try_handle_pending_action(conversation, user, raw_message)
        if pending_handled and pending_handled.get("handled"):
            reply_text = str(pending_handled.get("reply") or "")
            handled_atts = pending_handled.get("attachments") or pending_handled.get("media") or []
            assistant_meta = {"attachments": handled_atts} if handled_atts else None
            _append_chat_message(conversation, "assistant", reply_text, meta_data=assistant_meta)
            if pending_handled.get("ok"):
                _remember_conversation(conversation, last_intent="chat", last_topic=(raw_message or "")[:1000])
            payload = {
                "ok": bool(pending_handled.get("ok", True)),
                "intent": pending_handled.get("intent") or "chat",
                "reply": reply_text,
                "attachments": handled_atts or saved_chat_attachments,
                "media": handled_atts or [],
                "conversation_id": str(conversation.id),
                "pending_action": pending_handled.get("pending_action"),
                "needs_user_choice": bool(pending_handled.get("needs_user_choice")),
                "result": pending_handled.get("result"),
            }
            for k in ("job", "jobs", "pending_jobs"):
                if k in pending_handled:
                    payload[k] = pending_handled.get(k)
            return JsonResponse(payload)
    except Exception as exc:
        logger.warning("pending action handling error: %s", exc)

    # Ambiguous 3D upload: ask user where to place/store (no guessing, no placement).
    if saved_chat_attachments:
        try:
            from agent_app import chat_media as chat_media_mod
            pending_resp = chat_media_mod.maybe_create_3d_upload_pending_action(
                conversation, user, saved_chat_attachments, user_message=raw_message
            )
            if pending_resp:
                reply_text = str(pending_resp.get("reply") or "")
                _append_chat_message(conversation, "assistant", reply_text)
                _remember_conversation(conversation, last_intent="chat", last_topic=(raw_message or "")[:1000])
                return JsonResponse({
                    "ok": bool(pending_resp.get("ok", True)),
                    "intent": "chat",
                    "reply": reply_text,
                    "attachments": pending_resp.get("attachments") or saved_chat_attachments,
                    "conversation_id": str(conversation.id),
                    "pending_action": pending_resp.get("pending_action"),
                    "needs_user_choice": bool(pending_resp.get("needs_user_choice", True)),
                })
        except Exception as exc:
            logger.warning("3d upload pending action error: %s", exc)

    # Chat action intents requiring disambiguation/explicit choice (EntityClass instantiate, video edit)
    # Checked before generation so edit/instantiate disambiguation is not misrouted as new generation.
    try:
        from agent_app import chat_media as chat_media_mod
        action_resp = chat_media_mod.maybe_handle_chat_action_intents(conversation, user, raw_message)
        if action_resp:
            reply_text = str(action_resp.get("reply") or "")
            assistant_meta = None
            media_atts = action_resp.get("attachments") or action_resp.get("media") or []
            if media_atts:
                assistant_meta = {"attachments": media_atts}
            _append_chat_message(conversation, "assistant", reply_text, meta_data=assistant_meta)
            _remember_conversation(conversation, last_intent="chat", last_topic=(raw_message or "")[:1000])
            payload = {
                "ok": bool(action_resp.get("ok", True)),
                "intent": "chat",
                "reply": reply_text,
                "attachments": media_atts or saved_chat_attachments,
                "media": media_atts or [],
                "conversation_id": str(conversation.id),
            }
            for k in ("pending_action", "needs_user_choice", "job", "jobs", "pending_jobs", "result"):
                if k in action_resp:
                    payload[k] = action_resp.get(k)
            return JsonResponse(payload)
    except Exception as exc:
        logger.warning("chat action intent handling error: %s", exc)

    # Direct chat-driven generation intents (image/video/3D). 3D generation will ask for scene/placement choice.
    try:
        from agent_app import chat_media as chat_media_mod
        gen_resp = chat_media_mod.maybe_handle_chat_generation(conversation, user, raw_message)
        if gen_resp:
            reply_text = str(gen_resp.get("reply") or "")
            # For completed media results, persist assistant message with media attachments when appropriate
            assistant_meta = None
            media_atts = gen_resp.get("attachments") or gen_resp.get("media") or []
            if media_atts:
                assistant_meta = {"attachments": media_atts}
            _append_chat_message(conversation, "assistant", reply_text, meta_data=assistant_meta)
            _remember_conversation(conversation, last_intent="chat", last_topic=(raw_message or "")[:1000])
            payload = {
                "ok": bool(gen_resp.get("ok", True)),
                "intent": "chat",
                "reply": reply_text,
                "attachments": media_atts or saved_chat_attachments,
                "media": media_atts or [],
                "conversation_id": str(conversation.id),
            }
            # pass-through useful fields (pending/job polling)
            for k in ("pending_action", "needs_user_choice", "job", "jobs", "pending_jobs", "result"):
                if k in gen_resp:
                    payload[k] = gen_resp.get(k)
            return JsonResponse(payload)
    except Exception as exc:
        logger.warning("chat generation handling error: %s", exc)

    # "devam et" / "continue" gibi kisa mesajlar: niyet router'a gitmeden once
    # yarim kalmis/başarisiz son gorevi bul ve ayni projede yeniden baslat.
    continuation_request = _is_continuation_message(raw_message) if 'raw_message' in locals() else _is_continuation_message(message)
    if continuation_request:
        active = _find_active_task(user_id, session_project)
        if active:
            reply = "Görev zaten çalışıyor; mevcut çalıştırmaya bağlanıyorum."
            _append_chat_message(conversation, "assistant", reply)
            _remember_conversation(
                conversation,
                last_intent=active.get("mode") or "execute",
                last_run_id=active.get("run_id"),
                last_task=_task_display_text(active),
                last_mode=active.get("mode") or "execute",
                project_id=active.get("project_id") or session_project,
            )
            return JsonResponse({
                "ok": True,
                "intent": active.get("mode") or "execute",
                "reply": reply,
                "run_id": active.get("run_id"),
                "position": 0,
                "priority": active.get("priority") or "medium",
                "mode": active.get("mode") or "execute",
                "project_id": active.get("project_id") or session_project,
                "resumed": True,
                "attachments": saved_chat_attachments,
                "conversation_id": str(conversation.id),
            })

        previous = _find_resumable_task(user_id, session_project)
        if previous:
            project_id = previous.get("project_id") or session_project
            mode = previous.get("mode") or "execute"
            if project_id:
                request.session["current_project_id"] = project_id
                request.session["current_project_guid"] = project_id
                request.session["active_project_explicit"] = True
                request.session.modified = True
                try:
                    cancel_project_tasks(project_id, user_id)
                except Exception:
                    pass

            _start_queue_worker_once()
            run_id, position = enqueue_task(
                previous.get("task") or message,
                model=previous.get("model"),
                priority=previous.get("priority") or "high",
                project_id=project_id,
                user_id=user_id,
                mode=mode,
            )
            try:
                _update_task(run_id, conversation_id=str(conversation.id))
            except Exception:
                pass
            display = _task_display_text(previous)
            reply = (
                "Yarım kalan görevi yeniden başlatıyorum: "
                + (display[:220] + ("…" if len(display) > 220 else ""))
            )
            _append_chat_message(conversation, "assistant", reply)
            _remember_conversation(
                conversation,
                last_intent=mode,
                last_run_id=run_id,
                last_task=display,
                last_mode=mode,
                project_id=project_id,
            )
            return JsonResponse({
                "ok": True,
                "intent": mode,
                "reply": reply,
                "run_id": run_id,
                "position": position,
                "priority": previous.get("priority") or "high",
                "mode": mode,
                "project_id": project_id,
                "resumed": True,
                "attachments": saved_chat_attachments,
                "conversation_id": str(conversation.id),
            })

        # Devam edilecek basarisiz gorev yok: sohbet gecmisiyle normal akisa devam et.

    system_prompt = """You are EMOG's intent router. Read the user's Turkish or English message and return ONLY valid JSON.
Choose exactly one intent:
- chat: the user wants an explanation, answer, brainstorming, or conversation. Put a concise helpful answer in message.
- build: the user asks for a COMPLETE NEW product/app/site/game/system/platform to be created.
  Examples: "haber sitesi yap", "bana bir e-ticaret sitesi kur", "3d oyun yap",
  "androidde çalışan bir forum sistemi yap", "sosyal medya uygulaması geliştir",
  "yönetim paneli oluştur", "sağlık takip uygulaması yap", "bir SaaS platformu kur".
  Put the full request in task.
- execute: the user asks EMOG to modify, fix, test, explain or run EXISTING code — a NARROW job
  on an already existing project. Examples: "bu fonksiyonda hata var düzelt", "testleri çalıştır",
  "bu dosyayı oku ve açıkla", "bir script yaz", "şu SQL'i yaz", "refactor et".
  Put the complete task instruction in task.
- health: the user asks to check whether the model service/OmniRoute is reachable.
- models: the user asks which models are available.

Tie-breakers:
- If the user wants a brand new product that did not exist before -> build.
- If the user wants to change, fix, add a small feature to, or run something in an existing project -> execute.
- If the user only asks "how do I make X" (a question) -> chat.
Never execute code for a question that only asks for an explanation. Never invent a result.
Priority must be one of low, medium, high, urgent. Use urgent only when the user explicitly says it is urgent or blocking.
JSON shape: {"intent":"chat|execute|build|health|models","message":"...","task":"...","model":"","priority":"low|medium|high|urgent"}"""

    if latest_task:
        system_prompt += (
            "\n\n=== SON GÖREV ===\n"
            f"Durum: {latest_task.get('status')}\n"
            f"Mod: {latest_task.get('mode')}\n"
            f"Görev: {_task_display_text(latest_task)[:1200]}\n"
            "Kullanıcı 'devam et', 'bunu geliştir', 'kaldığın yerden' gibi bir şey derse "
            "bu son görevi/konuyu sürdür."
        )
    system_prompt += (
        "\n\n=== SON KONUŞMA ===\n"
        "Sana verilen mesaj geçmişini bağlam olarak kullan. 'devam et', 'bunu', 'önceki', "
        "'az önce' gibi ifadeleri bu geçmişe göre yorumla; kullanıcıya bağlamı hatırladığını göster."
    )
    # Hizli yoklama: ag engeli varsa bilgi olarak logla. Yoklama basarisiz olsa
    # bile LLM cagrisini dene — HTTP katmaninda IP failover var ve gercek cagri
    # bazi IP'ler engellenirken calisabilir; yalnizca gercek cagri da
    # ag hatasi verirse o zaman hata dondur.
    reachable, net_detail = provider_reachable(budget=4.0)
    if not reachable:
        logger.warning("assistant_message: NVIDIA yoklamasi basarisiz (%s); cagri yine de denenecek", net_detail)

    try:
        started = time.time()
        intent_model = getattr(settings, "NVIDIA_INTENT_MODEL", None) or getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "auto/best-chat")
        decision = None
        # Model bazen bos/gecersiz JSON dondurebilir; 3 kez dene.
        for attempt in range(3):
            response = llm_create_resilient(
                model=intent_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *chat_history,
                    {"role": "user", "content": message},
                ],
                temperature=0,
                max_tokens=300,
            )
            content = response.choices[0].message.content or ""
            decision = _parse_assistant_decision(content)
            if decision:
                break
            logger.warning("assistant_message: gecersiz JSON (deneme %d): %r", attempt + 1, content[:200])
        logger.info("assistant_message: intent yanit %.1fs (model: %s, decision: %s)", time.time() - started, intent_model, bool(decision))
    except Exception as exc:
        logger.exception("assistant_message: intent router LLM call failed")
        omni_cfg = getattr(settings, "OMNIROUTE", {})
        base = omni_cfg.get("BASE_URL", "")
        used_model = getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "")
        raw = str(exc)
        low = raw.lower()
        if _is_network_error(exc):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "NVIDIA servisine ulaşılamıyor (ağ/TLS engeli).",
                    "code": "provider_unreachable",
                    "detail": (
                        f"{raw}\n"
                        f"Yoklama: {net_detail}\n\n"
                        "Bu bir ağ/firewall engelidir, model hatası değildir.\n"
                        "Çözümler:\n"
                        "  1) VPN veya farklı ağ deneyin (datacenter IP'leri NVIDIA tarafından engellenir)\n"
                        "  2) Kendi reverse proxy'nizi kullanın: .env'de "
                        "NVIDIA_BASE_URLS=https://proxy.sizindomain.com/v1\n"
                        "  3) Kontrol: `python manage.py check_provider --probe`"
                    ),
                },
                status=503,
            )
        if "404" in low or "page not found" in low or "model_not_found" in low or "not found for account" in low:
            title = f"Model bulunamadı: {used_model}"
            hint = (
                "Bu model hesabınıza deploy edilmemiş olabilir. "
                "`python manage.py check_provider --probe` ile çalışan modelleri ölçün."
            )
        elif "401" in low or "403" in low or "unauthorized" in low:
            title = "API anahtarı reddedildi (401/403)."
            hint = "NVIDIA_API_KEY değerini .env içinde kontrol edin."
        elif "timeout" in low or "timed out" in low or "handshake" in low:
            title = "Model servisine ulaşılamadı (ağ/TLS engeli)."
            hint = (
                "Bağlantı birkaç saniye içinde kurulamadı (ağ/firewall engeli olabilir). "
                "Tekrar deneyebilirsiniz veya `python manage.py check_provider --probe` "
                "ile IP bazlı bağlantıyı ölçün."
            )
        else:
            title = "Niyet algılama servisine ulaşılamadı."
            hint = ""
        context = f"Sağlayıcı: {base} | Model: {used_model}"
        if not omni_cfg.get("API_KEY"):
            context += " | API anahtarı yok (NVIDIA_API_KEY)"
        return JsonResponse(
            {
                "ok": False,
                "error": title,
                "detail": "\n".join(part for part in (raw, context, hint) if part),
            },
            status=502,
        )

    if decision is None:
        return JsonResponse(
            {"ok": False, "error": "Model, isteğin niyetini geçerli biçimle belirleyemedi."},
            status=502,
        )

    # Guvenlik agi: niyet router "execute" dese bile ifade belirgin bir
    # "yeni urun yap" istegiyse otonom uretim dongusune yonlendir.
    if decision["intent"] == "execute" and looks_like_project_build(message):
        logger.info("assistant_message: build olarak yonlendirildi: %s", message[:80])
        decision["intent"] = "build"
        decision["task"] = decision.get("task") or message

    # "devam et" gibi kisa mesajlarda yeni bir gorev baslatma; sohbet gecmisiyle cevap ver.
    if continuation_request and decision["intent"] in ("build", "execute"):
        decision["intent"] = "chat"
        if not decision.get("message"):
            decision["message"] = (
                "Önceki konuşmaya göre devam ediyorum. Hangi kısmı sürdürmemi istersiniz?"
            )

    if decision["intent"] == "chat":
        reply = decision["message"] or "İsteğinizi anladım. Biraz daha ayrıntı verebilir misiniz?"
        _append_chat_message(conversation, "assistant", reply)
        _remember_conversation(
            conversation,
            last_intent="chat",
            last_topic=message[:1000],
        )
        return JsonResponse(
            {
                "ok": True,
                "intent": "chat",
                "reply": reply,
                "attachments": saved_chat_attachments,
                "conversation_id": str(conversation.id),
            }
        )

    if decision["intent"] == "health":
        return omni_health(request)

    if decision["intent"] == "models":
        return omni_models(request)

    task = decision["task"] or message
    is_build = decision["intent"] == "build"
    if is_build:
        task = (
            "Aşağıdaki isteği baştan sona hayata geçir. Önce konuyu derin araştır, aynı "
            "alandaki ürünleri incele, en gelişmiş versiyonunun spesifikasyonunu çıkar, "
            "ayrıntılı bir uygulama planı oluştur ve bu planı eksiksiz uygula.\n\n"
            f"Kullanıcı isteği: {task}"
        )
    # Kullanıcinin seçtiği model önceliklidir; yoksa router kararı / boşsa otomatik seçim.
    model = (request.POST.get("model") or decision["model"] or "").strip() or None
    if model in ("auto", "Auto"):
        model = None
    user_id = str(user.id) if user else None

    project_id = request.session.get("current_project_id") or request.session.get("current_project_guid")
    current_meta = project_store.read_project(project_id) if project_id else None
    if current_meta and not project_store.owner_matches(
        current_meta.get("owner_guid") or current_meta.get("user_id"),
        user_id=user_id,
        request=request,
    ):
        current_meta = None
    if not current_meta:
        project_id = None

    if is_build and not request.session.get("active_project_explicit"):
        created = project_store.create_project(user_id, task_text=task, status="building")
        project_id = created["id"]
        request.session["current_project_id"] = project_id
        request.session["current_project_guid"] = project_id
        request.session["active_project_explicit"] = False
        request.session.modified = True

    if not project_id:
        latest = project_store.find_latest_project(user_id=user_id)
        if latest:
            project_id = latest["id"]
            request.session["current_project_id"] = project_id
            request.session["current_project_guid"] = project_id
            request.session.modified = True

    # Cancel any existing running/queued tasks for this user in this project before starting new one
    if user_id and project_id:
        cancel_project_tasks(project_id, user_id)

    _start_queue_worker_once()
    run_id, position = enqueue_task(
        task, model=model, priority=decision["priority"], project_id=project_id,
        user_id=user_id, mode="build" if is_build else "execute",
    )
    try:
        _update_task(run_id, conversation_id=str(conversation.id))
    except Exception:
        pass

    # Auto-start sandbox container for preview
    if user_id and getattr(settings, 'EMOG_MULTI_TENANT', False):
        try:
            from agent_app.sandbox.user_sandbox import get_sandbox_manager
            manager = get_sandbox_manager()
            manager.ensure_running(user_id, project_id)
        except Exception:
            pass

    if is_build:
        queue_message = (
            "Proje üretimi başlatıldı: konuyu araştıracağım, rakipleri inceleyip en gelişmiş "
            "versiyonun planını çıkaracak, ardından planı eksiksiz uygulayacağım. "
            "İlerlemeyi adım adım göstereceğim."
        )
    else:
        queue_message = "Göreviniz çalıştırılıyor."
    mode_name = "build" if is_build else "execute"
    _append_chat_message(conversation, "assistant", queue_message)
    _remember_conversation(
        conversation,
        last_intent=decision["intent"],
        last_mode=mode_name,
        last_task=message[:4000],
        last_run_id=run_id,
        project_id=project_id,
    )
    return JsonResponse(
        {
            "ok": True,
            "intent": decision["intent"],
            "reply": queue_message,
            "run_id": run_id,
            "position": position,
            "priority": decision["priority"],
            "mode": mode_name,
            "project_id": project_id,
            "attachments": saved_chat_attachments,
            "conversation_id": str(conversation.id),
        }
    )


@require_http_methods(["GET"])
@csrf_exempt
def chat_history(request):
    """Kullanicinin son sohbet mesajlarini ve proje yol haritalarini dondur."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)

    project_id = (
        request.session.get("current_project_id")
        or request.session.get("current_project_guid")
    )
    conversation = _get_chat_conversation(request, user, project_id)
    if not project_id:
        project_id = str((conversation.meta_data or {}).get("project_id") or "")
    try:
        nodes = list(conversation.nodes.order_by("-timestamp")[:30])
    except Exception:
        nodes = []
    nodes.reverse()
    messages = []
    for node in nodes:
        if node.role not in ("user", "assistant"):
            continue
        content = str(node.content or "").strip()
        if not content:
            continue
        try:
            from agent_app.chat_media import serialize_attachments_for_node
            attachments = serialize_attachments_for_node(node)
        except Exception:
            attachments = []
        messages.append({
            "role": node.role,
            "content": content[:4000],
            "ts": node.timestamp.isoformat() if node.timestamp else "",
            "attachments": attachments,
        })

    # Eski yol haritaları: aynı projede çalışmış build görevlerinin planları.
    runs = []
    if project_id:
        try:
            from agent_app.buildkit.events import RunLogger
            from agent_app.buildkit.plan import PHASE_LABELS

            phase_labels = dict(PHASE_LABELS)
            phase_labels.update({
                "building": "Uygulama",
                "build": "Uygulama",
                "testing": "Test ve doğrulama",
                "review": "İnceleme",
            })

            with TASKS_LOCK:
                all_tasks = _load_tasks()
            for rec in reversed(all_tasks):
                if str(rec.get("project_id") or "") != str(project_id):
                    continue
                if str(rec.get("user_id") or "") != str(user.id):
                    continue
                if (rec.get("mode") or "build") != "build":
                    continue
                run_id = str(rec.get("run_id") or "")
                if not run_id:
                    continue
                plan_data = RunLogger(run_id=run_id).load("plan") or {}
                if not isinstance(plan_data, dict):
                    plan_data = {}
                raw_items = plan_data.get("items") or []
                if not isinstance(raw_items, list):
                    raw_items = []
                raw_items = [item for item in raw_items if isinstance(item, dict)]
                if not raw_items and isinstance(plan_data.get("phases"), list):
                    for phase in plan_data.get("phases") or []:
                        for item in phase.get("items") or []:
                            item = dict(item or {})
                            item.setdefault("phase", phase.get("id"))
                            raw_items.append(item)
                if not raw_items:
                    continue
                items = []
                for item in sorted(raw_items, key=lambda x: int((x or {}).get("order") or 0)):
                    if not isinstance(item, dict):
                        continue
                    title = str((item or {}).get("title") or "").strip()
                    if not title:
                        continue
                    phase = str(item.get("phase") or "foundation")
                    items.append({
                        "key": str(item.get("key") or ""),
                        "title": title[:300],
                        "phase": phase,
                        "phase_label": phase_labels.get(phase, phase),
                        "status": str(item.get("status") or "pending"),
                        "objective": str(item.get("objective") or "")[:300],
                        "order": int(item.get("order") or 0),
                    })
                if not items:
                    continue
                counts = {"done": 0, "failed": 0, "skipped": 0, "pending": 0}
                for item in items:
                    status = item["status"]
                    if status in ("done", "completed"):
                        counts["done"] += 1
                    elif status in ("failed", "error"):
                        counts["failed"] += 1
                    elif status == "skipped":
                        counts["skipped"] += 1
                    else:
                        counts["pending"] += 1
                runs.append({
                    "run_id": run_id,
                    "task": str(rec.get("task") or "")[:500],
                    "status": str(rec.get("status") or ""),
                    "enqueued_at": rec.get("enqueued_at") or "",
                    "completed_at": rec.get("completed_at") or "",
                    "total": len(items),
                    "done": counts["done"],
                    "failed": counts["failed"],
                    "skipped": counts["skipped"],
                    "pending": counts["pending"],
                    "items": items,
                })
                if len(runs) >= 8:
                    break
        except Exception as exc:
            logger.warning("Chat yol haritalari yuklenemedi: %s", exc)

    return JsonResponse({
        "ok": True,
        "conversation_id": str(conversation.id),
        "messages": messages,
        "runs": runs,
    })


@require_http_methods(["GET"])
@csrf_exempt
def build_run_detail(request, run_id):
    """Bir uretim kosusunun plan/arastirma/ozet durumu (sozlesme dosyadan okunur)."""
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)

    tasks = _load_tasks()
    task = next((t for t in tasks if t.get("run_id") == run_id), None)
    task_owner = (task or {}).get("user_id")
    if not task_owner or str(task_owner) != str(user.id):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    from agent_app.buildkit.events import RunLogger

    run_logger = RunLogger(run_id=run_id)
    payload = {
        "ok": True,
        "run_id": run_id,
        "status": (task or {}).get("status"),
        "mode": (task or {}).get("mode") or "execute",
        "task": (task or {}).get("task"),
        "plan": run_logger.load("plan"),
        "research": _research_summary(run_logger),
        "blueprint": run_logger.load("blueprint"),
        "summary": run_logger.load("summary"),
        "critic": run_logger.load("critic"),
    }
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})


def _research_summary(run_logger):
    dossier = run_logger.load("research") or {}
    if not dossier:
        return None
    return {
        "summary": dossier.get("summary", ""),
        "how_it_works": dossier.get("how_it_works", ""),
        "must_have_features": dossier.get("must_have_features", [])[:25],
        "advanced_features": dossier.get("advanced_features", [])[:25],
        "pitfalls": dossier.get("pitfalls", [])[:15],
        "tech_stack": dossier.get("tech_stack", [])[:15],
        "quality_bar": dossier.get("quality_bar", {}),
        "domain": (dossier.get("scope") or {}).get("domain", ""),
        "queries": dossier.get("queries", []),
        "url_count": dossier.get("url_count", 0),
    }


@require_http_methods(["GET"])
@csrf_exempt
def search_providers(request):
    """Hangi arama saglayicilari etkin (API anahtari + ucretsiz)."""
    from agent_app.buildkit import search as search_mod

    rows = search_mod.provider_status()
    return JsonResponse({
        "ok": True,
        "configured": sum(1 for r in rows if r["configured"] and not r["hot"]),
        "total": len(rows),
        "providers": rows,
    })


def task_logs(request, run_id):
    """Return JSON-lines for a run log. Query param `since` (int) returns entries after that 0-based index."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)

    # Ownership check: ONLY the task owner can read logs. Deny if owner unknown.
    tasks = _load_tasks()
    task_owner = next((t.get("user_id") for t in tasks if t.get("run_id") == run_id), None)
    if not task_owner or str(task_owner) != str(user.id):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    since = 0
    try:
        since = int(request.GET.get("since", 0))
    except Exception:
        since = 0

    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    path = os.path.join(dirp, f"{run_id}.log")
    lines = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        lines.append({"ts": None, "type": "raw", "text": line})
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)
    else:
        return JsonResponse({"ok": True, "lines": [], "count": 0})

    total = len(lines)
    new = lines[since:]
    return JsonResponse({"ok": True, "lines": new, "count": total})


@csrf_exempt
def task_stream(request, run_id):
    """Stream task logs in real-time using Server-Sent Events."""
    import time

    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)

    # Ownership check: ONLY the task owner can stream. Deny if owner unknown.
    tasks = _load_tasks()
    task_owner = next((t.get("user_id") for t in tasks if t.get("run_id") == run_id), None)
    if not task_owner or str(task_owner) != str(user.id):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    path = os.path.join(dirp, f"{run_id}.log")
    
    def event_stream():
        last_pos = 0
        buffer = b""
        while True:
            if os.path.exists(path):
                try:
                    current_size = os.path.getsize(path)
                    if current_size > last_pos:
                        with open(path, "rb") as handle:
                            handle.seek(last_pos)
                            data = handle.read(current_size - last_pos)
                        last_pos = current_size
                        buffer += data
                        parts = buffer.split(b"\n")
                        complete, buffer = parts[:-1], parts[-1]
                        for raw in complete:
                            line = raw.decode("utf-8", "replace").strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                if isinstance(obj, dict):
                                    yield f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
                                else:
                                    yield f"data: {json.dumps({'type': 'log', 'text': str(obj)}, ensure_ascii=False)}\n\n"
                            except Exception:
                                yield f"data: {json.dumps({'type': 'log', 'text': line}, ensure_ascii=False)}\n\n"
                    elif current_size < last_pos:
                        last_pos = 0
                        buffer = b""
                except Exception:
                    pass

            task = next((t for t in _load_tasks() if t.get("run_id") == run_id), None)
            status = (task or {}).get("status")
            if status in ("completed", "failed", "cancelled"):
                if buffer.strip():
                    line = buffer.decode("utf-8", "replace").strip()
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            yield f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
                    except Exception:
                        yield f"data: {json.dumps({'type': 'log', 'text': line}, ensure_ascii=False)}\n\n"
                    buffer = b""
                payload = {
                    "type": "done",
                    "ok": status == "completed",
                    "status": status,
                    "text": (task or {}).get("error") or "",
                    "error": (task or {}).get("error") or "",
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break

            if status not in ("queued", "running"):
                yield f"data: {json.dumps({'type': 'done', 'ok': False, 'status': status or 'unknown', 'error': 'Gorev durumu bilinmiyor'}, ensure_ascii=False)}\n\n"
                break

            yield ": heartbeat\n\n"
            time.sleep(0.5)
    
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ===== Queue / Tasks / Runs listeleri (sablonlarin bekledigi API'ler) =====


def queue_list(request):
    """Şu anda kuyrukta bekleyen görevler (chat sayfasındaki rozet)."""
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    # Also filter by current project if available
    project_id = request.session.get("current_project_id")

    with TASK_QUEUE_LOCK:
        if project_id and project_id in TASK_QUEUES and user_id in TASK_QUEUES[project_id]:
            user_queue = TASK_QUEUES[project_id][user_id]
        else:
            user_queue = []
        items = [
            {
                "run_id": item.get("run_id"),
                "title": item.get("task"),
                "task": item.get("task"),
                "model": item.get("model"),
                "priority": item.get("priority", "medium"),
                "status": "queued",
                "enqueued_at": item.get("enqueued_at"),
            }
            for item in user_queue
        ]
    return JsonResponse({"ok": True, "queue": items, "count": len(items)})


@csrf_exempt
@require_http_methods(["POST"])
def user_presence(request):
    """Mark user as active/inactive to pause/resume their queue."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)
    
    user_id = str(user.id)
    try:
        data = json.loads(request.body)
        raw_active = data.get("active", True)
        if isinstance(raw_active, bool):
            active = raw_active
        else:
            active = str(raw_active).strip().lower() not in {"false", "0", "no", "off", ""}
    except Exception:
        active = True
    
    set_user_active(
        user_id,
        bool(active),
        continue_in_background=bool(getattr(user, "continue_in_background", False)),
    )
    return JsonResponse({
        "ok": True,
        "active": bool(active),
        "paused": is_user_paused(user_id),
        "continue_in_background": bool(getattr(user, "continue_in_background", False)),
    })


@csrf_exempt
@require_http_methods(["POST"])
def queue_clear(request):
    """Clear current user's queued tasks in current project (keeps running task)."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)
    
    user_id = str(user.id)
    project_id = request.session.get("current_project_id")
    
    with TASK_QUEUE_LOCK:
        cleared = 0
        if project_id and project_id in TASK_QUEUES and user_id in TASK_QUEUES[project_id]:
            cleared = len(TASK_QUEUES[project_id][user_id])
            TASK_QUEUES[project_id][user_id].clear()
    return JsonResponse({"ok": True, "cleared": cleared})


@csrf_exempt
@require_http_methods(["POST"])
def queue_clear_all(request):
    """Clear ALL users' queues (admin/debug)."""
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)
    
    # Optional: check if admin
    # if not user.is_superuser: return JsonResponse({"ok": False, "error": "Admin required"}, status=403)
    
    with TASK_QUEUE_LOCK:
        total_cleared = 0
        for user_id, user_queue in TASK_QUEUES.items():
            total_cleared += len(user_queue)
            user_queue.clear()
        TASK_QUEUES.clear()
    return JsonResponse({"ok": True, "cleared": total_cleared})


def tasks_list(request):
    """Kaydedilmiş tüm görevler (tasks.json), en yeniden eskiye."""
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None

    with TASKS_LOCK:
        tasks = list(reversed(_load_tasks()))
    if user_id:
        tasks = [t for t in tasks if t.get("user_id") == user_id or t.get("user_id") is None]
    return JsonResponse({"ok": True, "tasks": tasks, "count": len(tasks)})


def tasks_page(request):
    return render(request, "agent_app/tasks.html")


def runs_list(request):
    """Çalıştırma geçmişi: task_runs/*.log dosyalarından türetilir. Kullanıcı bazlı."""
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    if not user_id:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)

    dirp = os.path.join(settings.BASE_DIR, "task_runs")
    # Only show logs for this user's runs
    with TASKS_LOCK:
        user_run_ids = {
            t.get("run_id") for t in _load_tasks()
            if t.get("user_id") == user_id
        }

    try:
        names = [n for n in os.listdir(dirp) if n.endswith(".log")]
        names.sort(key=lambda n: os.path.getmtime(os.path.join(dirp, n)), reverse=True)
    except OSError:
        names = []

    runs = []
    for name in names[:200]:
        run_id = name[:-4]  # strip .log
        if run_id not in user_run_ids:
            continue
        path = os.path.join(dirp, name)
        preview = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tail = [ln.strip() for ln in fh.readlines()[-60:] if ln.strip()]
            texts = []
            for line in tail:
                try:
                    obj = json.loads(line)
                except Exception:
                    texts.append(line)
                    continue
                if isinstance(obj, dict):
                    value = obj.get("text") or obj.get("message") or obj.get("output")
                    if value:
                        texts.append(str(value))
            preview = " ".join(texts).strip()[:300]
        except OSError:
            pass
        try:
            ts = os.path.getmtime(path)
        except OSError:
            ts = 0
        runs.append({"file": name, "preview": preview, "ts": ts})

    return JsonResponse({"ok": True, "runs": runs, "count": len(runs)})


# ===== Tools paneli API'si =====


def _current_project_root(request):
    """Oturumdaki aktif projenin kök dizini (yoksa None)."""
    project_id = request.session.get("current_project_id")
    if not project_id:
        return None
    try:
        from agent_app.project_scanner import set_project_context, get_project_root

        set_project_context(project_id)
        return get_project_root()
    except Exception:
        return None


_TOOL_BLOCKED_FILES = {
    "project.json", "db.sqlite3", ".env", ".env.local", ".env.production",
    ".project_meta.json",
}
_TOOL_BLOCKED_DIRS = {
    "node_modules", "venv", ".venv", "__pycache__", ".git", "uploads",
}


def _tool_path_blocked(path) -> bool:
    """Tools UI'ında gösterilmemesi/erişilmemesi gereken yollar."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p and p != "."]
    if any(part in _TOOL_BLOCKED_DIRS for part in parts):
        return True
    name = parts[-1] if parts else ""
    if name in _TOOL_BLOCKED_FILES:
        return True
    if name.startswith(".") and name not in (".env.example", ".gitignore"):
        return True
    return False


def _resolve_app_path(pdir, req_path):
    """Resolve user path relative to app_dir. Returns (app_dir_name, full_list_path)."""
    app_dir_name = None
    meta_file = pdir / 'project.json'
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
            app_dir_name = meta.get('app_dir')
        except Exception:
            pass
    if not app_dir_name:
        # Proje kökünde doğrudan uygulama dosyaları varsa kökü kullan;
        # aksi halde ilk uygun alt klasörü uygulama klasörü say.
        root_markers = (
            "index.html", "package.json", "manage.py", "requirements.txt",
            "pyproject.toml", "vite.config.js", "vite.config.ts", "next.config.js",
        )
        if any((pdir / marker).exists() for marker in root_markers):
            app_dir_name = ""
        else:
            for d in pdir.iterdir():
                if d.is_dir() and d.name not in ('uploads', '__pycache__', '.git') and not d.name.startswith('.'):
                    app_dir_name = d.name
                    break
    if req_path:
        full = f"{app_dir_name}/{req_path}" if app_dir_name else req_path
    else:
        full = app_dir_name or ''
    return app_dir_name, full


@require_auth
def tools_file_tree(request):
    """Aktif projenin dosya agaci - sadece AI'in olusturdugu uygulama klasoru."""
    from agent_app.project_scanner import tool_list_files
    from agent_app.projects.security import validate_project_access, get_user_guid
    
    # Try both session keys
    project_guid = request.session.get('current_project_guid') or request.session.get('current_project_id')
    if not project_guid:
        return JsonResponse({"ok": False, "error": "No active project"}, status=400)
    
    user_guid = get_user_guid(request)
    if not user_guid:
        return JsonResponse({"ok": False, "error": "User not identified"}, status=403)
    
    # Set thread-local context for tools
    from agent_app.project_scanner import set_project_context
    set_project_context(project_guid, user_guid, request)
    
    # Validate access
    err, pdir = validate_project_access(request, project_guid)
    if err:
        return err
    
    # Resolve app_dir
    req_path = (request.GET.get("path") or "").strip()
    app_dir_name, list_path = _resolve_app_path(pdir, req_path)
    
    result = tool_list_files(list_path)
    if not result.get("ok"):
        return JsonResponse({"ok": False, "error": result.get("error", "Failed to list files")}, status=400)
    
    tree = {}
    for entry in result.get("entries", []):
        name = entry.get("name", "")
        if not name or _tool_path_blocked(name):
            continue
        t = entry.get("type", "file")
        tree[name] = {
            "name": name,
            "type": "directory" if t in ("dir", "directory") else "file",
        }
    return JsonResponse({"ok": True, "tree": tree, "path": req_path, "app_dir": app_dir_name})


@require_auth
def tools_read_file(request):
    from agent_app.project_scanner import tool_read_file
    from agent_app.projects.security import validate_project_access, get_user_guid
    
    project_guid = request.session.get('current_project_guid') or request.session.get('current_project_id')
    if not project_guid:
        return JsonResponse({"ok": False, "error": "No active project"}, status=400)
    
    user_guid = get_user_guid(request)
    if not user_guid:
        return JsonResponse({"ok": False, "error": "User not identified"}, status=403)
    
    from agent_app.project_scanner import set_project_context
    set_project_context(project_guid, user_guid, request)
    
    err, pdir = validate_project_access(request, project_guid)
    if err:
        return err
    
    path = (request.GET.get("path") or "").strip()
    if not path:
        return JsonResponse({"ok": False, "error": "path is required"}, status=400)
    if _tool_path_blocked(path):
        return JsonResponse({"ok": False, "error": "Bu dosya/klasör erişime kapalı."}, status=403)

    
    _, full_path = _resolve_app_path(pdir, path)
    result = tool_read_file(full_path)
    status = 200 if result.get("ok") else 400
    return JsonResponse(result, status=status)


@csrf_exempt
@require_auth
def tools_write_file(request):
    from agent_app.project_scanner import tool_write_file
    from agent_app.projects.security import validate_project_access, get_user_guid
    
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    
    project_guid = request.session.get('current_project_guid') or request.session.get('current_project_id')
    if not project_guid:
        return JsonResponse({"ok": False, "error": "No active project"}, status=400)
    
    user_guid = get_user_guid(request)
    if not user_guid:
        return JsonResponse({"ok": False, "error": "User not identified"}, status=403)
    
    from agent_app.project_scanner import set_project_context
    set_project_context(project_guid, user_guid, request)
    
    err, pdir = validate_project_access(request, project_guid)
    if err:
        return err

    if request.content_type and "application/json" in request.content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "Invalid JSON body"}, status=400)
    else:
        data = request.POST

    path = (data.get("path") or "").strip()
    content = data.get("content")
    if not path or content is None:
        return JsonResponse({"ok": False, "error": "path and content are required"}, status=400)
    if _tool_path_blocked(path):
        return JsonResponse({"ok": False, "error": "Bu dosya/klasör erişime kapalı."}, status=403)

    _, full_path = _resolve_app_path(pdir, path)
    result = tool_write_file(full_path, content)
    return JsonResponse(result, status=200 if result.get("ok") else 400)


@csrf_exempt
@require_auth
def tools_run_command(request):
    """Run a command in the project directory. Supports detached/background mode for dev servers."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    
    # Get project_id from request body (passed by JavaScript) or session
    if request.content_type and "application/json" in request.content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "Invalid JSON body"}, status=400)
    else:
        data = request.POST
    
    project_guid = data.get('project_id') or request.session.get('current_project_guid') or request.session.get('current_project_id')
    if not project_guid:
        return JsonResponse({"ok": False, "error": "No active project"}, status=400)
    
    from agent_app.projects.security import validate_project_access
    from agent_app.permissions import current_user as get_user_guid
    user_guid = get_user_guid(request)
    if not user_guid:
        return JsonResponse({"ok": False, "error": "User not identified"}, status=403)
    
    from agent_app.project_scanner import set_project_context
    set_project_context(project_guid, user_guid, request)
    
    err, pdir = validate_project_access(request, project_guid)
    if err:
        return err
    
    if request.content_type and "application/json" in request.content_type:
        try:
            data = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "Invalid JSON body"}, status=400)
    else:
        data = request.POST
    
    command = (data.get("command") or "").strip()
    timeout = int(data.get("timeout", 60))
    detach = data.get("detach", False)
    
    if not command:
        return JsonResponse({"ok": False, "error": "command is required"}, status=400)
    
    from agent_app.project_scanner import tool_run_command
    
    # Auto-detect dev server port from command and update project dev_port
    def _detect_dev_port(cmd):
        """Extract likely dev server port from command."""
        cmd_lower = cmd.lower()
        if 'npm run dev' in cmd_lower or 'npm run start' in cmd_lower or 'pnpm dev' in cmd_lower or 'yarn dev' in cmd_lower or 'yarn start' in cmd_lower:
            return 5173  # Vite default (most common)
        if 'npm run dev -- --port' in cmd_lower or 'npm run start -- --port' in cmd_lower:
            import re
            match = re.search(r'--port\s+(\d+)', cmd_lower)
            if match:
                return int(match.group(1))
        if 'python -m http.server' in cmd_lower:
            import re
            match = re.search(r'http\.server\s+(\d+)', cmd_lower)
            if match:
                return int(match.group(1))
            return 8000
        if 'python manage.py runserver' in cmd_lower:
            import re
            match = re.search(r'runserver\s+(\d+)', cmd_lower)
            if match:
                return int(match.group(1))
            return 8000
        if 'next dev' in cmd_lower or 'next start' in cmd_lower:
            return 3000
        if 'nuxt dev' in cmd_lower:
            return 3000
        if 'astro dev' in cmd_lower:
            return 4321
        return None
    
    detected_port = _detect_dev_port(command)
    
    if detach:
        # Auto-set dev_port if detected
        if detected_port:
            try:
                meta_file = Path(str(pdir)) / 'project.json'
                if meta_file.exists():
                    meta = json.load(open(meta_file))
                    if meta.get('dev_port') != detected_port:
                        meta['dev_port'] = detected_port
                        meta['updated_at'] = datetime.utcnow().isoformat() + 'Z'
                        json.dump(meta, open(meta_file, 'w'), indent=2)
            except Exception:
                pass

        # Multi-tenant: use sandbox container instead of host process
        if getattr(settings, 'EMOG_MULTI_TENANT', False):
            from agent_app.permissions import current_user
            from agent_app.sandbox.user_sandbox import get_sandbox_manager

            user = current_user(request)
            user_id = str(user.id) if user else None
            project_id = request.session.get('current_project_id')

            if user_id and project_id:
                manager = get_sandbox_manager()
                result = manager.start(user_id, project_id, dev_command=command, port=detected_port or 3000)
                if result.get('ok'):
                    return JsonResponse({
                        "ok": True,
                        "detached": True,
                        "sandbox": True,
                        "message": f"Started in sandbox container",
                        "preview_url": manager.preview_url(user_id, project_id),
                        "container_id": result.get("container_id"),
                    })
                else:
                    return JsonResponse({"ok": False, "error": result.get("error", "Sandbox start failed")}, status=500)

        # Single-tenant fallback: run on host
        # Run in background, return immediately - use project directory
        import subprocess
        import threading
        import os
        
        def run_detached():
            try:
                proc = subprocess.Popen(
                    command, shell=True, cwd=str(pdir),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True  # Detach from parent
                )
                # Don't wait - let it run in background
            except Exception as e:
                print(f"Detached command failed: {e}")
        
        thread = threading.Thread(target=run_detached, daemon=True)
        thread.start()
        
        return JsonResponse({
            "ok": True,
            "detached": True,
            "message": f"Command started in background: {command}",
            "dev_port": detected_port
        })
    
    # Normal blocking execution
    result = tool_run_command(command, timeout=timeout)
    return JsonResponse(result, status=200 if result.get("ok") else 400)


# ===== Yasal sayfalar (kayit formundaki baglantilar) =====

_LEGAL_PAGES = {
    "terms": {
        "title": "Kullanim Sartlari",
        "updated": "2026",
        "sections": [
            {"heading": "Hizmet kullanimi", "body": "EMOG yerel calisan bir gelistirici asistanidir. Hesabinizla yaptiginiz istekler, secilen model saglayicisina gonderilir ve orada islenir."},
            {"heading": "Sorumluluk", "body": "Uretilen kod ve metinler incelemeden kullanilmalidir. EMOG gelistirici aracinin ciktilarindan dolasi olarak sorumluluk kabul edilmez."},
            {"heading": "Veri", "body": "Projeleriniz ve gecerlilik belirtecleriniz sunucuda saklanir. API anahtarlari yalnizca ortam degiskenlerinden okunur ve istemciye gonderilmez."},
        ],
    },
    "privacy": {
        "title": "Gizlilik Politikasi",
        "updated": "2026",
        "sections": [
            {"heading": "Toplanan veriler", "body": "Hesap bilgileri, proje dosyalari ve sohbet gecmisi. Odeme bilgisi tutulmaz."},
            {"heading": "Uctuncu taraflar", "body": "Mesajlariniz secilen model saglayicisina (ornegin NVIDIA) gonderilir. GitHub entegrasyonu kullaniliyorsa yalnizca erisim verilen depo verileri islenir."},
            {"heading": "Kontrol", "body": "Hesabinizi istediginiz zaman silebilir ve API baglantilarini temizleyebilirsiniz."},
        ],
    },
}


def legal_page(request, page_key):
    page = _LEGAL_PAGES.get(page_key)
    if page is None:
        return JsonResponse({"ok": False, "error": "Sayfa bulunamadi"}, status=404)
    return render(request, "agent_app/legal.html", {"page": page})


def terms_page(request):
    return legal_page(request, "terms")


def privacy_page(request):
    return legal_page(request, "privacy")


# ===== Preview Proxy =====

import urllib.request
import urllib.error
import urllib.parse
from urllib.parse import urljoin, urlparse
import re


def _rewrite_html_urls(html, base_url, proxy_base):
    """Rewrite relative URLs in HTML to go through proxy."""
    parsed_base = urlparse(base_url)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    
    # Rewrite href, src, action attributes
    def rewrite_attr(match):
        attr = match.group(1)
        quote = match.group(2)
        url = match.group(3)
        if not url or url.startswith(('data:', 'javascript:', 'mailto:', 'tel:', '#')):
            return match.group(0)
        # Skip absolute URLs to other domains (they'll fail anyway due to CORS/X-Frame-Options)
        if url.startswith(('http://', 'https://')) and not url.startswith(origin):
            return match.group(0)
        # Rewrite relative URLs
        absolute = urljoin(base_url, url)
        proxied = f"{proxy_base}?url={urllib.parse.quote(absolute)}"
        return f'{attr}={quote}{proxied}{quote}'
    
    # href, src, action
    html = re.sub(r'(href|src|action)\s*=\s*(["\'])([^"\']*)\2', rewrite_attr, html)
    
    # CSS url()
    def rewrite_css_url(match):
        url = match.group(1).strip('\'"')
        if url.startswith(('data:', 'http://', 'https://')):
            return match.group(0)
        absolute = urljoin(base_url, url)
        proxied = f"{proxy_base}?url={urllib.parse.quote(absolute)}"
        return f'url({proxied})'
    
    html = re.sub(r'url\(([^)]+)\)', rewrite_css_url, html)
    
    # Remove X-Frame-Options and CSP meta tags that block framing
    html = re.sub(r'<meta[^>]*http-equiv=["\'](X-Frame-Options|Content-Security-Policy)["\'][^>]*>', '', html, flags=re.IGNORECASE)
    
    # Add base tag for relative URLs
    if '<head>' in html.lower():
        html = html.replace('<head>', f'<head><base href="{base_url}">', 1)
    elif '<HEAD>' in html:
        html = html.replace('<HEAD>', f'<HEAD><base href="{base_url}">', 1)
    
    return html


def _fetch_page(url):
    """Fetch a page with proper headers."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get('Content-Type', '')
            body = resp.read()
            # Handle gzip
            if resp.headers.get('Content-Encoding') == 'gzip':
                import gzip
                body = gzip.decompress(body)
            return body.decode('utf-8', errors='replace'), content_type, resp.getcode()
    except urllib.error.HTTPError as e:
        return None, None, e.code
    except Exception as e:
        return None, None, str(e)


@csrf_exempt
def preview_proxy(request):
    """Proxy endpoint to fetch and serve web pages for iframe preview."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET required"}, status=405)
    
    url = request.GET.get('url', '').strip()
    if not url:
        return JsonResponse({"ok": False, "error": "url parameter required"}, status=400)
    
    # Validate URL
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return JsonResponse({"ok": False, "error": "Invalid URL"}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid URL format"}, status=400)
    
# Security: block private IPs (SSRF protection) - but allow localhost for preview
    import socket
    try:
        ip = socket.gethostbyname(parsed.hostname)
        # Block private ranges except localhost (127.0.0.1) for preview
        if ip.startswith(('10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.', '169.254.', '0.')):
            return JsonResponse({"ok": False, "error": "Access to private IPs blocked"}, status=403)
    except Exception:
        pass
    
    html, content_type, status = _fetch_page(url)
    
    if html is None:
        # Return an error page that displays in the iframe instead of "refused to connect"
        error_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Preview Error</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0a0f1a;color:#f1f5f9;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px}}
.card{{background:#111b2b;border:1px solid #1f2937;border-radius:16px;padding:40px;max-width:500px;text-align:center}}
h1{{color:#ef4444;margin:0 0 16px}}
p{{color:#94a3b8;margin:0 0 24px}}
code{{background:#0f141f;padding:2px 8px;border-radius:4px;color:#06b6d4}}
.btn{{display:inline-block;padding:12px 24px;background:#06b6d4;color:#042027;border:none;border-radius:8px;font-weight:600;text-decoration:none;cursor:pointer}}
.btn:hover{{filter:brightness(1.05)}}
.detected{{margin-top:16px;padding:12px;background:#1f2937;border-radius:8px;font-size:13px;color:#94a3b8}}
</style></head><body>
<div class="card">
<h1>⚠️ Preview Unavailable</h1>
<p>Could not load <code>{url}</code></p>
<p>Status: <strong>{status}</strong></p>
<p>The proxy couldn't fetch this URL. Possible reasons:</p>
<ul style="text-align:left;max-width:400px;margin:16px auto;color:#94a3b8">
<li>Server not responding or not serving HTML</li>
<li>Connection refused / timeout</li>
<li>SSL/TLS certificate issues</li>
<li>Target blocks iframe embedding (X-Frame-Options)</li>
</ul>
<a class="btn" href="{url}" target="_blank">Open in New Tab</a>
<div class="detected">
Try: <code>npm run dev</code> or <code>python -m http.server 8000</code> in your project folder
</div>
</div></body></html>"""
        response = HttpResponse(error_html, content_type='text/html; charset=utf-8')
        response['X-Frame-Options'] = 'ALLOWALL'
        response['Content-Security-Policy'] = "frame-ancestors 'self';"
        return response
    
    # Only rewrite HTML content
    if 'text/html' in (content_type or ''):
        proxy_base = request.build_absolute_uri('/preview/proxy/')
        html = _rewrite_html_urls(html, url, proxy_base)
        # Strip X-Frame-Options and CSP headers from response
        response = HttpResponse(html, content_type='text/html; charset=utf-8')
        response['X-Frame-Options'] = 'ALLOWALL'
        response['Content-Security-Policy'] = "frame-ancestors 'self';"
        return response
    
    # For other content (CSS, JS, images), proxy them too
    response = HttpResponse(html, content_type=content_type)
    response['X-Frame-Options'] = 'ALLOWALL'
    return response


# ===== Auto Preview Detection - Smart Port Management =====

import socket
import subprocess
import threading
import time
import json
from pathlib import Path
from django.http import FileResponse
from django.core.cache import cache

COMMON_DEV_PORTS = [3000, 5173, 8080, 8000, 8003, 4200, 3001, 5000, 8888, 9000, 3002, 3003, 3004, 3005, 4000, 4001, 4002, 5174, 5175, 8001, 8002, 8004, 8005, 8081, 8082, 9001, 9002]

# Global port registry - tracks which port belongs to which project
_PORT_REGISTRY = {}  # {port: {project_id, project_name, started_at, pid}}
_REGISTRY_LOCK = threading.Lock()

def _check_port(port, host='localhost'):
    """Check if a port is open and responding."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False

def _get_port_owner(port):
    """Get the project owning a port."""
    with _REGISTRY_LOCK:
        return _PORT_REGISTRY.get(port)

def _register_port(port, project_id, project_name, pid=None):
    """Register a port as owned by a project."""
    with _REGISTRY_LOCK:
        _PORT_REGISTRY[port] = {
            'project_id': project_id,
            'project_name': project_name,
            'started_at': time.time(),
            'pid': pid
        }

def _unregister_port(port):
    """Unregister a port."""
    with _REGISTRY_LOCK:
        _PORT_REGISTRY.pop(port, None)

def _get_all_active_ports():
    """Scan common dev ports and return list of active ports with HTML content - FAST VERSION with caching."""
    import urllib.request
    
    # Try cache first
    cached = cache.get('emog_active_ports')
    if cached:
        return cached
    
    active = []
    # Only check common dev ports for speed
    for port in COMMON_DEV_PORTS:
        if _check_port(port):
            try:
                url = f'http://localhost:{port}'
                req = urllib.request.Request(url, headers={'User-Agent': 'EMOG-Preview/1.0'})
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.getcode() < 500:
                        content_type = resp.headers.get('Content-Type', '')
                        body = resp.read(2048).decode('utf-8', errors='replace')
                        if 'text/html' in content_type or '<html' in body.lower() or '<!doctype' in body.lower():
                            owner = _get_port_owner(port)
                            active.append({
                                'port': port,
                                'url': f'http://localhost:{port}',
                                'owner': owner,
                                'is_emog': port == 8003  # EMOG's own port
                            })
            except Exception:
                pass
    
    # Cache for 30 seconds
    cache.set('emog_active_ports', active, 30)
    return active

def _find_free_port(start=3000, end=9999):
    """Find a free port for a new project - checks port availability, registry, and project configs."""
    # Collect all ports assigned in project.json files
    assigned_ports = set()
    base = Path(settings.BASE_DIR)
    projects_root = base / 'projects'
    if projects_root.exists():
        for project_dir in projects_root.iterdir():
            if project_dir.is_dir():
                meta_file = project_dir / 'project.json'
                if meta_file.exists():
                    try:
                        meta = json.load(open(meta_file))
                        dev_port = meta.get('dev_port')
                        if dev_port and isinstance(dev_port, int):
                            assigned_ports.add(dev_port)
                    except Exception:
                        pass
    
    for port in range(start, end + 1):
        if not _check_port(port):
            with _REGISTRY_LOCK:
                if port not in _PORT_REGISTRY and port not in assigned_ports:
                    return port
    return None


def _sync_port_registry():
    """Sync the port registry with currently active ports."""
    active = _get_all_active_ports()
    with _REGISTRY_LOCK:
        for server in active:
            port = server['port']
            if port not in _PORT_REGISTRY:
                owner = server.get('owner')
                _PORT_REGISTRY[port] = {
                    'project_id': owner.get('project_id') if owner else None,
                    'project_name': owner.get('project_name', 'unknown') if owner else 'auto-detected',
                    'started_at': time.time(),
                    'pid': None
                }

def _get_project_dev_port(project_dir):
    """Get or assign dev_port for a project."""
    meta_file = Path(project_dir) / 'project.json'
    if not meta_file.exists():
        return None
    try:
        meta = json.load(open(meta_file))
        return meta.get('dev_port')
    except Exception:
        return None

def _set_project_dev_port(project_dir, port):
    """Set dev_port in project.json."""
    meta_file = Path(project_dir) / 'project.json'
    if not meta_file.exists():
        return False
    try:
        meta = json.load(open(meta_file))
        meta['dev_port'] = port
        meta['updated_at'] = time.time()
        json.dump(meta, open(meta_file, 'w'), indent=2)
        return True
    except Exception:
        return False

def _get_project_dir(project_id=None):
    """Get project directory - user-scoped in multi-tenant mode."""
    if project_id:
        from agent_app.permissions import current_user
        from django.conf import settings as dj_settings

        if getattr(dj_settings, 'EMOG_MULTI_TENANT', False):
            # Multi-tenant: look in user-scoped directory
            # We need the request to get user - but this is a helper without request
            # Use session-based project resolution
            from .projects.security import get_project_dir
            return get_project_dir(project_id)
        else:
            from .projects.security import get_project_dir
            return get_project_dir(project_id)
    base = Path(settings.BASE_DIR)
    projects_root = base / 'projects'
    if projects_root.exists():
        dirs = [d for d in projects_root.iterdir() if d.is_dir()]
        if dirs:
            return max(dirs, key=lambda d: d.stat().st_mtime)
    return base

def _detect_project_type(project_dir):
    project_dir = Path(project_dir)
    if (project_dir / 'package.json').exists():
        return 'node'
    if (project_dir / 'requirements.txt').exists() or (project_dir / 'pyproject.toml').exists():
        return 'python'
    if (project_dir / 'index.html').exists() or list(project_dir.glob('*.html')):
        return 'static'
    return 'unknown'

def _get_package_json_script(project_dir, script_name):
    try:
        import json
        pkg_path = Path(project_dir) / 'package.json'
        if pkg_path.exists():
            data = json.loads(pkg_path.read_text())
            scripts = data.get('scripts', {})
            return scripts.get(script_name)
    except Exception:
        pass
    return None


_SKIP_PREVIEW_DIRS = {"node_modules", ".git", ".next", "__pycache__", "venv", ".venv"}


def _find_static_entry(project_dir):
    """Projede dogrudan tarayicida acilabilecek bir index.html var mi?"""
    base = Path(project_dir)
    candidates = (
        "index.html", "public/index.html", "src/index.html",
        "dist/index.html", "build/index.html", "out/index.html",
        "app/index.html", "html/index.html", "site/index.html",
    )
    for rel in candidates:
        try:
            if (base / rel).is_file():
                return rel.replace("\\", "/")
        except OSError:
            continue
    try:
        for child in base.iterdir():
            if child.is_dir() and child.name not in _SKIP_PREVIEW_DIRS:
                if (child / "index.html").is_file():
                    return f"{child.name}/index.html"
    except OSError:
        pass
    return ""


def _static_preview_url(project_id, entry):
    folder = entry.rsplit("/", 1)[0] if "/" in entry else ""
    url = f"/preview/site/{project_id}/"
    if folder:
        url += folder.strip("/") + "/"
    return url


_PREVIEW_STARTED = set()
_PREVIEW_START_LOCK = threading.Lock()


def _start_dev_server_for_project(project_dir, project_id):
    """Node projesi icin dev sunucusunu arka planda baslat (best effort)."""
    base = Path(project_dir)
    pkg = base / "package.json"
    if not pkg.is_file() or not (base / "node_modules").is_dir():
        return ""
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return ""
    scripts = data.get("scripts") or {}
    deps = {}
    deps.update(data.get("dependencies") or {})
    deps.update(data.get("devDependencies") or {})

    if "next" in deps:
        template = "npx next dev -p {port}"
    elif "vite" in deps:
        template = "npx vite --port {port} --host 127.0.0.1"
    elif scripts.get("dev"):
        template = "npm run dev"
    elif scripts.get("start"):
        template = "npm start"
    else:
        return ""

    with _PREVIEW_START_LOCK:
        if project_id in _PREVIEW_STARTED:
            return ""
        port = _find_free_port()
        if not port:
            return ""
        _PREVIEW_STARTED.add(project_id)

    command = template.format(port=port)
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["BROWSER"] = "none"
    try:
        log_file = open(base / ".preview.log", "ab")
        subprocess.Popen(
            command, shell=True, cwd=str(base), stdout=log_file, stderr=subprocess.STDOUT,
            env=env, start_new_session=True,
        )
    except Exception:
        logger.warning("preview dev sunucusu baslatilamadi: %s", command)
        with _PREVIEW_START_LOCK:
            _PREVIEW_STARTED.discard(project_id)
        return ""
    _set_project_dev_port(base, port)
    return f"http://localhost:{port}"


@csrf_exempt
def preview_start(request):
    """Aktif proje icin onizleme URL'i hazirla.

    Statik siteler /preview/site/<id>/ uzerinden servis edilir (sunucu gerekmez).
    Node projelerinde calisan dev sunucusu varsa o kullanilir; yoksa arka planda
    baslatmayi dener.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    project_id = request.session.get("current_project_id") or request.session.get("current_project_guid")
    if request.content_type and "application/json" in request.content_type:
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            project_id = body.get("project_id") or project_id
        except Exception:
            pass

    if not project_id:
        return JsonResponse({"ok": False, "error": "No active project"}, status=400)

    project_dir = _get_project_dir(project_id)
    if not project_dir or not Path(project_dir).is_dir():
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)

    entry = _find_static_entry(project_dir)
    if entry:
        return JsonResponse({
            "ok": True,
            "url": _static_preview_url(project_id, entry),
            "source": "static_preview",
            "project_type": "static",
            "entry": entry,
        })

    dev_port = _get_project_dev_port(project_dir)
    if dev_port and _check_port(dev_port):
        return JsonResponse({
            "ok": True,
            "url": f"http://localhost:{dev_port}",
            "source": "dev_server_project",
            "project_type": _detect_project_type(project_dir),
            "port": dev_port,
        })

    started_url = _start_dev_server_for_project(project_dir, project_id)
    if started_url:
        return JsonResponse({
            "ok": True,
            "url": started_url,
            "source": "dev_server_started",
            "project_type": _detect_project_type(project_dir),
            "pending": True,
            "message": "Dev sunucusu başlatılıyor; birkaç saniye içinde açılacak.",
        })

    project_type = _detect_project_type(project_dir)
    if project_type == "unknown":
        error = "Önizlenecek site bulunamadı: proje dizininde index.html yok ve çalışan bir dev sunucusu yok."
    else:
        error = "Önizleme sunucusu başlatılamadı. Önce bağımlılıkları kurun (npm install) ve dev sunucusunu çalıştırın."
    return JsonResponse({"ok": False, "error": error, "project_type": project_type, "entry": entry})


@csrf_exempt
@csrf_exempt
def preview_auto(request):
    """Auto-detect and return preview URL for the CURRENT PROJECT's dev server.
    
    Returns the project's assigned port if running, otherwise tells user to start it.
    """
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET required"}, status=405)
    
    # Sync port registry with currently active servers
    _sync_port_registry()
    
    project_id = request.GET.get('project_id') or request.session.get('current_project_id')
    if not project_id:
        return JsonResponse({"ok": False, "error": "No project selected"}, status=400)
    
    project_dir = _get_project_dir(project_id)
    if not project_dir:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    project_type = _detect_project_type(project_dir)

    # Statik site: dev sunucusu gerekmez, dosyalari dogrudan servis et.
    entry = _find_static_entry(project_dir)
    if entry:
        return JsonResponse({
            "ok": True,
            "url": _static_preview_url(project_id, entry),
            "source": "static_project",
            "project_type": "static",
            "entry": entry,
            "auto_assigned": False,
        })

    # Read project's assigned dev_port from project.json
    meta_file = Path(project_dir) / 'project.json'
    dev_port = None
    if meta_file.exists():
        try:
            meta = json.load(open(meta_file))
            dev_port = meta.get('dev_port')
        except Exception:
            pass
    
    # Current Django port (EMoG platform) - for exclusion
    current_port = request.META.get('SERVER_PORT')
    try:
        current_port = int(current_port) if current_port else None
    except (ValueError, TypeError):
        current_port = None
    
    # If project has assigned dev_port, check ONLY that port
    if dev_port:
        if _check_port(dev_port):
            url = f'http://localhost:{dev_port}'
            return JsonResponse({
                "ok": True,
                "url": url,
                "source": "dev_server_project",
                "port": dev_port,
                "project_type": _detect_project_type(Path(_get_project_dir(project_id))),
                "all_detected_ports": [dev_port],
                "auto_assigned": False
            })
        else:
            return JsonResponse({
                "ok": False,
                "error": f"Project dev server not running on assigned port {dev_port}. Run 'npm run dev' or your dev command.",
                "dev_port": dev_port,
                "project_type": _detect_project_type(Path(_get_project_dir(project_id))),
                "action_required": "start_dev_server"
            }, status=503)
    
    return JsonResponse({
        "ok": False,
        "url": None,
        "source": "no_preview",
        "project_type": project_type,
        "error": ("Önizlenecek site bulunamadı: projede index.html yok ve çalışan bir dev sunucusu yok."
                  if project_type == "unknown" else
                  "Proje için dev sunucusu çalışmıyor. Önizlemeyi başlatmak için önce bağımlılıkları kurun."),
        "action_required": "preview_start",
    })


@csrf_exempt
def preview_site(request, project_id, asset_path=""):
    """Proje dizinini statik servis et: /preview/site/<id>/...

    Boylece index.html + goreli CSS/JS yollari iframe icinde dogru calisir.
    """
    import mimetypes

    project_dir = _get_project_dir(project_id)
    if not project_dir:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)

    base = Path(project_dir).resolve()
    rel = (asset_path or "").strip().lstrip("/")
    target = (base / rel) if rel else base
    try:
        if target.is_dir():
            target = target / "index.html"
        resolved = target.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return JsonResponse({"ok": False, "error": "Path traversal blocked"}, status=403)

    if not resolved.is_file():
        return JsonResponse({"ok": False, "error": "File not found", "path": rel}, status=404)

    # Gizli dosyalar ve platform metadatasi servis edilmesin.
    if resolved.name.startswith(".") or resolved.name.lower() in {"project.json", "db.sqlite3"}:
        return JsonResponse({"ok": False, "error": "File not allowed"}, status=403)

    # Sadece web varliklari servis edilsin (.env, project.json vb. sizmasin).
    allowed = {
        ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json", ".map",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
        ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md",
    }
    if resolved.suffix.lower() not in allowed:
        return JsonResponse({"ok": False, "error": "File type not allowed"}, status=403)

    content_type, _ = mimetypes.guess_type(str(resolved))
    response = FileResponse(open(resolved, "rb"), content_type=content_type or "application/octet-stream")
    response["X-Frame-Options"] = "ALLOWALL"
    response["Cache-Control"] = "no-cache"
    return response


@csrf_exempt
def preview_all_active(request):
    """List ALL active dev servers across all ports - for dashboard/debugging."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET required"}, status=405)
    
    active = _get_all_active_ports()
    return JsonResponse({
        "ok": True,
        "active_servers": active,
        "total": len(active)
    })


@csrf_exempt
def preview_assign_port(request):
    """Manually assign a port to a project."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    project_id = data.get('project_id') or request.session.get('current_project_id')
    port = data.get('port')
    
    if not project_id:
        return JsonResponse({"ok": False, "error": "project_id required"}, status=400)
    if not port or not isinstance(port, int) or port < 3000 or port > 9999:
        return JsonResponse({"ok": False, "error": "port must be an integer between 3000 and 9999"}, status=400)
    
    project_dir = _get_project_dir(project_id)
    if not project_dir:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    if _check_port(port):
        owner = _get_port_owner(port)
        if owner and owner.get('project_id') != project_id:
            return JsonResponse({"ok": False, "error": f"Port {port} is already in use by another project"}, status=409)
    
    project_dir_path = Path(_get_project_dir(project_id))
    meta_file = Path(project_dir) / 'project.json'
    if not meta_file.exists():
        return JsonResponse({"ok": False, "error": "Project metadata not found"}, status=404)
    
    try:
        meta = json.load(open(meta_file))
        meta['dev_port'] = port
        meta['updated_at'] = time.time()
        json.dump(meta, open(meta_file, 'w'), indent=2)
        
        # Register the port
        meta_data = json.load(open(Path(project_dir) / 'project.json'))
        _register_port(port, project_id, meta.get('name', 'unknown'))
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Failed to update project: {e}"}, status=500)
    
    return JsonResponse({"ok": True, "dev_port": port, "message": f"Dev port set to {port}"})


@csrf_exempt
def preview_free_ports(request):
    """List free ports available for new projects."""
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET required"}, status=405)
    
    free_ports = []
    for port in range(3000, 10000):
        if not _check_port(port):
            free_ports.append(port)
            if len(free_ports) >= 20:
                break
    
    return JsonResponse({
        "ok": True,
        "free_ports": free_ports[:20],
        "suggested": free_ports[0] if free_ports else None
    })


def _detect_project_type(project_dir):
    project_dir = Path(project_dir)
    if (project_dir / 'package.json').exists():
        return 'node'
    if (project_dir / 'requirements.txt').exists() or (project_dir / 'pyproject.toml').exists():
        return 'python'
    if (project_dir / 'index.html').exists() or list(project_dir.glob('*.html')):
        return 'static'
    return 'unknown'


def _get_package_json_script(project_dir, script_name):
    try:
        import json
        pkg_path = Path(project_dir) / 'package.json'
        if pkg_path.exists():
            data = json.loads(pkg_path.read_text())
            scripts = data.get('scripts', {})
            return scripts.get(script_name)
    except Exception:
        pass
    return None


@csrf_exempt
@csrf_exempt

@csrf_exempt
def preview_set_dev_port(request):
    """Set the dev_port for a project."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    project_id = data.get('project_id') or request.session.get('current_project_id')
    dev_port = data.get('dev_port')
    
    if not project_id:
        return JsonResponse({"ok": False, "error": "project_id required"}, status=400)
    if not dev_port or not isinstance(dev_port, int) or dev_port < 1024 or dev_port > 65535:
        return JsonResponse({"ok": False, "error": "dev_port must be an integer between 1024 and 65535"}, status=400)
    
    project_dir = _get_project_dir(project_id)
    if not project_dir:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    meta_file = Path(project_dir) / 'project.json'
    if not meta_file.exists():
        return JsonResponse({"ok": False, "error": "Project metadata not found"}, status=404)
    
    try:
        meta = json.load(open(meta_file))
        meta['dev_port'] = dev_port
        meta['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        json.dump(meta, open(meta_file, 'w'), indent=2)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Failed to update project: {e}"}, status=500)
    
    return JsonResponse({"ok": True, "dev_port": dev_port, "message": f"Dev port set to {dev_port}"})


@csrf_exempt
def preview_static(request):
    """Serve static files from project directory for preview."""
    project_id = request.GET.get('project_id') or request.session.get('current_project_id')
    path = request.GET.get('path', '.')
    file_path = request.GET.get('file', 'index.html')
    
    project_dir = _get_project_dir(project_id)
    target = Path(project_dir) / path / file_path
    
    # Security: ensure target is within project_dir
    try:
        target.resolve().relative_to(Path(project_dir).resolve())
    except ValueError:
        return JsonResponse({"ok": False, "error": "Path traversal blocked"}, status=403)
    
    if not target.exists() or not target.is_file():
        return JsonResponse({"ok": False, "error": "File not found"}, status=404)
    
    # Determine content type
    import mimetypes
    content_type, _ = mimetypes.guess_type(str(target))
    if not content_type:
        content_type = 'application/octet-stream'
    
    response = FileResponse(open(target, 'rb'), content_type=content_type)
    response['X-Frame-Options'] = 'ALLOWALL'
    response['Cache-Control'] = 'no-cache'
    return response


def _preview_not_implemented(feature):
    return JsonResponse(
        {
            "ok": False,
            "error": f"'{feature}' özelliği henüz uygulanmadı. Gerekli model/servis erişimi yok.",
        },
        status=501,
    )
def preview_analyze_design(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    return _preview_not_implemented("analyze-design")


@csrf_exempt
def preview_visual_qa(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    return _preview_not_implemented("visual-qa")


# ===== Sunucu acilisinda calistirma kuyrugunu baslat =====
# Worker daha once yalnizca biri POST atinca basliyordu; sunucu yeniden
# basladiginda 'queued' gorevler sonsuza dek bekliyordu. Yalnizca gercek
# runserver surecinde (script/management komutunda degil) baslatilir.
if any(arg == "runserver" for arg in sys.argv):
    def _boot_start_queue_worker():
        import threading as _threading
        def _later():
            time.sleep(3)  # sunucu portu tasinca ve sistem kontrolleri bitsin
            try:
                _start_queue_worker_once()
            except Exception as _exc:
                logger.warning("Queue worker acilista baslatilamadi: %s", _exc)
        _threading.Thread(target=_later, daemon=True).start()
    _boot_start_queue_worker()

@require_http_methods(["GET"])
@csrf_exempt
def chat_attachment_download(request, att_id):
    from agent_app.chat_media import chat_attachment_download as _download
    return _download(request, att_id)

