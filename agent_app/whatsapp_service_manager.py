"""WhatsApp (Baileys) microservice yaşam döngüsü yardımcıları.

Django, kullanıcı WhatsApp bağlantısını başlattığında servis kapalıysa
`whatsapp_service` klasöründeki Node sürecini otomatik başlatır.
Uzak (localhost dışı) bir servis URL'i tanımlıysa asla süreç başlatılmaz.
"""
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_start_lock = threading.Lock()
_last_start_attempt = 0.0
_START_COOLDOWN_SECONDS = 20


def get_service_url():
    return str(
        getattr(settings, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:3002") or ""
    ).rstrip("/")


def _is_local_url(url):
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _service_is_healthy(timeout=1.5):
    url = get_service_url()
    if not url:
        return False
    try:
        response = requests.get(f"{url}/health", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _spawn_service():
    """Node servisini Django sürecinden bağımsız olarak başlat."""
    service_dir = Path(settings.BASE_DIR) / "whatsapp_service"
    entrypoint = service_dir / "src" / "index.js"
    if not entrypoint.exists():
        logger.error("WhatsApp service entrypoint bulunamadı: %s", entrypoint)
        return False

    node_bin = shutil.which("node") or shutil.which("node.exe")
    if not node_bin:
        logger.error("WhatsApp servisi başlatılamadı: node bulunamadı")
        return False

    log_dir = Path(settings.BASE_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "whatsapp_service.log"

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    try:
        with open(log_path, "ab") as log_file:
            subprocess.Popen(
                [node_bin, str(entrypoint)],
                cwd=str(service_dir),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                close_fds=True,
            )
    except OSError as exc:
        logger.error("WhatsApp servisi başlatılamadı: %s", exc)
        return False

    logger.info("WhatsApp servisi başlatıldı (log: %s)", log_path)
    return True


def ensure_service_running(wait_seconds=12):
    """Servis sağlıklı değilse yerelde başlatmayı dene.

    Servis hazır olduğunda True, aksi halde False döner.
    """
    url = get_service_url()
    if not url:
        return False
    if _service_is_healthy():
        return True
    if not _is_local_url(url):
        return False

    global _last_start_attempt
    with _start_lock:
        if _service_is_healthy():
            return True
        now = time.monotonic()
        if now - _last_start_attempt >= _START_COOLDOWN_SECONDS:
            _last_start_attempt = now
            if not _spawn_service():
                return False

    deadline = time.monotonic() + max(0, wait_seconds)
    while time.monotonic() < deadline:
        if _service_is_healthy():
            return True
        time.sleep(0.4)
    return _service_is_healthy()
