"""2D görsel üretim servisi.

Sağlayıcı soyutlaması (OpenAI, Stability, Replicate, NVIDIA) ve yerel PIL
fallback. Çevrimdışı modda veya anahtar yokken yalnızca yerel sağlayıcı
kullanılır; harici sağlayıcı hataları graceful biçimde ele alınır.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Django / settings yardımcıları
# ---------------------------------------------------------------------------

try:
    from django.conf import settings as _django_settings
except Exception:  # pragma: no cover - Django olmadan import edilebilsin
    _django_settings = None


def _cfg() -> dict:
    """IMAGE_GENERATION ayarlarını güvenli şekilde oku."""
    cfg = {}
    try:
        if _django_settings is not None:
            raw = getattr(_django_settings, "IMAGE_GENERATION", None)
            if isinstance(raw, dict):
                cfg = dict(raw)
    except Exception:
        cfg = {}

    defaults = {
        "enabled": True,
        "offline": False,
        "output_dir": "images",
        "upload_dir": "uploads",
        "formats": ["png", "jpg", "webp"],
        "default_format": "png",
        "default_width": 1024,
        "default_height": 1024,
        "max_width": 4096,
        "max_height": 4096,
        "min_width": 16,
        "min_height": 16,
        "providers": ["openai", "stability", "replicate", "nvidia", "local"],
        "provider_order": ["openai", "stability", "replicate", "nvidia", "local"],
        "media_root": None,
        "media_url": "/media/",
        "background_jobs": True,
    }
    for key, val in defaults.items():
        cfg.setdefault(key, val)

    if not cfg.get("providers"):
        cfg["providers"] = list(defaults["providers"])
    if not cfg.get("provider_order"):
        cfg["provider_order"] = list(cfg.get("providers") or defaults["providers"])
    if not cfg.get("formats"):
        cfg["formats"] = list(defaults["formats"])
    return cfg


def _setting(name: str, default: Any = None) -> Any:
    try:
        if _django_settings is not None:
            return getattr(_django_settings, name, default)
    except Exception:
        pass
    return default


def _media_root() -> Path:
    cfg = _cfg()
    raw = cfg.get("media_root") or _setting("MEDIA_ROOT")
    if not raw:
        raw = Path.cwd() / "media"
    return Path(raw)


def _output_dir() -> Path:
    cfg = _cfg()
    out = _media_root() / str(cfg.get("output_dir") or "images")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _upload_dir() -> Path:
    cfg = _cfg()
    up = _media_root() / str(cfg.get("upload_dir") or "uploads")
    up.mkdir(parents=True, exist_ok=True)
    return up


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _clamp_size(width, height, cfg: Optional[dict] = None) -> Tuple[int, int]:
    cfg = cfg or _cfg()
    w = _to_int(width, cfg.get("default_width", 1024))
    h = _to_int(height, cfg.get("default_height", 1024))
    min_w = _to_int(cfg.get("min_width", 16), 16)
    min_h = _to_int(cfg.get("min_height", 16), 16)
    max_w = _to_int(cfg.get("max_width", 4096), 4096)
    max_h = _to_int(cfg.get("max_height", 4096), 4096)
    w = max(min_w, min(w, max_w))
    h = max(min_h, min(h, max_h))
    return w, h


# ---------------------------------------------------------------------------
# Kullanıcı normalizasyonu
# ---------------------------------------------------------------------------


def _coerce_user(user):
    if user is None:
        return None
    try:
        from agent_app.models import User as _User

        if isinstance(user, _User):
            return user
    except Exception:
        pass
    try:
        from agent_app.models import User as _User

        if isinstance(user, (int, str, uuid.UUID)):
            try:
                return _User.objects.get(pk=user)
            except Exception:
                return None
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Sağlayıcılar
# ---------------------------------------------------------------------------


class ImageProvider:
    """Tüm 2D görsel sağlayıcıları için temel arayüz."""

    name = "base"
    display_name = "Base"

    def __init__(self, config: Optional[dict] = None):
        self.config = config or _cfg()

    def available(self) -> bool:
        return False

    def generate(self, job) -> dict:
        raise NotImplementedError


class BaseImageProvider(ImageProvider):
    pass


class Provider(ImageProvider):
    pass


class OpenAIProvider(ImageProvider):
    name = "openai"
    display_name = "OpenAI Images"
    ENV_KEYS = ("OPENAI_API_KEY",)

    def available(self) -> bool:
        cfg = self.config or _cfg()
        if cfg.get("offline"):
            return False
        if "openai" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def generate(self, job) -> dict:
        key = next((os.environ.get(k) for k in self.ENV_KEYS if os.environ.get(k)), None)
        if not key:
            raise RuntimeError("OpenAI API anahtarı yok")
        raise RuntimeError("OpenAI image üretimi bu ortamda yapılandırılmadı")


class StabilityProvider(ImageProvider):
    name = "stability"
    display_name = "Stability AI"
    ENV_KEYS = ("STABILITY_API_KEY", "STABILITY_KEY")

    def available(self) -> bool:
        cfg = self.config or _cfg()
        if cfg.get("offline"):
            return False
        if "stability" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def generate(self, job) -> dict:
        key = next((os.environ.get(k) for k in self.ENV_KEYS if os.environ.get(k)), None)
        if not key:
            raise RuntimeError("Stability API anahtarı yok")
        raise RuntimeError("Stability image üretimi bu ortamda yapılandırılmadı")


class ReplicateProvider(ImageProvider):
    name = "replicate"
    display_name = "Replicate"
    ENV_KEYS = ("REPLICATE_API_TOKEN",)

    def available(self) -> bool:
        cfg = self.config or _cfg()
        if cfg.get("offline"):
            return False
        if "replicate" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def generate(self, job) -> dict:
        token = os.environ.get("REPLICATE_API_TOKEN")
        if not token:
            raise RuntimeError("Replicate token yok")
        raise RuntimeError("Replicate image üretimi bu ortamda yapılandırılmadı")


class NvidiaProvider(ImageProvider):
    name = "nvidia"
    display_name = "NVIDIA"
    ENV_KEYS = ("NVIDIA_API_KEY",)

    def available(self) -> bool:
        cfg = self.config or _cfg()
        if cfg.get("offline"):
            return False
        if "nvidia" not in (cfg.get("providers") or []):
            return False
        key = os.environ.get("NVIDIA_API_KEY") or ""
        try:
            omni = _setting("OMNIROUTE", {}) or {}
            if isinstance(omni, dict):
                key = key or str(omni.get("API_KEY") or "")
        except Exception:
            pass
        return bool(str(key).strip())

    def generate(self, job) -> dict:
        # NVIDIA image üretimi yapılandırılmamışsa yerel fallback tercih edilir;
        # doğrudan çağrıldığında graceful hata.
        if not self.available():
            raise RuntimeError("NVIDIA API anahtarı yok")
        raise RuntimeError("NVIDIA image üretimi bu ortamda yapılandırılmadı")


class NVIDIAProvider(NvidiaProvider):
    name = "nvidia"


class LocalProvider(ImageProvider):
    name = "local"
    display_name = "Yerel (PIL)"

    def available(self) -> bool:
        return True

    def generate(self, job) -> dict:
        return _local_generate(job)


# Registry

PROVIDERS: Dict[str, Any] = {}
PROVIDER_REGISTRY: Dict[str, Any] = {}
PROVIDER_CLASSES: Dict[str, type] = {
    "openai": OpenAIProvider,
    "stability": StabilityProvider,
    "replicate": ReplicateProvider,
    "nvidia": NvidiaProvider,
    "local": LocalProvider,
}
# Aliaslar
PROVIDER_CLASSES.update(
    {
        "openai-images": OpenAIProvider,
        "stability-ai": StabilityProvider,
        "replicate-ai": ReplicateProvider,
        "nvidia-ai": NvidiaProvider,
        "pil": LocalProvider,
    }
)


def get_provider_class(name: str) -> Optional[type]:
    key = (name or "").strip().lower()
    return PROVIDER_CLASSES.get(key)


def get_provider(name: Optional[str] = None, config: Optional[dict] = None) -> ImageProvider:
    """Sağlayıcı örneği döndür."""
    cfg = config or _cfg()
    if not name:
        name = _select_provider_name(cfg)
    cls = get_provider_class(name) or LocalProvider
    try:
        return cls(cfg)
    except Exception:
        return LocalProvider(cfg)


def _select_provider_name(cfg: Optional[dict] = None, preferred: Optional[str] = None) -> str:
    cfg = cfg or _cfg()
    if cfg.get("offline"):
        return "local"
    if preferred:
        key = str(preferred).strip().lower()
        cls = get_provider_class(key)
        if cls is not None:
            try:
                if cls(cfg).available():
                    return key
            except Exception:
                pass
    order = [str(n).strip().lower() for n in (cfg.get("provider_order") or cfg.get("providers") or [])]
    for key in order:
        cls = get_provider_class(key)
        if cls is None:
            continue
        try:
            if cls(cfg).available():
                return key
        except Exception:
            continue
    return "local"


def available_providers(config: Optional[dict] = None) -> List[str]:
    cfg = config or _cfg()
    out: List[str] = []
    for name, cls in PROVIDER_CLASSES.items():
        if name in ("openai-images", "stability-ai", "replicate-ai", "nvidia-ai", "pil"):
            continue
        try:
            if cls(cfg).available():
                out.append(name)
        except Exception:
            continue
    if "local" not in out:
        out.append("local")
    seen = set()
    res = []
    for n in (cfg.get("provider_order") or cfg.get("providers") or []) + ["local"]:
        if n in out and n not in seen:
            seen.add(n)
            res.append(n)
    for n in out:
        if n not in seen:
            seen.add(n)
            res.append(n)
    return res


def list_providers(config: Optional[dict] = None):
    return available_providers(config)


def provider_available(name, config: Optional[dict] = None) -> bool:
    cfg = config or _cfg()
    cls = get_provider_class(name)
    if cls is None:
        return str(name).lower() == "local"
    try:
        return bool(cls(cfg).available())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Yerel üretim (PIL)
# ---------------------------------------------------------------------------


def _color_for_prompt(prompt: str) -> Tuple[int, int, int]:
    h = hashlib.md5((prompt or "").encode("utf-8", "replace")).hexdigest()
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _local_generate(job) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    cfg = _cfg()
    w = _to_int(getattr(job, "width", None), cfg.get("default_width", 1024))
    h = _to_int(getattr(job, "height", None), cfg.get("default_height", 1024))
    w, h = _clamp_size(w, h, cfg)

    fmt = (getattr(job, "format", None) or "png").lower()
    if fmt == "jpeg":
        fmt = "jpg"
    formats = [str(f).lower() for f in (cfg.get("formats") or ["png", "jpg", "webp"])]
    if fmt not in formats:
        fmt = "png"

    prompt = (getattr(job, "prompt", "") or "").strip() or "Image"
    base = _color_for_prompt(prompt)
    img = Image.new("RGB", (w, h), base)
    draw = ImageDraw.Draw(img)

    title = prompt[:120]
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    margin = max(8, min(w, h) // 24)
    max_text_w = max(1, w - 2 * margin)
    # Basit satır sarma
    words = title.split() or [title]
    lines: List[str] = []
    cur = ""
    for word in words:
        cand = (cur + " " + word).strip()
        try:
            bbox = draw.textbbox((0, 0), cand, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(cand) * 6
        if tw <= max_text_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    lines = lines[:6] if lines else [title[:40]]

    try:
        line_h = (draw.textbbox((0, 0), "Ag", font=font)[3] - draw.textbbox((0, 0), "Ag", font=font)[1]) + 4
    except Exception:
        line_h = 14
    total_h = line_h * len(lines)
    y = max(margin, (h - total_h) // 2)
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(line) * 6
        x = max(margin, (w - tw) // 2)
        draw.text((x + 1, y + 1), line, fill=(0, 0, 0), font=font)
        draw.text((x, y), line, fill=(255, 255, 255), font=font)
        y += line_h

    out_dir = _output_dir()
    filename = f"{job.id}.{fmt}"
    out_path = out_dir / filename
    rel_path = str(Path(cfg.get("output_dir") or "images") / filename)

    save_fmt = "JPEG" if fmt == "jpg" else fmt.upper()
    save_kwargs = {}
    if fmt == "jpg":
        save_kwargs["quality"] = 90
        img_to_save = img
    elif fmt == "png":
        save_kwargs["optimize"] = False
        img_to_save = img
    else:  # webp
        img_to_save = img
    img_to_save.save(str(out_path), format=save_fmt, **save_kwargs)

    # Thumbnail (opsiyonel)
    thumb_rel = ""
    try:
        thumb_name = f"{job.id}_thumb.jpg"
        thumb_path = out_dir / thumb_name
        thumb = img.copy()
        thumb.thumbnail((256, 256))
        thumb.save(str(thumb_path), format="JPEG", quality=85)
        thumb_rel = str(Path(cfg.get("output_dir") or "images") / thumb_name)
    except Exception:
        thumb_rel = ""

    result = {
        "provider": "local",
        "output": str(out_path),
        "relative_output": rel_path,
        "width": w,
        "height": h,
        "format": fmt,
    }
    if thumb_rel:
        result["thumbnail"] = thumb_rel
    return result


# ---------------------------------------------------------------------------
# Job işlemleri
# ---------------------------------------------------------------------------


def _fetch_job(job_id):
    from agent_app.models import ImageJob

    if isinstance(job_id, ImageJob):
        return job_id
    try:
        return ImageJob.objects.get(pk=job_id)
    except Exception:
        return ImageJob.objects.get(id=job_id)


def _patch_model_awaitable():
    try:
        from agent_app.models import ImageJob as _IJ

        if not getattr(_IJ, "_ig_awaitable", False):

            def _await(self):
                async def _coro():
                    return self

                return _coro().__await__()

            _IJ.__await__ = _await
            _IJ._ig_awaitable = True
    except Exception:
        pass


_patch_model_awaitable()


def _generate_image_impl(
    prompt,
    user=None,
    width=1024,
    height=1024,
    fmt="png",
    provider=None,
    mode="generate",
    **kwargs,
):
    """ImageJob oluşturur ve döndürür (üretim run_generation ile)."""
    from agent_app.models import ImageJob

    cfg = _cfg()
    user_obj = _coerce_user(user)
    w, h = _clamp_size(width, height, cfg)
    fmt_norm = (fmt or cfg.get("default_format") or "png").lower()
    if fmt_norm == "jpeg":
        fmt_norm = "jpg"

    if provider:
        prov_name = str(provider).strip().lower()
    else:
        prov_name = _select_provider_name(cfg)

    job = ImageJob.objects.create(
        user=user_obj,
        prompt=prompt or "",
        mode=mode or "generate",
        provider=prov_name,
        status="pending",
        progress=0,
        width=w,
        height=h,
        format=fmt_norm,
    )
    if "input_path" in kwargs and kwargs["input_path"]:
        job.input_path = str(kwargs["input_path"])
    if "meta" in kwargs and isinstance(kwargs["meta"], dict):
        job.meta = kwargs["meta"]
    elif kwargs:
        meta = job.meta or {}
        extra = {k: v for k, v in kwargs.items() if k not in ("input_path",)}
        if extra:
            meta = {**meta, **extra}
            job.meta = meta
    if job.input_path or job.meta:
        job.save()
    return job


def _run_generation_impl(job_id, background: bool = False, **kwargs):
    """Üretimi çalıştırır (senkron)."""
    if background:
        t = threading.Thread(
            target=_run_generation_impl,
            args=(job_id,),
            kwargs={k: v for k, v in kwargs.items() if k != "background"},
            daemon=True,
        )
        t.start()
        try:
            return _fetch_job(job_id)
        except Exception:
            return t

    from django.utils import timezone

    job = _fetch_job(job_id)
    job.status = "running"
    job.progress = 5
    job.started_at = timezone.now()
    job.save(update_fields=["status", "progress", "started_at", "updated_at"])
    try:
        prov_name = (job.provider or _select_provider_name(_cfg())).lower()
        provider = get_provider(prov_name)
        if not provider.available():
            provider = get_provider("local")
            prov_name = "local"
        job.provider = prov_name
        job.save(update_fields=["provider", "updated_at"])
        try:
            result = provider.generate(job)
        except Exception as _exc:
            logger.warning("Görsel sağlayıcı başarısız, yerel fallback: %s", _exc)
            provider = get_provider("local")
            prov_name = "local"
            job.provider = prov_name
            job.save(update_fields=["provider", "updated_at"])
            result = provider.generate(job)

        if isinstance(result, dict):
            job.result = result
            out = result.get("relative_output") or result.get("output") or result.get("path")
            if out:
                p = Path(str(out))
                if p.is_absolute():
                    try:
                        p = p.relative_to(_media_root())
                    except Exception:
                        p = Path(str(out))
                job.output_path = str(p)
            if result.get("relative_output"):
                job.output_path = str(result["relative_output"])
            thumb = result.get("thumbnail") or result.get("thumbnail_path")
            if thumb:
                tp = Path(str(thumb))
                if tp.is_absolute():
                    try:
                        tp = tp.relative_to(_media_root())
                    except Exception:
                        tp = Path(str(thumb))
                job.thumbnail_path = str(tp)

        job.status = "completed"
        job.progress = 100
        job.finished_at = timezone.now()
        job.error = ""
        job.save()
        return job
    except Exception as exc:
        logger.exception("Image üretimi başarısız")
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        try:
            job.finished_at = timezone.now()
        except Exception:
            pass
        job.progress = 100
        try:
            job.save()
        except Exception:
            pass
        return job


def run_generation_background(job_id, **kwargs):
    t = threading.Thread(target=_run_generation_impl, args=(job_id,), kwargs=kwargs, daemon=True)
    t.start()
    return t


async def run_generation_async(job_id, **kwargs):
    return await asyncio.to_thread(_run_generation_impl, job_id, **kwargs)


# Hibrit (sync/async) public arayüz
# ---------------------------------------------------------------------------


def generate_image(*a, **k):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():

        async def _coro():
            return await asyncio.to_thread(_generate_image_impl, *a, **k)

        return _coro()
    return _generate_image_impl(*a, **k)


def run_generation(*a, **k):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():

        async def _coro():
            return await asyncio.to_thread(_run_generation_impl, *a, **k)

        return _coro()
    return _run_generation_impl(*a, **k)


# Uyumluluk takma adları
generate = generate_image
run = run_generation
create_image = _generate_image_impl
create_image_job = _generate_image_impl

__all__ = [
    "generate_image",
    "run_generation",
    "run_generation_background",
    "run_generation_async",
    "_generate_image_impl",
    "_run_generation_impl",
    "available_providers",
    "get_provider",
    "get_provider_class",
    "ImageProvider",
    "BaseImageProvider",
    "Provider",
    "OpenAIProvider",
    "StabilityProvider",
    "ReplicateProvider",
    "NvidiaProvider",
    "NVIDIAProvider",
    "LocalProvider",
    "PROVIDERS",
    "PROVIDER_REGISTRY",
    "PROVIDER_CLASSES",
]

# Registry doldur
for _name, _cls in PROVIDER_CLASSES.items():
    PROVIDERS[_name] = _cls
    PROVIDER_REGISTRY[_name] = _cls
