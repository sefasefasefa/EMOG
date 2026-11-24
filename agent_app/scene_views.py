import json
import logging
import os
from uuid import UUID

from django.conf import settings
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from .models import Scene, SceneAsset, SceneNode, EntityClass, EntityInstance, SceneGenerationJob
from .permissions import current_user, require_auth

logger = logging.getLogger(__name__)


def _json_error(message, status=400, **extra):
    payload = {"ok": False, "error": str(message)}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request):
    try:
        if not request.body:
            return {}
        return json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return {}


def _require_user(request):
    user = current_user(request)
    if not user:
        return None, _json_error("Authentication required", status=401)
    return user, None


def _scene_for_user(scene_id, user):
    try:
        sid = UUID(str(scene_id))
    except Exception:
        raise Http404("Invalid scene id")
    scene = get_object_or_404(Scene, id=sid)
    if scene.user_id and user and scene.user_id != user.id:
        raise Http404("Scene not found")
    if scene.user_id is None and user is not None:
        # claim orphan scene for first authenticated owner in this context? safer to deny
        raise Http404("Scene not found")
    return scene


def _asset_for_user(asset_id, user):
    try:
        aid = UUID(str(asset_id))
    except Exception:
        raise Http404("Invalid asset id")
    asset = get_object_or_404(SceneAsset, id=aid)
    if asset.user_id and user and asset.user_id != user.id:
        raise Http404("Asset not found")
    if asset.user_id is None and user is not None:
        raise Http404("Asset not found")
    return asset


def _node_for_scene(node_id, scene):
    try:
        nid = UUID(str(node_id))
    except Exception:
        raise Http404("Invalid node id")
    return get_object_or_404(SceneNode, id=nid, scene=scene)


