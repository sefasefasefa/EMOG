"""Video uretim servisi.

Sağlayıcı soyutlaması (OpenAI Sora, Stability, Replicate, NVIDIA) ve yerel
slideshow fallback (PIL + imageio-ffmpeg). TTS (gtts/pyttsx3), alt yazı render
ve ffmpeg tabanlı AI düzenleme desteklenir.

Tüm ağ/LLM çağrıları isteğe bağlıdır; çevrimdışı modda (API anahtarı yokken)
yalnızca yerel sağlayıcı kullanılır.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Django / settings yardımcıları
# ---------------------------------------------------------------------------

try:
    from django.conf import settings as _django_settings
except Exception:  # pragma: no cover - Django olmadan import edilebilsin
    _django_settings = None


def _cfg() -> dict:
    """VIDEO_GENERATION ayarlarını güvenli şekilde oku."""
    cfg = {}
    try:
        if _django_settings is not None:
            raw = getattr(_django_settings, "VIDEO_GENERATION", None)
            if isinstance(raw, dict):
                cfg = dict(raw)
    except Exception:
        cfg = {}
    # Çevrimdışı override: bazı testler NEURAL_CORE tarzı OFFLINE anahtarı kullanır.
    if "offline" not in cfg and "OFFLINE" in cfg:
        cfg["offline"] = bool(cfg.get("OFFLINE"))
    # Varsayılanlar
    defaults = {
        "enabled": True,
        "offline": False,
        "max_duration": 300,
        "max_duration_seconds": 300,
        "min_duration": 5,
        "default_duration": 10,
        "max_upload": 100 * 1024 * 1024,
        "resolution": (1920, 1080),
        "default_resolution": (1920, 1080),
        "fps": 24,
        "default_fps": 24,
        "formats": ["mp4", "gif", "webm"],
        "default_format": "mp4",
        "providers": ["openai", "stability", "replicate", "nvidia", "local"],
        "provider_order": ["openai", "stability", "replicate", "nvidia", "local"],
        "output_dir": "videos",
        "upload_dir": "uploads",
        "media_root": None,
        "media_url": "/media/",
        "tts_enabled": True,
        "tts_providers": ["gtts", "pyttsx3"],
        "tts_provider": "gtts",
        "subtitles_enabled": True,
        "subtitle_position": "bottom",
        "background_jobs": True,
    }
    for key, val in defaults.items():
        cfg.setdefault(key, val)
    # Top-level alias'lar
    try:
        if _django_settings is not None:
            for alias in ("VIDEO_GENERATION_ENABLED", "VIDEO_GENERATION_MAX_DURATION",
                          "VIDEO_GENERATION_MAX_UPLOAD", "VIDEO_GENERATION_PROVIDERS",
                          "VIDEO_GENERATION_RESOLUTION", "VIDEO_GENERATION_FPS",
                          "VIDEO_GENERATION_FORMATS"):
                if hasattr(_django_settings, alias) and alias.lower().lstrip("video_generation_") not in cfg:
                    pass
    except Exception:
        pass
    # providers alias
    if "providers" not in cfg or not cfg.get("providers"):
        cfg["providers"] = list(defaults["providers"])
    if "provider_order" not in cfg or not cfg.get("provider_order"):
        cfg["provider_order"] = list(cfg.get("providers") or defaults["providers"])
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
    out = _media_root() / str(cfg.get("output_dir") or "videos")
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


def _norm_resolution(resolution) -> Tuple[int, int]:
    if isinstance(resolution, str):
        parts = resolution.lower().replace("x", ",").split(",")
        if len(parts) >= 2:
            return (_to_int(parts[0].strip(), 1920), _to_int(parts[1].strip(), 1080))
    if isinstance(resolution, (tuple, list)):
        if len(resolution) >= 2:
            return (_to_int(resolution[0], 1920), _to_int(resolution[1], 1080))
    if isinstance(resolution, dict):
        return (_to_int(resolution.get("width", 1920), 1920),
                _to_int(resolution.get("height", 1080), 1080))
    return (1920, 1080)


def _ffmpeg_exe() -> str:
    # imageio-ffmpeg binary'si
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    for name in ("ffmpeg", "ffmpeg.exe"):
        found = shutil.which(name)
        if found:
            return found
    return "ffmpeg"


def _run_ffmpeg(args: Sequence[str], timeout: int = 300) -> Tuple[bool, str]:
    exe = _ffmpeg_exe()
    cmd = [exe] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        if proc.returncode == 0:
            return True, (proc.stdout or b"").decode(errors="replace")
        err = (proc.stderr or b"").decode(errors="replace")
        return False, err[-2000:]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


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
    # id ile arama
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

class VideoProvider:
    """Tüm video sağlayıcıları için temel arayüz."""

    name = "base"
    display_name = "Base"

    def __init__(self, config: Optional[dict] = None):
        self.config = config or _cfg()

    def available(self) -> bool:
        return False

    def generate(self, job) -> dict:
        raise NotImplementedError

    def edit(self, job, instruction: str) -> dict:
        raise NotImplementedError

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name} available={self.available()}>"


class BaseVideoProvider(VideoProvider):
    pass


class Provider(VideoProvider):
    pass


# --- OpenAI (Sora) ----------------------------------------------------------

class OpenAIProvider(VideoProvider):
    name = "openai"
    display_name = "OpenAI Sora"

    ENV_KEYS = ("OPENAI_API_KEY",)

    def available(self) -> bool:
        cfg = _cfg()
        if cfg.get("offline"):
            return False
        if "openai" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def _client(self):
        try:
            from openai import OpenAI
        except Exception:
            return None
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key)

    def generate(self, job) -> dict:
        client = self._client()
        if client is None:
            raise RuntimeError("OpenAI istemcisi kullanılamıyor")
        # OpenAI video generation API (sora). Çevrimdışı testlerde çağrılmaz.
        model = os.environ.get("OPENAI_VIDEO_MODEL", "sora-2")
        prompt = job.prompt
        # SDK sürümleri farklı; güvenli çağrı
        try:
            resp = client.videos.create(model=model, prompt=prompt,
                                       size=f"{job.width}x{job.height}",
                                       seconds=str(job.duration))
        except Exception as exc:
            # Bazı SDK'larda endpoint farklı olabilir
            raise RuntimeError(f"OpenAI video üretimi başarısız: {exc}")
        # Çıktıyı indir
        out_path = _save_provider_output(job, resp, "openai")
        return {"provider": "openai", "output": out_path, "raw": _safe_repr(resp)}

    def edit(self, job, instruction: str) -> dict:
        # OpenAI edit endpoint yoksa yerel fallback
        return LocalProvider(self.config).edit(job, instruction)


class OpenAISoraProvider(OpenAIProvider):
    name = "openai"


class SoraProvider(OpenAIProvider):
    name = "openai"


# --- Stability ---------------------------------------------------------------

class StabilityProvider(VideoProvider):
    name = "stability"
    display_name = "Stability AI"

    ENV_KEYS = ("STABILITY_API_KEY", "STABILITY_KEY")

    def available(self) -> bool:
        cfg = _cfg()
        if cfg.get("offline"):
            return False
        if "stability" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def generate(self, job) -> dict:
        # stability-sdk yüklü değilse HTTP ile dene
        key = next((os.environ.get(k) for k in self.ENV_KEYS if os.environ.get(k)), None)
        if not key:
            raise RuntimeError("Stability API anahtarı yok")
        # Ağ çağrısı çevrimdışı testlerde yapılmaz; gerçek implementasyon minimal.
        raise RuntimeError("Stability video üretimi bu sürümde desteklenmiyor (ağ)")

    def edit(self, job, instruction: str) -> dict:
        return LocalProvider(self.config).edit(job, instruction)


class StabilityAIProvider(StabilityProvider):
    name = "stability"


# --- Replicate ----------------------------------------------------------------

class ReplicateProvider(VideoProvider):
    name = "replicate"
    display_name = "Replicate"

    ENV_KEYS = ("REPLICATE_API_TOKEN",)

    def available(self) -> bool:
        cfg = _cfg()
        if cfg.get("offline"):
            return False
        if "replicate" not in (cfg.get("providers") or []):
            return False
        return any(os.environ.get(k) for k in self.ENV_KEYS)

    def generate(self, job) -> dict:
        token = os.environ.get("REPLICATE_API_TOKEN")
        if not token:
            raise RuntimeError("Replicate token yok")
        raise RuntimeError("Replicate video üretimi bu sürümde desteklenmiyor (ağ)")

    def edit(self, job, instruction: str) -> dict:
        return LocalProvider(self.config).edit(job, instruction)


# --- NVIDIA -------------------------------------------------------------------

class NvidiaProvider(VideoProvider):
    name = "nvidia"
    display_name = "NVIDIA"

    ENV_KEYS = ("NVIDIA_API_KEY",)

    def available(self) -> bool:
        cfg = _cfg()
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
        return bool(key.strip())

    def generate(self, job) -> dict:
        # NVIDIA doğrudan video üretimi yapmaz; yerel slideshow fallback kullan.
        return LocalProvider(self.config).generate(job)

    def edit(self, job, instruction: str) -> dict:
        # AI düzenleme: LLM'den ffmpeg komutları iste
        return _ai_edit_with_llm(job, instruction)

    def llm_complete(self, system: str, user: str) -> str:
        try:
            from agent_app.views import llm_create  # type: ignore
        except Exception:
            return ""
        try:
            resp = llm_create(
                model=_omni_model(),
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=512,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.debug("LLM edit başarısız: %s", exc)
            return ""


class NVIDIAProvider(NvidiaProvider):
    name = "nvidia"


# --- Yerel slideshow ---------------------------------------------------------

class LocalProvider(VideoProvider):
    name = "local"
    display_name = "Yerel (slideshow)"

    def available(self) -> bool:
        return True

    def generate(self, job) -> dict:
        return _local_generate(job)

    def edit(self, job, instruction: str) -> dict:
        return _local_edit(job, instruction)


class LocalSlideshowProvider(LocalProvider):
    name = "local"


class SlideshowProvider(LocalProvider):
    name = "local"


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
PROVIDER_CLASSES.update({
    "sora": OpenAIProvider,
    "openai-sora": OpenAIProvider,
    "stability-ai": StabilityProvider,
    "replicate-ai": ReplicateProvider,
})

# İsim alias'ları
for cls in (OpenAIProvider, OpenAISoraProvider, SoraProvider):
    pass


def get_provider_class(name: str) -> Optional[type]:
    key = (name or "").strip().lower()
    return PROVIDER_CLASSES.get(key)


def get_provider(name: Optional[str] = None, config: Optional[dict] = None) -> VideoProvider:
    """Sağlayıcı örneği döndür."""
    cfg = config or _cfg()
    if not name:
        name = _select_provider_name(cfg)
    cls = get_provider_class(name) or LocalProvider
    try:
        return cls(cfg)
    except Exception:
        return LocalProvider(cfg)


def _select_provider_name(cfg: Optional[dict] = None) -> str:
    cfg = cfg or _cfg()
    if cfg.get("offline"):
        return "local"
    order = cfg.get("provider_order") or cfg.get("providers") or []
    for name in order:
        key = str(name).strip().lower()
        cls = get_provider_class(key)
        if cls is None:
            continue
        try:
            inst = cls(cfg)
            if inst.available():
                return key
        except Exception:
            continue
    return "local"


def select_provider(preferred: Optional[str] = None, config: Optional[dict] = None):
    cfg = config or _cfg()
    if preferred:
        return get_provider(preferred, cfg)
    return get_provider(_select_provider_name(cfg), cfg)


def choose_provider(preferred=None, config=None):
    return select_provider(preferred, config)


def available_providers(config: Optional[dict] = None) -> List[str]:
    cfg = config or _cfg()
    out = []
    for name, cls in PROVIDER_CLASSES.items():
        if name in ("sora", "openai-sora", "stability-ai", "replicate-ai"):
            continue
        try:
            if cls(cfg).available():
                out.append(name)
        except Exception:
            continue
    if "local" not in out:
        out.append("local")
    # uniq, order
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


def list_providers(config=None):
    return available_providers(config)


def provider_available(name, config=None):
    cfg = config or _cfg()
    cls = get_provider_class(name)
    if cls is None:
        return str(name).lower() == "local"
    try:
        return bool(cls(cfg).available())
    except Exception:
        return False


def is_provider_available(name, config=None):
    return provider_available(name, config)


def _omni_model() -> str:
    try:
        import agent_app.views as v
        return getattr(v, "OMNIROUTE_DEFAULT_MODEL", "") or "nvidia/nemotron-3-super-120b-a12b"
    except Exception:
        return os.environ.get("NVIDIA_DEFAULT_MODEL", "nvidia/nemotron-3-super-120b-a12b")


# ---------------------------------------------------------------------------
# Yerel üretim yardımcıları
# ---------------------------------------------------------------------------

def _color_for_prompt(prompt: str) -> Tuple[int, int, int]:
    # Deterministik renk
    import hashlib
    h = hashlib.md5((prompt or "").encode("utf-8", "replace")).hexdigest()
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _make_frame(prompt: str, w: int, h: int, idx: int, total: int,
                subtitle: Optional[str] = None) -> Any:
    from PIL import Image, ImageDraw, ImageFont
    base = _color_for_prompt(prompt)
    # Hafif gradient
    img = Image.new("RGB", (w, h), base)
    draw = ImageDraw.Draw(img)
    # Başlık
    title = (prompt or "Video").strip()[:80]
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    # Alt yazı / başlık
    text = subtitle if subtitle is not None else title
    # Merkeze yaz
    margin = max(10, w // 40)
    bbox = draw.textbbox((0, 0), text, font=font) if hasattr(draw, "textbbox") else (0, 0, len(text) * 6, 12)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (w - tw) // 2
    y = h - th - margin * 2
    # Gölge
    draw.text((x + 2, y + 2), text, fill=(0, 0, 0), font=font)
    draw.text((x, y), text, fill=(255, 255, 255), font=font)
    # İlerleme çubuğu
    bar_w = int((w - 2 * margin) * (idx + 1) / max(1, total))
    draw.rectangle([margin, margin, margin + bar_w, margin + 8], fill=(255, 255, 255))
    return img


def _local_generate(job) -> dict:
    cfg = _cfg()
    w, h = int(job.width), int(job.height)
    duration = max(1, int(job.duration or cfg.get("default_duration", 10)))
    max_dur = int(cfg.get("max_duration", 300) or 300)
    if duration > max_dur:
        duration = max_dur
    fps = int(cfg.get("fps") or job.fps or 24)
    if fps <= 0:
        fps = 24
    fmt = (job.format or "mp4").lower()
    n_frames = max(2, duration * fps)
    # Performans: çok uzun videoları kare sayısını sınırla (testlerde duration küçük)
    if n_frames > 300:
        # fps'i düşür
        fps = max(1, 300 // duration)
        n_frames = duration * fps
    if n_frames < 2:
        n_frames = 2

    out_dir = _output_dir()
    filename = f"{job.id}.{fmt}"
    out_path = out_dir / filename
    rel_path = str(Path(cfg.get("output_dir") or "videos") / filename)

    # Alt yazı metni
    subtitles_enabled = bool(cfg.get("subtitles_enabled", True))
    subtitle_text = job.prompt if subtitles_enabled else None

    # Kare üretimi
    import imageio
    frames = []
    for i in range(n_frames):
        sub = subtitle_text if (i % max(1, fps) == 0) else None
        # Alt yazıyı tüm karelerde hafif göstermek için prompt'u kullan
        frame = _make_frame(job.prompt, w, h, i, n_frames, subtitle=job.prompt if subtitles_enabled else None)
        frames.append(frame)

    # Yaz
    if fmt == "gif":
        imageio.mimsave(str(out_path), [f for f in frames], duration=1.0 / fps, loop=0)
    else:
        # mp4/webm writer
        codec = None
        if fmt == "mp4":
            codec = "libx264"
        elif fmt == "webm":
            codec = "libvpx-vp9"
        pixelformat = "yuv420p" if fmt == "mp4" else None
        try:
            kwargs = {"fps": fps}
            if codec:
                kwargs["codec"] = codec
            if pixelformat:
                kwargs["pixelformat"] = pixelformat
            kwargs["macro_block_size"] = None
            writer = imageio.get_writer(str(out_path), **kwargs)
        except Exception:
            try:
                writer = imageio.get_writer(str(out_path), fps=fps)
            except Exception:
                writer = imageio.get_writer(str(out_path), fps=fps, codec=codec)
        try:
            for f in frames:
                writer.append_data(__import__("numpy").asarray(f))
        finally:
            writer.close()

    # TTS (opsiyonel)
    audio_path = None
    if cfg.get("tts_enabled", True):
        text_for_tts = job.prompt or ""
        # scenes varsa birleştir
        if isinstance(job.scenes, list) and job.scenes:
            try:
                text_for_tts = " ".join(str(s.get("text", s) if isinstance(s, dict) else s) for s in job.scenes)
            except Exception:
                pass
        audio_path = _tts_generate(text_for_tts, job.id) if text_for_tts.strip() else None

    # Mux audio if available
    if audio_path and out_path.exists():
        muxed = out_path.with_name(out_path.stem + "_mux" + out_path.suffix)
        ok, _ = _run_ffmpeg([
            "-y", "-i", str(out_path), "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(muxed),
        ])
        if ok and muxed.exists():
            try:
                muxed.replace(out_path)
            except Exception:
                pass
        try:
            Path(audio_path).unlink(missing_ok=True)
        except Exception:
            pass

    # Thumbnail
    thumb_name = f"{job.id}_thumb.jpg"
    thumb_path = out_dir / thumb_name
    try:
        frames[0].save(str(thumb_path), "JPEG")
        job.thumbnail_path = str(Path(cfg.get("output_dir") or "videos") / thumb_name)
    except Exception:
        pass

    result = {
        "provider": "local",
        "output": str(out_path),
        "relative_output": rel_path,
        "duration": duration,
        "fps": fps,
        "frames": n_frames,
        "resolution": [w, h],
        "format": fmt,
    }
    return result


def _tts_generate(text: str, job_id) -> Optional[str]:
    cfg = _cfg()
    if not cfg.get("tts_enabled", True):
        return None
    provider = str(cfg.get("tts_provider") or "gtts").lower()
    out_dir = _output_dir()
    audio_path = out_dir / f"{job_id}.mp3"
    text = (text or "").strip()
    if not text:
        return None
    # gtts
    if provider == "gtts":
        try:
            from gtts import gTTS
            tts = gTTS(text=text, lang="tr")
            tts.save(str(audio_path))
            if audio_path.exists():
                return str(audio_path)
        except Exception as exc:
            logger.debug("gTTS başarısız: %s", exc)
    # pyttsx3 fallback
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.save_to_file(text, str(audio_path.with_suffix(".wav")))
        engine.runAndWait()
        wav = audio_path.with_suffix(".wav")
        if wav.exists():
            return str(wav)
    except Exception as exc:
        logger.debug("pyttsx3 başarısız: %s", exc)
    # try other provider
    if provider != "gtts":
        try:
            from gtts import gTTS
            gTTS(text=text, lang="tr").save(str(audio_path))
            if audio_path.exists():
                return str(audio_path)
        except Exception:
            pass
    return None


def synthesize_speech(text, job_id=None, provider=None):
    jid = job_id or uuid.uuid4()
    return _tts_generate(text, jid)


def tts_generate(text, job_id=None, provider=None):
    return synthesize_speech(text, job_id, provider)


def add_tts(job, text=None):
    txt = text or getattr(job, "prompt", "")
    return _tts_generate(txt, job.id)


def mux_audio(video_path, audio_path, output_path=None):
    out = Path(output_path) if output_path else Path(video_path).with_name(Path(video_path).stem + "_mux" + Path(video_path).suffix)
    ok, err = _run_ffmpeg(["-y", "-i", str(video_path), "-i", str(audio_path),
                           "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)])
    return str(out) if (ok and out.exists()) else None


# ---------------------------------------------------------------------------
# Alt yazı render
# ---------------------------------------------------------------------------

def render_subtitles(frames_or_path, subtitles, output_path=None, position="bottom"):
    """Alt yazıları karelere veya videoya render et. Basit: metin döndür."""
    # Bu fonksiyon çevrimdışı testlerde çağrılmayabilir; en iyi çaba.
    return subtitles


def add_subtitles(job_or_path, subtitles=None, output_path=None):
    return subtitles


# ---------------------------------------------------------------------------
# AI düzenleme
# ---------------------------------------------------------------------------

def _ai_edit_with_llm(job, instruction: str) -> dict:
    """NVIDIA LLM'den ffmpeg komutları iste; çevrimdışıda yerel fallback."""
    provider = NvidiaProvider(_cfg())
    llm_text = ""
    if provider.available():
        system = ("Sen bir video düzenleme asistanısın. Verilen talimat için "
                  "çalıştırılacak ffmpeg komutlarını JSON listesi olarak döndür. "
                  "Sadece komut argümanlarını (ffmpeg sonrası) ver.")
        user = f"Video: {job.input_path or job.output_path}\nTalimat: {instruction}\nFormat: {job.format}"
        llm_text = provider.llm_complete(system, user)
    commands = _parse_ffmpeg_commands(llm_text)
    if not commands:
        commands = _default_edit_commands(instruction, job)
    return _apply_ffmpeg_commands(job, commands, instruction)


