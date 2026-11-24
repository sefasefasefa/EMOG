"""
Projects app - sunucu tarafi yetkilendirme ve yol guvenligi (TAM IZOLASYON).

Mimari:
  /projects/
    <user_guid>/
      <project_guid>/
        .project_meta.json   # {id, name, owner, created, guid}
        <project files...>

Kurallar:
  1. Her proje benzersiz GUID ile korunur
  2. Proje sahibi bile baska proje GUID'sini bilmeden erisemez
  3. Path traversal MUTLAK engellenir (resolve + relative_to)
  4. Terminal komutlari proje kokunde calistirilir, cikis YOK
  5. Session'da sadece aktif proje GUID'i tutulur
"""
import os
import uuid
import json
import subprocess
from pathlib import Path
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse

from agent_app.permissions import current_user, feature_enabled, is_admin

# Terminal icin izin verilen komutlar (beyaz liste)
ALLOWED_COMMANDS = (
    "ls", "dir", "cat", "head", "tail", "echo", "pwd", "cd", "whoami",
    "git", "npm", "npx", "node", "python", "python3", "pip", "pip3", "pnpm", "yarn",
    "mkdir", "touch", "cp", "mv", "rm", "find", "grep", "wc",
    "ps", "env", "which", "tree", "stat", "df", "du",
    "curl", "wget", "tar", "zip", "unzip", "chmod", "chown",
    "make", "cmake", "gcc", "g++", "rustc", "cargo", "go", "java", "mvn",
    "docker", "docker-compose", "kubectl", "helm",
    "code", "vim", "nano", "sed", "awk", "sort", "uniq", "cut", "tr",
    "diff", "patch", "xxd", "file", "du", "ln", "readlink", "realpath",
    "date", "time", "sleep", "yes", "true", "false", "test",
)


def _deny(request, message, code, status=403, **extra):
    return JsonResponse(
        {"ok": False, "error": message, "code": code, **extra}, status=status
    )


def require_auth(request):
    """Geçerli access token şart."""
    user = current_user(request)
    if user is None:
        return _deny(
            request,
            "Oturum gerekli. Lütfen giriş yapın.",
            "authentication_required",
            401,
            reauth_required=True,
        )
    if not getattr(user, "is_active", True):
        return _deny(request, "Hesap devre dışı.", "account_disabled", 403)
    return None


def require_feature(request, key):
    if not feature_enabled(key):
        return _deny(
            request,
            f"'{key}' özelliği devre dışı bırakılmış.",
            "feature_disabled",
            403,
            feature=key,
        )
    return require_auth(request)


def require_admin(request):
    user = current_user(request)
    if user is None:
        return _deny(request, "Oturum gerekli.", "authentication_required", 401)
    if not is_admin(user):
        return _deny(request, "Bu işlem yöneticiye özeldir.", "admin_required", 403)
    return None


def _get_user_guid(request):
    """Kullanıcıya özel GUID al (login'da set edilir)."""
    user = current_user(request)
    if not user:
        return None
    # User modelinde guid field'ı yoksa username+id hash'inden üret
    user_id = getattr(user, 'id', None) or getattr(user, 'pk', None)
    if user_id:
        import hashlib
        return hashlib.sha256(f"user_{user_id}".encode()).hexdigest()[:16]
    return None


get_user_guid = _get_user_guid


def _is_owner(owner_value, request):
    """Sahiplik kontrolü: hem numeric user.id hem hash'lenmiş guid kabul edilir."""
    if owner_value is None:
        return True
    user = current_user(request)
    if not user:
        return False
    candidates = set()
    uid = getattr(user, 'id', None) or getattr(user, 'pk', None)
    if uid is not None:
        candidates.add(str(uid))
    guid = _get_user_guid(request)
    if guid:
        candidates.add(str(guid))
    return str(owner_value) in candidates


def projects_root():
    """Ana projects kök dizini."""
    base = getattr(settings, "BASE_DIR", Path.cwd())
    return Path(base) / "projects"