def _serialize_asset(asset, request=None):
    url = ""
    try:
        if asset.file:
            url = asset.file.url
            if request is not None:
                url = request.build_absolute_uri(url)
    except Exception:
        url = ""
    return {
        "id": str(asset.id),
        "scene_id": str(asset.scene_id) if asset.scene_id else None,
        "name": asset.name or "",
        "file_type": asset.file_type,
        "url": url,
        "source_url": asset.source_url or "",
        "metadata": asset.metadata or {},
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _default_components(node_type="object"):
    try:
        from . import entity_system
        anim = entity_system.normalize_animation({})
        phys = entity_system.normalize_physics({"enabled": False, "body": "static", "mass": 1.0, "collider": None})
    except Exception:
        anim = {"clips": [], "active": None, "pose": {}, "keyframes": []}
        phys = {"enabled": False, "body": "static", "mass": 1.0, "collider": None, "constraints": []}
    base = {
        "identity": {"stable_id": "", "label": "", "locked_fields": []},
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "mesh": {"asset_id": None, "primitive": None},
        "skeleton": {"root": None, "bones": []},
        "animation": anim,
        "physics": phys,
    }
    return base


def _serialize_node(node, request=None):
    comps = copy_components = (node.components or {}) if hasattr(node, "components") else {}
    defaults = _default_components(getattr(node, "node_type", "object"))
    merged = {**defaults, **{k: v for k, v in (comps or {}).items() if v is not None}}
    # normalize key components for UI/timeline consistency
    try:
        from . import entity_system
        merged["animation"] = entity_system.normalize_animation(merged.get("animation"))
        merged["physics"] = entity_system.normalize_physics(merged.get("physics"))
    except Exception:
        pass
    return {
        "id": str(node.id),
        "scene_id": str(node.scene_id),
        "parent_id": str(node.parent_id) if node.parent_id else None,
        "asset_id": str(node.asset_id) if node.asset_id else None,
        "name": node.name,
        "node_type": node.node_type,
        "components": merged,
        "sort_order": node.sort_order,
        "visible": bool(node.visible),
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def _serialize_scene(scene, request=None, include_children=False):
    data = {
        "id": str(scene.id),
        "name": scene.name,
        "description": scene.description or "",
        "settings": scene.settings or {},
        "meta": scene.meta or {},
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
        "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
    }
    if include_children:
        data["assets"] = [_serialize_asset(a, request) for a in scene.assets.all().order_by("-created_at")]
        data["nodes"] = [_serialize_node(n, request) for n in scene.nodes.all().order_by("sort_order", "created_at")]
    return data


@require_http_methods(["GET", "POST"])
def scenes_api(request):
    user, err = _require_user(request)
    if err:
        return err
    if request.method == "GET":
        qs = Scene.objects.filter(user=user).order_by("-updated_at", "-created_at")
        return JsonResponse({"ok": True, "scenes": [_serialize_scene(s, request) for s in qs]})

    data = _parse_json_body(request)
    name = (data.get("name") or "Yeni Sahne").strip()[:200] or "Yeni Sahne"
    description = (data.get("description") or "").strip()
    settings_data = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    scene = Scene.objects.create(user=user, name=name, description=description, settings=settings_data)
    # default root nodes? none
    return JsonResponse({"ok": True, "scene": _serialize_scene(scene, request, include_children=True)}, status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def scene_detail_api(request, scene_id):
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)

    if request.method == "GET":
        return JsonResponse({"ok": True, "scene": _serialize_scene(scene, request, include_children=True)})

    if request.method == "DELETE":
        scene.delete()
        return JsonResponse({"ok": True})

    data = _parse_json_body(request)
    if "name" in data:
        name = (data.get("name") or "").strip()
        if name:
            scene.name = name[:200]
    if "description" in data:
        scene.description = str(data.get("description") or "")
    if "settings" in data and isinstance(data.get("settings"), dict):
        scene.settings = {**(scene.settings or {}), **data["settings"]}
    if "meta" in data and isinstance(data.get("meta"), dict):
        scene.meta = {**(scene.meta or {}), **data["meta"]}
    scene.save()
    return JsonResponse({"ok": True, "scene": _serialize_scene(scene, request, include_children=True)})


@require_http_methods(["GET", "POST"])
def scene_assets_api(request, scene_id):
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)

    if request.method == "GET":
        assets = scene.assets.all().order_by("-created_at")
        return JsonResponse({"ok": True, "assets": [_serialize_asset(a, request) for a in assets]})

    # upload
    upload = request.FILES.get("file") or request.FILES.get("asset")
    if not upload:
        return _json_error("Dosya gerekli (file)")
    name = (request.POST.get("name") or os.path.basename(upload.name) or "asset").strip()[:255]
    ext = os.path.splitext(upload.name or "")[1].lower()
    allowed = {".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp", ".bin"}
    if ext and ext not in allowed:
        return _json_error(f"Desteklenmeyen dosya türü: {ext}")
    max_bytes = int(getattr(settings, "SCENE_ASSET_MAX_BYTES", 50 * 1024 * 1024))
    if upload.size and upload.size > max_bytes:
        return _json_error(f"Dosya çok büyük (max {max_bytes} bayt)", status=413)
    file_type = "gltf" if ext in (".glb", ".gltf") else ("image" if ext in (".png", ".jpg", ".jpeg", ".webp") else "other")
    asset = SceneAsset.objects.create(
        user=user,
        scene=scene,
        name=name,
        file=upload,
        file_type=file_type,
        metadata={"original_name": upload.name, "size": upload.size},
    )
    # optionally auto-create a node for gltf
    auto_node = request.POST.get("auto_node", "1")
    node_data = None
    if auto_node not in ("0", "false", "False") and file_type == "gltf":
        comps = _default_components("mesh")
        comps["mesh"]["asset_id"] = str(asset.id)
        comps["identity"]["label"] = name
        node = SceneNode.objects.create(
            scene=scene,
            asset=asset,
            name=name or "Model",
            node_type="mesh",
            components=comps,
        )
        node_data = _serialize_node(node, request)
    return JsonResponse({
        "ok": True,
        "asset": _serialize_asset(asset, request),
        "node": node_data,
    }, status=201)


@require_http_methods(["GET", "POST"])
def scene_nodes_api(request, scene_id):
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)

    if request.method == "GET":
        nodes = scene.nodes.all().order_by("sort_order", "created_at")
        return JsonResponse({"ok": True, "nodes": [_serialize_node(n, request) for n in nodes]})

    data = _parse_json_body(request)
    name = (data.get("name") or "Node").strip()[:200] or "Node"
    node_type = (data.get("node_type") or "object").strip()
    valid_types = {c[0] for c in SceneNode.NODE_TYPE_CHOICES}
    if node_type not in valid_types:
        node_type = "object"
    parent = None
    parent_id = data.get("parent_id")
    if parent_id:
        try:
            parent = _node_for_scene(parent_id, scene)
        except Http404:
            parent = None
    asset = None
    asset_id = data.get("asset_id")
    if asset_id:
        try:
            a = _asset_for_user(asset_id, user)
            if a.scene_id == scene.id or a.scene_id is None:
                asset = a
        except Http404:
            asset = None
    components = data.get("components") if isinstance(data.get("components"), dict) else _default_components(node_type)
    node = SceneNode.objects.create(
        scene=scene,
        parent=parent,
        asset=asset,
        name=name,
        node_type=node_type,
        components=components,
        sort_order=int(data.get("sort_order") or 0),
        visible=bool(data.get("visible", True)),
    )
    return JsonResponse({"ok": True, "node": _serialize_node(node, request)}, status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def scene_node_detail_api(request, scene_id, node_id):
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)
    node = _node_for_scene(node_id, scene)

    if request.method == "GET":
        return JsonResponse({"ok": True, "node": _serialize_node(node, request)})

    if request.method == "DELETE":
        node.delete()
        return JsonResponse({"ok": True})

    data = _parse_json_body(request)
    if "name" in data:
        node.name = str(data.get("name") or node.name)[:200]
    if "node_type" in data:
        nt = str(data.get("node_type") or node.node_type)
        if nt in {c[0] for c in SceneNode.NODE_TYPE_CHOICES}:
            node.node_type = nt
    if "components" in data and isinstance(data.get("components"), dict):
        incoming = data["components"]
        merged = {**(node.components or {})}
        # deep-ish merge for known component maps, but preserve identity consistency
        existing_identity = (node.components or {}).get("identity") or {}
        locked = set(existing_identity.get("locked_fields") or [])
        stable_id = existing_identity.get("stable_id")
        for k, v in incoming.items():
            if k == "identity" and isinstance(v, dict):
                new_id = {**existing_identity, **v}
                if stable_id:
                    new_id["stable_id"] = stable_id
                for lk in locked:
                    if lk in existing_identity:
                        new_id[lk] = existing_identity[lk]
                merged["identity"] = new_id
            elif isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged.get(k), **v}
            else:
                merged[k] = v
        # if incoming omitted identity, keep existing; ensure stable_id/locked retained
        if "identity" not in incoming and existing_identity:
            merged["identity"] = existing_identity
        node.components = merged
    if "visible" in data:
        node.visible = bool(data.get("visible"))
    if "sort_order" in data:
        try:
            node.sort_order = int(data.get("sort_order"))
        except Exception:
            pass
    if "parent_id" in data:
        pid = data.get("parent_id")
        if pid is None or pid == "":
            node.parent = None
        else:
            try:
                p = _node_for_scene(pid, scene)
                if p.id != node.id:
                    node.parent = p
            except Http404:
                pass
    if "asset_id" in data:
        aid = data.get("asset_id")
        if not aid:
            node.asset = None
        else:
            try:
                a = _asset_for_user(aid, user)
                if a.scene_id == scene.id or a.scene_id is None:
                    node.asset = a
            except Http404:
                pass
    node.save()
    return JsonResponse({"ok": True, "node": _serialize_node(node, request)})


