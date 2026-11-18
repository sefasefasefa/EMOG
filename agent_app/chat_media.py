import mimetypes
import os
from pathlib import Path
from typing import Any, Iterable

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, JsonResponse

from agent_app.models import ChatAttachment, Conversation, Node, User
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
    resp["Content-Disposition"] = f'attachment; filename="{Path(att.name).name}"'
    return resp
