import copy
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from agent_app.models import (
    ChatAttachment,
    Conversation,
    EntityClass,
    ImageJob,
    Node,
    Scene,
    SceneAsset,
    SceneGenerationJob,
    SceneNode,
    User,
    VideoJob,
)
from agent_app.permissions import current_user

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
MODEL3D_EXTENSIONS = {".glb", ".gltf", ".obj", ".fbx", ".stl", ".zip"}

ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | MODEL3D_EXTENSIONS | {
    ".txt", ".md", ".csv", ".json", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}

MAX_CHAT_ATTACHMENT_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_CHAT_ATTACHMENTS = 10


def _guess_kind(name: str, mime_type: str = "") -> str:
    ext = Path(name or "").suffix.lower()
    mime = (mime_type or "").lower()
    if ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    if ext in VIDEO_EXTENSIONS or mime.startswith("video/"):
        return "video"
    if ext in AUDIO_EXTENSIONS or mime.startswith("audio/"):
        return "audio"
    if ext in MODEL3D_EXTENSIONS:
        return "3d_model"
    return "file"


def _attachment_type(kind: str) -> str:
    if kind in {"image", "video", "file", "3d_model"}:
        return kind
    return "file"


def _content_type_for(name: str, mime_type: str = "") -> str:
    if mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(name or "")
    return guessed or "application/octet-stream"


def _content_disposition_attachment(filename: str) -> str:
    name = Path(filename or "download").name.replace("\r", "").replace("\n", "")
    if not name:
        name = "download"
    ascii_fallback = name.encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "'").strip(" .") or "download"
    utf8_name = quote(name, safe="!#$&+-.^_`|~ ")
    if not utf8_name:
        utf8_name = "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{utf8_name}"


def attachment_to_dict(att: ChatAttachment) -> dict:
    att_id = str(getattr(att, "id", "") or "")
    name = getattr(att, "name", "") or ""
    mime = getattr(att, "mime_type", "") or ""
    kind = getattr(att, "kind", "") or _guess_kind(name, mime)
    return {
        "id": att_id,
        "type": _attachment_type(kind),
        "name": name,
        "mime": mime,
        "size": int(getattr(att, "size", 0) or 0),
        "url": f"/api/chat/attachments/{att_id}/download/" if att_id else "",
        "status": "ready",
        "kind": kind,
    }


def serialize_attachments_for_node(node_or_meta: Any) -> list[dict]:
    if node_or_meta is None:
        return []
    if isinstance(node_or_meta, list):
        items = node_or_meta
    elif isinstance(node_or_meta, dict):
        if "attachments" in node_or_meta:
            items = node_or_meta.get("attachments") or []
        elif "meta_data" in node_or_meta and isinstance(node_or_meta.get("meta_data"), dict):
            items = (node_or_meta.get("meta_data") or {}).get("attachments") or []
        else:
            items = []
    else:
        meta = getattr(node_or_meta, "meta_data", None) or {}
        items = meta.get("attachments") or []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
    return out


def _iter_uploaded_files(request) -> list[UploadedFile]:
    if not hasattr(request, "FILES"):
        return []
    files = []
    for key in ("attachments", "attachments[]", "files", "files[]"):
        try:
            files.extend(request.FILES.getlist(key))
        except Exception:
            pass
    # de-duplicate same objects while preserving order
    seen = set()
    uniq = []
    for f in files:
        marker = id(f)
        if marker in seen:
            continue
        seen.add(marker)
        uniq.append(f)
    return uniq


def validate_uploaded_file(uploaded: UploadedFile):
    name = Path(getattr(uploaded, "name", "") or "").name
    if not name:
        raise ValueError("Dosya adı geçersiz.")
    ext = Path(name).suffix.lower()
    mime = (getattr(uploaded, "content_type", "") or "").lower()
    size = int(getattr(uploaded, "size", 0) or 0)
    if size <= 0:
        raise ValueError(f"{name}: boş dosya yüklenemez.")
    if size > MAX_CHAT_ATTACHMENT_SIZE:
        raise ValueError(f"{name}: dosya boyutu çok büyük.")
    # Allow known media/3d extensions, or safe generic types; reject clearly unsafe/unknown executables is out of scope
    # but we must validate type/size as specified.
    if ext and ext not in ALLOWED_EXTENSIONS:
        # allow common image/video/audio mime even if ext odd? keep conservative but usable
        if not (mime.startswith("image/") or mime.startswith("video/") or mime.startswith("audio/")):
            raise ValueError(f"{name}: desteklenmeyen dosya türü.")
    if not ext and not (mime.startswith("image/") or mime.startswith("video/") or mime.startswith("audio/")):
        raise ValueError(f"{name}: dosya uzantısı gerekli.")
    return name, ext, mime, size


def save_chat_attachments(request, user, conversation, node=None) -> list[dict]:
    if user is None or conversation is None:
        return []
    files = _iter_uploaded_files(request)
    if not files:
        return []
    if len(files) > MAX_CHAT_ATTACHMENTS:
        files = files[:MAX_CHAT_ATTACHMENTS]

    saved = []
    for uploaded in files:
        name, _ext, mime, size = validate_uploaded_file(uploaded)
        kind = _guess_kind(name, mime)
        att = ChatAttachment.objects.create(
            user=user,
            conversation=conversation,
            node=node,
            kind=kind,
            name=name,
            mime_type=mime or _content_type_for(name),
            size=size,
            file=uploaded,
            meta={"status": "ready"},
        )
        saved.append(attachment_to_dict(att))
    return saved


def _safe_media_path(file_field) -> Path | None:
    try:
        name = getattr(file_field, "name", "") or ""
    except Exception:
        return None
    if not name:
        return None
    media_root = Path(getattr(settings, "MEDIA_ROOT", "media")).resolve()
    try:
        full = (media_root / name).resolve()
        full.relative_to(media_root)
    except Exception:
        return None
    return full


def get_attachment_for_request(request, att_id):
    user = current_user(request)
    if user is None:
        return None, JsonResponse({"ok": False, "error": "Authentication required"}, status=401)
    try:
        att = ChatAttachment.objects.select_related("conversation", "user").filter(id=att_id).first()
    except Exception:
        att = None
    if att is None:
        return None, JsonResponse({"ok": False, "error": "Attachment not found", "code": "not_found"}, status=404)
    owner_id = getattr(att, "user_id", None)
    conv_user_id = None
    try:
        conv_user_id = getattr(att.conversation, "app_user_id", None)
    except Exception:
        conv_user_id = None
    if str(owner_id) != str(user.id) and str(conv_user_id) != str(user.id):
        return None, JsonResponse({"ok": False, "error": "Attachment not found", "code": "not_found"}, status=404)
    return att, None


def chat_attachment_download(request, att_id):
    att, error = get_attachment_for_request(request, att_id)
    if error is not None:
        return error
    path = _safe_media_path(att.file)
    if path is None or not path.is_file():
        return JsonResponse({"ok": False, "error": "File not found", "code": "file_not_found"}, status=404)
    ctype = _content_type_for(att.name, att.mime_type)
    try:
        fh = open(path, "rb")
    except OSError:
        return JsonResponse({"ok": False, "error": "Cannot open file", "code": "io_error"}, status=500)
    resp = FileResponse(fh, content_type=ctype)
    resp["Content-Disposition"] = _content_disposition_attachment(att.name)
    return resp


# ---------------------------------------------------------------------------
# Pending action / chat-driven generation helpers (Task 8)
# ---------------------------------------------------------------------------

SCENE_3D_EXTENSIONS = {".glb", ".gltf", ".obj", ".fbx", ".stl", ".zip"}
GLTF_NODE_EXTENSIONS = {".glb", ".gltf"}


def is_3d_attachment(att: dict | ChatAttachment | None) -> bool:
    if att is None:
        return False
    if isinstance(att, ChatAttachment):
        name = att.name or ""
        mime = att.mime_type or ""
        kind = att.kind or ""
    else:
        name = str(att.get("name") or att.get("filename") or "")
        mime = str(att.get("mime") or att.get("mime_type") or att.get("content_type") or "")
        kind = str(att.get("kind") or att.get("type") or "")
    ext = Path(name).suffix.lower()
    if kind == "3d_model" or ext in SCENE_3D_EXTENSIONS:
        return True
    if mime in ("model/gltf-binary", "model/gltf+json", "application/octet-stream") and ext in SCENE_3D_EXTENSIONS:
        return True
    return False