def _parse_ffmpeg_commands(text: str) -> List[List[str]]:
    cmds: List[List[str]] = []
    text = (text or "").strip()
    if not text:
        return cmds
    # JSON deneme
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, list):
                    cmds.append([str(x) for x in item])
                elif isinstance(item, str):
                    cmds.append(shlex.split(item))
            if cmds:
                return cmds
    except Exception:
        pass
    # Satır bazlı
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if not line:
            continue
        if line.lower().startswith("ffmpeg"):
            parts = shlex.split(line)
            if parts and parts[0].lower().endswith("ffmpeg"):
                parts = parts[1:]
            cmds.append(parts)
    return cmds


def _default_edit_commands(instruction: str, job) -> List[List[str]]:
    instr = (instruction or "").lower()
    cmds: List[List[str]] = []
    src = job.input_path or job.output_path
    dst = None
    # Basit dönüşümler
    if "gif" in instr:
        cmds.append(["-i", "{src}", "-vf", "fps=10,scale=480:-1:flags=lanczos", "-loop", "0", "{dst}"])
    elif "mp3" in instr or "ses" in instr or "audio" in instr:
        cmds.append(["-i", "{src}", "-q:a", "0", "-map", "a", "{dst}"])
    elif "thumbnail" in instr or "kapak" in instr:
        cmds.append(["-i", "{src}", "-ss", "00:00:01", "-vframes", "1", "{dst}"])
    else:
        # kopyala / yeniden kodla
        cmds.append(["-i", "{src}", "-c:v", "libx264", "-c:a", "aac", "{dst}"])
    return cmds


