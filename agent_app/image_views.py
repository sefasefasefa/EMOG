"""2D image generation REST API endpoints.

Endpoints:
- POST /api/image/generate/
- GET  /api/image/
- GET  /api/image/<id>/
- GET  /api/image/<id>/download/
"""

import asyncio
import concurrent.futures
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from django.http import FileResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from agent_app import image_generation as ig
from agent_app.models import ImageJob
from agent_app.permissions import current_user, require_feature

logger = logging.getLogger(__name__)


def _json(payload, status=200):
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def _body(request):
    if request.content_type and "json" in (request.content_type or ""):
        try:
            data = json.loads((request.body or b"{}").decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}
    data = {}
    for key in request.POST:
        data[key] = request.POST.get(key)
    return data


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _media_root():
    try:
        return ig._media_root()
    except Exception:
        from django.conf import settings as _s

        return Path(getattr(_s, "MEDIA_ROOT", Path.cwd() / "media"))


def _get_job(user, job_id):
    try:
        return ImageJob.objects.filter(id=job_id, user=user).first()
    except Exception:
        return None


def _safe_relative_path(p):
    if not p:
        return None
    media = _media_root().resolve()
    candidate = Path(str(p))
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            rel = resolved.relative_to(media)
            return str(rel)
        except ValueError:
            return None
    try:
        resolved = (media / candidate).resolve()
        rel = resolved.relative_to(media)
        return str(rel)
    except (OSError, ValueError):
        return None


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


def _serialize_job(job):
    output_path = job.output_path or ""
    download_url = f"/api/image/{job.id}/download/" if output_path else None
    return {
        "ok": True,
        "id": str(job.id),
        "job_id": str(job.id),
        "prompt": job.prompt,
        "mode": job.mode,
        "provider": job.provider,
        "status": job.status,
        "progress": job.progress,
        "width": job.width,
        "height": job.height,
        "format": job.format,
        "fmt": job.format,
        "output_path": output_path,
        "input_path": job.input_path,
        "thumbnail_path": job.thumbnail_path,
        "error": job.error,
        "result": job.result,
        "meta": job.meta,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "is_finished": job.is_finished,
        "download_url": download_url,
    }


