"""
Docker-free preview system.

Docker olmadığında basit HTTP server ile preview sunar.
"""
import json
import logging
import os
import subprocess
import sys
import time
import threading
from pathlib import Path
from django.conf import settings

logger = logging.getLogger(__name__)

# user_id:project_id -> process info
_local_servers = {}
_lock = threading.Lock()

# Build araclari ve ciktilari asla "uygulama dizini" sayilmaz.
# (Node her seviyede node_modules olusturdugu icin oncelikle eleme kritik:
#  aksi halde preview, node_modules dizin listesini servis eder.)
_SKIP_DIRS = {
    "uploads", "__pycache__", ".git", "node_modules", "dist", "build", "out",
    "coverage", ".next", ".nuxt", ".output", ".turbo", ".vite", ".cache",
    "logs", "log", "tmp", "temp", "vendor", ".venv", "venv", "env",
    "docs", "doc", "test", "tests", "e2e", "__snapshots__",
}

# Calistirilabilir node scriptleri (varsa "node" server tipi)
_NODE_SCRIPT_SIGNALS = ("dev", "start", "serve")


def _read_package_scripts(directory):
    """package.json scripts dict'i; okunamazsa None."""
    pf = directory / "package.json"
    if not pf.exists():
        return None
    try:
        return json.loads(pf.read_text(encoding="utf-8")).get("scripts") or {}
    except Exception:
        return None


def _app_score(directory):
    """Dizinin calisabilir bir uygulama olma skoru (yuksek = daha iyi)."""
    s = 0
    if (directory / "index.html").exists() or (directory / "index.htm").exists():
        s += 50
    if (directory / "manage.py").exists():
        s += 80
    for f in ("app.py", "main.py", "server.py", "run.py", "index.py"):
        if (directory / f).exists():
            s += 60
    scripts = _read_package_scripts(directory)
    if scripts is not None:
        s += 30
        if any(k in scripts for k in _NODE_SCRIPT_SIGNALS):
            s += 40
        if (directory / "package-lock.json").exists() or (directory / "pnpm-lock.yaml").exists():
            s += 3
    if (directory / "src").is_dir():
        s += 20
    if (directory / "app").is_dir() or (directory / "pages").is_dir():
        s += 15
    if (directory / "public").is_dir() or (directory / "static").is_dir():
        s += 5
    if (directory / "vite.config.js").exists() or (directory / "vite.config.ts").exists():
        s += 6
    if (directory / "next.config.js").exists() or (directory / "next.config.mjs").exists():
        s += 10
    if (directory / "dist" / "index.html").exists():
        s += 4
    return s


def _project_path(user_id, project_id):
    """Kullanıcının proje dizinini döndürür. Flat ve user-scoped layout destekler."""
    base = Path(settings.BASE_DIR) / "projects"
    
    # 1. User-scoped: projects/<user_id>/<project_id>/
    user_scoped = base / str(user_id) / str(project_id)
    if user_scoped.exists():
        return _resolve_app_dir(user_scoped)
    
    # 2. Flat: projects/<project_id>/
    flat = base / str(project_id)
    if flat.exists():
        return _resolve_app_dir(flat)
    
    return None

def _resolve_app_dir(project_path):
    """Uygulamanin calistigi dizini bul.

    Once project.json'daki app_dir'e bakilir; yoksa kok + alt klasorler
    skorlanir (node_modules / dist / .git vb. eleme disi) ve en iyi aday secilir.
    """
    # 1) project.json'dan app_dir oku (kesin tercih)
    meta_file = project_path / "project.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            app_dir = meta.get("app_dir")
            if app_dir:
                app_path = project_path / app_dir
                if app_path.exists() and app_path.is_dir():
                    return app_path
        except Exception:
            pass

    # 2) Adaylar: proje koku + eleme listesine takilmayan alt klasorler
    candidates = [project_path]
    try:
        subdirs = [d for d in project_path.iterdir() if d.is_dir()]
    except OSError:
        subdirs = []
    for d in subdirs:
        name = d.name
        if name.startswith(".") or name.lower() in _SKIP_DIRS:
            continue
        candidates.append(d)

    best, best_score = None, 0
    for cand in candidates:
        score = _app_score(cand)
        if score > best_score:
            best, best_score = cand, score
    if best is not None:
        return best

    # 3) Hicbir aday sinyal vermiyorsa: eleme disindaki ilk alt klasor
    if len(candidates) > 1:
        return candidates[1]
    return project_path