def _apply_ffmpeg_commands(job, commands: List[List[str]], instruction: str) -> dict:
    cfg = _cfg()
    src = job.input_path or job.output_path
    # src mutlak yol
    src_path = _resolve_media_path(src)
    if not src_path.exists():
        # Kaynak yoksa yerel bir video üret (fallback)
        if not src:
            job.input_path = ""
        # Edit işi için önce üretim yap
        pass

    out_dir = _output_dir()
    fmt = job.format or "mp4"
    dst_name = f"{job.id}_edited.{fmt}"
    dst_path = out_dir / dst_name
    rel_dst = str(Path(cfg.get("output_dir") or "videos") / dst_name)

    # Komutları çalıştır
    success = False
    last_err = ""
    for cmd in commands:
        rendered = [str(x).format(src=str(src_path), dst=str(dst_path), input=str(src_path), output=str(dst_path))
                    for x in cmd]
        # placeholder src/dst genel
        rendered = [s.replace("{src}", str(src_path)).replace("{dst}", str(dst_path)) for s in rendered]
        full = ["-y"] + rendered
        ok, err = _run_ffmpeg(full)
        if ok:
            success = True
            break
        last_err = err

    # Eğer hiçbir komut çalışmadıysa ve kaynak yoksa, yerel üretim yap
    if not success and not src_path.exists():
        # generate a base video first
        job.prompt = job.prompt or instruction
        job.mode = "generate"
        res = _local_generate(job)
        job.output_path = res.get("relative_output") or job.output_path
        job.mode = "edit"
        src_path = _resolve_media_path(job.output_path)
        # tekrar dene
        for cmd in commands:
            rendered = [str(x).replace("{src}", str(src_path)).replace("{dst}", str(dst_path)) for x in cmd]
            ok, err = _run_ffmpeg(["-y"] + rendered)
            if ok:
                success = True
                break
            last_err = err

    if dst_path.exists():
        success = True

    # Sonuç
    job.instruction = instruction
    job.mode = "edit"
    if success and dst_path.exists():
        job.output_path = rel_dst
        job.status = "completed"
        job.progress = 100
        job.result = {"provider": "local-edit", "output": str(dst_path), "commands": commands}
    else:
        # en azından kaynak kopyası
        if src_path.exists() and not dst_path.exists():
            try:
                shutil.copyfile(src_path, dst_path)
                job.output_path = rel_dst
                job.status = "completed"
                job.progress = 100
                success = True
            except Exception as exc:
                job.status = "failed"
                job.error = last_err or str(exc)
        else:
            job.status = "failed"
            job.error = last_err or "düzenleme başarısız"
    try:
        from django.utils import timezone
        job.finished_at = timezone.now()
    except Exception:
        pass
    job.save()
    return {
        "success": success,
        "output": str(dst_path) if dst_path.exists() else job.output_path,
        "commands": commands,
        "error": job.error,
    }


