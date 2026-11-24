"""Kosu (run) gunlugu ve yan dosya kaliciligi.

`views.task_stream` SSE ucu `task_runs/<run_id>.log` dosyasini kuyruklari; bu
dosyaya yazan her sey arayuzde gorunur. Pipeline'in butun asamalarinda
olaylar buradan yayilir.

Olay turleri (arayuz bunlari tanir):
    info / status / stage / plan / plan_item / research / blueprint /
    tool_call / tool_result / exception / summary

Ayrica plan, arastirma ve benchmark ciktilarini atomik olarak
`task_runs/<run_id>.<ad>.json` yan dosyalarina yazariz. Boylece worker cokse
bile sonraki turda kaldigimiz yerden devam edebiliriz.
"""
import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime

from django.conf import settings

logger = logging.getLogger(__name__)

RUN_DIR_NAME = "task_runs"


def runs_dir() -> str:
    path = os.path.join(settings.BASE_DIR, RUN_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def atomic_write_json(path: str, payload) -> bool:
    """Yazma yarida kesilirse dosya bozulmasin diye once gecici dosyaya yaz."""
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning("atomic_write_json basarisiz (%s): %s", path, exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


class RunLogger:
    """Tek bir kosunun olay gunlugu + yan dosya deposu.

    Yazma islemleri kilitli: pipeline worker tek thread, ama arama/okuma
    asamalarini paralel thread'lerde calistiriyoruz.
    """

    def __init__(self, run_id: str = None, console: bool = False):
        self.run_id = run_id or uuid.uuid4().hex
        self.console = console
        self._lock = threading.Lock()
        self._counters = {}

    # --- dosya yollari -----------------------------------------------------
    @property
    def log_path(self) -> str:
        return os.path.join(runs_dir(), f"{self.run_id}.log")

    def sidecar_path(self, name: str) -> str:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
        return os.path.join(runs_dir(), f"{self.run_id}.{safe}.json")

    # --- olay gunlugu ------------------------------------------------------
    def log(self, kind: str = "info", text=None, /, **extra) -> dict:
        """Olay satiri yaz.

        `kind` ve `text` konumsal-ozeldir (positional-only). Boylece cagiran
        taraf `text=` / `kind=` anahtar kelimelerini `**extra` icinde gecirse
        bile "got multiple values for argument" hatasi olusmaz. Anahtar kelime
        olarak verilen `text` degeri konumsal degere ustundur.
        """
        if text is None:
            text = extra.pop("text", "")
        elif "text" in extra:
            # Acikca verilen `text=` anahtar kelimesi konumsal basligi ezer.
            text = extra.pop("text")
        if "kind" in extra:
            kind = extra.pop("kind") or kind
        # Rezerve alanlar olay zarfini bozmasin.
        extra.pop("ts", None)
        extra.pop("type", None)
        entry = {"ts": utcnow(), "type": str(kind) if kind else "info",
                 "text": "" if text is None else str(text)}
        if extra:
            entry.update(extra)
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except Exception as exc:
                logger.warning("RunLogger yazamadi (%s): %s", self.log_path, exc)
            if self.console:
                print(f"[{kind}] {text}", flush=True)
        return entry

    def info(self, text=None, /, **extra):
        return self.log("info", text, **extra)

    def status(self, text=None, /, **extra):
        return self.log("status", text, **extra)

    def stage(self, name: str, title: str, **extra):
        """Asama gecisi: hem 'stage' hem 'status' yazar, arayuz ikisini de gosterir."""
        # Cagiran taraf zaten `text=` / `stage=` / `progress=` gecmisse cakismasin.
        message = extra.pop("text", None) or title
        extra.pop("stage", None)
        progress = dict(extra.get("progress") or {})
        progress["currentStage"] = name
        extra["progress"] = progress
        self.log("stage", message, stage=name, **extra)
        return self.log("status", message, stage=name, **extra)

    def warn(self, text=None, /, **extra):
        if text is None:
            text = extra.pop("text", "")
        else:
            extra.pop("text", None)
        return self.log("status", f"! {text}" if text else "!", **extra)

    # -- plan olaylari ---------------------------------------------------
    def emit_plan(self, plan) -> dict:
        """Plan olustugunda/arayuzune tek seferde tum listeyi gonder."""
        phases = []
        for phase, items in plan.by_phase().items():
            phases.append({
                "phase": phase,
                "label": getattr(items[0], "phase_label", phase) if items else phase,
                "items": [
                    {
                        "key": item.key,
                        "title": item.title,
                        "objective": item.objective,
                        "status": item.status,
                        "complexity": item.complexity,
                        "user_requested": item.user_requested,
                        "files": item.files,
                        "acceptance": item.acceptance,
                    }
                    for item in items
                ],
            })
        return self.log(
            "plan",
            f"Plan yayınlandı: {len(plan)} kalem",
            progress=plan.progress(),
            phases=phases,
        )

    def emit_plan_item(self, item, *, round_index: int = 0) -> dict:
        return self.log(
            "plan_item",
            item.title,
            key=item.key,
            phase=item.phase,
            phase_label=item.phase_label,
            status=item.status,
            objective=item.objective,
            detail=item.detail,
            files=item.files,
            acceptance=item.acceptance,
            attempts=item.attempts,
            user_requested=item.user_requested,
            round=round_index,
        )

    def emit_progress(self, done: int, total: int, label: str) -> None:
        """Yuzde ilerlemeyi tek satirlik status olarak yaz (SSE akisi ucuz kalmali)."""
        if total <= 0:
            self.status(f"{label} (0/0)")
            return
        pct = max(0, min(100, int(done * 100 / total)))
        self.status(f"{label}: {done}/{total} (%{pct})",
                    progress=pct, done=done, total=total)

    # --- sayaclar ----------------------------------------------------------
    def count(self, bucket: str, delta: int = 1) -> int:
        with self._lock:
            self._counters[bucket] = self._counters.get(bucket, 0) + delta
            return self._counters[bucket]

    def counters(self) -> dict:
        with self._lock:
            return dict(self._counters)

    # --- yan dosyalar ------------------------------------------------------
    def save(self, name: str, payload) -> bool:
        return atomic_write_json(self.sidecar_path(name), payload)

    def load(self, name: str, default=None):
        return read_json(self.sidecar_path(name), default)

    def has(self, name: str) -> bool:
        return os.path.exists(self.sidecar_path(name))