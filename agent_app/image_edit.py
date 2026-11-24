"""2D görsel düzenleme servisi (PIL tabanlı).

Desteklenen işlemler:
- format: format dönüştürme (png, jpg, webp)
- resize: yeniden boyutlandırma
- crop: kırpma (sol/üst/x, y, genişlik, yükseklik)
- rotate: döndürme (derece)
- grayscale: siyah-beyaz
- brightness: parlaklık ayarı (-100..100)
- contrast: kontrast ayarı (-100..100)
- watermark: üzerine yazı ekle
- compress: sıkıştırma / kalite düşürme (kalite 1..100)
- resolution: çözünürlük düşürme (yeniden boyutlandırma eş değildir, dpi/boyut)

Tüm işlemler yerel PIL ile yapılır; harici bağımlılık yok.
"""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageEnhance, ImageOps, ImageDraw, ImageFont
except Exception as _exc:  # pragma: no cover - PIL yoksa graceful
    Image = None  # type: ignore
    ImageEnhance = None  # type: ignore
    ImageOps = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    logger.warning("PIL/Pillow import edilemedi: %s", _exc)

# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = {"png", "jpg", "jpeg", "webp", "bmp", "gif"}

SUPPORTED_OPERATIONS = [
    {
        "id": "format",
        "label": "Format Dönüştürme",
        "description": "Görseli başka bir biçime dönüştür (png, jpg, webp, bmp, gif).",
        "params": {"format": "hedef format (png/jpg/webp/bmp/gif)"},
    },
    {
        "id": "resize",
        "label": "Yeniden Boyutlandırma",
        "description": "Görseli belirtilen genişlik/yükseklik'e getir.",
        "params": {"width": "genişlik (px)", "height": "yükseklik (px)"},
    },
    {
        "id": "crop",
        "label": "Kırpma",
        "description": "Belirli bir bölgeyi kırp (sol, üst, genişlik, yükseklik).",
        "params": {"left": "sol (px)", "top": "üst (px)", "width": "genişlik (px)", "height": "yükseklik (px)"},
    },
    {
        "id": "rotate",
        "label": "Döndürme",
        "description": "Görseli belirtilen derece döndür.",
        "params": {"degrees": "derece (örn. 90, 180, 270)"},
    },
    {
        "id": "grayscale",
        "label": "Siyah-Beyaz",
        "description": "Görseli gri tonlamalı yapar.",
        "params": {},
    },
    {
        "id": "brightness",
        "label": "Parlaklık",
        "description": "Parlaklığı ayarla (-100 koyu, +100 açık).",
        "params": {"value": "değer (-100..100)"},
    },
    {
        "id": "contrast",
        "label": "Kontrast",
        "description": "Kontrastı ayarla (-100 düşük, +100 yüksek).",
        "params": {"value": "değer (-100..100)"},
    },
    {
        "id": "watermark",
        "label": "Filigran / Yazı Üzerine Ekle",
        "description": "Görsele metin filigranı ekler.",
        "params": {"text": "eklenecek metin", "x": "sol konum (px)", "y": "üst konum (px)"},
    },
    {
        "id": "compress",
        "label": "Sıkıştırma / Kalite",
        "description": "JPEG/WebP kalitesini düşürerek dosya boyutunu küçültür (1..100).",
        "params": {"quality": "kalite (1..100)"},
    },
    {
        "id": "resolution",
        "label": "Çözünürlük Düşürme",
        "description": "Görsel boyutunu yüzde olarak düşürür (örn. %50).",
        "params": {"percent": "yüzde (1..100)"},
    },
]


def _is_available() -> bool:
    return Image is not None


def _open_image(path: str) -> Optional[Image.Image]:
    if not _is_available():
        return None
    try:
        return Image.open(path)
    except Exception as exc:
        logger.warning("Görsel açılamadı %s: %s", path, exc)
        return None


def _pil_format(fmt: str) -> str:
    f = (fmt or "png").strip().lower()
    if f == "jpeg":
        f = "jpg"
    if f == "jpg":
        return "JPEG"
    if f == "png":
        return "PNG"
    if f == "webp":
        return "WEBP"
    if f == "bmp":
        return "BMP"
    if f == "gif":
        return "GIF"
    return "PNG"