@require_http_methods(["POST"])
def scene_node_action_api(request, scene_id, node_id):
    """Apply a high-level action (animation/physics mapping) to a node."""
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)
    node = _node_for_scene(node_id, scene)
    data = _parse_json_body(request)
    action_name = (data.get("action") or data.get("name") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if not action_name:
        return _json_error("action gerekli")
    from . import entity_system
    comps = node.components or {}
    # enforce identity consistency: locked_fields remain
    identity = comps.get("identity") or {}
    locked = set(identity.get("locked_fields") or [])
    new_comps, action_def = entity_system.apply_action_to_components(comps, action_name, params=params)
    # preserve locked identity keys if someone tried to overwrite
    if action_def:
        new_id = new_comps.get("identity") or {}
        for k in locked:
            if k in identity:
                new_id[k] = identity[k]
        new_comps["identity"] = {**identity, **new_id}
    node.components = new_comps
    node.save()
    return JsonResponse({"ok": True, "node": _serialize_node(node, request), "action": action_def or None})


@require_http_methods(["GET", "PUT"])
def scene_timeline_api(request, scene_id):
    """Timeline/keyframe storage on scene (normalized, backward-compatible)."""
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)
    from . import entity_system
    if request.method == "GET":
        timeline = entity_system.normalize_timeline((scene.settings or {}).get("timeline") or {})
        return JsonResponse({"ok": True, "timeline": timeline})
    data = _parse_json_body(request)
    timeline = data.get("timeline") if isinstance(data.get("timeline"), dict) else data
    if not isinstance(timeline, dict):
        return _json_error("timeline objesi gerekli")
    timeline = entity_system.normalize_timeline(timeline)
    settings_data = dict(scene.settings or {})
    settings_data["timeline"] = timeline
    scene.settings = settings_data
    scene.save()
    return JsonResponse({"ok": True, "timeline": timeline})


