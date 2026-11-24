"""Video analiz ve yorumlama servisi.

Aşağıdaki araçları opsiyonel olarak kullanır (yoksa net hata döner):
- FFmpeg/ffprobe: teknik metaveri, kare çıkarma, sahne değişim tespiti
- faster-whisper: konuşmayı zaman damgalı metne çevirme (SRT/VTT/JSON)
- PySceneDetect / FFmpeg select filter: sahne/plan değişiklikleri
- OpenCV / YOLOv8 / MediaPipe: karelerde nesne, kişi, hareket tespiti
- Tesseract OCR: ekrandaki yazıları okuma

Tüm araç opsiyoneldir; yoksa kurulum talimatı döner.
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

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"


def _check_tool(name: str) -> bool:
    try:
        subprocess.run(
            [name, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except Exception:
        return False


def _check_python_module(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def tools_status() -> dict:
    """Hangi video analiz araçları mevcut?"""
    status = {
        "ffmpeg": _check_tool("ffmpeg"),
        "ffprobe": _check_tool("ffprobe"),
        "faster_whisper": _check_python_module("faster_whisper"),
        "whisper": _check_python_module("whisper"),
        "opencv": _check_python_module("cv2"),
        "scenedetect": _check_python_module("scenedetect"),
        "ultralytics": _check_python_module("ultralytics"),
        "mediapipe": _check_python_module("mediapipe"),
        "pytesseract": _check_python_module("pytesseract"),
    }
    status["any_ffmpeg"] = status["ffmpeg"] and status["ffprobe"]
    status["any_ai"] = any(
        status[k] for k in ("faster_whisper", "whisper", "opencv", "scenedetect", "ultralytics", "mediapipe", "pytesseract")
    )
    return status


def probe_video(path: str) -> Dict[str, Any]:
    """ffprobe ile video teknik metaverisi (süre, FPS, çözünürlük, codec, bitrate)."""
    if not _check_tool("ffprobe"):
        return {
            "ok": False,
            "error": "ffprobe bulunamadı. FFmpeg'i kurun: https://ffmpeg.org/download.html",
            "code": "ffprobe_not_found",
        }
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    try:
        p.relative_to(media_root)
    except ValueError:
        return {"ok": False, "error": "Geçersiz yol", "code": "invalid_path"}
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(p),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[-1000:], "code": "ffprobe_failed"}
        data = json.loads(result.stdout)
        # Özetle
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        vs = video_streams[0] if video_streams else {}
        as_ = audio_streams[0] if audio_streams else {}
        summary = {
            "duration": fmt.get("duration"),
            "size_bytes": fmt.get("size"),
            "bit_rate": fmt.get("bit_rate"),
            "format_name": fmt.get("format_name"),
            "video_codec": vs.get("codec_name"),
            "width": vs.get("width"),
            "height": vs.get("height"),
            "fps": vs.get("avg_frame_rate"),
            "audio_codec": as_.get("codec_name"),
            "audio_sample_rate": as_.get("sample_rate"),
            "audio_channels": as_.get("channels"),
        }
        return {"ok": True, "summary": summary, "raw": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "probe_error"}


def extract_frames(
    path: str,
    fps: float = 1.0,
    scene_detect: bool = False,
    max_frames: int = 50,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """FFmpeg ile kare çıkarma. scene_detect=True ise sahne değişimlerinde kare alır."""
    if not _check_tool("ffmpeg"):
        return {
            "ok": False,
            "error": "ffmpeg bulunamadı. FFmpeg'i kurun: https://ffmpeg.org/download.html",
            "code": "ffmpeg_not_found",
        }
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    try:
        p.relative_to(media_root)
    except ValueError:
        return {"ok": False, "error": "Geçersiz yol", "code": "invalid_path"}
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    out_dir = media_root / "videos" / "frames" / uuid.uuid4().hex[:12]
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if scene_detect:
            args = [
                "-i", str(p),
                "-vf", "select='gt(scene,0.3)',showinfo",
                "-vsync", "vfr",
                "-frames:v", str(max_frames),
                str(out_dir / "frame_%04d.png"),
            ]
        else:
            args = [
                "-i", str(p),
                "-vf", f"fps={fps}",
                "-frames:v", str(max_frames),
                str(out_dir / "frame_%04d.png"),
            ]
        result = subprocess.run(
            ["ffmpeg", "-y", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[-1000:], "code": "extract_failed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "İşlem zaman aşımına uğradı.", "code": "timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "extract_error"}

    frames = sorted(out_dir.glob("frame_*.png"))
    return {
        "ok": True,
        "frames": [str(f) for f in frames],
        "frame_dir": str(out_dir),
        "count": len(frames),
        "scene_detect": scene_detect,
    }


def transcribe_audio(
    path: str,
    model_size: str = "base",
    language: Optional[str] = None,
    output_format: str = "json",
) -> Dict[str, Any]:
    """faster-whisper veya openai-whisper ile konuşmayı metne çevirir."""
    # faster-whisper dene
    if _check_python_module("faster_whisper"):
        try:
            from faster_whisper import WhisperModel
            from django.conf import settings

            media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
            p = Path(path)
            if not p.is_absolute():
                p = (media_root / p).resolve()
            if not p.is_file():
                return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(str(p), language=language, vad_filter=True)
            segments_list = []
            for seg in segments:
                segments_list.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                })
            result = {
                "ok": True,
                "language": info.language,
                "duration": info.duration,
                "segments": segments_list,
                "text": " ".join(s["text"] for s in segments_list),
            }
            if output_format in ("srt", "vtt"):
                result["subtitle_file"] = _write_subtitle_file(segments_list, output_format, p, media_root)
            return result
        except Exception as exc:
            logger.exception("faster-whisper failed")
            return {"ok": False, "error": str(exc), "code": "whisper_failed"}

    # openai-whisper dene
    if _check_python_module("whisper"):
        try:
            import whisper
            from django.conf import settings

            media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
            p = Path(path)
            if not p.is_absolute():
                p = (media_root / p).resolve()
            if not p.is_file():
                return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

            model = whisper.load_model(model_size)
            result = model.transcribe(str(p), language=language)
            segments_list = []
            for seg in result.get("segments", []):
                segments_list.append({
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                    "text": seg.get("text", "").strip(),
                })
            out = {
                "ok": True,
                "language": result.get("language"),
                "duration": result.get("duration"),
                "segments": segments_list,
                "text": result.get("text", ""),
            }
            if output_format in ("srt", "vtt"):
                out["subtitle_file"] = _write_subtitle_file(segments_list, output_format, p, media_root)
            return out
        except Exception as exc:
            logger.exception("openai-whisper failed")
            return {"ok": False, "error": str(exc), "code": "whisper_failed"}

    return {
        "ok": False,
        "error": (
            "Konuşma metne çevirme aracı yok. Kurun: pip install faster-whisper "
            "veya pip install openai-whisper"
        ),
        "code": "whisper_not_available",
    }


def _write_subtitle_file(segments: list, fmt: str, video_path: Path, media_root: Path) -> str:
    out_dir = media_root / "videos" / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem
    if fmt == "srt":
        out_path = out_dir / f"{stem}.srt"
        lines = []
        for i, seg in enumerate(segments, 1):
            lines.append(str(i))
            lines.append(f"{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}")
            lines.append(seg["text"])
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)
    else:  # vtt
        out_path = out_dir / f"{stem}.vtt"
        lines = ["WEBVTT", ""]
        for seg in segments:
            lines.append(f"{_fmt_vtt_time(seg['start'])} --> {_fmt_vtt_time(seg['end'])}")
            lines.append(seg["text"])
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)


def _fmt_srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def detect_scenes(path: str, threshold: float = 0.3) -> Dict[str, Any]:
    """FFmpeg select filter ile sahne değişimlerini tespit eder."""
    if not _check_tool("ffmpeg"):
        return {
            "ok": False,
            "error": "ffmpeg bulunamadı. FFmpeg'i kurun: https://ffmpeg.org/download.html",
            "code": "ffmpeg_not_found",
        }
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(p),
                "-vf", f"select='gt(scene,{threshold})',showinfo",
                "-vsync", "vfr",
                "-frames:v", "100",
                "-f", "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        stderr = result.stderr
        scenes = []
        for line in stderr.splitlines():
            if "pts_time:" in line:
                try:
                    pt = line.split("pts_time:")[1].split()[0]
                    scenes.append(float(pt))
                except Exception:
                    pass
        return {"ok": True, "scenes": scenes, "count": len(scenes), "threshold": threshold}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Zaman aşımı", "code": "timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "scene_error"}


def analyze_objects_opencv(path: str, sample_fps: float = 1.0) -> Dict[str, Any]:
    """OpenCV ile kare örnekleme ve temel nesne/renk analizi (YOLO yoksa temel özellikler)."""
    if not _check_python_module("cv2"):
        return {
            "ok": False,
            "error": "OpenCV yok. Kurun: pip install opencv-python",
            "code": "opencv_not_found",
        }
    import cv2  # type: ignore

    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    try:
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return {"ok": False, "error": "Video açılamadı", "code": "open_failed"}
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / fps if fps else 0

        interval = max(1, int(fps / sample_fps))
        frames_info = []
        idx = 0
        saved = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % interval == 0 and saved < 30:
                # Basit renk ortalaması
                b, g, r = frame.reshape(-1, 3).mean(axis=0)
                frames_info.append({
                    "frame": idx,
                    "time": idx / fps if fps else 0,
                    "avg_color": {"r": round(float(r), 1), "g": round(float(g), 1), "b": round(float(b), 1)},
                    "brightness": round(float(frame.mean()), 1),
                })
                saved += 1
            idx += 1
        cap.release()
        return {
            "ok": True,
            "duration": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "frame_count": frame_count,
            "samples": frames_info,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "opencv_error"}


def yolo_detect(path: str, model_name: str = "yolov8n.pt") -> Dict[str, Any]:
    """YOLOv8 ile nesne tespiti (ultralytics gerektirir)."""
    if not _check_python_module("ultralytics"):
        return {
            "ok": False,
            "error": "YOLOv8 yok. Kurun: pip install ultralytics",
            "code": "yolo_not_found",
        }
    from ultralytics import YOLO  # type: ignore

    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    try:
        model = YOLO(model_name)
        results = model(str(p), stream=True)
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
                    "class": model.names[cls],
                    "confidence": round(conf, 2),
                    "box": [round(v, 1) for v in xyxy],
                })
        return {"ok": True, "detections": detections, "count": len(detections), "model": model_name}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "yolo_error"}


def ocr_frames(path: str, sample_fps: float = 1.0) -> Dict[str, Any]:
    """Tesseract OCR ile karelerdeki yazıları okur."""
    if not _check_python_module("pytesseract"):
        return {
            "ok": False,
            "error": "pytesseract yok. Kurun: pip install pytesseract (ve sistemde tesseract-ocr)",
            "code": "ocr_not_found",
        }
    import pytesseract  # type: ignore

    if not _check_python_module("cv2"):
        return {
            "ok": False,
            "error": "OpenCV yok. Kurun: pip install opencv-python",
            "code": "opencv_not_found",
        }
    import cv2  # type: ignore

    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    if not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    try:
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return {"ok": False, "error": "Video açılamadı", "code": "open_failed"}
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        interval = max(1, int(fps / sample_fps))
        idx = 0
        results = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % interval == 0:
                text = pytesseract.image_to_string(frame, lang="tur+eng").strip()
                if text:
                    results.append({
                        "frame": idx,
                        "time": round(idx / fps, 2),
                        "text": text[:500],
                    })
            idx += 1
            if len(results) >= 30:
                break
        cap.release()
        return {"ok": True, "ocr_results": results, "count": len(results)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "code": "ocr_error"}


def full_analysis(path: str) -> Dict[str, Any]:
    """Kullanılabilir araçlarla kapsamlı video analizi."""
    results: Dict[str, Any] = {"ok": True, "path": path, "analyses": {}}

    # 1. ffprobe metaverisi
    probe = probe_video(path)
    results["analyses"]["probe"] = probe

    # 2. Kare çıkarma (1 fps, max 20)
    frames = extract_frames(path, fps=1.0, max_frames=20)
    results["analyses"]["frames"] = frames

    # 3. Sahne değişimleri
    scenes = detect_scenes(path)
    results["analyses"]["scenes"] = scenes

    # 4. Whisper transkripsiyonu (varsa)
    whisper = transcribe_audio(path)
    results["analyses"]["transcription"] = whisper

    # 5. OpenCV örnekleme
    cv2_res = analyze_objects_opencv(path)
    results["analyses"]["opencv"] = cv2_res

    # 6. OCR (varsa)
    ocr = ocr_frames(path)
    results["analyses"]["ocr"] = ocr

    # Özet metin
    summary_parts = []
    if probe.get("ok"):
        s = probe.get("summary", {})
        summary_parts.append(
            f"Video: {s.get('width')}x{s.get('height')}, "
            f"süre: {s.get('duration')}sn, codec: {s.get('video_codec')}"
        )
    if scenes.get("ok"):
        summary_parts.append(f"sahne değişimi: {scenes.get('count')} kez")
    if whisper.get("ok"):
        summary_parts.append(f"konuşma dili: {whisper.get('language')}, metin uzunluğu: {len(whisper.get('text',''))}")
    if cv2_res.get("ok"):
        summary_parts.append(f"örneklenen kare: {len(cv2_res.get('samples', []))}")
    results["summary"] = " | ".join(summary_parts) if summary_parts else "Analiz tamamlandı (sınırlı araç)."
    return results