def _save_path(user_id: str, original_name: str, operation: str, fmt: str) -> Tuple[Path, str]:
    """Çıktı dosya yolu ve relative path döndürür."""
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
    out_dir = media_root / "images" / "edits"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(original_name or "image").stem or "edit"
    safe_stem = "".join(c for c in stem if c.isalnum() or c in ("-", "_"))[:60] or "edit"
    ext = fmt if fmt in ("png", "jpg", "jpeg", "webp", "bmp", "gif") else "png"
    if ext == "jpeg":
        ext = "jpg"
    filename = f"{uuid.uuid4().hex[:12]}_{operation}_{safe_stem}.{ext}"
    out_path = out_dir / filename
    rel_path = str(Path("images") / "edits" / filename)
    return out_path, rel_path


def _apply_operation(img: Image.Image, operation: str, params: Dict[str, Any]) -> Image.Image:
    """Tek bir PIL işlemi uygular."""
    op = (operation or "").strip().lower()

    if op == "format":
        # Format dönüşümü kaydetmede handled; burada sadece mode uyarlaması
        mode = img.mode
        if params.get("format", "png").lower() in ("jpg", "jpeg") and mode == "RGBA":
            # JPEG alpha kanalını desteklemez; beyaz arka plana birleştir
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            return bg
        if mode not in ("RGB", "L", "1"):
            return img.convert("RGB")
        return img

    if op == "resize":
        w = int(params.get("width") or img.width)
        h = int(params.get("height") or img.height)
        return img.resize((w, h), Image.LANCZOS)

    if op == "crop":
        left = int(params.get("left") or 0)
        top = int(params.get("top") or 0)
        w = int(params.get("width") or (img.width - left))
        h = int(params.get("height") or (img.height - top))
        right = min(left + w, img.width)
        bottom = min(top + h, img.height)
        left = max(0, min(left, img.width))
        top = max(0, min(top, img.height))
        if right <= left or bottom <= top:
            return img
        return img.crop((left, top, right, bottom))

    if op == "rotate":
        degrees = float(params.get("degrees") or 0)
        expand = bool(params.get("expand", True))
        return img.rotate(degrees, expand=expand, resample=Image.BICUBIC)

    if op == "grayscale":
        return ImageOps.grayscale(img)

    if op == "brightness":
        value = float(params.get("value") or 0)
        # -100..100 -> 0.0..2.0 (0 = siyah, 1 = orijinal, 2 = 2x açık)
        factor = 1.0 + (value / 100.0)
        factor = max(0.0, min(2.0, factor))
        return ImageEnhance.Brightness(img).enhance(factor)

    if op == "contrast":
        value = float(params.get("value") or 0)
        factor = 1.0 + (value / 100.0)
        factor = max(0.0, min(3.0, factor))
        return ImageEnhance.Contrast(img).enhance(factor)

    if op == "watermark":
        text = str(params.get("text") or "EMOG")
        x = int(params.get("x") or 10)
        y = int(params.get("y") or 10)
        img = img.convert("RGBA")
        txt = Image.new("RGBA", img.size, (255, 255, 255, 0))
        d = ImageDraw.Draw(txt)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        try:
            bbox = d.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw = len(text) * 6
            th = 10
        # Kenarlardan taşmasın
        x = max(0, min(x, max(0, img.width - tw - 4)))
        y = max(0, min(y, max(0, img.height - th - 4)))
        d.text((x, y), text, fill=(255, 255, 255, 128), font=font)
        return Image.alpha_composite(img, txt)

    if op == "compress":
        # Kalite dönüşümü kaydetmede handled; burada sadece mode uyarlaması
        mode = img.mode
        if params.get("format", "jpg").lower() in ("jpg", "jpeg") and mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            return bg
        if mode not in ("RGB", "L", "1"):
            return img.convert("RGB")
        return img

    if op == "resolution":
        percent = float(params.get("percent") or 100)
        percent = max(1, min(100, percent))
        new_w = max(1, int(img.width * percent / 100.0))
        new_h = max(1, int(img.height * percent / 100.0))
        return img.resize((new_w, new_h), Image.LANCZOS)

    return img