@require_http_methods(["GET", "POST"])
def scene_timeline_state_api(request, scene_id):
    """Compute non-destructive preview state at time t (no DB writes)."""
    user, err = _require_user(request)
    if err:
        return err
    scene = _scene_for_user(scene_id, user)
    from . import entity_system
    if request.method == "GET":
        t = request.GET.get("t", request.GET.get("time", 0))
    else:
        data = _parse_json_body(request)
        t = data.get("t", data.get("time", 0))
    try:
        t = float(t)
    except Exception:
        t = 0.0
    timeline = entity_system.normalize_timeline((scene.settings or {}).get("timeline") or {})
    state = entity_system.timeline_state_at(timeline, t)
    # optional: include preview components for nodes present in scene
    nodes_by_id = {str(n.id): n for n in scene.nodes.all()}
    node_states = {}
    for nid, nstate in (state.get("nodes") or {}).items():
        if nid == "__global__":
            continue
        node = nodes_by_id.get(str(nid))
        if not node:
            continue
        comps = node.components or {}
        preview = entity_system.apply_timeline_state(comps, nstate, preview=True)
        # practical physics preview step (deterministic) from 0..t in coarse steps
        try:
            fps = max(1, int(timeline.get("fps") or 30))
            dt = 1.0 / fps
            steps = min(600, max(0, int(max(0.0, t) / dt)))
            tr = preview.get("transform") or {}
            ph = preview.get("physics") or {}
            # apply impulse hint if keyframe carries it
            if isinstance(nstate.get("physics"), dict) and (nstate["physics"].get("last_impulse")):
                ph = entity_system.normalize_physics({**entity_system.normalize_physics(ph), **nstate["physics"]})
            for _ in range(steps):
                tr, ph = entity_system.step_physics(tr, ph, dt)
            preview["transform"] = tr
            preview["physics"] = entity_system.normalize_physics(ph)
        except Exception:
            pass
        node_states[str(nid)] = {"state": nstate, "components": preview}
    return JsonResponse({"ok": True, "time": t, "timeline": timeline, "state": state, "node_previews": node_states})


@require_http_methods(["GET"])
def generation_providers_api(request):
    """3D üretim sağlayıcıları (async job + hook)."""
    user, err = _require_user(request)
    if err:
        return err
    try:
        from . import scene_generation
        info = scene_generation.get_provider_info()
        return JsonResponse(info)
    except Exception as exc:
        logger.exception("generation_providers_api error: %s", exc)
        return JsonResponse({"ok": True, "providers": [{"id": "procedural", "name": "Procedural (yerleşik)", "available": True, "modes": ["procedural"], "async_jobs": True}], "consistency": {"locked_fields_respected": True, "stable_id_preserved": True}})


def _job_for_user(job_id, user):
    try:
        jid = UUID(str(job_id))
    except Exception:
        raise Http404("Invalid job id")
    job = get_object_or_404(SceneGenerationJob, id=jid)
    if job.user_id and job.user_id != user.id:
        raise Http404("Job not found")
    if job.user_id is None and user is not None:
        raise Http404("Job not found")
    return job


@require_http_methods(["GET", "POST"])
def scene_generation_jobs_api(request):
    """3D generation jobs: list/create (async)."""
    user, err = _require_user(request)
    if err:
        return err
    from . import scene_generation
    if request.method == "GET":
        qs = SceneGenerationJob.objects.filter(user=user).order_by("-created_at")[:100]
        return JsonResponse({"ok": True, "jobs": [scene_generation.serialize_job(j, request) for j in qs]})

    data = _parse_json_body(request)
    scene = None
    scene_id = data.get("scene_id")
    if scene_id:
        try:
            scene = _scene_for_user(scene_id, user)
        except Exception:
            return _json_error("Sahne bulunamadı", status=404)
    entity_class = None
    class_id = data.get("entity_class_id") or data.get("class_id")
    if class_id:
        try:
            ec = EntityClass.objects.get(id=UUID(str(class_id)))
            if ec.user_id and ec.user_id != user.id:
                return _json_error("Sınıf bulunamadı", status=404)
            entity_class = ec
        except Exception:
            return _json_error("Sınıf bulunamadı", status=404)
    provider = str(data.get("provider") or "procedural").strip().lower() or "procedural"
    mode = str(data.get("mode") or "procedural").strip().lower() or "procedural"
    prompt = str(data.get("prompt") or "")
    input_path = str(data.get("input_path") or "")
    parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    consistency = data.get("consistency") if isinstance(data.get("consistency"), dict) else {}
    name = str(data.get("name") or prompt or (entity_class.name if entity_class else "3D Job"))
    run_now = bool(data.get("run", True))
    background = bool(data.get("background", True))

    job = scene_generation.create_generation_job(
        user=user,
        scene=scene,
        entity_class=entity_class,
        provider=provider,
        mode=mode,
        prompt=prompt,
        input_path=input_path,
        parameters=parameters,
        consistency=consistency,
        name=name,
    )
    if run_now:
        try:
            job = scene_generation.run_generation_job(str(job.id), background=background)
            try:
                job.refresh_from_db()
            except Exception:
                pass
        except Exception as exc:
            logger.exception("3D job run error: %s", exc)
    return JsonResponse({"ok": True, "job": scene_generation.serialize_job(job, request)}, status=201)