def _find_port(user_id, project_id):
    """Boş bir port bul."""
    import socket
    # Her kullanıcı-proje çifti için deterministik port aralığı
    base_port = 3100 + (hash(f"{user_id}:{project_id}") % 500)
    for port in range(base_port, base_port + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return None

def _detect_server_type(project_path):
    """Proje tipini tespit et."""
    # Node.js — sadece calistirilabilir script VARSA gercekten node
    scripts = _read_package_scripts(project_path)
    if scripts is not None and any(k in scripts for k in _NODE_SCRIPT_SIGNALS):
        return "node"
    
    # Django projesi
    if (project_path / "manage.py").exists():
        return "django"
    
    # Python projesi
    for f in ["app.py", "main.py", "server.py", "run.py", "index.py"]:
        if (project_path / f).exists():
            return "python"
    
    # node ama dev/start scripti yok: varsa derlenmis ciktiyi static sun
    if (project_path / "dist" / "index.html").exists():
        return "static-dist"
    
    # Static HTML - herhangi bir html dosyası varsa
    html_files = list(project_path.glob("*.html"))
    if html_files:
        return "static"
    
    # İçerik varsa static olarak serve et
    if any(project_path.iterdir()):
        return "static"
    
    return "empty"

def _get_start_command(project_path, server_type, port):
    """Start komutunu döndür."""
    if server_type == "node":
        scripts = _read_package_scripts(project_path) or {}
        # Vite icin --host gerekir, Next.js icin PORT env yeterli, --host hata verir
        is_nextjs = any((project_path / f).exists() for f in
            ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.mts"))
        if "dev" in scripts:
            if is_nextjs:
                return f"npm run dev -- --port {port}"
            return f"npm run dev -- --port {port} --host 127.0.0.1"
        if "start" in scripts:
            return f"npm start"
        return f"npm start"
    
    elif server_type == "django":
        return f"{sys.executable} manage.py runserver 127.0.0.1:{port} --noreload"
    
    elif server_type == "python":
        for f in ["app.py", "main.py", "server.py", "run.py", "index.py"]:
            if (project_path / f).exists():
                return f"{sys.executable} {f}"
    
    return None

def _serve_dir(project_path, server_type):
    """Static/DOSYA sunumunda hangi dizinin servis edilecegi."""
    if server_type == "static-dist":
        dist = project_path / "dist"
        if (dist / "index.html").exists():
            return dist
    return project_path


def _ensure_dependencies(project_path, server_type):
    """Proje bağımlılıklarını kur (node_modules vb.)."""
    if server_type == "node":
        node_modules = project_path / "node_modules"
        if not node_modules.exists():
            logger.info(f"Installing npm dependencies for {project_path}")
            try:
                subprocess.run(
                    "npm install",
                    shell=True,
                    cwd=str(project_path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
            except Exception as e:
                logger.warning(f"npm install failed: {e}")

def _wait_http_ready(port, timeout=15.0):
    """Yerel sunucu HTTP 200 dondurana kadar bekle.

    Sabit `sleep` yerine bu kullanilir: Vite/Next ilk saniyelerde henuz hazir
    olmayabilir ve iframe'e hazir olmadan once 404 yazilir.
    """
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError:
            # Sunucu ayakta (404 vb.) ama uygulama henuz hazir degil -> bekle
            pass
        except Exception:
            pass
        time.sleep(0.4)
    return False


def start_local_server(user_id, project_id):
    """
    Docker olmadan lokal server başlatır.
    Static dosyalar için basit HTTP server, Node/Python için subprocess.
    """
    project_path = _project_path(user_id, project_id)
    if not project_path:
        return {"ok": False, "error": "Proje dizini bulunamadı"}
    
    key = f"{user_id}:{project_id}"
    
    with _lock:
        # Zaten çalışıyorsa
        if key in _local_servers:
            info = _local_servers[key]
            if info.get("process") and info["process"].poll() is None:
                return {"ok": True, "url": info.get("url"), "port": info.get("port")}
            # Ölü process'i temizle
            _local_servers.pop(key, None)
    
    port = _find_port(user_id, project_id)
    if not port:
        return {"ok": False, "error": "Boş port bulunamadı"}
    
    server_type = _detect_server_type(project_path)
    
    if server_type == "empty":
        # Boş proje - basit bir placeholder sayfası serve et
        return _start_placeholder_server(project_path, port, key, user_id, project_id)
    
    try:
        # Bağımlılıkları kur
        _ensure_dependencies(project_path, server_type)
        
        if server_type in ("static", "static-dist"):
            # Basit HTTP server (derlenmis cikti varsa dist/ servis edilir)
            serve_dir = _serve_dir(project_path, server_type)
            process = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                cwd=str(serve_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            cmd = _get_start_command(project_path, server_type, port)
            if not cmd:
                return {"ok": False, "error": "Start komutu bulunamadı"}
            
            env = os.environ.copy()
            env["PORT"] = str(port)
            env["HOST"] = "127.0.0.1"
            env["NODE_ENV"] = "development"
            
            process = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(project_path),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        
        # Hazir olana kadar bekle: surec yasarken "/" 200 donene dek
        # (Vite/Next ilk anlarda 404 donebilir; hazir degilken cevirici yanit yazilmasin)
        _wait_http_ready(port, timeout=15.0)
        
        # Process hâlâ çalışıyor mu kontrol et
        if process.poll() is not None:
            return {"ok": False, "error": f"Server başlatılamadı (exit code: {process.returncode})"}
        
        url = f"/preview/local/{user_id}/{project_id}/"
        info = {
            "process": process,
            "port": port,
            "url": url,
            "server_type": server_type,
            "started_at": time.time(),
        }
        
        with _lock:
            _local_servers[key] = info
        
        logger.info(f"Started local server for user={user_id} project={project_id} port={port} type={server_type}")
        return {"ok": True, "url": url, "port": port, "server_type": server_type}
        
    except Exception as e:
        logger.error(f"Failed to start local server: {e}")
        return {"ok": False, "error": str(e)}

def _start_placeholder_server(project_path, port, key, user_id, project_id):
    """Boş proje için placeholder sayfa serve et."""
    # Placeholder HTML oluştur
    placeholder_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>EMOG Preview</title>
    <style>
        body { font-family: system-ui, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #0a0a0a; color: #e0e0e0; }
        .container { text-align: center; padding: 40px; }
        h1 { color: #00d4aa; margin-bottom: 16px; }
        p { color: #888; max-width: 400px; line-height: 1.6; }
        .spinner { width: 40px; height: 40px; border: 3px solid #333; border-top-color: #00d4aa; border-radius: 50%; animation: spin 1s linear infinite; margin: 20px auto; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Proje Oluşturuluyor</h1>
        <div class="spinner"></div>
        <p>EMOG projeniz üzerinde çalışıyor. Dosyalar oluşturulduktan sonra burada görünecek.</p>
    </div>
</body>
</html>"""
    
    # Geçici dizin oluştur ve HTML yaz
    import tempfile
    preview_dir = Path(settings.BASE_DIR) / "task_runs" / "previews" / key.replace(":", "_")
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / "index.html").write_text(placeholder_html)
    
    # HTTP server başlat
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(preview_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    
    time.sleep(0.5)
    
    url = f"/preview/local/{user_id}/{project_id}/"
    info = {
        "process": process,
        "port": port,
        "url": url,
        "server_type": "placeholder",
        "started_at": time.time(),
    }
    
    with _lock:
        _local_servers[key] = info
    
    return {"ok": True, "url": url, "port": port, "server_type": "placeholder"}

def stop_local_server(user_id, project_id):
    """Lokal server'ı durdur."""
    key = f"{user_id}:{project_id}"
    with _lock:
        info = _local_servers.pop(key, None)
    if info and info.get("process"):
        try:
            info["process"].terminate()
            info["process"].wait(timeout=3)
        except Exception:
            try:
                info["process"].kill()
            except Exception:
                pass

def get_local_server_status(user_id, project_id):
    """Lokal server durumunu sorgula."""
    key = f"{user_id}:{project_id}"
    with _lock:
        info = _local_servers.get(key)
    if not info:
        return {"ok": False, "running": False}
    
    process = info.get("process")
    if process and process.poll() is None:
        return {"ok": True, "running": True, "port": info.get("port")}
    return {"ok": False, "running": False}

def get_local_server_port(user_id, project_id):
    """Lokal server portunu döndür."""
    key = f"{user_id}:{project_id}"
    with _lock:
        info = _local_servers.get(key)
    return info.get("port") if info else None

def list_local_servers(user_id):
    """Kullanıcının tüm lokal server'larını listele."""
    servers = []
    with _lock:
        for key, info in _local_servers.items():
            uid, pid = key.split(":")
            if uid == str(user_id):
                process = info.get("process")
                running = process and process.poll() is None
                servers.append({
                    "project_id": pid,
                    "port": info.get("port"),
                    "running": running,
                    "server_type": info.get("server_type"),
                })
    return servers
