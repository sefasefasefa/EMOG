"""Persistent project registry backed by ``projects/<id>/project.json``."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def projects_root() -> Path:
    from django.conf import settings

    base = getattr(settings, "BASE_DIR", Path.cwd())
    return Path(base) / "projects"


def project_dir(project_id: str) -> Path:
    safe = "".join(ch for ch in str(project_id or "") if ch.isalnum() or ch in "-_")
    return projects_root() / safe


def _meta_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def _owner_candidates(user_id=None, user_guid=None) -> set:
    candidates = set()
    if user_id:
        candidates.add(str(user_id))
        try:
            candidates.add(hashlib.sha256(f"user_{user_id}".encode()).hexdigest()[:16])
        except Exception:
            pass
    if user_guid:
        candidates.add(str(user_guid))
    return candidates


def owner_matches(owner, user_id=None, user_guid=None, request=None) -> bool:
    if owner is None or str(owner) == "":
        return True
    candidates = _owner_candidates(user_id, user_guid)
    if request is not None:
        try:
            from agent_app.projects.security import _is_owner

            if _is_owner(owner, request):
                return True
        except Exception:
            pass
    return str(owner) in candidates


def _normalize(meta: Dict[str, Any], project_id: str = None) -> Dict[str, Any]:
    data = dict(meta or {})
    pid = str(data.get("id") or project_id or "")
    data["id"] = pid
    pdir = project_dir(pid)
    data.setdefault("path", str(pdir))
    data.setdefault("name", "Yeni Proje")
    data.setdefault("description", "")
    data.setdefault("status", "ready")
    data.setdefault("language", "Unknown")
    data.setdefault("archived", False)
    data.setdefault("last_run_id", "")
    data.setdefault("last_run_status", "")
    data.setdefault("last_error", "")
    data.setdefault("integrations", {})
    if not data.get("created_at"):
        data["created_at"] = utcnow()
    if not data.get("updated_at"):
        data["updated_at"] = data["created_at"]
    return data


def read_project(project_id: str) -> Optional[Dict[str, Any]]:
    path = _meta_path(project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _normalize(data, project_id)


def _sync_registry(meta: Dict[str, Any]) -> None:
    try:
        from agent_app.projects.views import PROJECTS, PROJECTS_LOCK

        with PROJECTS_LOCK:
            PROJECTS[str(meta["id"])] = dict(meta)
    except Exception:
        pass


def write_project(meta: Dict[str, Any]) -> Dict[str, Any]:
    data = _normalize(meta)
    pdir = project_dir(data["id"])
    pdir.mkdir(parents=True, exist_ok=True)
    data["path"] = str(pdir)
    data["updated_at"] = utcnow()
    path = pdir / "project.json"
    tmp = pdir / f".project.json.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    compat = {
        "id": data["id"],
        "guid": data["id"],
        "name": data.get("name") or "Yeni Proje",
        "owner_guid": data.get("owner_guid") or data.get("user_id"),
        "created": data.get("created_at") or utcnow(),
        "version": 1,
    }
    try:
        (pdir / ".project_meta.json").write_text(
            json.dumps(compat, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    try:
        from agent_app.project_scaffold import ensure_project_security

        ensure_project_security(pdir)
    except Exception:
        pass
    _sync_registry(data)
    return data


def update_project(project_id: str, **changes) -> Optional[Dict[str, Any]]:
    meta = read_project(project_id)
    if meta is None:
        meta = {"id": project_id}
    changes.pop("id", None)
    meta.update(changes)
    return write_project(meta)


def project_title(task_text: str) -> str:
    text = " ".join(str(task_text or "").split())
    if not text:
        return "Yeni Proje"
    marker = re.search(r"(?:Kullan[ıi]c[ıi]\s+iste[ğg]i|Kullanici istegi|User request)\s*:\s*", text, re.I)
    if marker:
        text = text[marker.end():]
    text = text.strip(" .")
    if len(text) > 72:
        text = text[:72].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."
    return text or "Yeni Proje"


def create_project(
    user_id,
    title: str = "",
    description: str = "",
    task_text: str = "",
    status: str = "ready",
) -> Dict[str, Any]:
    new_id = uuid.uuid4().hex[:12]
    now = utcnow()
    name = (title or project_title(task_text)).strip() or "Yeni Proje"
    meta = {
        "id": new_id,
        "name": name,
        "description": description or name,
        "source": "generated",
        "owner_guid": str(user_id) if user_id else None,
        "user_id": str(user_id) if user_id else None,
        "status": status,
        "language": "Unknown",
        "created_at": now,
        "updated_at": now,
        "last_run_id": "",
        "last_run_status": "",
        "last_error": "",
    }
    return write_project(meta)


def list_projects(user_id=None, user_guid=None) -> List[Dict[str, Any]]:
    root = projects_root()
    if not root.exists():
        return []
    projects: List[Dict[str, Any]] = []
    for child in root.iterdir():
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        meta = read_project(child.name)
        if not meta or meta.get("id") != child.name:
            continue
        if meta.get("archived"):
            continue
        owner = meta.get("owner_guid") or meta.get("user_id")
        if not owner_matches(owner, user_id=user_id, user_guid=user_guid):
            continue
        meta["path"] = str(child)
        projects.append(meta)
    projects.sort(key=lambda p: str(p.get("updated_at") or ""), reverse=True)
    return projects


def find_latest_project(user_id=None, user_guid=None) -> Optional[Dict[str, Any]]:
    projects = list_projects(user_id=user_id, user_guid=user_guid)
    return projects[0] if projects else None


def active_project(user_id, user_guid=None, session=None, request=None) -> Optional[Dict[str, Any]]:
    requested = None
    if request is not None:
        requested = request.GET.get("project") or request.GET.get("project_id")
    if requested:
        meta = read_project(requested)
        if meta and owner_matches(
            meta.get("owner_guid") or meta.get("user_id"),
            user_id=user_id,
            user_guid=user_guid,
            request=request,
        ):
            return meta
        return None
    if session is not None:
        sid = session.get("current_project_id") or session.get("current_project_guid")
        if sid:
            meta = read_project(sid)
            if meta and owner_matches(
                meta.get("owner_guid") or meta.get("user_id"),
                user_id=user_id,
                user_guid=user_guid,
                request=request,
            ):
                return meta
    return find_latest_project(user_id=user_id, user_guid=user_guid)