@require_http_methods(["GET"])
def scene_generation_job_detail_api(request, job_id):
    user, err = _require_user(request)
    if err:
        return err
    from . import scene_generation
    job = _job_for_user(job_id, user)
    return JsonResponse({"ok": True, "job": scene_generation.serialize_job(job, request)})


@require_http_methods(["POST"])
def scene_generation_job_run_api(request, job_id):
    user, err = _require_user(request)
    if err:
        return err
    from . import scene_generation
    job = _job_for_user(job_id, user)
    data = _parse_json_body(request)
    background = bool(data.get("background", False))
    try:
        job = scene_generation.run_generation_job(str(job.id), background=background)
        try:
            job.refresh_from_db()
        except Exception:
            pass
    except Exception as exc:
        logger.exception("3D job run error: %s", exc)
        return _json_error(f"Job çalıştırma hatası: {exc}", status=500)
    return JsonResponse({"ok": True, "job": scene_generation.serialize_job(job, request)})


def _serialize_learned_entity_knowledge(obj):
    hier = obj.hierarchy or {}
    layers = hier.get("layers") if isinstance(hier, dict) else []
    return {
        "id": str(obj.id),
        "concept": obj.concept,
        "category": obj.category,
        "confidence": float(obj.confidence or 0.0),
        "research_count": int(obj.research_count or 0),
        "last_researched_at": obj.last_researched_at.isoformat() if obj.last_researched_at else None,
        "sources_count": len(obj.sources or []),
        "hierarchy_levels": len(layers or []),
        "hierarchy": hier,
        "features": obj.features or {},
        "learned_spec": obj.learned_spec or {},
        "research_summary": obj.research_summary or "",
        "entity_class_id": str(obj.entity_class_id) if obj.entity_class_id else None,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
    }


@require_http_methods(["GET"])
def learned_entity_knowledge_api(request):
    """3D öğrenen bilgi modeli kayıtları (ayrı model)."""
    user, err = _require_user(request)
    if err:
        return err
    from .models import LearnedEntityKnowledge
    qs = LearnedEntityKnowledge.objects.filter(user=user) | LearnedEntityKnowledge.objects.filter(user__isnull=True)
    qs = qs.order_by("-updated_at", "-created_at").distinct()[:100]
    return JsonResponse({"ok": True, "items": [_serialize_learned_entity_knowledge(o) for o in qs]})


@require_http_methods(["GET"])
def learned_entity_knowledge_detail_api(request, knowledge_id):
    user, err = _require_user(request)
    if err:
        return err
    from .models import LearnedEntityKnowledge
    try:
        kid = UUID(str(knowledge_id))
    except Exception:
        return _json_error("Geçersiz id", status=400)
    obj = get_object_or_404(LearnedEntityKnowledge, id=kid)
    if obj.user_id and obj.user_id != user.id:
        return _json_error("Bulunamadı", status=404)
    data = _serialize_learned_entity_knowledge(obj)
    data["research_history"] = (obj.research_history or [])[-30:]
    data["sources"] = (obj.sources or [])[-30:]
    return JsonResponse({"ok": True, "item": data})