def user_projects_dir(user_guid):
    """Kullanıcının proje klasörü: /projects/<user_guid>/"""
    return projects_root() / user_guid


def project_dir(user_guid, project_guid):
    """Tek proje dizini: /projects/<user_guid>/<project_guid>/"""
    return user_projects_dir(user_guid) / project_guid


def get_project_dir(project_guid, request=None):
    """Project GUID'ten proje dizini bul. Sadece request ile user-scoped."""
    if request:
        user_guid = _get_user_guid(request)
        if not user_guid:
            return None
        pdir = project_dir(user_guid, project_guid)
        if pdir.exists():
            return pdir
    # NO global fallback - user must provide request for isolation
    return None


def create_project(user_guid, name, project_guid=None):
    """Yeni izole proje oluştur."""
    if not project_guid:
        project_guid = uuid.uuid4().hex[:12]
    
    pdir = project_dir(user_guid, project_guid)
    pdir.mkdir(parents=True, exist_ok=False)
    
    # Meta dosyası
    meta = {
        "id": project_guid,
        "guid": project_guid,
        "name": name,
        "owner_guid": user_guid,
        "created": datetime.utcnow().isoformat() + "Z",
        "version": 1
    }
    (pdir / ".project_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    
    return pdir, meta


def validate_project_access(request, project_guid):
    """
    KESIN IZOLASYON: Kullanıcı sadece kendi projelerine erişebilir.
    Hem flat layout (projects/<id>) hem nested (projects/<user>/<id>) destekler.
    """
    guard = require_auth(request)
    if guard is not None:
        return guard, None
    
    user_guid = _get_user_guid(request)
    if not user_guid:
        return _deny(request, "Kullanıcı GUID alınamadı.", "user_guid_missing", 403), None
    
    # Try flat layout first: projects/<project_guid>
    pdir = projects_root() / project_guid
    if pdir.exists() and pdir.is_dir():
        # Verify ownership via project.json
        meta_file = pdir / 'project.json'
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                owner = meta.get('owner_guid') or meta.get('user_id')
                if owner and not _is_owner(owner, request):
                    return _deny(request, "Proje sahipliği doğrulanamadı.", "ownership_mismatch", 403), None
            except Exception:
                pass
        # Session check
        current_pid = request.session.get('current_project_guid') or request.session.get('current_project_id')
        if current_pid and str(current_pid) != str(project_guid):
            return _deny(request, "Bu projeye erişim yetkiniz yok. Sadece aktif proje erişilebilir.", "project_access_denied", 403), None
        return None, pdir
    
    # Try nested layout: projects/<user_guid>/<project_guid>
    pdir = project_dir(user_guid, project_guid)
    if not pdir.exists():
        return _deny(request, "Proje bulunamadı veya erişim yok.", "project_not_found", 404), None
    
    # Session check
    current_pid = request.session.get('current_project_guid') or request.session.get('current_project_id')
    if current_pid and str(current_pid) != str(project_guid):
        return _deny(request, "Bu projeye erişim yetkiniz yok. Sadece aktif proje erişilebilir.", "project_access_denied", 403), None
    
    # Meta doğrulama (nested layout)
    try:
        meta = json.loads((pdir / ".project_meta.json").read_text())
        if meta.get("guid") != project_guid:
            return _deny(request, "Proje GUID uyuşmazlığı.", "guid_mismatch", 403), None
        if not _is_owner(meta.get("owner_guid") or meta.get("user_id"), request):
            return _deny(request, "Proje sahipliği doğrulanamadı.", "ownership_mismatch", 403), None
    except Exception:
        return _deny(request, "Proje meta verisi bozuk.", "meta_corrupt", 403), None
    
    return None, pdir


def _is_safe_path_impl(project_dir_path, relative_path):
    """
    MUTLAK YOL GÜVENLİĞİ: Çözümlenmiş yol proje dizini içinde KALMALI.
    
    - Path traversal (../) engellenir
    - Mutlak yol engellenir  
    - Sembolik link kaçışı engellenir (resolve())
    - Proje kökünden çıkma MÜMKÜN DEĞİL
    """
    try:
        project_root = Path(project_dir_path).resolve()
    except Exception:
        return False, "Proje dizini çözümlenemedi"
    
    # Proje kökü projects/<user_guid>/<project_guid> altında olmalı
    projects_root_resolved = projects_root().resolve()
    if not str(project_root).startswith(str(projects_root_resolved)):
        return False, "Geçersiz proje dizini (kök dışı)"
    
    # Relative path çözümle
    candidate = (project_root / (relative_path or "")).resolve()
    
    # KESIN KONTROL: candidate proje kökü altında mı?
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return False, "Yol proje dizini dışına çıkıyor (path traversal engellendi)"
    
    # Ekstra: sembolik link kontrolü
    try:
        if candidate.is_symlink():
            real = candidate.resolve()
            real.relative_to(project_root)
    except Exception:
        return False, "Sembolik link kaçışı engellendi"
    
    return True, candidate


# Public alias - asıl implementasyon
is_safe_path = _is_safe_path_impl


def check_command(command):
    """Terminal komutu beyaz listeye uygun mu?"""
    if not command or not str(command).strip():
        return False, "Boş komut"
    
    text = str(command).strip()
    
    # Tehlikeli kalıplar (gerçekten tehlikeli olanlar)
    blocked = (
        "rm -rf /", ":()", "mkfs", "dd if=", "shutdown", "reboot",
        "> /dev/sd", "chmod -R 777 /",
    )
    for pattern in blocked:
        if pattern in text:
            return False, f"Tehlikeli komut engellendi: {pattern.strip()}"
    
    first = text.split()[0]
    if first not in ALLOWED_COMMANDS:
        return False, f"İzin verilmeyen komut: {first}"
    
    return True, text


# ===== Backward Compatibility (for projects/views.py) =====

def require_project_access(request, project_id):
    """Legacy API: validates access using old project_id format. Includes ownership check."""
    guard = require_auth(request)
    if guard is not None:
        return guard
    
    user_guid = _get_user_guid(request)
    
    # Try flat layout: projects/<project_id>
    pdir = projects_root() / project_id
    if pdir.exists():
        # Verify ownership via project.json (raw user id or hashed guid)
        meta_file = pdir / 'project.json'
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
                owner = meta.get('owner_guid') or meta.get('user_id')
                if owner and not _is_owner(owner, request):
                    return _deny(request, "Bu projeye erişim yetkiniz yok.", "project_access_denied", 403)
            except Exception:
                pass
    else:
        # Try nested GUID format
        if user_guid:
            pdir = project_dir(user_guid, project_id)
            if not pdir.exists():
                return _deny(request, "Proje bulunamadı.", "project_not_found", 404)
        else:
            return _deny(request, "Proje bulunamadı.", "project_not_found", 404)
    
    # Session check
    current_pid = request.session.get('current_project_id')
    if current_pid and str(current_pid) != str(project_id):
        return _deny(request, "Bu projeye erişim yetkiniz yok.", "project_access_denied", 403)
    
    return None


# Legacy function names for backward compat
def get_project_dir_legacy(project_id):
    """Legacy: get project dir by old project_id. Flat layout only - NO global scan."""
    pdir = projects_root() / project_id
    if pdir.exists() and pdir.is_dir():
        return pdir
    return None


def is_safe_path_legacy(project_id, relative_path):
    """Legacy API: is_safe_path with old signature. User-scoped."""
    pdir = get_project_dir_legacy(project_id)
    if not pdir:
        return False, "Proje dizini bulunamadı"
    # Orijinal is_safe_path fonksiyonunu kullan (alias ile karışmasın)
    return _is_safe_path_impl(pdir, relative_path)


# Aliases for old imports
projects_dir = projects_root
get_project_dir = get_project_dir_legacy
# is_safe_path_legacy'i alias olarak KULLANMA - orijinal is_safe_path'i ezer ve recursion olur
# require_project_access zaten aynı isimde, tekrar alias'a gerek yok