def _norm_text(value: str) -> str:
    value = str(value or "").strip().lower()
    # basic Turkish fold for matching
    tr_map = str.maketrans({
        "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    })
    value = value.translate(tr_map)
    value = re.sub(r"\s+", " ", value)
    return value


def _now_iso():
    try:
        return timezone.now().isoformat()
    except Exception:
        return datetime.utcnow().isoformat() + "Z"


def list_user_scenes(user, limit=30):
    if user is None:
        return []
    try:
        qs = Scene.objects.filter(user=user).order_by("-updated_at", "-created_at")[:limit]
        return list(qs)
    except Exception:
        return []


def build_place_3d_options(user):
    scenes = list_user_scenes(user)
    options = []
    for idx, scene in enumerate(scenes, start=1):
        options.append({
            "key": str(idx),
            "id": f"scene:{scene.id}",
            "action": "place_existing_scene",
            "type": "scene",
            "scene_id": str(scene.id),
            "label": scene.name or f"Sahne {idx}",
        })
    next_key = str(len(options) + 1)
    options.append({
        "key": next_key,
        "id": "new_scene",
        "action": "create_new_scene",
        "type": "new_scene",
        "label": "Yeni sahne oluştur ve yerleştir",
    })
    options.append({
        "key": str(int(next_key) + 1),
        "id": "store_only",
        "action": "store_only",
        "type": "store_only",
        "label": "Sadece dosya olarak ekle (sahneye koyma)",
    })
    return options, scenes


def format_pending_question(pending: dict, user=None) -> str:
    ptype = (pending or {}).get("type")
    options = (pending or {}).get("options") or []
    if ptype == "place_3d_upload":
        lines = [
            "3D dosyayı nereye eklemek istediğinizi seçin (tahmin yapmadan ilerleyebilmem için seçiminiz gerekli):",
        ]
        # show scene options explicitly (list already in options)
        for i, opt in enumerate(options, start=1):
            key = opt.get("key") or str(i)
            label = opt.get("label") or opt.get("name") or opt.get("id") or "Seçenek"
            extra = ""
            if opt.get("scene_id"):
                extra = f" [sahne:{opt.get('scene_id')}]"
            lines.append(f"{key}) {label}{extra}")
        lines.append("")
        lines.append("Yanıt olarak seçenek numarasını (1/2/3), sahne adını/ID'sini veya seçeneği yazabilirsiniz.")
        return "\n".join(lines)
    # generic fallback
    lines = [str((pending or {}).get("prompt") or "Devam etmek için bir seçim yapın:")]
    for i, opt in enumerate(options, start=1):
        key = opt.get("key") or str(i)
        label = opt.get("label") or opt.get("name") or opt.get("id") or "Seçenek"
        lines.append(f"{key}) {label}")
    return "\n".join(lines)


def _get_chat_attachment(att_id, user):
    try:
        return ChatAttachment.objects.filter(id=att_id).first()
    except Exception:
        return None


def _copy_attachment_to_scene_asset(att: ChatAttachment, user, scene: Scene | None, auto_node=True):
    """Create SceneAsset from an existing ChatAttachment file (copy)."""
    if att is None:
        return None, None
    ext = Path(att.name or "").suffix.lower()
    file_type = "gltf" if ext in (".glb", ".gltf") else ("image" if ext in IMAGE_EXTENSIONS else "other")
    # read bytes
    try:
        src_path = _safe_media_path(att.file)
        if src_path and src_path.is_file():
            data = src_path.read_bytes()
        else:
            with att.file.open("rb") as fh:
                data = fh.read()
    except Exception:
        data = b""
    safe_name = Path(att.name or "asset").name or "asset"
    # avoid clobber
    filename = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    asset = SceneAsset(
        user=user,
        scene=scene,
        name=safe_name[:255],
        file_type=file_type if ext in (".glb", ".gltf", ".png", ".jpg", ".jpeg", ".webp") else ("gltf" if ext in SCENE_3D_EXTENSIONS else "other"),
        metadata={
            "original_name": att.name,
            "size": att.size,
            "mime": att.mime_type,
            "source_chat_attachment_id": str(att.id),
        },
    )
    if data:
        asset.file.save(filename, ContentFile(data), save=False)
    asset.save()

    node = None
    if auto_node and scene is not None and ext in GLTF_NODE_EXTENSIONS:
        try:
            from .scene_views import _default_components, _serialize_node  # reuse component defaults
            comps = _default_components("mesh")
            comps["mesh"]["asset_id"] = str(asset.id)
            comps.setdefault("identity", {})["label"] = asset.name
            node = SceneNode.objects.create(
                scene=scene,
                asset=asset,
                name=asset.name or "Model",
                node_type="mesh",
                components=comps,
            )
        except Exception:
            try:
                # fallback minimal node
                node = SceneNode.objects.create(
                    scene=scene,
                    asset=asset,
                    name=asset.name or "Model",
                    node_type="mesh",
                    components={
                        "identity": {"stable_id": str(uuid.uuid4()), "label": asset.name, "locked_fields": []},
                        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                        "mesh": {"asset_id": str(asset.id)},
                        "animation": {"clips": [], "active": None, "pose": {}, "keyframes": []},
                        "physics": {"enabled": False, "body": "static", "mass": 1.0, "collider": None, "constraints": []},
                    },
                )
            except Exception:
                node = None
    return asset, node


def _serialize_scene_asset_result(asset: SceneAsset | None, node: SceneNode | None = None):
    data = {}
    if asset:
        data["asset_id"] = str(asset.id)
        data["scene_id"] = str(asset.scene_id) if asset.scene_id else None
        data["name"] = asset.name
        try:
            data["url"] = asset.file.url if asset.file else ""
        except Exception:
            data["url"] = ""
    if node:
        data["node_id"] = str(node.id)
        data["node_name"] = node.name
    return data


def resolve_pending_action_reply(pending: dict, reply_text: str, user):
    """Try to resolve a user's reply against a pending action. Returns (matched_option, info_dict or None)."""
    text = (reply_text or "").strip()
    norm = _norm_text(text)
    options = (pending or {}).get("options") or []
    if not options:
        return None, None

    # 1) numeric index (1-based) by option order or explicit key
    m = re.match(r"^\s*(\d+)\s*[\).:\-]?\s*$", text)
    if m:
        idx = int(m.group(1))
        # prefer key match first
        for opt in options:
            if str(opt.get("key")) == str(idx):
                return opt, {"matched_by": "key"}
        if 1 <= idx <= len(options):
            return options[idx - 1], {"matched_by": "index"}

    # 2) explicit id/scene id
    for opt in options:
        oid = str(opt.get("id") or "")
        if text == oid or norm == _norm_text(oid):
            return opt, {"matched_by": "id"}
        sid = str(opt.get("scene_id") or "")
        if sid and (text == sid or norm == sid.lower()):
            return opt, {"matched_by": "scene_id"}

    # 3) keywords for non-scene options
    for opt in options:
        action = str(opt.get("action") or opt.get("type") or "")
        if action in ("create_new_scene", "new_scene"):
            if any(k in norm for k in ("yeni sahne", "yeni", "olustur", "olustur ve yerlestir", "create new", "new scene")):
                # but avoid matching "yeni" inside a scene name accidentally? require stronger signal
                if "yeni" in norm and ("sahne" in norm or "olustur" in norm or norm in ("2", "yeni")):
                    return opt, {"matched_by": "keyword"}
                if norm in ("yeni sahne", "yeni sahne olustur", "yeni sahne olustur ve yerlestir"):
                    return opt, {"matched_by": "keyword"}
        if action in ("store_only", "sahnesiz", "only_store", "file_only"):
            if any(k in norm for k in ("sadece", "sahnesiz", "dosya olarak", "yerlestirme", "yerlestirme yapma", "sakla", "store only", "only file", "olmadan")):
                return opt, {"matched_by": "keyword"}

    # 4) label/name contains match (prefer exact/strong)
    norm_compact = norm.replace(" ", "")
    for opt in options:
        label = _norm_text(opt.get("label") or opt.get("name") or "")
        if label and (norm == label or norm_compact == label.replace(" ", "")):
            return opt, {"matched_by": "label"}
    # contains (careful)
    candidates = []
    for opt in options:
        label = _norm_text(opt.get("label") or opt.get("name") or "")
        if label and len(label) >= 3 and label in norm:
            candidates.append(opt)
        elif norm and len(norm) >= 3 and norm in label:
            candidates.append(opt)
    if len(candidates) == 1:
        return candidates[0], {"matched_by": "label_contains"}

    # 5) scene name lookup if option is scene
    for opt in options:
        if str(opt.get("scene_id") or ""):
            label = _norm_text(opt.get("label") or opt.get("name") or "")
            if label and (norm == label or (len(norm) >= 3 and norm in label) or (len(label) >= 3 and label in norm)):
                return opt, {"matched_by": "scene_label"}
    return None, None


def _create_scene_for_user(user, name=None):
    name = (name or "Yeni Sahne").strip()[:200] or "Yeni Sahne"
    return Scene.objects.create(user=user, name=name, description="", settings={})


def execute_place_3d_upload(pending: dict, selected: dict, user):
    """Execute a resolved place_3d_upload pending action. Returns result dict."""
    action = str(selected.get("action") or selected.get("type") or "")
    att_ids = list((pending or {}).get("source_attachment_ids") or [])
    attachments = []
    for aid in att_ids:
        att = _get_chat_attachment(aid, user)
        if att and str(getattr(att, "user_id", "")) == str(getattr(user, "id", "")):
            attachments.append(att)
    if not attachments:
        # try to resolve by conversation ownership fallback
        for aid in att_ids:
            att = _get_chat_attachment(aid, user)
            if att:
                conv_uid = getattr(getattr(att, "conversation", None), "app_user_id", None)
                if user and str(conv_uid) == str(user.id):
                    attachments.append(att)
    if not attachments:
        return {"ok": False, "error": "Kaynak 3D eki bulunamadı veya erişim yok.", "code": "attachment_not_found"}

    if action in ("store_only", "file_only", "sahnesiz"):
        # keep as chat attachment only
        items = [attachment_to_dict(a) for a in attachments]
        for it in items:
            it["status"] = "ready"
            it["placement"] = "stored_only"
        return {
            "ok": True,
            "mode": "stored_only",
            "message": "3D dosya(lar) sahneye yerleştirilmeden dosya olarak saklandı.",
            "attachments": items,
            "media": items,
        }

    # resolve/create scene
    scene = None
    if action in ("place_existing_scene", "scene"):
        scene_id = selected.get("scene_id")
        if scene_id:
            try:
                scene = Scene.objects.filter(id=scene_id, user=user).first()
            except Exception:
                scene = None
        if scene is None:
            return {"ok": False, "error": "Seçilen sahne bulunamadı.", "code": "scene_not_found"}
    elif action in ("create_new_scene", "new_scene"):
        desired_name = selected.get("scene_name") or (pending.get("params") or {}).get("new_scene_name") or "Yeni Sahne"
        scene = _create_scene_for_user(user, desired_name)
    else:
        return {"ok": False, "error": "Geçersiz yerleştirme seçeneği.", "code": "invalid_option"}

    results = []
    media_items = []
    for att in attachments:
        asset, node = _copy_attachment_to_scene_asset(att, user, scene, auto_node=True)
        if asset is None:
            results.append({"attachment_id": str(att.id), "ok": False, "error": "Asset oluşturulamadı"})
            continue
        info = _serialize_scene_asset_result(asset, node)
        info["ok"] = True
        info["attachment_id"] = str(att.id)
        results.append(info)
        media_items.append({
            "type": "3d_model" if is_3d_attachment(att) else "file",
            "name": asset.name,
            "url": info.get("url") or "",
            "status": "completed",
            "scene_id": str(scene.id),
            "asset_id": str(asset.id),
            "node_id": str(node.id) if node else None,
            "kind": "3d_model",
        })

    msg = f"3D dosya(lar) '{scene.name}' sahnesine yerleştirildi." if scene else "3D dosya(lar) işlendi."
    return {
        "ok": True,
        "mode": "placed",
        "scene_id": str(scene.id) if scene else None,
        "scene_name": scene.name if scene else None,
        "results": results,
        "attachments": media_items,
        "media": media_items,
        "message": msg,
    }


def maybe_create_3d_upload_pending_action(conversation, user, saved_attachments, user_message=""):
    """Detect ambiguous 3D upload and create pending action. Returns response dict or None."""
    atts = [a for a in (saved_attachments or []) if is_3d_attachment(a)]
    if not atts:
        return None
    # Spec: if 3D file uploaded and target scene not specified, ask user (never auto-select active scene).
    # Be conservative and always ask unless a very explicit structured resolution exists (none implemented) to avoid guessing.
    options, scenes = build_place_3d_options(user)
    pending = {
        "id": uuid.uuid4().hex,
        "type": "place_3d_upload",
        "created_at": _now_iso(),
        "source_attachment_ids": [str(a.get("id")) for a in atts if a.get("id")],
        "params": {"user_message": str(user_message or "")[:2000]},
        "options": options,
    }
    meta = dict(conversation.meta_data or {})
    meta["pending_action"] = pending
    conversation.meta_data = meta
    try:
        conversation.save()
    except Exception:
        pass
    question = format_pending_question(pending, user)
    return {
        "ok": True,
        "intent": "chat",
        "reply": question,
        "conversation_id": str(conversation.id),
        "pending_action": pending,
        "needs_user_choice": True,
        "attachments": saved_attachments or [],
    }


def build_scene_choice_options(user, include_store_only=True, store_label="Sadece üret/sakla (sahnesiz)"):
    scenes = list_user_scenes(user)
    options = []
    for idx, scene in enumerate(scenes, start=1):
        options.append({
            "key": str(idx),
            "id": f"scene:{scene.id}",
            "action": "place_existing_scene",
            "type": "scene",
            "scene_id": str(scene.id),
            "label": scene.name or f"Sahne {idx}",
        })
    next_key = str(len(options) + 1)
    options.append({
        "key": next_key,
        "id": "new_scene",
        "action": "create_new_scene",
        "type": "new_scene",
        "label": "Yeni sahne oluştur ve yerleştir",
    })
    if include_store_only:
        options.append({
            "key": str(int(next_key) + 1),
            "id": "store_only",
            "action": "store_only",
            "type": "store_only",
            "label": store_label,
        })
    return options, scenes


def format_generate_3d_question(pending: dict) -> str:
    options = (pending or {}).get("options") or []
    prompt = ((pending or {}).get("params") or {}).get("prompt") or ""
    lines = [
        "3D üretim için hedef sahne/yerleştirme seçin (tahmin yapmadan ilerleyebilmem için seçiminiz gerekli):",
    ]
    if prompt:
        lines.insert(1, f"İstek: {str(prompt)[:200]}")
    for i, opt in enumerate(options, start=1):
        key = opt.get("key") or str(i)
        label = opt.get("label") or opt.get("name") or "Seçenek"
        extra = f" [sahne:{opt.get('scene_id')}]" if opt.get("scene_id") else ""
        lines.append(f"{key}) {label}{extra}")
    lines.append("")
    lines.append("Yanıt olarak seçenek numarasını, sahne adını/ID'sini veya seçeneği yazabilirsiniz.")
    return "\n".join(lines)


def _extract_image_generation_intent(text: str):
    norm = _norm_text(text)
    if not norm:
        return None
    # simple heuristics (TR/EN)
    if any(k in norm for k in ("resim uret", "gorsel uret", "image generate", "generate image", "resim olustur", "gorsel olustur", "bir resim", "bir gorsel")):
        prompt = _strip_generation_command(text, kind="image")
        return {"kind": "image", "prompt": prompt}
    # "resim/görsel çiz/oluştur" with intent
    if ("resim" in norm or "gorsel" in norm or "image" in norm) and any(k in norm for k in ("uret", "olustur", "ciz", "draw", "create", "generate")):
        prompt = _strip_generation_command(text, kind="image")
        return {"kind": "image", "prompt": prompt}
    return None


def _extract_video_generation_intent(text: str):
    norm = _norm_text(text)
    if not norm:
        return None
    if "video" in norm and any(k in norm for k in ("uret", "olustur", "create", "generate", "yap")):
        prompt = _strip_generation_command(text, kind="video")
        return {"kind": "video", "prompt": prompt}
    return None


def _extract_3d_generation_intent(text: str):
    norm = _norm_text(text)
    if not norm:
        return None
    has_3d = any(k in norm for k in ("3d", "uc boyut", "uc boyutlu", "3 boyut", "model"))
    if has_3d and any(k in norm for k in ("uret", "olustur", "create", "generate", "yap")):
        # avoid catching "sahneye ekle" upload placement requests
        if "ekle" in norm and "uret" not in norm and "olustur" not in norm and "generate" not in norm:
            return None
        prompt = _strip_generation_command(text, kind="3d")
        return {"kind": "3d", "prompt": prompt}
    if any(k in norm for k in ("text to 3d", "text-to-3d", "image to 3d", "image-to-3d")):
        prompt = _strip_generation_command(text, kind="3d")
        return {"kind": "3d", "prompt": prompt}
    return None


def _strip_generation_command(text: str, kind: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # try to take content after ':' if present
    if ":" in raw:
        tail = raw.split(":", 1)[1].strip()
        if len(tail) >= 3:
            return tail[:4000]
    # remove common command phrases (best-effort)
    patterns = [
        r"(?i)\b(bir|bir tane)?\s*(resim|gorsel|image|video|3d\s*model|3d|model)\b",
        r"(?i)\b(uret|uretir misin|olustur|olusturur musun|ciz|generate|create|yap)\b",
        r"(?i)\b(lutfen|lütfen)\b",
    ]
    cleaned = raw
    for p in patterns:
        cleaned = re.sub(p, " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,.!?")
    if len(cleaned) < 3:
        # fallback to original minus leading keywords
        cleaned = raw
    return cleaned[:4000]


def detect_chat_generation_intent(message: str, saved_attachments=None):
    """Return a detected generation intent dict or None. Conservative heuristics."""
    msg = (message or "").strip()
    # prioritize 3D generation over upload placement only when explicit generate verbs used; upload case handled separately
    img = _extract_image_generation_intent(msg)
    if img and img.get("prompt"):
        return {"type": "generate_image", **img}
    vid = _extract_video_generation_intent(msg)
    if vid and vid.get("prompt"):
        return {"type": "generate_video", **vid}
    gen3d = _extract_3d_generation_intent(msg)
    if gen3d and gen3d.get("prompt"):
        # If user also uploaded a 3D model file, ambiguous - prefer asking? but "3d üret" is explicit generation.
        return {"type": "generate_3d", **gen3d}
    return None


def _job_attachment(job_kind: str, job, poll_url: str = "", extra: dict | None = None):
    att = {
        "type": "video" if job_kind == "video" else ("image" if job_kind == "image" else "3d_model"),
        "name": getattr(job, "prompt", None) or f"{job_kind} job",
        "status": getattr(job, "status", "pending"),
        "job_id": str(getattr(job, "id", "")),
        "job_kind": job_kind,
        "poll_url": poll_url,
    }
    # download/thumbnail if ready
    try:
        out = getattr(job, "output_path", "") or ""
        if out:
            if job_kind == "video":
                att["download_url"] = f"/api/video/{job.id}/download/"
                att["url"] = att["download_url"]
            elif job_kind == "image":
                att["download_url"] = f"/api/image/{job.id}/download/"
                att["url"] = att["download_url"]
            elif job_kind == "3d":
                # 3D job output may be SceneAsset; include asset id/url if present
                asset = getattr(job, "output_asset", None)
                if asset and getattr(asset, "file", None):
                    try:
                        att["download_url"] = asset.file.url
                        att["url"] = asset.file.url
                    except Exception:
                        pass
                att["output_asset_id"] = str(getattr(job, "output_asset_id", "") or "")
                att["output_node_id"] = str(getattr(job, "output_node_id", "") or "")
                att["scene_id"] = str(getattr(job, "scene_id", "") or "") or None
    except Exception:
        pass
    thumb = getattr(job, "thumbnail_path", "") or ""
    if thumb:
        # may be relative media path; expose as-is, frontend can decide
        att["thumbnail"] = thumb
        att["thumbnail_path"] = thumb
    if job_kind in ("video", "image"):
        att["studio_url"] = "/studio/?tab=media"
    if extra:
        att.update(extra)
    return att


def _run_image_job_sync(prompt, user):
    from agent_app import image_generation as ig
    job = ig._generate_image_impl(prompt=prompt, user=user)
    # run sync for chat UX
    try:
        job = ig._run_generation_impl(str(job.id), background=False)
        try:
            job.refresh_from_db()
        except Exception:
            pass
    except Exception:
        pass
    return job


def _run_video_job(prompt, user):
    from agent_app import video_generation as vg
    job = vg._generate_video_impl(prompt=prompt, user=user)
    # short sync run attempt like API; if long/duration large, may still be quick in impl but be safe
    try:
        dur = int(getattr(job, "duration", 10) or 10)
    except Exception:
        dur = 10
    if dur <= 15:
        try:
            job = vg._run_generation_impl(str(job.id))
            try:
                job.refresh_from_db()
            except Exception:
                pass
        except Exception:
            pass
    else:
        try:
            vg.run_generation_background(str(job.id))
        except Exception:
            pass
    return job


def _run_3d_job(prompt, user, scene=None, background=True):
    from agent_app import scene_generation as sg
    job = sg.create_generation_job(user=user, scene=scene, provider="procedural", mode="text_to_3d", prompt=prompt, name=prompt[:120])
    try:
        job = sg.run_generation_job(str(job.id), background=background)
        try:
            job.refresh_from_db()
        except Exception:
            pass
    except Exception:
        pass
    return job


def maybe_handle_chat_generation(conversation, user, message: str):
    """Detect and handle direct generation intents (image/video/3D). Returns response dict or None."""
    intent = detect_chat_generation_intent(message)
    if not intent:
        return None
    itype = intent.get("type")
    prompt = (intent.get("prompt") or message or "").strip()
    if not prompt:
        return None

    # 3D generation: scene/placement is ambiguous unless explicitly resolved; spec requires asking.
    if itype == "generate_3d":
        options, scenes = build_scene_choice_options(user, include_store_only=True, store_label="Sadece üret/sakla (sahnesiz)")
        pending = {
            "id": uuid.uuid4().hex,
            "type": "generate_3d",
            "created_at": _now_iso(),
            "params": {"prompt": prompt, "mode": "text_to_3d", "provider": "procedural"},
            "options": options,
        }
        meta = dict(conversation.meta_data or {})
        meta["pending_action"] = pending
        conversation.meta_data = meta
        try:
            conversation.save()
        except Exception:
            pass
        question = format_generate_3d_question(pending)
        return {
            "ok": True,
            "intent": "chat",
            "reply": question,
            "conversation_id": str(conversation.id),
            "pending_action": pending,
            "needs_user_choice": True,
        }

    # 2D image generation: create and run (sync for local provider) then return media attachment.
    if itype == "generate_image":
        job = _run_image_job_sync(prompt, user)
        poll_url = f"/api/image/{job.id}/" if job and getattr(job, "id", None) else ""
        att = _job_attachment("image", job, poll_url=poll_url)
        status = getattr(job, "status", "pending")
        if status == "completed":
            reply = f"Görsel üretimi tamamlandı: {prompt[:120]}"
        elif status == "failed":
            reply = f"Görsel üretimi başarısız oldu: {getattr(job, 'error', '') or 'bilinmeyen hata'}"
        else:
            reply = f"Görsel üretimi başlatıldı (durum: {status}). İş tamamlandığında sonuç burada görünecek."
        return {
            "ok": status != "failed",
            "intent": "chat",
            "reply": reply,
            "conversation_id": str(conversation.id),
            "attachments": [att],
            "media": [att],
            "job": {"id": str(job.id), "kind": "image", "status": status, "poll_url": poll_url},
            "pending_jobs": [{"id": str(job.id), "kind": "image", "poll_url": poll_url, "status": status}],
        }

    # Video generation
    if itype == "generate_video":
        job = _run_video_job(prompt, user)
        poll_url = f"/api/video/{job.id}/" if job and getattr(job, "id", None) else ""
        att = _job_attachment("video", job, poll_url=poll_url)
        status = getattr(job, "status", "pending")
        if status == "completed":
            reply = f"Video üretimi tamamlandı: {prompt[:120]}"
        elif status == "failed":
            reply = f"Video üretimi başarısız oldu: {getattr(job, 'error', '') or 'bilinmeyen hata'}"
        else:
            reply = f"Video üretimi başlatıldı (durum: {status}). İş tamamlandığında sonuç burada görünecek."
        return {
            "ok": status != "failed",
            "intent": "chat",
            "reply": reply,
            "conversation_id": str(conversation.id),
            "attachments": [att],
            "media": [att],
            "job": {"id": str(job.id), "kind": "video", "status": status, "poll_url": poll_url},
            "pending_jobs": [{"id": str(job.id), "kind": "video", "poll_url": poll_url, "status": status}],
        }

    return None


def execute_generate_3d(pending: dict, selected: dict, user):
    """Execute resolved generate_3d pending action."""
    params = (pending or {}).get("params") or {}
    prompt = (params.get("prompt") or "3D model").strip()
    action = str(selected.get("action") or selected.get("type") or "")
    scene = None
    place = False

    if action in ("store_only", "file_only", "sahnesiz"):
        scene = None
        place = False
    elif action in ("place_existing_scene", "scene"):
        scene_id = selected.get("scene_id")
        if scene_id:
            try:
                scene = Scene.objects.filter(id=scene_id, user=user).first()
            except Exception:
                scene = None
        if scene is None:
            return {"ok": False, "error": "Seçilen sahne bulunamadı.", "code": "scene_not_found"}
        place = True
    elif action in ("create_new_scene", "new_scene"):
        scene = _create_scene_for_user(user, selected.get("scene_name") or "Yeni Sahne")
        place = True
    else:
        return {"ok": False, "error": "Geçersiz seçim.", "code": "invalid_option"}

    # create and run 3D job (procedural). Run in background for responsiveness but try quick sync is complex; run foreground briefly?
    from agent_app import scene_generation as sg
    job = sg.create_generation_job(
        user=user,
        scene=scene,
        provider=str(params.get("provider") or "procedural"),
        mode=str(params.get("mode") or "text_to_3d"),
        prompt=prompt,
        parameters={**(params.get("parameters") or {})},
        name=prompt[:120],
    )
    # run synchronously for procedural (fast) when possible
    try:
        job = sg.run_generation_job(str(job.id), background=False)
        try:
            job.refresh_from_db()
        except Exception:
            pass
    except Exception:
        try:
            # fallback background
            sg.run_generation_job(str(job.id), background=True)
            job.refresh_from_db()
        except Exception:
            pass

    poll_url = f"/api/3d-jobs/{job.id}/"
    att = _job_attachment("3d", job, poll_url=poll_url)
    status = getattr(job, "status", "pending")
    if status == "completed":
        if scene and place:
            msg = f"3D üretim tamamlandı ve '{scene.name}' sahnesine yerleştirildi: {prompt[:120]}"
        else:
            msg = f"3D üretim tamamlandı (sahnesiz): {prompt[:120]}"
    elif status == "failed":
        msg = f"3D üretim başarısız oldu: {getattr(job, 'error', '') or 'bilinmeyen hata'}"
    else:
        msg = f"3D üretim başlatıldı (durum: {status}). Tamamlandığında sonuç chat ve Studio'da görünecek."

    return {
        "ok": status != "failed",
        "message": msg,
        "attachments": [att],
        "media": [att],
        "job": {"id": str(job.id), "kind": "3d", "status": status, "poll_url": poll_url, "scene_id": str(scene.id) if scene else None},
        "pending_jobs": [] if status in ("completed", "failed") else [{"id": str(job.id), "kind": "3d", "poll_url": poll_url, "status": status}],
        "scene_id": str(scene.id) if scene else None,
    }



# ---------------------------------------------------------------------------
# EntityClass instantiate via chat (spec 8.5c)
# ---------------------------------------------------------------------------

_INSTANTIATE_KEYWORDS = ("instantiate", "ornekle", "örnekle", "instance", "sınıfını", "sinifini", "entity class")


def _user_accessible_entity_classes(user):
    if user is None:
        return EntityClass.objects.none()
    try:
        from django.db.models import Q
        return EntityClass.objects.filter(Q(user=user) | Q(user__isnull=True)).distinct()
    except Exception:
        try:
            return EntityClass.objects.filter(user=user)
        except Exception:
            return EntityClass.objects.none()


def _resolve_entity_class_from_message(message: str, user):
    """Return (resolved_class_or_None, candidates_list). No guessing when ambiguous."""
    msg = (message or "").strip()
    norm = _norm_text(msg)
    if not norm:
        return None, []
    qs = _user_accessible_entity_classes(user)
    try:
        classes = list(qs)
    except Exception:
        classes = []
    if not classes:
        return None, []

    # Prefer explicit id/uuid mention? unlikely in chat, skip.

    exact = []
    partial = []
    for ec in classes:
        name = _norm_text(getattr(ec, "name", ""))
        slug = _norm_text(getattr(ec, "slug", ""))
        if not name and not slug:
            continue
        if name and (norm == name or re.search(rf"(?<!\w){re.escape(name)}(?!\w)", norm)):
            exact.append(ec)
            continue
        if slug and (norm == slug or re.search(rf"(?<!\w){re.escape(slug)}(?!\w)", norm)):
            exact.append(ec)
            continue
        # partial: class name appears inside message (min length to avoid noise)
        if name and len(name) >= 3 and name in norm:
            partial.append(ec)
        elif slug and len(slug) >= 3 and slug in norm:
            partial.append(ec)

    if len(exact) == 1:
        return exact[0], [exact[0]]
    if len(exact) > 1:
        # de-dup by id
        seen = set()
        uniq = []
        for ec in exact:
            if ec.id in seen:
                continue
            seen.add(ec.id)
            uniq.append(ec)
        if len(uniq) == 1:
            return uniq[0], uniq
        return None, uniq

    if len(partial) == 1:
        return partial[0], [partial[0]]
    if len(partial) > 1:
        seen = set()
        uniq = []
        for ec in partial:
            if ec.id in seen:
                continue
            seen.add(ec.id)
            uniq.append(ec)
        if len(uniq) == 1:
            return uniq[0], uniq
        return None, uniq

    return None, []


def _extract_instantiate_entity_intent(message: str):
    """Detect instantiate-existing-EntityClass intent. Conservative to avoid catching 3D generation."""
    msg = (message or "").strip()
    norm = _norm_text(msg)
    if not norm:
        return None
    # avoid catching text-to-3d generation prompts
    if _extract_3d_generation_intent(msg):
        # "3D model üret/oluştur/generate" is generation, not instantiate of existing class
        # but allow explicit instantiate words even if "3d" present
        if "instantiate" not in norm and "ornekle" not in norm and "örnekle" not in norm and "ornek" not in norm:
            return None
    has_instantiate = any(k in norm for k in ("instantiate", "ornekle", "ornek olustur", "ornekini olustur", "instance"))
    has_class_ref = any(k in norm for k in ("sinif", "sınıf", "entity class", "entityclass", "varligi", "varlığı"))
    has_action = any(k in norm for k in ("sahneye ekle", "sahneye yerlestir", "sahneye yerlestir", "ekle", "yerlestir", "yerleştir", "olustur", "oluştur", "koy", "spawn"))
    # Heuristic: explicit instantiate terms, or "class + place/add" phrasing
    if has_instantiate and (has_class_ref or has_action):
        return {"type": "instantiate_entity_class"}
    if has_class_ref and any(k in norm for k in ("sahneye ekle", "sahneye yerlestir", "sahneye yerleştir", "instantiate", "ornekle", "ornek olustur")):
        return {"type": "instantiate_entity_class"}
    # common phrasing: "<name> sınıfını sahneye ekle" / "mevcut <name> entity'sini ekle"
    if ("sınıf" in msg.lower() or "sinif" in norm) and any(k in norm for k in ("sahneye ekle", "sahneye yerlestir", "sahneye yerleştir", "ekle")):
        # exclude pure generation requests
        if not any(k in norm for k in ("uret", "generate", "text to 3d", "text-to-3d")):
            return {"type": "instantiate_entity_class"}
    return None


def _build_entity_class_choice_options(user, message=""):
    resolved, candidates = _resolve_entity_class_from_message(message, user)
    options = []
    if resolved is not None:
        options.append({
            "key": "1",
            "id": f"entity_class:{resolved.id}",
            "action": "select_entity_class",
            "type": "entity_class",
            "class_id": str(resolved.id),
            "label": getattr(resolved, "name", "EntityClass"),
        })
        return options, [resolved], resolved
    if candidates:
        for idx, ec in enumerate(candidates[:20], start=1):
            options.append({
                "key": str(idx),
                "id": f"entity_class:{ec.id}",
                "action": "select_entity_class",
                "type": "entity_class",
                "class_id": str(ec.id),
                "label": getattr(ec, "name", f"EntityClass {idx}"),
            })
        return options, candidates, None
    # no candidates from message; list user-accessible classes for selection
    try:
        all_classes = list(_user_accessible_entity_classes(user).order_by("name")[:30])
    except Exception:
        all_classes = []
    for idx, ec in enumerate(all_classes, start=1):
        options.append({
            "key": str(idx),
            "id": f"entity_class:{ec.id}",
            "action": "select_entity_class",
            "type": "entity_class",
            "class_id": str(ec.id),
            "label": getattr(ec, "name", f"EntityClass {idx}"),
        })
    return options, all_classes, None


def format_instantiate_entity_question(pending: dict) -> str:
    params = (pending or {}).get("params") or {}
    options = (pending or {}).get("options") or []
    missing = list((pending or {}).get("missing") or [])
    class_name = params.get("class_name") or params.get("entity_class_name")
    if "class" in missing or "entity_class" in missing:
        lines = ["Hangi EntityClass örneklenecek? Lütfen bir sınıf seçin (tahmin yapmadan ilerleyebilmem için seçiminiz gerekli):"]
        if options:
            for i, opt in enumerate(options, start=1):
                key = opt.get("key") or str(i)
                label = opt.get("label") or opt.get("name") or "Sınıf"
                lines.append(f"{key}) {label}")
        else:
            lines.append("(Kullanılabilir EntityClass bulunamadı. Lütfen sınıf adını net belirtin.)")
        lines.append("")
        lines.append("Yanıt olarak seçenek numarasını veya sınıf adını yazabilirsiniz.")
        return "\n".join(lines)
    # class known, ask scene/placement
    title = "EntityClass örneği için hedef sahne/yerleştirme seçin (tahmin yapmadan ilerleyebilmem için seçiminiz gerekli):"
    lines = [title]
    if class_name:
        lines.insert(1, f"Sınıf: {class_name}")
    for i, opt in enumerate(options, start=1):
        key = opt.get("key") or str(i)
        label = opt.get("label") or opt.get("name") or "Seçenek"
        extra = f" [sahne:{opt.get('scene_id')}]" if opt.get("scene_id") else ""
        lines.append(f"{key}) {label}{extra}")
    lines.append("")
    lines.append("Yanıt olarak seçenek numarasını, sahne adını/ID'sini veya seçeneği yazabilirsiniz.")
    return "\n".join(lines)


def execute_instantiate_entity_class(pending: dict, selected: dict, user):
    params = (pending or {}).get("params") or {}
    missing = list((pending or {}).get("missing") or [])

    # Step 1: class selection pending
    if selected.get("action") == "select_entity_class" or "class" in missing or "entity_class" in missing:
        class_id = selected.get("class_id")
        ec = None
        if class_id:
            try:
                ec = _user_accessible_entity_classes(user).filter(id=class_id).first()
            except Exception:
                ec = None
        if ec is None:
            return {"ok": False, "error": "Seçilen EntityClass bulunamadı veya erişim yok.", "code": "entity_class_not_found"}
        # Now ask for scene/placement (never auto-select scene)
        options, scenes = build_scene_choice_options(user, include_store_only=True, store_label="Sahneye yerleştirme, sadece üret/sakla (sahnesiz)")
        # Store as follow-up pending by returning special marker? easier: caller should set new pending.
        # But execute_* returns result after pending cleared; we need to keep asking. Handle in try_handle_pending_action.
        return {
            "ok": True,
            "needs_followup_choice": True,
            "next_pending_action": {
                "id": uuid.uuid4().hex,
                "type": "instantiate_entity_class",
                "created_at": _now_iso(),
                "params": {**params, "class_id": str(ec.id), "class_name": ec.name},
                "options": options,
                "missing": ["scene"],
            },
            "message": format_instantiate_entity_question({
                "type": "instantiate_entity_class",
                "params": {**params, "class_id": str(ec.id), "class_name": ec.name},
                "options": options,
                "missing": ["scene"],
            }),
        }

    # Step 2: scene/placement selection (class known)
    class_id = params.get("class_id")
    ec = None
    if class_id:
        try:
            ec = _user_accessible_entity_classes(user).filter(id=class_id).first()
        except Exception:
            ec = None
    if ec is None:
        # try resolve again from name
        ec, _ = _resolve_entity_class_from_message(params.get("class_name") or "", user)
    if ec is None:
        return {"ok": False, "error": "EntityClass bulunamadı veya erişim yok.", "code": "entity_class_not_found"}

    action = str(selected.get("action") or selected.get("type") or "")
    scene = None
    place = False
    if action in ("store_only", "file_only", "sahnesiz"):
        scene = None
        place = False
    elif action in ("place_existing_scene", "scene"):
        scene_id = selected.get("scene_id")
        if scene_id:
            try:
                scene = Scene.objects.filter(id=scene_id, user=user).first()
            except Exception:
                scene = None
        if scene is None:
            return {"ok": False, "error": "Seçilen sahne bulunamadı.", "code": "scene_not_found"}
        place = True
    elif action in ("create_new_scene", "new_scene"):
        scene = _create_scene_for_user(user, selected.get("scene_name") or "Yeni Sahne")
        place = True
    else:
        return {"ok": False, "error": "Geçersiz seçim.", "code": "invalid_option"}

    try:
        from agent_app import entity_system
        instance, asset, node = entity_system.instantiate_entity_class(
            ec, user=user, scene=scene, name=(params.get("instance_name") or ec.name), parameters=params.get("parameters") or {}, place=place
        )
    except Exception as exc:
        return {"ok": False, "error": f"Instantiate başarısız: {exc}", "code": "instantiate_failed"}

    media = []
    try:
        if asset and getattr(asset, "file", None):
            url = asset.file.url
        else:
            url = ""
    except Exception:
        url = ""
    media.append({
        "type": "3d_model",
        "kind": "3d_model",
        "name": getattr(asset, "name", None) or getattr(instance, "name", None) or ec.name,
        "url": url,
        "download_url": url,
        "status": "completed",
        "scene_id": str(scene.id) if scene else None,
        "asset_id": str(asset.id) if asset else None,
        "node_id": str(node.id) if node else None,
        "entity_instance_id": str(getattr(instance, "id", "") or ""),
        "entity_class_id": str(ec.id),
    })
    if scene and place:
        msg = f"'{ec.name}' sınıfı '{scene.name}' sahnesine eklendi."
    else:
        msg = f"'{ec.name}' sınıfı sahnesiz olarak oluşturuldu/saklandı."
    return {
        "ok": True,
        "message": msg,
        "attachments": media,
        "media": media,
        "scene_id": str(scene.id) if scene else None,
        "entity_instance_id": str(getattr(instance, "id", "") or ""),
    }


def maybe_handle_instantiate_entity_intent(conversation, user, message: str):
    intent = _extract_instantiate_entity_intent(message)
    if not intent:
        return None
    resolved, candidates = _resolve_entity_class_from_message(message, user)
    params = {"user_message": (message or "")[:2000]}
    if resolved is not None:
        params["class_id"] = str(resolved.id)
        params["class_name"] = resolved.name
        options, scenes = build_scene_choice_options(user, include_store_only=True, store_label="Sahneye yerleştirme, sadece üret/sakla (sahnesiz)")
        pending = {
            "id": uuid.uuid4().hex,
            "type": "instantiate_entity_class",
            "created_at": _now_iso(),
            "params": params,
            "options": options,
            "missing": ["scene"],
        }
        question = format_instantiate_entity_question(pending)
    else:
        # ambiguous or unspecified class: ask user to choose class (no guessing)
        class_options, _, _ = _build_entity_class_choice_options(user, message)
        if not class_options:
            # cannot proceed without a class; ask explicitly
            pending = {
                "id": uuid.uuid4().hex,
                "type": "instantiate_entity_class",
                "created_at": _now_iso(),
                "params": params,
                "options": [],
                "missing": ["class"],
            }
            question = "Hangi EntityClass örneklenecek? Lütfen sınıf adını net belirtin (tahmin yapmadan ilerleyemem)."
        else:
            pending = {
                "id": uuid.uuid4().hex,
                "type": "instantiate_entity_class",
                "created_at": _now_iso(),
                "params": params,
                "options": class_options,
                "missing": ["class"],
            }
            question = format_instantiate_entity_question(pending)
    meta = dict(conversation.meta_data or {})
    meta["pending_action"] = pending
    conversation.meta_data = meta
    try:
        conversation.save()
    except Exception:
        pass
    return {
        "ok": True,
        "intent": "chat",
        "reply": question,
        "conversation_id": str(conversation.id),
        "pending_action": pending,
        "needs_user_choice": True,
    }


# ---------------------------------------------------------------------------
# Video edit disambiguation (spec 8.6)
# ---------------------------------------------------------------------------

def _is_explicit_user_videos_request(message: str) -> bool:
    norm = _norm_text(message)
    # explicit request to pick from user's videos/library
    phrases = (
        "videolarimdan", "videolarımdan", "videolarim arasindan", "videolarım arasından",
        "kendi videolarimdan", "kendi videolarımdan", "videolarimdan sec", "videolarımdan seç",
        "video listemden", "kutuphanemden", "kütüphanemden", "mevcut videolarimdan", "mevcut videolarımdan",
        "my videos", "from my videos", "video library",
    )
    return any(p in norm for p in phrases)


def _collect_conversation_video_candidates(conversation, limit=20):
    candidates = []
    try:
        nodes = conversation.nodes.order_by("-timestamp")[:200]
    except Exception:
        nodes = []
    seen_jobs = set()
    for node in nodes:
        try:
            meta = getattr(node, "meta_data", None) or {}
            atts = meta.get("attachments") or []
        except Exception:
            atts = []
        if not isinstance(atts, list):
            continue
        for att in atts:
            if not isinstance(att, dict):
                continue
            job_kind = str(att.get("job_kind") or "").lower()
            atype = str(att.get("type") or "").lower()
            kind = str(att.get("kind") or "").lower()
            job_id = str(att.get("job_id") or att.get("id") if job_kind == "video" and att.get("job_id") else (att.get("job_id") or ""))
            # conversation video results: job_kind video or explicit video result attachment
            is_video = job_kind == "video" or atype == "video" or kind == "video"
            if not is_video:
                continue
            # prefer job-backed videos (editable via existing API)
            if not job_id:
                # try to infer from download_url like /api/video/<id>/download/
                url = str(att.get("download_url") or att.get("url") or "")
                m = re.search(r"/api/video/([^/]+)/", url)
                if m:
                    job_id = m.group(1)
            if not job_id or job_id in seen_jobs:
                continue
            seen_jobs.add(job_id)
            label = att.get("name") or att.get("prompt") or f"Video {job_id[:8]}"
            status = att.get("status") or "ready"
            candidates.append({
                "job_id": job_id,
                "label": str(label)[:120],
                "status": str(status),
                "source": "conversation",
            })
            if len(candidates) >= limit:
                return candidates
    return candidates


def _list_user_video_options(user, limit=20):
    opts = []
    if user is None:
        return opts
    try:
        qs = VideoJob.objects.filter(user=user).order_by("-created_at")[:limit]
        for v in qs:
            label = (getattr(v, "prompt", None) or getattr(v, "instruction", None) or "Video")[:80]
            status = getattr(v, "status", "")
            opts.append({
                "job_id": str(v.id),
                "label": f"{label} ({status})" if status else label,
                "status": status,
                "source": "user_videos",
            })
    except Exception:
        return []
    return opts


def _strip_video_edit_command(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    # try to take content after ':' if present and meaningful
    if ":" in raw:
        tail = raw.split(":", 1)[1].strip()
        if len(tail) >= 3:
            return tail[:2000]
    cleaned = re.sub(r"(?i)\b(bu|su|şu|o)?\s*(videoyu|video|videoda)\b", " ", raw)
    cleaned = re.sub(r"(?i)\b(duzenle|düzenle|edit|duzenler misin|düzenler misin)\b", " ", cleaned)
    cleaned = re.sub(r"(?i)\b(lutfen|lütfen)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,.!?")
    if len(cleaned) < 3:
        return ""
    # if it's effectively same as a generic request, treat unclear
    if _norm_text(cleaned) in ("", "bunu", "onu", "sunuu", "bunu duzenle", "bunu düzenle"):
        return ""
    return cleaned[:2000]


def _extract_video_edit_intent(message: str):
    msg = (message or "").strip()
    norm = _norm_text(msg)
    if not norm:
        return None

    explicit_edit_markers = (
        "duzenle", "düzenle", "edit", "trim", "kes", "kirp", "kırp", "crop",
        "efekt ekle", "efekt", "hizlandir", "hızlandır", "yavaslat", "yavaşlat",
        "slow motion", "subtitle", "altyazi", "altyazı", "rotate", "dondur", "döndür",
        "ses ekle", "muzik ekle", "müzik ekle", "sesi cikar", "sesi çıkar",
    )
    transform_markers = ("gif yap", "gif'e cevir", "gif e cevir", "mp3 yap", "ses dosyasi yap", "thumbnail")
    refers_existing = any(k in norm for k in ("bu video", "su video", "şu video", "bu videoyu", "su videoyu", "şu videoyu", "mevcut video", "videoyu", "videoda"))

    # "video üret/oluştur/generate" is generation, but allow explicit edit/transform requests on existing video
    if _extract_video_generation_intent(msg):
        if not (("video" in norm and refers_existing and any(k in norm for k in (explicit_edit_markers + transform_markers + ("gif",)))) or any(k in norm for k in explicit_edit_markers)):
            return None

    if "video" in norm and any(k in norm for k in explicit_edit_markers):
        instruction = _strip_video_edit_command(msg)
        return {"type": "edit_video", "instruction": instruction}

    # "bu videoyu gif yap" / similar transforms on an existing/referenced video -> edit intent
    if "video" in norm and refers_existing and any(k in norm for k in ("gif", "mp3", "thumbnail", "kes", "kirp", "kırp", "trim")):
        instruction = _strip_video_edit_command(msg)
        return {"type": "edit_video", "instruction": instruction or msg}

    # generic "bu videoyu düzenle"
    if "video" in norm and ("duzenle" in norm or "düzenle" in norm or "edit" in norm):
        instruction = _strip_video_edit_command(msg)
        return {"type": "edit_video", "instruction": instruction}
    return None


def format_edit_video_question(pending: dict) -> str:
    params = (pending or {}).get("params") or {}
    options = (pending or {}).get("options") or []
    missing = list((pending or {}).get("missing") or [])
    lines = []
    if "target" in missing:
        source = params.get("target_source") or "conversation"
        if source == "user_videos":
            lines.append("Hangi videoyu düzenlemek istediğinizi seçin (videolarınızdan). Tahmin yapmadan ilerleyebilmem için seçiminiz gerekli:")
        else:
            lines.append("Hangi videoyu düzenlemek istediğinizi seçin (bu sohbet akışındaki video medya/sonuçlarından). Tahmin yapmadan ilerleyebilmem için seçiminiz gerekli:")
        for i, opt in enumerate(options, start=1):
            key = opt.get("key") or str(i)
            label = opt.get("label") or "Video"
            extra = f" [video:{opt.get('job_id')}]" if opt.get("job_id") else ""
            lines.append(f"{key}) {label}{extra}")
        if not options:
            lines.append("(Seçilebilir video bulunamadı. Lütfen hedef videoyu net belirtin.)")
        lines.append("")
        lines.append("Yanıt olarak seçenek numarasını veya video ID'sini yazabilirsiniz.")
        return "\n".join(lines)
    if "instruction" in missing:
        target_label = params.get("target_label") or params.get("target_job_id") or "seçilen video"
        lines.append(f"'{target_label}' için düzenleme talimatını net yazın (mevcut düzenleme yetenekleri uygulanır). Tahmin yapmadan ilerleyebilmem için talimat gerekli:")
        lines.append("Örnek: 'videoyu 5 saniyeye kırp', 'gif yap', 'sesi çıkar' vb.")
        return "\n".join(lines)
    # generic
    return format_pending_question(pending)


def execute_edit_video(pending: dict, selected: dict, user):
    params = (pending or {}).get("params") or {}
    missing = list((pending or {}).get("missing") or [])

    # Target selection step
    if "target" in missing or selected.get("action") == "select_video":
        job_id = selected.get("job_id") or params.get("target_job_id")
        if not job_id:
            return {"ok": False, "error": "Video hedefi seçilmedi.", "code": "video_target_missing"}
        # ownership check
        job = None
        try:
            job = VideoJob.objects.filter(id=job_id, user=user).first()
        except Exception:
            job = None
        if job is None:
            return {"ok": False, "error": "Seçilen video bulunamadı veya erişim yok.", "code": "video_not_found"}
        target_label = selected.get("label") or params.get("target_label") or (getattr(job, "prompt", None) or "Video")
        instruction = (params.get("instruction") or "").strip()
        if not instruction:
            # ask for instruction next
            return {
                "ok": True,
                "needs_followup_choice": True,
                "next_pending_action": {
                    "id": uuid.uuid4().hex,
                    "type": "edit_video",
                    "created_at": _now_iso(),
                    "params": {**params, "target_job_id": str(job.id), "target_label": str(target_label)[:120], "instruction": ""},
                    "options": [],
                    "missing": ["instruction"],
                },
                "message": format_edit_video_question({
                    "type": "edit_video",
                    "params": {**params, "target_job_id": str(job.id), "target_label": str(target_label)[:120]},
                    "options": [],
                    "missing": ["instruction"],
                }),
            }
        # both known -> execute below using params updated
        params = {**params, "target_job_id": str(job.id), "target_label": str(target_label)[:120], "instruction": instruction}

    # Instruction provided as free text (target already known)
    job_id = params.get("target_job_id")
    instruction = (params.get("instruction") or "").strip()
    if not job_id or not instruction:
        return {"ok": False, "error": "Video düzenleme için hedef ve talimat gerekli.", "code": "video_edit_incomplete"}
    try:
        job = VideoJob.objects.filter(id=job_id, user=user).first()
    except Exception:
        job = None
    if job is None:
        return {"ok": False, "error": "Video bulunamadı veya erişim yok.", "code": "video_not_found"}
    try:
        from agent_app import video_generation as vg
        updated = vg._edit_video_impl(str(job.id), instruction)
        try:
            updated.refresh_from_db()
        except Exception:
            pass
        job = updated if isinstance(updated, VideoJob) else VideoJob.objects.get(id=job.id)
    except Exception as exc:
        try:
            job.status = "failed"
            job.error = str(exc)
            job.save(update_fields=["status", "error"])
        except Exception:
            pass
        return {"ok": False, "error": f"Video düzenleme başarısız: {exc}", "code": "video_edit_failed"}

    poll_url = f"/api/video/{job.id}/"
    att = _job_attachment("video", job, poll_url=poll_url)
    status = getattr(job, "status", "pending")
    if status == "completed":
        msg = f"Video düzenleme tamamlandı: {params.get('target_label') or 'video'}"
    elif status == "failed":
        msg = f"Video düzenleme başarısız oldu: {getattr(job, 'error', '') or 'bilinmeyen hata'}"
    else:
        msg = f"Video düzenleme başlatıldı (durum: {status})."
    return {
        "ok": status != "failed",
        "message": msg,
        "attachments": [att],
        "media": [att],
        "job": {"id": str(job.id), "kind": "video", "status": status, "poll_url": poll_url},
        "pending_jobs": [] if status in ("completed", "failed") else [{"id": str(job.id), "kind": "video", "poll_url": poll_url, "status": status}],
    }


def maybe_handle_chat_video_edit(conversation, user, message: str):
    intent = _extract_video_edit_intent(message)
    if not intent:
        return None
    instruction = (intent.get("instruction") or "").strip()
    explicit_user = _is_explicit_user_videos_request(message)

    if explicit_user:
        candidates = _list_user_video_options(user)
        source = "user_videos"
    else:
        candidates = _collect_conversation_video_candidates(conversation)
        source = "conversation"

    # If conversation has no candidates and not explicitly asking user videos, it's ambiguous/unsupported to guess;
    # but if there are user videos and request is clearly an edit request in chat context, spec says priority is conversation media/results.
    # With no conversation video targets, we must ask user to specify/choose (could offer user's videos only if explicitly requested;
    # otherwise ask them to clarify target from conversation or explicitly request from their videos?) - to avoid guessing, ask.
    options = []
    for idx, c in enumerate(candidates[:20], start=1):
        options.append({
            "key": str(idx),
            "id": f"video_job:{c['job_id']}",
            "action": "select_video",
            "type": "video",
            "job_id": c["job_id"],
            "label": c.get("label") or f"Video {idx}",
        })

    # Determine missing pieces
    if len(options) == 1 and instruction:
        # Unambiguous target in prioritized source and instruction clear -> execute directly
        selected = options[0]
        exec_result = execute_edit_video({
            "type": "edit_video",
            "params": {"instruction": instruction, "target_job_id": selected.get("job_id"), "target_label": selected.get("label")},
            "missing": [],
        }, selected, user)
        return {
            "ok": bool(exec_result.get("ok", False)),
            "intent": "chat",
            "reply": exec_result.get("message") or "",
            "conversation_id": str(conversation.id),
            "attachments": exec_result.get("attachments") or [],
            "media": exec_result.get("media") or [],
            "job": exec_result.get("job"),
            "pending_jobs": exec_result.get("pending_jobs") or [],
            "result": exec_result,
        }

    missing = []
    params = {"instruction": instruction, "target_source": source, "user_message": (message or "")[:2000]}
    if len(options) != 1:
        missing.append("target")
    if not instruction:
        missing.append("instruction")

    # If target is unambiguous but instruction missing
    if len(options) == 1 and "instruction" in missing:
        params["target_job_id"] = options[0].get("job_id")
        params["target_label"] = options[0].get("label")
        pending = {
            "id": uuid.uuid4().hex,
            "type": "edit_video",
            "created_at": _now_iso(),
            "params": params,
            "options": [],
            "missing": ["instruction"],
        }
        question = format_edit_video_question(pending)
    elif "target" in missing:
        # ask for target selection (and keep instruction in params if provided for later)
        pending = {
            "id": uuid.uuid4().hex,
            "type": "edit_video",
            "created_at": _now_iso(),
            "params": params,
            "options": options,
            "missing": ["target"] + (["instruction"] if not instruction else []),
        }
        # If no options at all, be explicit
        if not options:
            if source == "conversation":
                question = (
                    "Video düzenlemek için bu sohbet akışında hedef bir video sonucu bulunamadı ve talimat/ hedef belirsiz olabilir. "
                    "Lütfen düzenlemek istediğiniz videoyu bu sohbette belirtin/paylaşın veya videolarınızdan seçim istediğinizi açıkça yazın. "
                    "Ayrıca düzenleme talimatını net belirtin (mevcut düzenleme yetenekleri uygulanır)."
                )
            else:
                question = (
                    "Videolarınız arasında düzenlenecek uygun bir video bulunamadı. "
                    "Lütfen hedef videoyu ve düzenleme talimatını net belirtin."
                )
            # still store pending so user can clarify by replying? but no options to resolve; better to just ask without pending options
            # create a clarification pending without resolvable options is tricky; instead return question without pending_action to avoid dead-end
            return {
                "ok": True,
                "intent": "chat",
                "reply": question,
                "conversation_id": str(conversation.id),
                "needs_user_choice": True,
            }
        question = format_edit_video_question(pending)
    else:
        # shouldn't happen, but ask generically
        pending = {
            "id": uuid.uuid4().hex,
            "type": "edit_video",
            "created_at": _now_iso(),
            "params": params,
            "options": options,
            "missing": missing or ["target", "instruction"],
        }
        question = format_edit_video_question(pending)

    meta = dict(conversation.meta_data or {})
    meta["pending_action"] = pending
    conversation.meta_data = meta
    try:
        conversation.save()
    except Exception:
        pass
    return {
        "ok": True,
        "intent": "chat",
        "reply": question,
        "conversation_id": str(conversation.id),
        "pending_action": pending,
        "needs_user_choice": True,
    }


def maybe_handle_chat_action_intents(conversation, user, message: str):
    """Handle chat intents that may require clarification (EntityClass instantiate, video edit)."""
    # EntityClass instantiate (spec 8.5c)
    resp = maybe_handle_instantiate_entity_intent(conversation, user, message)
    if resp:
        return resp
    # Video edit disambiguation (spec 8.6)
    resp = maybe_handle_chat_video_edit(conversation, user, message)
    if resp:
        return resp
    return None


def _format_pending_question_for_type(pending: dict, user=None) -> str:
    ptype = (pending or {}).get("type")
    if ptype == "generate_3d":
        return format_generate_3d_question(pending)
    if ptype == "instantiate_entity_class":
        return format_instantiate_entity_question(pending)
    if ptype == "edit_video":
        return format_edit_video_question(pending)
    return format_pending_question(pending, user)


def try_handle_pending_action(conversation, user, reply_text):
    """If conversation has pending_action, try to resolve and execute it. Returns response dict or None."""
    meta = dict(conversation.meta_data or {})
    pending = meta.get("pending_action")
    if not isinstance(pending, dict) or not pending:
        return None

    ptype = pending.get("type")
    params = dict((pending.get("params") or {}))
    missing = list((pending.get("missing") or []))
    options = list((pending.get("options") or []))

    selected = None
    info = None

    # Free-text instruction step for video edit: target known, instruction missing.
    if ptype == "edit_video" and "instruction" in missing and params.get("target_job_id") and not options:
        instruction = (reply_text or "").strip()
        if instruction:
            pending = {**pending, "params": {**params, "instruction": instruction}, "missing": [m for m in missing if m != "instruction"]}
            selected = {"action": "provide_instruction", "type": "instruction"}
        else:
            selected = None
    else:
        selected, info = resolve_pending_action_reply(pending, reply_text, user)

    if selected is None:
        # re-ask without guessing
        question = _format_pending_question_for_type(pending, user)
        return {
            "handled": True,
            "ok": True,
            "intent": "chat",
            "reply": "Seçiminizi netleştiremedim. Lütfen aşağıdaki seçeneklerden birini belirtin.\n\n" + question,
            "conversation_id": str(conversation.id),
            "pending_action": pending,
            "needs_user_choice": True,
        }

    if ptype == "place_3d_upload":
        exec_result = execute_place_3d_upload(pending, selected, user)
    elif ptype == "generate_3d":
        exec_result = execute_generate_3d(pending, selected, user)
    elif ptype == "instantiate_entity_class":
        exec_result = execute_instantiate_entity_class(pending, selected, user)
    elif ptype == "edit_video":
        exec_result = execute_edit_video(pending, selected, user)
    else:
        exec_result = {"ok": False, "error": "Bu bekleyen işlem türü desteklenmiyor.", "code": "unsupported_pending"}

    # Follow-up clarification (e.g. class selected -> now ask scene/placement)
    if isinstance(exec_result, dict) and exec_result.get("needs_followup_choice") and exec_result.get("next_pending_action"):
        next_pending = exec_result.get("next_pending_action")
        meta["pending_action"] = next_pending
        conversation.meta_data = meta
        try:
            conversation.save()
        except Exception:
            pass
        reply = exec_result.get("message") or _format_pending_question_for_type(next_pending, user)
        return {
            "handled": True,
            "ok": True,
            "intent": "chat",
            "reply": reply,
            "conversation_id": str(conversation.id),
            "pending_action": next_pending,
            "needs_user_choice": True,
            "attachments": exec_result.get("attachments") or [],
            "media": exec_result.get("media") or [],
        }

    meta.pop("pending_action", None)
    conversation.meta_data = meta
    try:
        conversation.save()
    except Exception:
        pass

    reply = exec_result.get("message") or (exec_result.get("error") if not exec_result.get("ok") else "İşlem tamamlandı.")
    return {
        "handled": True,
        "ok": bool(exec_result.get("ok", False)) or bool(exec_result.get("message")),
        "intent": "chat",
        "reply": reply,
        "conversation_id": str(conversation.id),
        "result": exec_result,
        "attachments": exec_result.get("attachments") or exec_result.get("media") or [],
        "pending_action": None,
        "job": exec_result.get("job"),
        "pending_jobs": exec_result.get("pending_jobs") or [],
        "media": exec_result.get("media") or exec_result.get("attachments") or [],
    }


# ---------------------------------------------------------------------------
# Vision analizi: kullanici fotoğraf/resim eki + "analiz"-like mesajla
# ---------------------------------------------------------------------------

def _attachment_image_bytes(att):
    """ChatAttachment'dan ham imaj bayt'larını media dizininden okur."""
    from agent_app.models import ChatAttachment
    try:
        att_obj = ChatAttachment.objects.filter(id=att.get("id")).first()
    except Exception:
        return None
    if att_obj is None:
        return None
    try:
        path = _safe_media_path(att_obj.file)
        if path and path.is_file():
            return path.read_bytes()
        with att_obj.file.open("rb") as fh:
            return fh.read()
    except Exception:
        return None


def _conversation_latest_image_atts(conversation, limit=5):
    """Sohbet geçmişindeki en son imaj eklerini döner (meta_data'tan)."""
    out = []
    if conversation is None:
        return out
    try:
        nodes = conversation.nodes.order_by("-timestamp")[:80]
    except Exception:
        return out
    for node in nodes:
        meta = getattr(node, "meta_data", None) or {}
        atts = meta.get("attachments") or []
        for att in atts:
            if isinstance(att, dict) and att.get("kind") == "image" and att.get("id"):
                out.append(att)
                if len(out) >= limit:
                    return out
    return out


def _user_latest_image_atts(user, limit=5):
    """Kullanicinin eklerindeki (sohbetlerindeki) en son imaj eklerini döner."""
    if user is None:
        return []
    try:
        from agent_app.models import ChatAttachment
        out = []
        for a in ChatAttachment.objects.filter(user=user, kind="image").order_by("-created_at")[:limit]:
            d = attachment_to_dict(a)
            if d.get("kind") == "image" and d.get("id"):
                out.append(d)
        return out
    except Exception:
        return []


def _pil_summary_for_attachment(att):
    """Vision model yoksa deterministik, ayrıntılı PIL analizi özeti."""
    import io
    import math
    try:
        data = _attachment_image_bytes(att)
        if not data:
            return ""
        from collections import Counter
        from PIL import Image, ImageStat

        with Image.open(io.BytesIO(data)) as source:
            source.load()
            fmt = source.format or "?"
            mode = source.mode or "?"
            w, h = source.size
            rgb_image = source.convert("RGB")
            grayscale = source.convert("L")

        total = w * h or 1
        raw_rgb = rgb_image.tobytes()
        counts = Counter(zip(raw_rgb[0::3], raw_rgb[1::3], raw_rgb[2::3]))
        top = counts.most_common(6)
        colors = ", ".join(
            f"rgb({red},{green},{blue}):{count * 100 // total}%"
            for (red, green, blue), count in top
        )
        brightness = round(ImageStat.Stat(grayscale).mean[0])
        colorfulness = (
            "renkli"
            if any(red != green or green != blue for (red, green, blue) in counts)
            else "siyah-beyaz/gri-ton"
        )
        divisor = math.gcd(w, h) or 1
        aspect_ratio = f"{w // divisor}:{h // divisor}"
        if w > h:
            orientation = "geniş (yatay)"
        elif h > w:
            orientation = "yüksek (dikey)"
        else:
            orientation = "kare"

        return (
            f"{fmt}, {w}x{h} px ({total} piksel), {mode} modu, {len(data)} bayt. "
            f"En-boy oranı: {aspect_ratio} ({w / h:.2f}), yön: {orientation}. "
            f"Ortalama parlaklık: {brightness}/255. "
            f"Renklilik: {colorfulness}. "
            f"Baskın renkler: {colors}."
        )
    except Exception:
        return ""


def maybe_handle_chat_vision_analysis(conversation, user, message: str, saved_attachments=None):
    # External vision API disabled by default: serve local PIL analysis only
    # (no server load, no external call).
    if not getattr(settings, "VISION_LLM_ENABLED", False):
        image_atts = [
            a for a in (saved_attachments or [])
            if isinstance(a, dict) and a.get("kind") == "image" and a.get("id")
        ]
        if not image_atts and user is not None:
            image_atts = _user_latest_image_atts(user) or _conversation_latest_image_atts(conversation)
        att = image_atts[0] if image_atts else None
        summary = _pil_summary_for_attachment(att) if att else "Görsel dosyası okunamadı."
        return {
            "ok": True,
            "intent": "chat",
            "reply": f"Görsel analizi (yerel PIL, dış API kapalı): {summary}",
            "attachments": image_atts,
            "media": image_atts,
            "analyzed_by": "pil-local",
            "analyzed_count": len(image_atts),
        }
    """
    Kullanici 'bu fotoğrafı analiz et' gibi bir talep + imaj eki ile sohbet ettiğinde:
    Vision destekli LLM ile eki oku ve chat yanıtında analizini açikla.

    Returns: {"ok": True, "reply": str, "attachments": [...], "intent": "chat"} or None.
    """
    norm = _norm_text(message or "")
    image_atts = [
        a for a in (saved_attachments or [])
        if isinstance(a, dict) and a.get("kind") == "image" and a.get("id")
    ]
    if not image_atts and user is not None:
        # Kullanici yeni mesajda yeniden eklemediyse, kullanicinin son eklerindeki en son imajı kullan.
        image_atts = _user_latest_image_atts(user) or _conversation_latest_image_atts(conversation)
    if not image_atts:
        return None
    if not any(k in norm for k in ("analiz", "incele", "tara", "ne gore", "bak")):
        return None

    parts = [{"type": "text", "text": ((message or "").strip() or "Bu fotoğrafı analiz et.")[:2000]}]
    n_images = 0
    for a in image_atts[:5]:
        data = _attachment_image_bytes(a)
        if not data:
            continue
        import base64 as _b64
        b64 = _b64.b64encode(data).decode("ascii")
        mime = str(a.get("mime") or "image/png").split(";")[0] or "image/png"
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        n_images += 1
    if n_images == 0:
        return None

    system = (
        "You analyze user-provided images. Describe the image content concisely and factually: "
        "the main subject, visible text/OCR if any, colors/style, and any notable issues or errors. "
        "If the image shows a specific error message (e.g., SSL/certificate errors), quote it. "
        "Reply in Turkish. Do not refuse."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": parts},
    ]

    from agent_app import views as _v
    reply = ""
    used_model = ""
    seen = set()
    vision_models = [str(m) for m in (getattr(settings, "NVIDIA_VISION_MODELS", None) or [getattr(settings, "NVIDIA_VISION_MODEL", "")])]
    for model in vision_models:
        if not model or model in seen:
            continue
        seen.add(model)
        try:
            resp = _v.llm_create_vision(messages=messages, model=model, temperature=0)
            text = (resp.choices[0].message.content or "").strip()
            if text:
                reply = text
                used_model = model
                break
        except Exception:
            continue

    if not reply:
        seen_s = set()
        summaries = []
        for a in image_atts[:5]:
            s = _pil_summary_for_attachment(a)
            if s and s not in seen_s:
                seen_s.add(s)
                summaries.append(s)
        if summaries:
            reply = "Fotoğraf analizi (PIL): " + " | ".join(summaries[:3])
        else:
            reply = "Fotoğraf analiz edebilmemize yardımcı olabilecek bir dosya olmadı — lütfen fotoğrafı tekrar paylaşın."

    attachments = [a for a in image_atts[:5] if isinstance(a, dict)]
    return {
        "ok": True,
        "intent": "chat",
        "reply": reply,
        "attachments": attachments,
        "media": attachments,
        "analyzed_by": used_model,
        "analyzed_count": n_images,
    }