def _run_awaitable(result):
    if not hasattr(result, "__await__"):
        return result
    import inspect

    async def _consume(awaitable):
        return await awaitable

    if inspect.iscoroutine(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        with concurrent.futures.ThreadPoolExecutor() as ex:
            fut = ex.submit(asyncio.run, result)
            return fut.result()

    # Awaitable but non-coroutine (e.g. awaitable model wrappers in tests/runtimes)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_consume(result))
    with concurrent.futures.ThreadPoolExecutor() as ex:
        fut = ex.submit(asyncio.run, _consume(result))
        return fut.result()


def _call_generate(**kwargs):
    return _run_awaitable(ig.generate_image(**kwargs))


def _call_run(job_id):
    return _run_awaitable(ig.run_generation(job_id))


@require_feature("image_generation")
@require_http_methods(["GET"])
def image_list(request):
    user = current_user(request)
    if user is None:
        return _json({"ok": False, "error": "Authentication required"}, status=401)

    qs = ImageJob.objects.filter(user=user).order_by("-created_at")
    total = qs.count()

    try:
        limit = int(request.GET.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.GET.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    jobs = [_serialize_job(j) for j in qs[offset : offset + limit]]
    job_payloads = [{k: v for k, v in j.items() if k != "ok"} for j in jobs]
    return _json(
        {
            "ok": True,
            "jobs": job_payloads,
            "images": job_payloads,
            "results": job_payloads,
            "items": job_payloads,
            "data": job_payloads,
            "count": len(job_payloads),
            "total": total,
        }
    )


@require_feature("image_generation")
@require_http_methods(["GET"])
def image_detail(request, job_id):
    user = current_user(request)
    if user is None:
        return _json({"ok": False, "error": "Authentication required"}, status=401)

    job = _get_job(user, job_id)
    if job is None:
        return _json({"ok": False, "error": "Image job not found", "code": "not_found"}, status=404)

    serialized = _serialize_job(job)
    job_payload = {k: v for k, v in serialized.items() if k != "ok"}
    return _json(
        {
            "ok": True,
            "job": job_payload,
            "image": job_payload,
            "data": job_payload,
            "id": str(job.id),
            "status": job.status,
            "progress": job.progress,
            "output_path": job.output_path,
            "download_url": f"/api/image/{job.id}/download/" if job.output_path else None,
        }
    )


@require_feature("image_generation")
@require_http_methods(["POST"])
def image_generate(request):
    user = current_user(request)
    if user is None:
        return _json({"ok": False, "error": "Authentication required"}, status=401)

    body = _body(request)
    prompt = body.get("prompt")
    if prompt is None:
        prompt = request.POST.get("prompt", "")
    prompt = "" if prompt is None else str(prompt)

    mode = str(body.get("mode") or request.POST.get("mode") or "generate").strip().lower()
    if mode not in ("generate", "edit"):
        mode = "generate"

    width = body.get("width")
    height = body.get("height")
    if width is None:
        width = request.POST.get("width")
    if height is None:
        height = request.POST.get("height")
    if width is None and body.get("size"):
        # allow size like "512x512"
        size = str(body.get("size"))
        if "x" in size:
            try:
                w_s, h_s = size.lower().split("x", 1)
                width = width or w_s
                height = height or h_s
            except Exception:
                pass

    fmt = body.get("format", body.get("fmt"))
    if fmt is None:
        fmt = request.POST.get("format")
    provider = body.get("provider")
    input_path = body.get("input_path", body.get("input"))

    kwargs = {}
    if input_path:
        safe = _safe_relative_path(input_path)
        if safe is None:
            return _json({"ok": False, "error": "Invalid input path", "code": "invalid_path"}, status=400)
        kwargs["input_path"] = safe

    try:
        job = _call_generate(
            prompt=prompt,
            user=user,
            width=width if width is not None else 1024,
            height=height if height is not None else 1024,
            fmt=fmt if fmt else "png",
            mode=mode,
            provider=provider,
            **kwargs,
        )
    except Exception as exc:
        logger.exception("Image generation create failed")
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}", "code": "create_failed"}, status=500)

    if not isinstance(job, ImageJob):
        try:
            job = ImageJob.objects.get(id=getattr(job, "id", job))
        except Exception:
            pass

    # Run: sync by default for image jobs; background if explicitly requested.
    sync_requested = str(body.get("sync", request.GET.get("sync", ""))).lower() in ("1", "true", "yes")
    background_requested = str(body.get("background", request.GET.get("background", ""))).lower() in ("1", "true", "yes")
    run_sync = sync_requested or not background_requested

    run_error = None
    try:
        if run_sync:
            job = _call_run(str(job.id))
            try:
                job.refresh_from_db()
            except Exception:
                pass
        else:
            ig.run_generation_background(str(job.id))
    except Exception as exc:
        logger.exception("Image generation run failed")
        run_error = f"{type(exc).__name__}: {exc}"
        try:
            job.refresh_from_db()
        except Exception:
            pass

    serialized = _serialize_job(job)
    job_payload = {k: v for k, v in serialized.items() if k != "ok"}

    if run_sync and getattr(job, "status", None) != "completed":
        error_msg = run_error or getattr(job, "error", "") or "Image generation failed"
        return _json(
            {
                "ok": False,
                "error": error_msg,
                "code": "generation_failed",
                "job": job_payload,
                "image": job_payload,
                "id": str(job.id),
                "status": getattr(job, "status", None),
            },
            status=500,
        )

    return _json(
        {
            "ok": True,
            "job": job_payload,
            "image": job_payload,
            "id": str(job.id),
            "status": job.status,
        },
        status=200,
    )


@require_feature("image_generation")
@require_http_methods(["GET"])
def image_download(request, job_id):
    user = current_user(request)
    if user is None:
        return _json({"ok": False, "error": "Authentication required"}, status=401)

    job = _get_job(user, job_id)
    if job is None:
        return _json({"ok": False, "error": "Image job not found", "code": "not_found"}, status=404)

    if not job.output_path:
        return _json({"ok": False, "error": "Output not ready", "code": "not_ready", "status": job.status}, status=404)

    safe = _safe_relative_path(job.output_path)
    if safe is None:
        return _json({"ok": False, "error": "Invalid output path", "code": "invalid_path"}, status=403)

    path = (_media_root() / safe).resolve()
    media = _media_root().resolve()
    try:
        path.relative_to(media)
    except ValueError:
        return _json({"ok": False, "error": "Path traversal blocked", "code": "traversal"}, status=403)

    if not path.is_file():
        return _json({"ok": False, "error": "File not found", "code": "file_not_found"}, status=404)

    content_type = "application/octet-stream"
    suffix = path.suffix.lower()
    if suffix == ".png":
        content_type = "image/png"
    elif suffix in (".jpg", ".jpeg"):
        content_type = "image/jpeg"
    elif suffix == ".webp":
        content_type = "image/webp"
    elif suffix == ".gif":
        content_type = "image/gif"

    try:
        fh = open(path, "rb")
    except OSError:
        return _json({"ok": False, "error": "Cannot open file", "code": "io_error"}, status=500)

    response = FileResponse(fh, content_type=content_type)
    response["Content-Disposition"] = _content_disposition_attachment(path.name)
    return response