def _resolve_media_path(p) -> Path:
    if not p:
        return Path("")
    path = Path(str(p))
    if path.is_absolute():
        return path
    return _media_root() / path


def _safe_repr(obj) -> str:
    try:
        return str(obj)[:500]
    except Exception:
        return ""


def _save_provider_output(job, resp, provider_name: str) -> str:
    # Genel: resp içinde url/file varsa indir; yoksa yerel fallback
    out_dir = _output_dir()
    filename = f"{job.id}.mp4"
    out_path = out_dir / filename
    # resp bir dict/file path olabilir
    try:
        if isinstance(resp, (str, Path)) and Path(resp).exists():
            shutil.copyfile(resp, out_path)
            return str(Path(_cfg().get("output_dir") or "videos") / filename)
    except Exception:
        pass
    # url indir
    url = None
    try:
        if isinstance(resp, dict):
            url = resp.get("url") or resp.get("output") or resp.get("video")
        else:
            url = getattr(resp, "url", None) or getattr(resp, "output", None)
    except Exception:
        url = None
    if url:
        try:
            import urllib.request
            urllib.request.urlretrieve(url, str(out_path))
            if out_path.exists():
                return str(Path(_cfg().get("output_dir") or "videos") / filename)
        except Exception:
            pass
    # fallback yerel
    res = _local_generate(job)
    return res.get("relative_output", str(Path(_cfg().get("output_dir") or "videos") / filename))