def edit_image(
    input_path: str,
    operation: str,
    params: Optional[Dict[str, Any]] = None,
    output_format: Optional[str] = None,
    quality: int = 85,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Tek bir görseli düzenler ve çıktı dosyasını kaydeder.

    Returns:
        dict: {"ok": bool, "output_path": str|None, "relative_output": str|None,
               "width": int, "height": int, "format": str, "error": str|None, ...}
    """
    if not _is_available():
        return {"ok": False, "error": "PIL/Pillow yüklü değil", "code": "pil_not_available"}

    params = dict(params or {})
    in_path = Path(input_path)
    if not in_path.is_absolute():
        from django.conf import settings

        media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
        in_path = (media_root / input_path).resolve()

    if not in_path.is_file():
        return {"ok": False, "error": "Girdi dosyası bulunamadı", "code": "file_not_found"}

    img = _open_image(str(in_path))
    if img is None:
        return {"ok": False, "error": "Görsel açılamadı", "code": "open_failed"}

    orig_w, orig_h = img.size
    original_name = in_path.name

    # İşlemi uygula
    try:
        img = _apply_operation(img, operation, params)
    except Exception as exc:
        logger.exception("İşlem uygulanamadı %s", operation)
        return {"ok": False, "error": f"İşlem hatası: {exc}", "code": "operation_failed"}

    # Format belirleme
    fmt = (output_format or params.get("format") or in_path.suffix.lstrip(".") or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in SUPPORTED_FORMATS:
        fmt = "png"

    out_path, rel_path = _save_path(user_id or "anon", original_name, operation, fmt)

    # Kaydet
    pil_fmt = _pil_format(fmt)
    save_kwargs: Dict[str, Any] = {}
    if pil_fmt == "JPEG":
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode not in ("RGB", "L", "1"):
            img = img.convert("RGB")
        save_kwargs["quality"] = max(1, min(100, int(params.get("quality", quality))))
        save_kwargs["optimize"] = True
    elif pil_fmt == "PNG":
        save_kwargs["optimize"] = True
    elif pil_fmt == "WEBP":
        save_kwargs["quality"] = max(1, min(100, int(params.get("quality", quality))))

    try:
        img.save(str(out_path), format=pil_fmt, **save_kwargs)
    except Exception as exc:
        logger.exception("Görsel kaydedilemedi")
        return {"ok": False, "error": f"Kaydetme hatası: {exc}", "code": "save_failed"}

    return {
        "ok": True,
        "output": str(out_path),
        "output_path": str(out_path),
        "relative_output": rel_path,
        "width": img.width,
        "height": img.height,
        "format": fmt,
        "operation": operation,
        "size": out_path.stat().st_size if out_path.is_file() else 0,
        "original": {"width": orig_w, "height": orig_h, "name": original_name},
    }


def edit_image_multi(
    input_path: str,
    operations: list,
    output_format: Optional[str] = None,
    quality: int = 85,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Birden fazla işlemi sırayla uygular."""
    if not _is_available():
        return {"ok": False, "error": "PIL/Pillow yüklü değil", "code": "pil_not_available"}

    in_path = Path(input_path)
    if not in_path.is_absolute():
        from django.conf import settings

        media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
        in_path = (media_root / input_path).resolve()

    if not in_path.is_file():
        return {"ok": False, "error": "Girdi dosyası bulunamadı", "code": "file_not_found"}

    img = _open_image(str(in_path))
    if img is None:
        return {"ok": False, "error": "Görsel açılamadı", "code": "open_failed"}

    original_name = in_path.name
    applied = []

    for step in (operations or []):
        op = str(step.get("operation") or step.get("op") or "").strip()
        params = dict(step.get("params") or {})
        if not op:
            continue
        try:
            img = _apply_operation(img, op, params)
            applied.append({"operation": op, "params": params, "ok": True})
        except Exception as exc:
            logger.exception("Çoklu işlem hatası %s", op)
            applied.append({"operation": op, "params": params, "ok": False, "error": str(exc)})

    fmt = (output_format or in_path.suffix.lstrip(".") or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in SUPPORTED_FORMATS:
        fmt = "png"

    out_path, rel_path = _save_path(user_id or "anon", original_name, "multi", fmt)

    pil_fmt = _pil_format(fmt)
    save_kwargs: Dict[str, Any] = {}
    if pil_fmt == "JPEG":
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode not in ("RGB", "L", "1"):
            img = img.convert("RGB")
        save_kwargs["quality"] = max(1, min(100, quality))
        save_kwargs["optimize"] = True
    elif pil_fmt == "PNG":
        save_kwargs["optimize"] = True
    elif pil_fmt == "WEBP":
        save_kwargs["quality"] = max(1, min(100, quality))

    try:
        img.save(str(out_path), format=pil_fmt, **save_kwargs)
    except Exception as exc:
        logger.exception("Çoklu işlem kaydedilemedi")
        return {"ok": False, "error": f"Kaydetme hatası: {exc}", "code": "save_failed", "applied": applied}

    return {
        "ok": True,
        "output": str(out_path),
        "output_path": str(out_path),
        "relative_output": rel_path,
        "width": img.width,
        "height": img.height,
        "format": fmt,
        "operation": "multi",
        "size": out_path.stat().st_size if out_path.is_file() else 0,
        "applied": applied,
    }


def list_operations() -> list:
    return list(SUPPORTED_OPERATIONS)