@require_http_methods(["GET"])
def learned_video_knowledge_api(request):
    """Video öğrenen bilgi modeli (ayrı model)."""
    user, err = _require_user(request)
    if err:
        return err
    from .models import LearnedVideoKnowledge
    qs = LearnedVideoKnowledge.objects.filter(user=user) | LearnedVideoKnowledge.objects.filter(user__isnull=True)
    qs = qs.order_by("-updated_at", "-created_at").distinct()[:50]
    items = []
    for o in qs:
        items.append({
            "id": str(o.id),
            "key": o.key,
            "profile": o.profile or {},
            "successful_jobs": int(o.successful_jobs or 0),
            "failed_jobs": int(o.failed_jobs or 0),
            "last_learned_at": o.last_learned_at.isoformat() if o.last_learned_at else None,
            "research_summary": o.research_summary or "",
            "insights_count": len(o.insights or []),
            "updated_at": o.updated_at.isoformat() if o.updated_at else None,
        })
    return JsonResponse({"ok": True, "items": items})


@require_auth
@require_http_methods(["GET"])
def studio_page(request):
    return render(request, "agent_app/studio.html")


def _serialize_entity_class(ec):
    return {
        "id": str(ec.id),
        "name": ec.name,
        "slug": ec.slug,
        "category": ec.category,
        "description": ec.description or "",
        "is_system": bool(ec.is_system),
        "components_template": ec.components_template or {},
        "skeleton_template": ec.skeleton_template or {},
        "render_template": ec.render_template or {},
        "parameters": ec.parameters or {},
        "version": ec.version,
        "created_at": ec.created_at.isoformat() if ec.created_at else None,
        "updated_at": ec.updated_at.isoformat() if ec.updated_at else None,
    }


def _serialize_entity_instance(inst, request=None):
    asset_url = ""
    try:
        if inst.asset and inst.asset.file:
            asset_url = inst.asset.file.url
            if request is not None:
                asset_url = request.build_absolute_uri(asset_url)
    except Exception:
        asset_url = ""
    return {
        "id": str(inst.id),
        "name": inst.name,
        "status": inst.status,
        "class_id": str(inst.entity_class_id),
        "class_name": inst.entity_class.name if inst.entity_class_id else "",
        "scene_id": str(inst.scene_id) if inst.scene_id else None,
        "node_id": str(inst.node_id) if inst.node_id else None,
        "asset_id": str(inst.asset_id) if inst.asset_id else None,
        "asset_url": asset_url,
        "components": inst.components or {},
        "parameters": inst.parameters or {},
        "created_at": inst.created_at.isoformat() if inst.created_at else None,
        "updated_at": inst.updated_at.isoformat() if inst.updated_at else None,
    }


@require_http_methods(["GET", "POST"])
def entity_classes_api(request):
    user, err = _require_user(request)
    if err:
        return err
    if request.method == "GET":
        qs = EntityClass.objects.filter(user__isnull=True) | EntityClass.objects.filter(user=user)
        qs = qs.order_by("category", "name")
        return JsonResponse({"ok": True, "classes": [_serialize_entity_class(c) for c in qs.distinct()]})

    data = _parse_json_body(request)
    name = (data.get("name") or "").strip()[:200]
    if not name:
        return _json_error("name gerekli")
    category = (data.get("category") or "other").strip()
    valid = {c[0] for c in EntityClass.CATEGORY_CHOICES}
    if category not in valid:
        category = "other"
    components_template = data.get("components_template") if isinstance(data.get("components_template"), dict) else {}
    skeleton_template = data.get("skeleton_template") if isinstance(data.get("skeleton_template"), dict) else {}
    render_template = data.get("render_template") if isinstance(data.get("render_template"), dict) else {}
    parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    ec = EntityClass.objects.create(
        user=user,
        name=name,
        slug=(data.get("slug") or "").strip()[:220],
        category=category,
        description=str(data.get("description") or ""),
        components_template=components_template,
        skeleton_template=skeleton_template,
        render_template=render_template,
        parameters=parameters,
        is_system=False,
    )
    # Şablonsuz/öğrenen akış: varsayılan olarak araştır & öğren (boş/eksik tanımda her zaman),
    # kullanıcı açıkça research:false verirse ve dolu tanım sağladıysa atlanabilir.
    explicit_no_research = (str(data.get("research", "")).lower() in ("0", "false", "no"))
    spec_empty = (not components_template and not skeleton_template and not render_template)
    do_research = bool(data.get("research", False) or data.get("learn", False) or data.get("auto_research", True) or spec_empty)
    if explicit_no_research and not spec_empty:
        do_research = False
    research_info = None
    if do_research:
        try:
            from . import learned_models
            res = learned_models.research_and_apply_entity_class(ec, user=user, context={"source": "create"})
            ec = res["entity_class"]
            knowledge = res.get("knowledge")
            research_info = {
                "researched": True,
                "knowledge_id": str(getattr(knowledge, "id", "")) if knowledge else None,
                "confidence": float(getattr(knowledge, "confidence", 0.0) or 0.0) if knowledge else 0.0,
                "research_count": int(getattr(knowledge, "research_count", 0) or 0) if knowledge else 0,
                "hierarchy_levels": len(((res.get("spec") or {}).get("hierarchy") or {}).get("layers") or []),
                "sources_count": len((res.get("spec") or {}).get("sources") or []),
                "offline": bool((res.get("research") or {}).get("offline")),
            }
        except Exception as exc:
            logger.exception("Entity class araştırma hatası: %s", exc)
            research_info = {"researched": False, "error": str(exc)}
    return JsonResponse({"ok": True, "class": _serialize_entity_class(ec), "research": research_info}, status=201)