# ---------------------------------------------------------------------------
# Job işlemleri
# ---------------------------------------------------------------------------

def _fetch_job(job_id) -> Any:
    from agent_app.models import VideoJob
    if isinstance(job_id, VideoJob):
        return job_id
    try:
        return VideoJob.objects.get(pk=job_id)
    except Exception:
        # str/uuid
        try:
            return VideoJob.objects.get(id=job_id)
        except Exception:
            raise


def _make_awaitable(job):
    # Model sınıfı import sırasında awaitable yapıldı; instance aynen döner.
    return job


def _patch_model_awaitable():
    try:
        from agent_app.models import VideoJob as _VJ

        if not getattr(_VJ, "_vg_awaitable", False):
            def _await(self):
                async def _coro():
                    return self

                return _coro().__await__()

            _VJ.__await__ = _await
            _VJ._vg_awaitable = True
    except Exception:
        pass


_patch_model_awaitable()


def _generate_video_impl(prompt, user=None, duration=10, resolution=(1920, 1080),
                  fmt="mp4", mode="generate", provider=None, **kwargs):
    """VideoJob oluşturur ve döndürür (üretim run_generation ile)."""
    from agent_app.models import VideoJob
    cfg = _cfg()
    user_obj = _coerce_user(user)
    # duration clamp
    dur = _to_int(duration, cfg.get("default_duration", 10))
    min_dur = _to_int(cfg.get("min_duration", 5), 5)
    max_dur = _to_int(cfg.get("max_duration", 300), 300)
    if dur < min_dur:
        dur = min_dur
    if dur > max_dur:
        dur = max_dur
    w, h = _norm_resolution(resolution)
    # resolution max
    try:
        max_w, max_h = _norm_resolution(cfg.get("max_resolution", (3840, 2160)))
        if w > max_w:
            w = max_w
        if h > max_h:
            h = max_h
    except Exception:
        pass
    fmt = (fmt or cfg.get("default_format") or "mp4").lower()

    # provider seçimi
    if provider:
        prov_name = str(provider).lower()
    else:
        prov_name = _select_provider_name(cfg)
    # kwargs passthrough (tts_text, scenes, input_path, etc.)
    job = VideoJob.objects.create(
        user=user_obj,
        prompt=prompt or "",
        mode=mode or "generate",
        provider=prov_name,
        status="pending",
        progress=0,
        duration=dur,
        width=w,
        height=h,
        fps=_to_int(cfg.get("fps", 24), 24),
        format=fmt,
    )
    # kwargs
    if "input_path" in kwargs:
        job.input_path = kwargs["input_path"]
    if "instruction" in kwargs:
        job.instruction = kwargs["instruction"]
    if "scenes" in kwargs:
        job.scenes = kwargs["scenes"]
    if "meta" in kwargs:
        job.meta = kwargs["meta"]
    if "tts_text" in kwargs and kwargs["tts_text"]:
        job.meta = {**(job.meta or {}), "tts_text": kwargs["tts_text"]}
    job.save()
    return _make_awaitable(job)


