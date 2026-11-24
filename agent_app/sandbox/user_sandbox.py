"""
Multi-tenant per-user sandbox manager.

Her kullanıcı kendi izole Docker container'ında çalışır.
Preview, /preview/<user_id>/<project_id>/ path'i ile reverse proxy üzerinden erişilir.
Portlar host'a expose edilmez, sadece internal network üzerinden erişilir.
"""
import docker
import json
import logging
import time
import threading
from pathlib import Path
from django.conf import settings

logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

INTERNAL_NETWORK = "emog-internal"
SANDBOX_IMAGE = "emog-sandbox:latest"  # Kullanıcı projeleri için image
SANDBOX_PREFIX = "emog-user"

# Resource limits per user container
USER_CPU_LIMIT = "1.0"          # 1 CPU
USER_MEMORY_LIMIT = "512m"      # 512MB RAM
USER_PIDS_LIMIT = 128
USER_TIMEOUT_SECONDS = 3600     # 1 saat idle timeout

# Container lifecycle
CONTAINER_IDLE_TIMEOUT = 1800   # 30 dk boyunca istek gelmezse durdur
CONTAINER_MAX_LIFETIME = 7200   # En fazla 2 saat çalışır

_client = None
_client_lock = threading.Lock()
# user_id -> container info
_containers = {}
_containers_lock = threading.Lock()


def get_docker_client():
    global _client
    with _client_lock:
        if _client is None:
            try:
                _client = docker.from_env()
            except Exception as e:
                logger.error(f"Docker connection failed: {e}")
                raise
        return _client


def _container_name(user_id, project_id):
    return f"{SANDBOX_PREFIX}-{user_id}-{project_id}"


def _ensure_network():
    client = get_docker_client()
    try:
        client.networks.get(INTERNAL_NETWORK)
    except docker.errors.NotFound:
        client.networks.create(INTERNAL_NETWORK, driver="bridge", internal=True)


def _get_user_project_path(user_id, project_id):
    """Kullanıcının proje dizinini döndürür."""
    base = Path(settings.BASE_DIR) / "projects" / str(user_id) / str(project_id)
    return base


def start_user_container(user_id, project_id, dev_command=None, port=3000):
    """
    Kullanıcının projesi için izole container başlatır.
    Container internal network'te, host'a port expose edilmez.
    """
    client = get_docker_client()
    _ensure_network()

    name = _container_name(user_id, project_id)
    project_path = _get_user_project_path(user_id, project_id)

    if not project_path.exists():
        return {"ok": False, "error": "Project directory not found"}

    # Mevcut container varsa kaldır
    stop_user_container(user_id, project_id)

    # Container config
    config = {
        "image": SANDBOX_IMAGE,
        "name": name,
        "detach": True,
        "network": INTERNAL_NETWORK,
        "cpu_quota": int(float(USER_CPU_LIMIT) * 100000),
        "mem_limit": USER_MEMORY_LIMIT,
        "pids_limit": USER_PIDS_LIMIT,
        "restart_policy": {"Name": "no"},
        "labels": {
            "emog.user_id": str(user_id),
            "emog.project_id": str(project_id),
            "emog.managed": "true",
        },
        "environment": {
            "PORT": str(port),
            "HOST": "0.0.0.0",
        },
        "working_dir": "/workspace",
        "volumes": {
            str(project_path): {"bind": "/workspace", "mode": "rw"},
        },
        "command": dev_command or self._detect_start_command(project_path),
    }

    try:
        container = client.containers.run(**config)
        info = {
            "container_id": container.id[:12],
            "name": name,
            "user_id": user_id,
            "project_id": project_id,
            "port": port,
            "internal_url": f"http://{name}:{port}",
            "proxy_path": f"/preview/{user_id}/{project_id}/",
            "started_at": time.time(),
            "status": "running",
        }
        with _containers_lock:
            _containers[f"{user_id}:{project_id}"] = info
        logger.info(f"Started sandbox for user={user_id} project={project_id} container={name}")
        return {"ok": True, **info}
    except Exception as e:
        logger.error(f"Failed to start container for user={user_id} project={project_id}: {e}")
        return {"ok": False, "error": str(e)}


