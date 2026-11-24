"""2D video düzenleme ve analiz servisi (FFmpeg tabanlı).

Desteklenen işlemler:
- cut: videoyu kesme (başlangıç/bitiş saniyesi)
- merge: birden fazla videoyu birleştirme
- crop: kırpma (genişlik, yükseklik, x, y)
- resize: yeniden boyutlandırma
- fps: kare hızı değiştirme
- bitrate: bitrate ayarlama
- audio_extract: sesi ayıkırma (mp3/wav)
- audio_add: ses dosyası ekleme/değiştirme
- subtitles: altyazı gömme (srt/vtt)
- speed: hızlandırma/yavaşlatma (0.5x .. 4x)
- format: format dönüştürme (mp4, webm, mov, avi, mkv)
- compress: sıkıştırma (crf kalite)
- flip: yatay/dikey çevirme
- rotate: döndürme (90, 180, 270)

Tüm işlemler FFmpeg/FFprobe üzerinden yapılır. Araçlar yoksa
net bir kurulum talimatı döner.
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

SUPPORTED_VIDEO_FORMATS = {"mp4", "webm", "mov", "avi", "mkv", "flv", "wmv"}
SUPPORTED_AUDIO_FORMATS = {"mp3", "wav", "aac", "ogg", "flac"}

SUPPORTED_OPERATIONS = [
    {
        "id": "cut",
        "label": "Video Kesme",
        "description": "Belirli bir zaman aralığını keser (start, end saniye).",
        "params": {"start": "başlangıç saniye", "end": "bitiş saniye"},
    },
    {
        "id": "merge",
        "label": "Video Birleştirme",
        "description": "Birden fazla videoyu sırayla birleştirir.",
        "params": {"inputs": "birleştirilecek video yolu listesi"},
    },
    {
        "id": "crop",
        "label": "Video Kırpma",
        "description": "Belirli bir bölgeyi kırp (w, h, x, y).",
        "params": {"w": "genişlik", "h": "yükseklik", "x": "sol", "y": "üst"},
    },
    {
        "id": "resize",
        "label": "Yeniden Boyutlandırma",
        "description": "Videoyu belirli çözünürlüğe getirir.",
        "params": {"w": "genişlik", "h": "yükseklik"},
    },
    {
        "id": "fps",
        "label": "Kare Hızı",
        "description": "FPS değerini değiştirir.",
        "params": {"fps": "kare/saniye (örn. 24, 30, 60)"},
    },
    {
        "id": "bitrate",
        "label": "Bitrate",
        "description": "Video bitrate'ını ayarlar.",
        "params": {"bitrate": "örn. 1M, 2M, 5M"},
    },
    {
        "id": "audio_extract",
        "label": "Ses Ayıkırma",
        "description": "Videodan sesi ayıkır (mp3/wav).",
        "params": {"format": "mp3/wav/aac"},
    },
    {
        "id": "audio_add",
        "label": "Ses Ekleme",
        "description": "Videoya ses dosyası ekler/değiştirir.",
        "params": {"audio": "ses dosyası yolu"},
    },
    {
        "id": "subtitles",
        "label": "Altyazı Gömme",
        "description": "SRT/VTT altyazı dosyasını videoya gömer.",
        "params": {"subtitles": "altyazı dosyası yolu"},
    },
    {
        "id": "speed",
        "label": "Hız Değişikliği",
        "description": "Oynatma hızını değiştirir (0.5x .. 4x).",
        "params": {"speed": "çarpan (örn. 1.5, 2.0)"},
    },
    {
        "id": "format",
        "label": "Format Dönüştürme",
        "description": "Videoyu başka bir biçime dönüştürür.",
        "params": {"format": "mp4/webm/mov/avi/mkv"},
    },
    {
        "id": "compress",
        "label": "Sıkıştırma",
        "description": "CRF kalite ile dosya boyutunu küçültür (18..28).",
        "params": {"crf": "kalite (18..28, düşük = yüksek kalite)"},
    },
    {
        "id": "flip",
        "label": "Yansıma / Döndürme",
        "description": "Yatay veya dikey çevirir.",
        "params": {"direction": "h (yatay) veya v (dikey)"},
    },
    {
        "id": "rotate",
        "label": "Döndürme",
        "description": "90, 180 veya 270 derece döndürür.",
        "params": {"degrees": "90/180/270"},
    },
]


def _is_ffmpeg_available() -> bool:
    try:
        subprocess.run(
            [FFMPEG_BIN, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except Exception:
        return False


def _is_ffprobe_available() -> bool:
    try:
        subprocess.run(
            [FFPROBE_BIN, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return True
    except Exception:
        return False


def _tools_available() -> Tuple[bool, str]:
    ffmpeg_ok = _is_ffmpeg_available()
    ffprobe_ok = _is_ffprobe_available()
    if not ffmpeg_ok and not ffprobe_ok:
        return False, (
            "FFmpeg/ffprobe kurulu değil. Video düzenleme için FFmpeg'i kurun: "
            "https://ffmpeg.org/download.html (Windows: chocolatey 'choco install ffmpeg' "
            "veya winget 'winget install ffmpeg')."
        )
    if not ffmpeg_ok:
        return False, "ffmpeg bulunamadı. FFmpeg'i kurun: https://ffmpeg.org/download.html"
    if not ffprobe_ok:
        return False, "ffprobe bulunamadı. FFmpeg paketinin tamamını kurun: https://ffmpeg.org/download.html"
    return True, ""


def _safe_media_path(rel_or_abs: str) -> Optional[Path]:
    """Media kökünden güvenli bir dosya yolu çözer."""
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media")).resolve()
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = (media_root / p).resolve()
    try:
        p.relative_to(media_root)
    except ValueError:
        return None
    return p


def _output_path(user_id: str, original_name: str, operation: str, ext: str) -> Tuple[Path, str]:
    from django.conf import settings

    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
    out_dir = media_root / "videos" / "edits"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(original_name or "video").stem or "edit"
    safe_stem = "".join(c for c in stem if c.isalnum() or c in ("-", "_"))[:60] or "edit"
    if ext == "jpeg":
        ext = "jpg"
    filename = f"{uuid.uuid4().hex[:12]}_{operation}_{safe_stem}.{ext}"
    out_path = out_dir / filename
    rel_path = str(Path("videos") / "edits" / filename)
    return out_path, rel_path


def _run_ffmpeg(args: List[str], timeout: int = 300) -> Tuple[bool, str]:
    """FFmpeg komutunu çalıştırır. (ok, stderr) döner."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-y", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr[-2000:]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "İşlem zaman aşımına uğradı."
    except FileNotFoundError:
        return False, "ffmpeg çalıştırılabilir dosyası bulunamadı."
    except Exception as exc:
        return False, str(exc)


