"""Async 3D generation jobs and provider hooks.

- Procedural provider: uçtan uca çalışır (EntityClass instantiate / basit üretim).
- Harici text/image-to-3D provider'lar: API anahtarı/konfigürasyon varsa gerçek HTTP entegrasyonu,
  yoksa hook modunda açıklayıcı durum döner.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _cfg() -> dict:
    cfg = {}
    try:
        raw = getattr(settings, "SCENE_GENERATION", None)
        if isinstance(raw, dict):
            cfg = dict(raw)
    except Exception:
        cfg = {}
    return cfg


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _provider_config():
    cfg = _cfg()
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    def g(key, envs, url_key=None, model_key=None):
        p = providers.get(key) if isinstance(providers.get(key), dict) else {}
        api_key = str(p.get("api_key") or p.get("key") or "").strip()
        if not api_key:
            for e in envs:
                api_key = _env(e)
                if api_key:
                    break
        base_url = str(p.get("base_url") or p.get("url") or (cfg.get(url_key) if url_key else "") or "").strip()
        model = str(p.get("model") or (cfg.get(model_key) if model_key else "") or "").strip()
        enabled = bool(p.get("enabled", True))
        return {"api_key": api_key, "base_url": base_url, "model": model, "enabled": enabled}

    meshy = g("meshy", ["MESHY_API_KEY", "MESHY_KEY"])
    if not meshy["base_url"]:
        meshy["base_url"] = _env("MESHY_BASE_URL", "https://api.meshy.ai")
    tripo = g("tripo", ["TRIPO_API_KEY", "TRIPO3D_API_KEY", "TRIPO_KEY"])
    if not tripo["base_url"]:
        tripo["base_url"] = _env("TRIPO_BASE_URL", "https://api.tripo3d.ai/v2")
    generic = g("generic", ["SCENE_3D_API_KEY", "TEXT_TO_3D_API_KEY", "IMAGE_TO_3D_API_KEY"])
    if not generic["base_url"]:
        generic["base_url"] = _env("SCENE_3D_BASE_URL", "")
    return {"meshy": meshy, "tripo": tripo, "generic": generic}


def _http_json(method: str, url: str, headers: Optional[dict] = None, payload: Any = None, timeout: int = 60) -> Tuple[int, Any, str]:
    data = None
    req_headers = {"Accept": "application/json", "User-Agent": "EMOG-SceneGeneration/1.0"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        data = body
    req = urlrequest.Request(url=url, data=data, headers=req_headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read() or b""
            text = raw.decode("utf-8", errors="replace")
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "json" in ctype:
                try:
                    return resp.status, json.loads(text or "null"), text
                except Exception:
                    return resp.status, None, text
            try:
                return resp.status, json.loads(text), text
            except Exception:
                return resp.status, None, text
    except HTTPError as e:
        try:
            raw = e.read() or b""
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = str(e)
        try:
            return e.code, json.loads(text), text
        except Exception:
            return e.code, None, text
    except URLError as e:
        return 0, None, str(e)
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def _download_to_media(url: str, user, scene, base_name="external_model") -> Optional[str]:
    """Download remote model/image to SceneAsset and return asset id string."""
    if not url:
        return None
    try:
        req = urlrequest.Request(url, headers={"User-Agent": "EMOG-SceneGeneration/1.0"})
        with urlrequest.urlopen(req, timeout=120) as resp:
            content = resp.read() or b""
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except Exception as exc:
        logger.warning("External 3D download failed: %s", exc)
        return None
    if not content:
        return None
    ext = ".glb"
    if "gltf" in ctype or url.lower().endswith(".gltf"):
        ext = ".gltf"
    elif "zip" in ctype or url.lower().endswith(".zip"):
        ext = ".zip"
    elif "png" in ctype:
        ext = ".png"
    elif "jpg" in ctype or "jpeg" in ctype:
        ext = ".jpg"
    try:
        from django.core.files.base import ContentFile
        from .models import SceneAsset
        safe = "".join(c for c in (base_name or "model") if c.isalnum() or c in ("-","_"))[:40] or "model"
        filename = f"{safe}-{uuid.uuid4().hex[:8]}{ext}"
        ftype = "gltf" if ext in (".glb", ".gltf") else ("image" if ext in (".png", ".jpg", ".jpeg", ".webp") else "other")
        asset = SceneAsset(user=user, scene=scene, name=base_name or "External 3D", file_type=ftype, metadata={"source_url": url, "external": True})
        asset.file.save(filename, ContentFile(content), save=True)
        return str(asset.id)
    except Exception as exc:
        logger.warning("External 3D asset save failed: %s", exc)
        return None


def get_provider_info():
    """Studio/API için provider bilgisi (async job + gerçek/hook durum)."""
    pcfg = _provider_config()
    providers = [
        {
            "id": "procedural",
            "name": "Procedural (yerleşik)",
            "available": True,
            "modes": ["procedural", "text_to_3d"],
            "async_jobs": True,
            "note": "Sınıf tabanlı/öğrenilmiş üretim ve yerleştirme.",
        },
        {
            "id": "meshy",
            "name": "Meshy (API)",
            "available": bool(pcfg["meshy"]["api_key"] and pcfg["meshy"]["enabled"]),
            "modes": ["text_to_3d", "image_to_3d"],
            "async_jobs": True,
            "note": "MESHY_API_KEY ile etkinleşir." if not pcfg["meshy"]["api_key"] else "Yapılandırıldı.",
        },
        {
            "id": "tripo",
            "name": "Tripo3D (API)",
            "available": bool(pcfg["tripo"]["api_key"] and pcfg["tripo"]["enabled"]),
            "modes": ["text_to_3d", "image_to_3d"],
            "async_jobs": True,
            "note": "TRIPO_API_KEY ile etkinleşir." if not pcfg["tripo"]["api_key"] else "Yapılandırıldı.",
        },
        {
            "id": "external_text_to_3d",
            "name": "Generic External Text-to-3D",
            "available": bool(pcfg["generic"]["api_key"] and pcfg["generic"]["base_url"] and pcfg["generic"]["enabled"]),
            "modes": ["text_to_3d"],
            "async_jobs": True,
            "note": "SCENE_3D_API_KEY + SCENE_3D_BASE_URL ile etkinleşir." if not (pcfg["generic"]["api_key"] and pcfg["generic"]["base_url"]) else "Yapılandırıldı.",
        },
        {
            "id": "external_image_to_3d",
            "name": "Generic External Image-to-3D",
            "available": bool(pcfg["generic"]["api_key"] and pcfg["generic"]["base_url"] and pcfg["generic"]["enabled"]),
            "modes": ["image_to_3d"],
            "async_jobs": True,
            "note": "SCENE_3D_API_KEY + SCENE_3D_BASE_URL ile etkinleşir." if not (pcfg["generic"]["api_key"] and pcfg["generic"]["base_url"]) else "Yapılandırıldı.",
        },
    ]
    return {
        "ok": True,
        "providers": providers,
        "consistency": {"locked_fields_respected": True, "stable_id_preserved": True},
    }


def _serialize_job(job, request=None):
    asset_url = ""
    try:
        if job.output_asset and job.output_asset.file:
            asset_url = job.output_asset.file.url
            if request is not None:
                asset_url = request.build_absolute_uri(asset_url)
    except Exception:
        asset_url = ""
    return {
        "id": str(job.id),
        "status": job.status,
        "progress": int(job.progress or 0),
        "provider": job.provider,
        "mode": job.mode,
        "prompt": job.prompt or "",
        "input_path": job.input_path or "",
        "scene_id": str(job.scene_id) if job.scene_id else None,
        "entity_class_id": str(job.entity_class_id) if job.entity_class_id else None,
        "output_asset_id": str(job.output_asset_id) if job.output_asset_id else None,
        "output_asset_url": asset_url,
        "output_node_id": str(job.output_node_id) if job.output_node_id else None,
        "parameters": job.parameters or {},
        "result": job.result or {},
        "error": job.error or "",
        "consistency": job.consistency or {},
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def create_generation_job(
    user,
    scene=None,
    entity_class=None,
    provider="procedural",
    mode="procedural",
    prompt="",
    input_path="",
    parameters=None,
    consistency=None,
    name=None,
):
    from .models import SceneGenerationJob
    parameters = parameters or {}
    consistency = copy.deepcopy(consistency or {})
    if entity_class and not consistency:
        tpl = (entity_class.components_template or {}) if getattr(entity_class, "components_template", None) else {}
        ident = (tpl.get("identity") or {}) if isinstance(tpl, dict) else {}
        if isinstance(ident, dict):
            if ident.get("locked_fields"):
                consistency["locked_fields"] = list(ident.get("locked_fields") or [])
            if ident.get("stable_id"):
                consistency["stable_id"] = ident.get("stable_id")
    job = SceneGenerationJob.objects.create(
        user=user,
        scene=scene,
        entity_class=entity_class,
        provider=(provider or "procedural")[:32],
        mode=(mode or "procedural")[:32],
        prompt=(prompt or "")[:10000],
        input_path=(input_path or "")[:500],
        status="pending",
        progress=0,
        parameters=parameters,
        consistency=consistency,
        result={"name": (name or "")[:200]} if name else {},
    )
    return job


def _resolve_or_create_entity_class(user, prompt: str, category: str = "other"):
    from .models import EntityClass
    from . import learned_models
    concept = (prompt or "").strip() or "Generated Object"
    name = concept[:200]
    ec = EntityClass.objects.filter(name__iexact=name).filter(user=user).first() or EntityClass.objects.filter(name__iexact=name, user__isnull=True).first()
    if ec is None:
        ec = EntityClass.objects.create(
            user=user,
            name=name,
            category=category or "other",
            description=f"Auto-created for 3D generation: {concept[:300]}",
            components_template={},
            skeleton_template={},
            render_template={},
            parameters={},
            is_system=False,
        )
    try:
        learned_models.research_and_apply_entity_class(ec, user=user, context={"source": "scene_generation_job"})
        ec.refresh_from_db()
    except Exception as exc:
        logger.warning("3D job research failed (%s): %s", ec.id, exc)
    return ec


def _run_procedural(job):
    from . import entity_system
    from . import learned_models
    params = copy.deepcopy(job.parameters or {})
    name = (job.result or {}).get("name") or (job.prompt or "Generated 3D")[:200]
    ec = job.entity_class

    if ec is None and (job.mode in ("procedural", "text_to_3d")) and (job.prompt or "").strip():
        ec = _resolve_or_create_entity_class(job.user, job.prompt, params.get("category") or "other")
        job.entity_class = ec
        job.save(update_fields=["entity_class", "updated_at"])

    if ec is not None:
        try:
            comp_empty = not isinstance(ec.components_template, dict) or not ec.components_template
            skel = ec.skeleton_template if isinstance(ec.skeleton_template, dict) else {}
            hier = (skel.get("hierarchy") or {}) if isinstance(skel, dict) else {}
            layers = hier.get("layers") if isinstance(hier, dict) else []
            if comp_empty and not layers:
                learned_models.research_and_apply_entity_class(ec, user=job.user, context={"source": "scene_generation_job_pre"})
                ec.refresh_from_db()
        except Exception:
            pass
        instance, asset, node = entity_system.instantiate_entity_class(
            ec,
            user=job.user,
            scene=job.scene,
            name=name or ec.name,
            parameters=params,
            place=bool(job.scene_id),
        )
        try:
            comps = copy.deepcopy(instance.components or {})
            ident = comps.get("identity") or {}
            locked = set((job.consistency or {}).get("locked_fields") or ident.get("locked_fields") or [])
            if (job.consistency or {}).get("stable_id"):
                ident["stable_id"] = job.consistency["stable_id"]
            if locked:
                ident["locked_fields"] = list(locked)
            comps["identity"] = ident
            instance.components = comps
            instance.save()
            if node:
                ncomps = copy.deepcopy(node.components or {})
                nident = ncomps.get("identity") or {}
                if ident.get("stable_id"):
                    nident["stable_id"] = ident["stable_id"]
                if locked:
                    nident["locked_fields"] = list(locked)
                ncomps["identity"] = nident
                node.components = ncomps
                node.save()
        except Exception:
            pass
        job.output_asset = asset
        job.output_node = node
        job.result = {
            **(job.result or {}),
            "instance_id": str(instance.id),
            "asset_id": str(asset.id) if asset else None,
            "node_id": str(node.id) if node else None,
            "class_id": str(ec.id),
            "provider": "procedural",
        }
        job.status = "completed"
        job.progress = 100
        return job

    from .models import EntityClass
    from . import entity_system
    tmp_class = EntityClass(
        name=name or "Generated",
        category="other",
        components_template={},
        skeleton_template={},
        render_template={"type": "box", "size": [1, 1, 1], "center": [0, 0.5, 0]},
        parameters={},
        is_system=False,
    )
    gltf = entity_system.generate_gltf_for_entity(tmp_class, instance_name=name or "Generated", params=params)
    asset = entity_system.write_gltf_asset(gltf, user=job.user, scene=job.scene, base_name=name or "generated")
    node = None
    if job.scene_id:
        from .models import SceneNode
        comps = {
            "identity": {"stable_id": (job.consistency or {}).get("stable_id") or str(uuid.uuid4()), "locked_fields": (job.consistency or {}).get("locked_fields") or [], "label": name},
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "mesh": {"asset_id": str(asset.id), "generated": True},
            "animation": entity_system.normalize_animation({}),
            "physics": entity_system.normalize_physics({}),
        }
        node = SceneNode.objects.create(scene=job.scene, asset=asset, name=name or "Generated", node_type="mesh", components=comps, visible=True)
    job.output_asset = asset
    job.output_node = node
    job.result = {**(job.result or {}), "asset_id": str(asset.id), "node_id": str(node.id) if node else None, "provider": "procedural", "fallback": True}
    job.status = "completed"
    job.progress = 100
    return job


def _run_meshy(job, conf) -> None:
    """Meshy Text-to-3D basic integration. Polls until completion or timeout."""
    from . import entity_system
    base = (conf.get("base_url") or "https://api.meshy.ai").rstrip("/")
    key = conf.get("api_key")
    headers = {"Authorization": f"Bearer {key}"}
    prompt = job.prompt or "a 3d object"
    if job.mode == "image_to_3d" and job.input_path:
        if not str(job.input_path).startswith("http"):
            raise RuntimeError("Meshy image_to_3d için erişilebilir image URL (input_path URL) gerekir.")
        payload = {"image_url": job.input_path, "enable_pbr": True}
        endpoint = f"{base}/openapi/v1/image-to-3d"
    else:
        payload = {"mode": "preview", "prompt": prompt, "art_style": "realistic", "negative_prompt": "low quality"}
        endpoint = f"{base}/openapi/v2/text-to-3d"
    code, data, text = _http_json("POST", endpoint, headers=headers, payload=payload, timeout=60)
    task_id = data.get("result") if isinstance(data, dict) else None
    if code not in (200, 201, 202) or not task_id:
        raise RuntimeError(f"Meshy create failed ({code}): {text[:300]}")
    job.result = {**(job.result or {}), "provider": "meshy", "task_id": task_id}
    job.progress = 20
    job.save(update_fields=["result", "progress", "updated_at"])
    model_url = None
    poll_endpoint = f"{base}/openapi/v2/text-to-3d/{task_id}" if "text-to-3d" in endpoint else f"{base}/openapi/v1/image-to-3d/{task_id}"
    for _ in range(120):
        time.sleep(5)
        code, data, text = _http_json("GET", poll_endpoint, headers=headers, timeout=60)
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "").upper()
        prog = data.get("progress")
        try:
            job.progress = max(job.progress, min(95, int(20 + float(prog or 0) * 0.7)))
        except Exception:
            job.progress = min(95, job.progress + 1)
        job.save(update_fields=["progress", "updated_at"])
        if status in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
            model_urls = data.get("model_urls") or {}
            model_url = model_urls.get("glb") or model_urls.get("gltf") or model_urls.get("fbx") or data.get("thumbnail_url")
            job.result = {**(job.result or {}), "meshy": {"status": status, "model_urls": model_urls, "thumbnail_url": data.get("thumbnail_url")}}
            break
        if status in ("FAILED", "EXPIRED", "CANCELED"):
            raise RuntimeError(f"Meshy task failed: {status} {text[:200]}")
    if not model_url:
        raise RuntimeError("Meshy task timeout or no model URL returned")
    asset_id = _download_to_media(model_url, job.user, job.scene, base_name=(job.prompt or "meshy_model")[:40])
    if not asset_id:
        raise RuntimeError("Meshy model download/save failed")
    from .models import SceneAsset, SceneNode
    asset = SceneAsset.objects.filter(id=asset_id).first()
    node = None
    if job.scene_id and asset:
        comps = {
            "identity": {"stable_id": (job.consistency or {}).get("stable_id") or str(uuid.uuid4()), "locked_fields": (job.consistency or {}).get("locked_fields") or [], "label": (job.prompt or "Meshy Model")[:120]},
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "mesh": {"asset_id": str(asset.id), "generated": True, "external": True, "provider": "meshy"},
            "animation": entity_system.normalize_animation({}),
            "physics": entity_system.normalize_physics({}),
        }
        node = SceneNode.objects.create(scene=job.scene, asset=asset, name=(job.prompt or "Meshy Model")[:120], node_type="mesh", components=comps, visible=True)
    job.output_asset = asset
    job.output_node = node
    job.status = "completed"
    job.progress = 100


def _run_tripo(job, conf) -> None:
    """Tripo3D basic integration (task create + poll)."""
    from . import entity_system
    base = (conf.get("base_url") or "https://api.tripo3d.ai/v2").rstrip("/")
    key = conf.get("api_key")
    headers = {"Authorization": f"Bearer {key}"}
    prompt = job.prompt or "a 3d object"
    if job.mode == "image_to_3d":
        if not (job.input_path or "").startswith("http"):
            raise RuntimeError("Tripo image_to_3d için erişilebilir image URL gerekir.")
        payload = {"type": "image_to_model", "file": {"type": "url", "url": job.input_path}}
    else:
        payload = {"type": "text_to_model", "prompt": prompt}
    code, data, text = _http_json("POST", f"{base}/openapi/task", headers=headers, payload=payload, timeout=60)
    task_id = None
    if isinstance(data, dict):
        task_id = (data.get("data") or {}).get("task_id") or data.get("task_id")
    if code not in (200, 201, 202) or not task_id:
        raise RuntimeError(f"Tripo create failed ({code}): {text[:300]}")
    job.result = {**(job.result or {}), "provider": "tripo", "task_id": task_id}
    job.progress = 20
    job.save(update_fields=["result", "progress", "updated_at"])
    model_url = None
    for _ in range(120):
        time.sleep(5)
        code, data, text = _http_json("GET", f"{base}/openapi/task/{task_id}", headers=headers, timeout=60)
        if not isinstance(data, dict):
            continue
        d = data.get("data") or data
        status = str(d.get("status") or "").lower()
        prog = d.get("progress")
        try:
            job.progress = max(job.progress, min(95, int(20 + float(prog or 0) * 0.7)))
        except Exception:
            job.progress = min(95, job.progress + 1)
        job.save(update_fields=["progress", "updated_at"])
        if status in ("success", "succeeded", "completed", "done"):
            out = d.get("output") or {}
            model_url = out.get("pbr_model") or out.get("model") or out.get("base_model") or out.get("glb")
            if isinstance(model_url, dict):
                model_url = model_url.get("url") or model_url.get("path")
            job.result = {**(job.result or {}), "tripo": d}
            break
        if status in ("failed", "cancelled", "canceled", "error"):
            raise RuntimeError(f"Tripo task failed: {status} {text[:200]}")
    if not model_url:
        raise RuntimeError("Tripo task timeout or no model URL returned")
    asset_id = _download_to_media(str(model_url), job.user, job.scene, base_name=(job.prompt or "tripo_model")[:40])
    if not asset_id:
        raise RuntimeError("Tripo model download/save failed")
    from .models import SceneAsset, SceneNode
    asset = SceneAsset.objects.filter(id=asset_id).first()
    node = None
    if job.scene_id and asset:
        comps = {
            "identity": {"stable_id": (job.consistency or {}).get("stable_id") or str(uuid.uuid4()), "locked_fields": (job.consistency or {}).get("locked_fields") or [], "label": (job.prompt or "Tripo Model")[:120]},
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "mesh": {"asset_id": str(asset.id), "generated": True, "external": True, "provider": "tripo"},
            "animation": entity_system.normalize_animation({}),
            "physics": entity_system.normalize_physics({}),
        }
        node = SceneNode.objects.create(scene=job.scene, asset=asset, name=(job.prompt or "Tripo Model")[:120], node_type="mesh", components=comps, visible=True)
    job.output_asset = asset
    job.output_node = node
    job.status = "completed"
    job.progress = 100


def _run_generic_external(job, conf) -> None:
    """Generic external provider: POST {base_url}/generate then optional poll/download."""
    from . import entity_system
    base = (conf.get("base_url") or "").rstrip("/")
    key = conf.get("api_key") or ""
    if not base or not key:
        raise RuntimeError("Generic external 3D provider yapılandırılmadı (SCENE_3D_API_KEY + SCENE_3D_BASE_URL).")
    headers = {"Authorization": f"Bearer {key}"}
    payload = {
        "mode": job.mode,
        "prompt": job.prompt,
        "input_path": job.input_path,
        "parameters": job.parameters or {},
    }
    code, data, text = _http_json("POST", f"{base}/generate", headers=headers, payload=payload, timeout=90)
    if code not in (200, 201, 202):
        raise RuntimeError(f"External provider create failed ({code}): {text[:300]}")
    if not isinstance(data, dict):
        data = {}
    model_url = data.get("model_url") or data.get("output_url") or data.get("url")
    task_id = data.get("task_id") or data.get("id")
    if not model_url and task_id:
        status_url = data.get("status_url") or f"{base}/tasks/{task_id}"
        for _ in range(120):
            time.sleep(5)
            c2, d2, t2 = _http_json("GET", status_url, headers=headers, timeout=60)
            if isinstance(d2, dict):
                st = str(d2.get("status") or "").lower()
                model_url = d2.get("model_url") or d2.get("output_url") or ((d2.get("result") or {}).get("url") if isinstance(d2.get("result"), dict) else None)
                if st in ("completed", "success", "succeeded", "done") and model_url:
                    data = d2
                    break
                if st in ("failed", "error", "canceled"):
                    raise RuntimeError(f"External provider task failed: {t2[:200]}")
    if not model_url:
        raise RuntimeError("External provider did not return a model URL.")
    asset_id = _download_to_media(str(model_url), job.user, job.scene, base_name=(job.prompt or "external_model")[:40])
    if not asset_id:
        raise RuntimeError("External model download/save failed")
    from .models import SceneAsset, SceneNode
    asset = SceneAsset.objects.filter(id=asset_id).first()
    node = None
    if job.scene_id and asset:
        comps = {
            "identity": {"stable_id": (job.consistency or {}).get("stable_id") or str(uuid.uuid4()), "locked_fields": (job.consistency or {}).get("locked_fields") or [], "label": (job.prompt or "External Model")[:120]},
            "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
            "mesh": {"asset_id": str(asset.id), "generated": True, "external": True, "provider": job.provider},
            "animation": entity_system.normalize_animation({}),
            "physics": entity_system.normalize_physics({}),
        }
        node = SceneNode.objects.create(scene=job.scene, asset=asset, name=(job.prompt or "External Model")[:120], node_type="mesh", components=comps, visible=True)
    job.output_asset = asset
    job.output_node = node
    job.status = "completed"
    job.progress = 100
    job.result = {**(job.result or {}), "external": data}


def _run_impl(job_id):
    from .models import SceneGenerationJob
    job = SceneGenerationJob.objects.select_related("scene", "entity_class", "user").get(id=job_id)
    job.status = "running"
    job.progress = 5
    job.started_at = timezone.now()
    job.error = ""
    job.save(update_fields=["status", "progress", "started_at", "error", "updated_at"])
    try:
        provider = (job.provider or "procedural").lower()
        pcfg = _provider_config()
        if provider == "procedural":
            job = _run_procedural(job)
        elif provider == "meshy":
            if not (pcfg["meshy"]["api_key"] and pcfg["meshy"]["enabled"]):
                raise RuntimeError("Meshy API anahtarı yapılandırılmadı (MESHY_API_KEY).")
            _run_meshy(job, pcfg["meshy"])
        elif provider == "tripo":
            if not (pcfg["tripo"]["api_key"] and pcfg["tripo"]["enabled"]):
                raise RuntimeError("Tripo API anahtarı yapılandırılmadı (TRIPO_API_KEY).")
            _run_tripo(job, pcfg["tripo"])
        elif provider in ("external_text_to_3d", "external_image_to_3d", "generic"):
            if not (pcfg["generic"]["api_key"] and pcfg["generic"]["base_url"] and pcfg["generic"]["enabled"]):
                raise RuntimeError("Generic external 3D provider yapılandırılmadı (SCENE_3D_API_KEY + SCENE_3D_BASE_URL).")
            _run_generic_external(job, pcfg["generic"])
        else:
            job.provider = "procedural"
            job = _run_procedural(job)
        if job.status not in ("completed", "failed"):
            job.status = "completed"
            job.progress = 100
    except Exception as exc:
        logger.exception("3D generation job failed (%s)", job_id)
        job.status = "failed"
        job.progress = 100
        job.error = f"{type(exc).__name__}: {exc}"
    job.finished_at = timezone.now()
    job.save()
    return job


def run_generation_job(job_id, background=False):
    if background:
        return run_generation_job_background(job_id)
    try:
        return _run_impl(job_id)
    except Exception:
        try:
            from .models import SceneGenerationJob
            job = SceneGenerationJob.objects.get(id=job_id)
            if job.status not in ("completed", "failed"):
                job.status = "failed"
                job.error = job.error or "Job execution failed"
                job.finished_at = timezone.now()
                job.save()
            return job
        except Exception:
            raise


def run_generation_job_background(job_id):
    t = threading.Thread(target=run_generation_job, args=(job_id, False), daemon=True)
    t.start()
    return t


def serialize_job(job, request=None):
    return _serialize_job(job, request=request)