def _detect_start_command(project_path):
    """Proje tipine göre start komutunu tespit et."""
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            scripts = data.get("scripts", {})
            if "dev" in scripts:
                return "npm run dev"
            if "start" in scripts:
                return "npm start"
        except Exception:
            pass

    # Python project
    for candidate in ["manage.py", "app.py", "main.py", "server.py", "run.py"]:
        if (project_path / candidate).exists():
            return f"python {candidate}"

    # Static HTML
    if (project_path / "index.html").exists():
        return "python -m http.server 3000"

    return "sleep infinity"


def stop_user_container(user_id, project_id):
    """Kullanıcının container'ını durdur ve sil."""
    client = get_docker_client()
    name = _container_name(user_id, project_id)
    try:
        container = client.containers.get(name)
        container.stop(timeout=5)
        container.remove(force=True)
        logger.info(f"Stopped sandbox user={user_id} project={project_id}")
    except docker.errors.NotFound:
        pass
    except Exception as e:
        logger.error(f"Failed to stop container {name}: {e}")

    with _containers_lock:
        _containers.pop(f"{user_id}:{project_id}", None)


def get_container_status(user_id, project_id):
    """Container durumunu sorgula."""
    client = get_docker_client()
    name = _container_name(user_id, project_id)
    try:
        container = client.containers.get(name)
        container.reload()
        return {
            "ok": True,
            "status": container.status,
            "running": container.status == "running",
            "container_id": container.id[:12],
        }
    except docker.errors.NotFound:
        return {"ok": False, "status": "not_found", "running": False}


def get_preview_url(user_id, project_id):
    """Kullanıcının preview URL'ini döndürür (reverse proxy path)."""
    return f"/preview/{user_id}/{project_id}/"


def list_user_containers(user_id):
    """Kullanıcının tüm aktif container'larını listele."""
    client = get_docker_client()
    containers = []
    try:
        for c in client.containers.list(all=True, filters={"label": f"emog.user_id={user_id}"}):
            labels = c.labels
            containers.append({
                "container_id": c.id[:12],
                "name": c.name,
                "project_id": labels.get("emog.project_id"),
                "status": c.status,
            })
    except Exception as e:
        logger.error(f"Failed to list containers for user={user_id}: {e}")
    return containers


def cleanup_stale_containers():
    """Süresi dolmuş container'ları temizle. Background thread tarafından çağrılır."""
    client = get_docker_client()
    try:
        for c in client.containers.list(all=True, filters={"label": "emog.managed=true"}):
            c.reload()
            labels = c.labels
            created = c.attrs.get("Created", "")
            # Container'ın ne kadar çalıştığını kontrol et
            if c.status == "running":
                # Idle timeout kontrolü - son activity zamanı
                pass  # TODO: implement idle tracking
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


# Singleton instance
_manager = None
_manager_lock = threading.Lock()


class UserSandboxManager:
    """Thread-safe singleton sandbox manager."""

    def __init__(self):
        self._start_command_cache = {}

    def start(self, user_id, project_id, dev_command=None, port=3000):
        return start_user_container(user_id, project_id, dev_command, port)

    def stop(self, user_id, project_id):
        stop_user_container(user_id, project_id)

    def status(self, user_id, project_id):
        return get_container_status(user_id, project_id)

    def preview_url(self, user_id, project_id):
        return get_preview_url(user_id, project_id)

    def list(self, user_id):
        return list_user_containers(user_id)

    def is_running(self, user_id, project_id):
        status = get_container_status(user_id, project_id)
        return status.get("running", False)

    def ensure_running(self, user_id, project_id, dev_command=None, port=3000):
        """Container çalışmıyorsa başlat."""
        if not self.is_running(user_id, project_id):
            return self.start(user_id, project_id, dev_command, port)
        return {"ok": True, "status": "already_running"}


def get_sandbox_manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = UserSandboxManager()
        return _manager