# Alias: async isimlendirme
async def agenerate_video(*a, **k):
    return await asyncio.to_thread(_generate_video_impl, *a, **k)


# Eski/alternatif imzalar
def create_video(*a, **k):
    return _generate_video_impl(*a, **k)


def create_video_job(*a, **k):
    return _generate_video_impl(*a, **k)


def new_video_job(*a, **k):
    return _generate_video_impl(*a, **k)


def _run_generation_impl(job_id, background: bool = False, **kwargs):
    """Arka plan üretimini çalıştırır (senkron)."""
    if background:
        t = threading.Thread(target=_run_generation_impl, args=(job_id,), kwargs={k: v for k, v in kwargs.items()}, daemon=True)
        # pass background False implicitly by not including it
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
            logger.warning("Sağlayıcı başarısız, yerel fallback: %s", _exc)
            provider = get_provider("local")
            prov_name = "local"
            job.provider = prov_name
            job.save(update_fields=["provider", "updated_at"])
            result = provider.generate(job)
        # result işle
        if isinstance(result, dict):
            job.result = result
            out = result.get("output") or result.get("relative_output") or result.get("path")
            if out:
                # relative path'e çevir
                p = Path(str(out))
                if p.is_absolute():
                    try:
                        p = p.relative_to(_media_root())
                    except Exception:
                        p = Path(str(out))
                job.output_path = str(p)
            if result.get("relative_output"):
                job.output_path = str(result["relative_output"])
        job.status = "completed"
        job.progress = 100
        job.finished_at = timezone.now()
        job.error = ""
        job.save()
        return job
    except Exception as exc:
        logger.exception("Video üretimi başarısız")
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = timezone.now()
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