def probe_video(path: str) -> Dict[str, Any]:
    """Videonun teknik metaverisini ffprobe ile çıkarır."""
    if not _is_ffprobe_available():
        return {"ok": False, "error": "ffprobe bulunamadı", "code": "ffprobe_not_found"}
    p = _safe_media_path(path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}
    try:
        result = subprocess.run(
            [
                FFPROBE_BIN,
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
        return {"ok": True, "data": data}
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
    ok, msg = _tools_available()
    if not ok:
        return {"ok": False, "error": msg, "code": "tools_not_available"}
    p = _safe_media_path(path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Dosya bulunamadı", "code": "file_not_found"}

    from django.conf import settings
    media_root = Path(getattr(settings, "MEDIA_ROOT", Path.cwd() / "media"))
    out_dir = media_root / "videos" / "frames" / uuid.uuid4().hex[:12]
    out_dir.mkdir(parents=True, exist_ok=True)

    if scene_detect:
        # Sahne değişiminde kare: select='gt(scene,0.3)'
        vf = f"select='gt(scene,0.3)',showinfo"
        args = [
            "-i", str(p),
            "-vf", vf,
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

    ok_run, err = _run_ffmpeg(args, timeout=600)
    if not ok_run:
        return {"ok": False, "error": err, "code": "extract_failed"}

    frames = sorted(out_dir.glob("frame_*.png"))
    return {
        "ok": True,
        "frames": [str(f) for f in frames],
        "frame_dir": str(out_dir),
        "count": len(frames),
        "scene_detect": scene_detect,
    }


def edit_video(
    input_path: str,
    operation: str,
    params: Optional[Dict[str, Any]] = None,
    output_format: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Tek bir video düzenleme işlemi uygular."""
    ok, msg = _tools_available()
    if not ok:
        return {"ok": False, "error": msg, "code": "tools_not_available"}

    params = dict(params or {})
    p = _safe_media_path(input_path)
    if p is None or not p.is_file():
        return {"ok": False, "error": "Girdi dosyası bulunamadı", "code": "file_not_found"}

    fmt = (output_format or params.get("format") or p.suffix.lstrip(".") or "mp4").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in SUPPORTED_VIDEO_FORMATS:
        fmt = "mp4"

    out_path, rel_path = _output_path(user_id or "anon", p.name, operation, fmt)

    op = (operation or "").strip().lower()
    args: List[str] = ["-i", str(p)]

    if op == "cut":
        start = params.get("start", "0")
        end = params.get("end")
        args += ["-ss", str(start)]
        if end is not None:
            args += ["-to", str(end)]
        args += ["-c", "copy"]
    elif op == "crop":
        w = params.get("w", "iw")
        h = params.get("h", "ih")
        x = params.get("x", "0")
        y = params.get("y", "0")
        args += ["-vf", f"crop={w}:{h}:{x}:{y}"]
    elif op == "resize":
        w = params.get("w", "iw")
        h = params.get("h", "ih")
        args += ["-vf", f"scale={w}:{h}"]
    elif op == "fps":
        fps = params.get("fps", "24")
        args += ["-vf", f"fps={fps}"]
    elif op == "bitrate":
        br = params.get("bitrate", "1M")
        args += ["-b:v", str(br)]
    elif op == "audio_extract":
        af = params.get("format", "mp3")
        if af not in SUPPORTED_AUDIO_FORMATS:
            af = "mp3"
        out_path, rel_path = _output_path(user_id or "anon", p.name, "audio_extract", af)
        args = ["-i", str(p), "-vn", "-acodec", "libmp3lame" if af == "mp3" else af, str(out_path)]
    elif op == "audio_add":
        audio = params.get("audio")
        if not audio:
            return {"ok": False, "error": "audio dosya yolu gerekli", "code": "missing_audio"}
        ap = _safe_media_path(audio)
        if ap is None or not ap.is_file():
            return {"ok": False, "error": "Ses dosyası bulunamadı", "code": "audio_not_found"}
        args += ["-i", str(ap), "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    elif op == "subtitles":
        sub = params.get("subtitles")
        if not sub:
            return {"ok": False, "error": "altyazı dosya yolu gerekli", "code": "missing_subtitles"}
        sp = _safe_media_path(sub)
        if sp is None or not sp.is_file():
            return {"ok": False, "error": "Altyazı dosyası bulunamadı", "code": "subtitles_not_found"}
        args += ["-vf", f"subtitles={sp}"]
    elif op == "speed":
        speed = float(params.get("speed", 1.0))
        speed = max(0.5, min(4.0, speed))
        args += ["-vf", f"setpts={1.0/speed}*PTS", "-af", f"atempo={speed}"]
    elif op == "compress":
        crf = int(params.get("crf", 23))
        crf = max(18, min(28, crf))
        args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-c:a", "aac"]
    elif op == "flip":
        direction = str(params.get("direction", "h")).lower()
        if direction == "h":
            args += ["-vf", "hflip"]
        elif direction == "v":
            args += ["-vf", "vflip"]
        else:
            args += ["-vf", "hflip"]
    elif op == "rotate":
        degrees = int(params.get("degrees", 90))
        if degrees == 90:
            args += ["-vf", "transpose=1"]
        elif degrees == 180:
            args += ["-vf", "transpose=2,transpose=2"]
        elif degrees == 270:
            args += ["-vf", "transpose=2"]
        else:
            args += ["-vf", "transpose=1"]
    elif op == "format":
        pass  # format conversion handled by output extension
    else:
        return {"ok": False, "error": f"Bilinmeyen işlem: {operation}", "code": "unknown_operation"}

    args += [str(out_path)]
    ok_run, err = _run_ffmpeg(args, timeout=600)
    if not ok_run:
        return {"ok": False, "error": err, "code": "ffmpeg_failed"}

    return {
        "ok": True,
        "output": str(out_path),
        "output_path": str(out_path),
        "relative_output": rel_path,
        "format": fmt,
        "operation": operation,
        "size": out_path.stat().st_size if out_path.is_file() else 0,
    }


def list_operations() -> list:
    return list(SUPPORTED_OPERATIONS)


def tools_status() -> dict:
    ffmpeg_ok = _is_ffmpeg_available()
    ffprobe_ok = _is_ffprobe_available()
    return {
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "available": ffmpeg_ok and ffprobe_ok,
        "message": "" if (ffmpeg_ok and ffprobe_ok) else (
            "FFmpeg kurulu değil. https://ffmpeg.org/download.html adresinden kurun."
        ),
    }