@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def entity_class_detail_api(request, class_id):
    user, err = _require_user(request)
    if err:
        return err
    try:
        cid = UUID(str(class_id))
    except Exception:
        return _json_error("Geçersiz class id", status=400)
    ec = get_object_or_404(EntityClass, id=cid)
    # allow reading system/global classes, but only owner can modify/delete user classes
    if request.method == "GET":
        if ec.user_id and ec.user_id != user.id:
            return _json_error("Class bulunamadı", status=404)
        return JsonResponse({"ok": True, "class": _serialize_entity_class(ec)})

    if ec.is_system and ec.user_id is None:
        # sabit sistem şablonu yerine öğrenen/kopyalanabilir yaklaşım: doğrudan düzenleme kapalı
        return _json_error("Sabit sistem sınıfları düzenlenemez; kopyalayıp araştırarak öğrenin.", status=403)
    if ec.user_id and ec.user_id != user.id:
        return _json_error("Class bulunamadı", status=404)

    if request.method == "DELETE":
        ec.delete()
        return JsonResponse({"ok": True})

    data = _parse_json_body(request)
    if "name" in data:
        name = (data.get("name") or "").strip()
        if name:
            ec.name = name[:200]
    if "slug" in data:
        ec.slug = str(data.get("slug") or "")[:220]
    if "category" in data:
        cat = str(data.get("category") or ec.category)
        if cat in {c[0] for c in EntityClass.CATEGORY_CHOICES}:
            ec.category = cat
    if "description" in data:
        ec.description = str(data.get("description") or "")
    for key in ("components_template", "skeleton_template", "render_template", "parameters"):
        if key in data and isinstance(data.get(key), dict):
            setattr(ec, key, data[key])
    ec.version = int(getattr(ec, "version", 1) or 1) + 1
    ec.save()

    # isteğe bağlı: düzenleme sonrası yeniden araştır/öğren
    research_info = None
    if bool(data.get("research", False) or data.get("learn", False)):
        try:
            from . import learned_models
            res = learned_models.research_and_apply_entity_class(ec, user=user, context={"source": "update"})
            ec = res["entity_class"]
            knowledge = res.get("knowledge")
            research_info = {
                "researched": True,
                "knowledge_id": str(getattr(knowledge, "id", "")) if knowledge else None,
                "confidence": float(getattr(knowledge, "confidence", 0.0) or 0.0) if knowledge else 0.0,
                "research_count": int(getattr(knowledge, "research_count", 0) or 0) if knowledge else 0,
            }
        except Exception as exc:
            logger.exception("Entity class araştırma hatası: %s", exc)
            research_info = {"researched": False, "error": str(exc)}
    return JsonResponse({"ok": True, "class": _serialize_entity_class(ec), "research": research_info})