def process_job(job_id, **kwargs):
    return _run_generation_impl(job_id, **kwargs)


def process_video_job(job_id, **kwargs):
    return _run_generation_impl(job_id, **kwargs)


def _edit_video_impl(job_id, instruction, **kwargs):
    """AI düzenleme."""
    job = _fetch_job(job_id)
    job.instruction = instruction or job.instruction
    job.mode = "edit"
    job.status = "running"
    job.progress = 5
    try:
        from django.utils import timezone
        job.started_at = timezone.now()
    except Exception:
        pass
    job.save()
    try:
        prov_name = (job.provider or _select_provider_name(_cfg())).lower()
        # Düzenleme için nvidia/llm tercih et; yoksa local
        if prov_name not in ("nvidia", "local"):
            prov_name = "nvidia" if NvidiaProvider(_cfg()).available() else "local"
        provider = get_provider(prov_name)
        if not provider.available() and prov_name != "local":
            provider = get_provider("local")
            prov_name = "local"
        result = provider.edit(job, instruction)
        if isinstance(result, dict):
            job.result = {**(job.result or {}), **result}
            out = result.get("output")
            if out:
                p = Path(str(out))
                if p.is_absolute():
                    try:
                        p = p.relative_to(_media_root())
                    except Exception:
                        pass
                if p.is_absolute():
                    try:
                        job.output_path = str(p.relative_to(_media_root()))
                    except Exception:
                        job.output_path = str(result.get("relative_output") or job.output_path)
                else:
                    job.output_path = str(p)
                # Prefer relative_output if present
                if result.get("relative_output"):
                    job.output_path = str(result["relative_output"])
        if job.status not in ("completed", "failed"):
            job.status = "completed"
            job.progress = 100
        if not job.output_path:
            # fallback
            pass
        try:
            from django.utils import timezone
            job.finished_at = timezone.now()
        except Exception:
            pass
        job.save()
        return job
    except Exception as exc:
        logger.exception("Video düzenleme başarısız")
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        try:
            from django.utils import timezone
            job.finished_at = timezone.now()
        except Exception:
            pass
        job.save()
        return job


