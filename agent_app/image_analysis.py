"""Görsel analiz ve düzenleme araçları (model bağlantı katmanı).

Bu modül, EMOG modelinin görsel üzerinde çalışmasını sağlar:
- OCR: görseldeki yazıları düz metin olarak okur (Tesseract / PaddleOCR)
- Nesne/sahne tespiti: YOLOv8 / Florence-2 / BLIP ile JSON koordinat çıkışı
- Piksel/renk analizi: OpenCV/Pillow ile baskın renk (HEX), keskinlik, koordinatlar
- Düzenleme/kesme: FFmpeg/ImageMagick ile koordinat bazlı crop ve işlemler

Tüm araç opsiyoneldir; yoksa net kurulum talimatı döner.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Araç varlık kontrolü
# ---------------------------------------------------------------------------

TESSERACT_BIN = shutil.which("tesseract")
IMAGEMAGICK_BIN = shutil.which("magick")  # ImageMagick v7 uses 'magick'; Windows 'convert.exe' is NOT ImageMagick
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"


def _check_bin(name: str) -> bool:
    return shutil.which(name) is not None


def _check_module(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def tools_status() -> dict:
    """Hangi görsel analiz/düzenleme araçları mevcut?"""
    status = {
        "tesseract": bool(TESSERACT_BIN),
        "pytesseract": _check_module("pytesseract"),
        "paddleocr": _check_module("paddleocr"),
        "cv2": _check_module("cv2"),
        "PIL": _check_module("PIL"),
        "ultralytics": _check_module("ultralytics"),
        "transformers": _check_module("transformers"),
        "ffmpeg": _check_bin("ffmpeg"),
        "imagemagick": bool(IMAGEMAGICK_BIN),
    }
    status["ocr_available"] = status["tesseract"] and (
        status["pytesseract"] or status["paddleocr"]
    )
    status["object_detect_available"] = status["ultralytics"] or status["transformers"]
    status["color_analysis_available"] = status["cv2"] or status["PIL"]
    status["edit_available"] = status["ffmpeg"] or bool(IMAGEMAGICK_BIN)
    return status


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

from django.conf import settings  # type: ignore


def _media_root() -> Path:
    return Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()


def _resolve_path(input_path: str) -> Optional[Path]:
    """Media köküne göre güvenli dosya yolu çöz."""
    media_root = _media_root()
    p = Path(input_path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    try:
        p.relative_to(media_root)
    except ValueError:
        return None
    return p


def _output_path(user_id: str, original_name: str, operation: str, ext: str) -> Tuple[Path, str]:
    media_root = _media_root()
    out_dir = media_root / "images" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name or "image").stem or "edit"
    safe_stem = "".join(c for c in stem if c.isalnum() or c in ("-", "_"))[:60] or "edit"
    if ext == "jpeg":
        ext = "jpg"
    filename = f"{uuid.uuid4().hex[:12]}_{operation}_{safe_stem}.{ext}"
    out_path = out_dir / filename
    rel_path = str(Path("images") / "analysis" / filename)
    return out_path, rel_path


def _open_image(path: Path):
    from PIL import Image  # type: ignore
    return Image.open(path)


# ---------------------------------------------------------------------------
# 1. OCR - Görseldeki yazıları okuma
# ---------------------------------------------------------------------------

def ocr_image(input_path: str, lang: str = "tur+eng") -> Dict[str, Any]:
    """Görseldeki tüm metni düz string olarak döner."""
    status = tools_status()
    if not status["ocr_available"]:
        return {
            "ok": False,
            "error": (
                "OCR aracı yok. Kurulum: "
                "pip install pytesseract paddleocr  ve sistemde tesseract-ocr kurulmalı "
                "(Windows: winget install tesseract)."
            ),
            "code": "ocr_not_available",
        }

    p = _resolve_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    # PaddleOCR öncelik
    if status["paddleocr"]:
        try:
            from paddleocr import PaddleOCR  # type: ignore
            ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            result = ocr.ocr(str(p), cls=True)
            lines = []
            for page in result or []:
                for line in page or []:
                    if line and len(line) >= 2:
                        lines.append(line[1][0])
            text = "\n".join(lines)
            return {
                "ok": True,
                "text": text,
                "engine": "paddleocr",
                "lang": lang,
                "line_count": len(lines),
            }
        except Exception as exc:
            logger.exception("PaddleOCR failed")
            # tesseract'e düş

    if status["pytesseract"] and TESSERACT_BIN:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_BIN
            img = Image.open(p)
            text = pytesseract.image_to_string(img, lang=lang).strip()
            return {
                "ok": True,
                "text": text,
                "engine": "tesseract",
                "lang": lang,
                "line_count": len([l for l in text.splitlines() if l.strip()]),
            }
        except Exception as exc:
            logger.exception("Tesseract failed")
            return {"ok": False, "error": str(exc), "code": "ocr_failed"}

    return {"ok": False, "error": "OCR motoru çalıştırılamadı", "code": "ocr_engine_failed"}


# ---------------------------------------------------------------------------
# 2. Nesne ve Sahne Tespiti (YOLOv8 / Florence-2 / BLIP)
# ---------------------------------------------------------------------------

def detect_objects(input_path: str, model: str = "yolov8n") -> Dict[str, Any]:
    """Görseldeki nesneleri JSON olarak döner: {nesne, konum [x1,y1,x2,y2], güven}."""
    status = tools_status()
    if not status["object_detect_available"]:
        return {
            "ok": False,
            "error": (
                "Nesne tespiti aracı yok. Kurulum: pip install ultralytics "
                "(YOLOv8) veya pip install transformers (Florence-2/BLIP)."
            ),
            "code": "object_detect_not_available",
        }

    p = _resolve_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    # YOLOv8 (ultralytics)
    if status["ultralytics"]:
        try:
            from ultralytics import YOLO  # type: ignore
            model_name = f"{model}.pt" if not model.endswith(".pt") else model
            yolo = YOLO(model_name)
            results = yolo(str(p))
            detections = []
            for r in results:
                boxes = r.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    xyxy = box.xyxy[0].tolist()
                    detections.append({
                        "nesne": yolo.names[cls],
                        "konum": [round(v, 1) for v in xyxy],
                        "güven": round(conf, 2),
                    })
            return {
                "ok": True,
                "detections": detections,
                "count": len(detections),
                "engine": "yolov8",
                "model": model_name,
            }
        except Exception as exc:
            logger.exception("YOLOv8 failed")

    # Florence-2 / BLIP (transformers)
    if status["transformers"]:
        try:
            from transformers import AutoProcessor, AutoModelForCausalLM  # type: ignore
            from PIL import Image  # type: ignore
            # Florence-2 base
            model_id = "microsoft/Florence-2-base-ft"
            processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=True)
            img = Image.open(p).convert("RGB")
            task = "<OD>"  # object detection
            inputs = processor(text=task, images=img, return_tensors="pt")
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
            )
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            parsed = processor.post_process_generation(
                generated_text, task=task, image_size=(img.width, img.height)
            )
            detections = []
            for label, bbox, conf in zip(
                parsed.get("<OD>", {}).get("labels", []),
                parsed.get("<OD>", {}).get("bboxes", []),
                parsed.get("<OD>", {}).get("bboxes", []),
            ):
                detections.append({
                    "nesne": label,
                    "konum": [round(float(v), 1) for v in bbox],
                    "güven": 0.8,
                })
            return {
                "ok": True,
                "detections": detections,
                "count": len(detections),
                "engine": "florence-2",
                "model": model_id,
            }
        except Exception as exc:
            logger.exception("Florence-2 failed")
            return {"ok": False, "error": str(exc), "code": "object_detect_failed"}

    return {"ok": False, "error": "Nesne tespiti motoru çalıştırılamadı", "code": "object_detect_engine_failed"}


# ---------------------------------------------------------------------------
# 3. Piksel / Renk Analizi (OpenCV / Pillow)
# ---------------------------------------------------------------------------

def analyze_colors(input_path: str) -> Dict[str, Any]:
    """Görselin baskın renklerini (HEX), keskinliğini ve boyutlarını döner."""
    status = tools_status()
    if not status["color_analysis_available"]:
        return {
            "ok": False,
            "error": "Renk analizi aracı yok. Kurulum: pip install pillow opencv-python",
            "code": "color_analysis_not_available",
        }

    p = _resolve_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    try:
        from PIL import Image, ImageStat  # type: ignore
        import io  # noqa

        img = Image.open(p)
        img_rgb = img.convert("RGB")
        w, h = img_rgb.size

        # En sık 5 rengi bul (piksel örnekleme)
        pixels = list(img_rgb.getdata())
        from collections import Counter
        cnt = Counter(pixels)
        dominant = []
        total = w * h
        for (r, g, b), count in cnt.most_common(5):
            dominant.append({
                "hex": f"#{r:02x}{g:02x}{b:02x}",
                "rgb": [r, g, b],
                "piksel": count,
                "yuzde": round(count * 100 / total, 2),
            })

        # Keskinlik (variance of Laplacian via OpenCV if available)
        sharpness = None
        if status["cv2"]:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            cv_img = cv2.imread(str(p))
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # Ortalama renk
        stat = ImageStat.Stat(img_rgb)
        mean_rgb = [round(v, 1) for v in stat.mean]

        return {
            "ok": True,
            "boyut": {"genişlik": w, "yükseklik": h},
            "baskın_renkler": dominant,
            "ortalama_rgb": mean_rgb,
            "keskinlik_variance": sharpness,
            "engine": "pillow" + ("+opencv" if status["cv2"] else ""),
        }
    except Exception as exc:
        logger.exception("Color analysis failed")
        return {"ok": False, "error": str(exc), "code": "color_analysis_failed"}


# ---------------------------------------------------------------------------
# 4. Düzenleme / Kesme (FFmpeg / ImageMagick)
# ---------------------------------------------------------------------------

def crop_image(
    input_path: str,
    left: int,
    top: int,
    width: int,
    height: int,
    output_format: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """FFmpeg/ImageMagick ile koordinat bazlı kırpma."""
    status = tools_status()
    if not status["edit_available"]:
        return {
            "ok": False,
            "error": (
                "Düzenleme aracı yok. Kurulum: FFmpeg (https://ffmpeg.org/download.html) "
                "veya ImageMagick (https://imagemagick.org)."
            ),
            "code": "edit_not_available",
        }

    p = _resolve_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    fmt = (output_format or p.suffix.lstrip(".") or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"

    out_path, rel_path = _output_path(user_id or "anon", p.name, "crop", fmt)

    try:
        if status["ffmpeg"]:
            args = [
                FFMPEG_BIN, "-y",
                "-i", str(p),
                "-vf", f"crop={width}:{height}:{left}:{top}",
                str(out_path),
            ]
            result = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=300,
            )
            if result.returncode != 0:
                # FFmpeg başarısızsa ImageMagick'e düş
                raise RuntimeError(result.stderr[-500:])
        elif IMAGEMAGICK_BIN:
            args = [
                IMAGEMAGICK_BIN, str(p),
                "-crop", f"{width}x{height}+{left}+{top}",
                str(out_path),
            ]
            result = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=300,
            )
            if result.returncode != 0:
                return {"ok": False, "error": result.stderr[-500:], "code": "imagemagick_failed"}
        else:
            return {"ok": False, "error": "Hiçbir düzenleme aracı yok", "code": "no_edit_tool"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "İşlem zaman aşımına uğradı", "code": "timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "crop_failed"}

    return {
        "ok": True,
        "output": str(out_path),
        "output_path": str(out_path),
        "relative_output": rel_path,
        "format": fmt,
        "operation": "crop",
        "konum": {"sol": left, "üst": top, "genişlik": width, "yükseklik": height},
        "size": out_path.stat().st_size if out_path.is_file() else 0,
    }


def resize_image(
    input_path: str,
    width: int,
    height: int,
    output_format: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """FFmpeg/ImageMagick ile yeniden boyutlandırma."""
    status = tools_status()
    if not status["edit_available"]:
        return {
            "ok": False,
            "error": "Düzenleme aracı yok. FFmpeg veya ImageMagick kurun.",
            "code": "edit_not_available",
        }

    p = _resolve_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    fmt = (output_format or p.suffix.lstrip(".") or "png").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"

    out_path, rel_path = _output_path(user_id or "anon", p.name, "resize", fmt)

    try:
        if status["ffmpeg"]:
            args = [
                FFMPEG_BIN, "-y",
                "-i", str(p),
                "-vf", f"scale={width}:{height}",
                str(out_path),
            ]
            result = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-500:])
        elif IMAGEMAGICK_BIN:
            args = [
                IMAGEMAGICK_BIN, str(p),
                "-resize", f"{width}x{height}",
                str(out_path),
            ]
            result = subprocess.run(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=300,
            )
            if result.returncode != 0:
                return {"ok": False, "error": result.stderr[-500:], "code": "imagemagick_failed"}
        else:
            return {"ok": False, "error": "Hiçbir düzenleme aracı yok", "code": "no_edit_tool"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "İşlem zaman aşımına uğradı", "code": "timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "resize_failed"}

    return {
        "ok": True,
        "output": str(out_path),
        "output_path": str(out_path),
        "relative_output": rel_path,
        "format": fmt,
        "operation": "resize",
        "boyut": {"genişlik": width, "yükseklik": height},
        "size": out_path.stat().st_size if out_path.is_file() else 0,
    }


# ---------------------------------------------------------------------------
# Kapsamlı görsel analiz (model için tek çağrı)
# ---------------------------------------------------------------------------

def full_visual_analysis(input_path: str) -> Dict[str, Any]:
    """Kullanılabilir araçlarla tek seferde tüm analizleri çalıştırır."""
    results: Dict[str, Any] = {"ok": True, "path": input_path, "analyses": {}}

    ocr = ocr_image(input_path)
    results["analyses"]["ocr"] = ocr

    objects = detect_objects(input_path)
    results["analyses"]["objects"] = objects

    colors = analyze_colors(input_path)
    results["analyses"]["colors"] = colors

    # Özet metin
    parts = []
    if ocr.get("ok") and ocr.get("text"):
        parts.append(f"Yazı: {ocr['text'][:200]}")
    if objects.get("ok"):
        parts.append(f"nesne: {objects.get('count')} tane")
    if colors.get("ok"):
        c = colors.get("baskın_renkler", [])
        if c:
            parts.append(f"baskın renk: {c[0].get('hex')}")
        else:
            parts.append(f"renk: ortalama {colors.get('ortalama_rgb')}")
    if not parts:
        parts.append("Görsel analiz edildi (aracı sınırlı).")
    results["özet"] = " | ".join(parts)
    return results