@require_http_methods(["POST"])
def instantiate_entity_class_api(request, class_id):
    user, err = _require_user(request)
    if err:
        return err
    from . import entity_system
    try:
        cid = UUID(str(class_id))
    except Exception:
        return _json_error("Geçersiz class id", status=400)
    ec = get_object_or_404(EntityClass, id=cid)
    if ec.user_id and ec.user_id != user.id:
        return _json_error("Class bulunamadı", status=404)

    data = _parse_json_body(request)
    scene_id = data.get("scene_id")
    if not scene_id:
        return _json_error("scene_id gerekli")
    scene = _scene_for_user(scene_id, user)
    name = (data.get("name") or ec.name).strip()[:200] or ec.name
    parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    place = bool(data.get("place", True))

    # Şablonsuz akış: sınıf henüz öğrenilmemiş/boşsa instantiate öncesi araştırmayı tetikle (her zaman internet araştırması)
    try:
        needs_research = bool(data.get("research", False) or data.get("learn", False))
        if not needs_research:
            comp_empty = not isinstance(ec.components_template, dict) or not ec.components_template
            skel = ec.skeleton_template if isinstance(ec.skeleton_template, dict) else {}
            hier = (skel.get("hierarchy") or {}) if isinstance(skel, dict) else {}
            layers = hier.get("layers") if isinstance(hier, dict) else []
            spec_empty = not isinstance(ec.render_template, dict) or not ec.render_template
            if comp_empty and not layers and spec_empty:
                needs_research = True
        if needs_research:
            from . import learned_models
            learned_models.research_and_apply_entity_class(ec, user=user, context={"source": "instantiate"})
            ec.refresh_from_db()
    except Exception as exc:
        logger.warning("Instantiate öncesi araştırma başarısız (%s): %s", ec.id, exc)

    try:
        instance, asset, node = entity_system.instantiate_entity_class(
            ec, user=user, scene=scene, name=name, parameters=parameters, place=place
        )
    except Exception as exc:
        logger.exception("Entity instantiate hatası: %s", exc)
        return _json_error(f"Üretim hatası: {exc}", status=500)

    payload = {
        "ok": True,
        "instance": _serialize_entity_instance(instance, request),
        "asset": _serialize_asset(asset, request),
        "node": _serialize_node(node, request) if node else None,
        "scene_id": str(scene.id),
    }
    return JsonResponse(payload, status=201)


@require_http_methods(["POST"])
def entity_class_research_api(request, class_id=None):
    """Sınıf için her zaman internet araştırması yapar, bottom-up öğrenir ve EntityClass'a uygular."""
    user, err = _require_user(request)
    if err:
        return err
    from . import learned_models
    data = _parse_json_body(request)

    ec = None
    if class_id is not None:
        try:
            cid = UUID(str(class_id))
        except Exception:
            return _json_error("Geçersiz class id", status=400)
        ec = get_object_or_404(EntityClass, id=cid)
        if ec.user_id and ec.user_id != user.id:
            return _json_error("Class bulunamadı", status=404)
        if ec.is_system and ec.user_id is None:
            return _json_error("Sabit sistem sınıfı yerine yeni bir sınıf oluşturup araştırın.", status=403)
        # allow overriding name/category hints
        if data.get("name"):
            ec.name = str(data.get("name")).strip()[:200] or ec.name
        if data.get("category"):
            cat = str(data.get("category")).strip()
            if cat in {c[0] for c in EntityClass.CATEGORY_CHOICES}:
                ec.category = cat
        ec.save()
    else:
        name = (data.get("name") or data.get("concept") or "").strip()[:200]
        if not name:
            return _json_error("name/concept gerekli")
        category = (data.get("category") or "other").strip()
        if category not in {c[0] for c in EntityClass.CATEGORY_CHOICES}:
            category = "other"
        ec = EntityClass.objects.create(
            user=user,
            name=name,
            category=category,
            description=str(data.get("description") or ""),
            components_template={},
            skeleton_template={},
            render_template={},
            parameters={},
            is_system=False,
        )

    try:
        res = learned_models.research_and_apply_entity_class(ec, user=user, context={"source": "research_api"})
    except Exception as exc:
        logger.exception("Entity research hatası: %s", exc)
        return _json_error(f"Araştırma/öğrenme hatası: {exc}", status=500)

    knowledge = res.get("knowledge")
    spec = res.get("spec") or {}
    layers = ((spec.get("hierarchy") or {}).get("layers")) or []
    return JsonResponse({
        "ok": True,
        "class": _serialize_entity_class(res["entity_class"]),
        "knowledge": {
            "id": str(getattr(knowledge, "id", "")) if knowledge else None,
            "concept": getattr(knowledge, "concept", ec.name) if knowledge else ec.name,
            "confidence": float(getattr(knowledge, "confidence", 0.0) or 0.0) if knowledge else 0.0,
            "research_count": int(getattr(knowledge, "research_count", 0) or 0) if knowledge else 0,
            "last_researched_at": knowledge.last_researched_at.isoformat() if (knowledge and knowledge.last_researched_at) else None,
            "sources_count": len(spec.get("sources") or []),
            "hierarchy_levels": len(layers),
            "summary": (getattr(knowledge, "research_summary", "") or "")[:1200] if knowledge else "",
        },
        "hierarchy": spec.get("hierarchy") or {},
        "research": res.get("research") or {},
    }, status=201 if class_id is None else 200)