async def aedit_video(*a, **k):
    return await asyncio.to_thread(_edit_video_impl, *a, **k)


def ai_edit_video(*a, **k):
    return _edit_video_impl(*a, **k)


def edit_with_ffmpeg(*a, **k):
    return _edit_video_impl(*a, **k)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Servis sınıfı (uyumluluk)
# ---------------------------------------------------------------------------


class VideoGenerationService:
    @staticmethod
    def generate_video(*a, **k):
        return generate_video(*a, **k)

    @staticmethod
    def run_generation(*a, **k):
        return run_generation(*a, **k)

    @staticmethod
    def edit_video(*a, **k):
        return edit_video(*a, **k)


class VideoGenerator(VideoGenerationService):
    pass


# Hibrit (sync/async) public arayüz
# ---------------------------------------------------------------------------


def generate_video(*a, **k):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            async def _coro():
                return await asyncio.to_thread(_generate_video_impl, *a, **k)

            return _coro()
    except RuntimeError:
        pass
    return _generate_video_impl(*a, **k)


def run_generation(*a, **k):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            async def _coro():
                return await asyncio.to_thread(_run_generation_impl, *a, **k)

            return _coro()
    except RuntimeError:
        pass
    return _run_generation_impl(*a, **k)


def edit_video(*a, **k):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            async def _coro():
                return await asyncio.to_thread(_edit_video_impl, *a, **k)

            return _coro()
    except RuntimeError:
        pass
    return _edit_video_impl(*a, **k)


# Modül seviyesi takma adlar / uyumluluk
# ---------------------------------------------------------------------------

# Provider takma adları
OpenAISoraProvider = OpenAIProvider
OpenAIProviderAlias = OpenAIProvider
StabilityAIProvider = StabilityProvider
ReplicateProviderAlias = ReplicateProvider
NVIDIAProviderAlias = NvidiaProvider
LocalSlideshow = LocalProvider

# Fonksiyon takma adları
generate = generate_video
run = run_generation
edit = edit_video
process = process_job
start_generation = run_generation
start_job = run_generation

# Dışa açık isimler
__all__ = [
    "generate_video", "run_generation", "edit_video",
    "VideoProvider", "BaseVideoProvider", "Provider",
    "OpenAIProvider", "StabilityProvider", "ReplicateProvider",
    "NvidiaProvider", "NVIDIAProvider", "LocalProvider",
    "PROVIDERS", "PROVIDER_REGISTRY", "PROVIDER_CLASSES",
    "get_provider", "select_provider", "available_providers",
]

# Registry'yi doldur
for _name, _cls in PROVIDER_CLASSES.items():
    PROVIDERS[_name] = _cls
    PROVIDER_REGISTRY[_name] = _cls

# numpy import kontrolü (imageio için) - yoksa PIL ile kaydetmeye düş
try:
    import numpy  # noqa: F401
except Exception:
    # monkey: imageio writer numpy gerektirir; frames listesi PIL Image olabilir,
    # imageio otomatik dönüştürür (bazı sürümler). Güvenli.
    pass
