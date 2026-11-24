import os
import json
import subprocess
import shutil
import uuid
import threading
import time
import tempfile
from pathlib import Path
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View

from .security import (
    require_auth, require_admin, is_safe_path,
    check_command, projects_dir, get_project_dir, ALLOWED_COMMANDS,
    require_project_access,
)

# Simple in-memory project store (replace with DB in production)
PROJECTS = {}
PROJECTS_LOCK = threading.Lock()

def _projects_dir():
    base = getattr(settings, 'BASE_DIR', Path.cwd())
    return Path(base) / 'projects'

def _get_project_dir(project_id):
    return _projects_dir() / project_id

def _load_projects():
    """Load projects from disk"""
    global PROJECTS
    projects_dir = _projects_dir()
    if not projects_dir.exists():
        return
    with PROJECTS_LOCK:
        for project_dir in projects_dir.iterdir():
            if project_dir.is_dir():
                meta_file = project_dir / 'project.json'
                if meta_file.exists():
                    try:
                        with open(meta_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            PROJECTS[data['id']] = data
                    except Exception:
                        pass

# Load on import
_load_projects()

def _save_project(project):
    """Save project metadata to disk"""
    project_dir = _get_project_dir(project['id'])
    project_dir.mkdir(parents=True, exist_ok=True)
    meta_file = project_dir / 'project.json'
    with open(meta_file, 'w', encoding='utf-8') as f:
        json.dump(project, f, ensure_ascii=False, indent=2)

    # ZORUNLU: her projede .gitignore / .env.example / SECURITY.md olmali
    # (API anahtarlari ve sirlar ASLA commit edilmemeli)
    try:
        from agent_app.project_scaffold import ensure_project_security

        ensure_project_security(project_dir)
    except Exception:
        # Guvenlik dosyalari yazilamasa proje kaydi yine de tutulur
        pass

def _get_github_token(request):
    """Get GitHub token from session"""
    return request.session.get('github_token')

def _github_proxy(path, method="GET", body=None, request=None):
    """Call GitHub API using token from session or environment"""
    import urllib.request
    import urllib.error
    
    token = None
    if request:
        token = request.session.get('github_token')
    if not token:
        token = os.environ.get('GITHUB_TOKEN') or getattr(settings, 'GITHUB_TOKEN', None)
    if not token:
        return {"ok": False, "status": 401, "body": {"message": "GitHub bağlantısı yok."}}
    
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "EMOG-Agent",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            resp_body = resp.read().decode("utf-8")
            try:
                parsed = json.loads(resp_body) if resp_body else None
            except json.JSONDecodeError:
                parsed = resp_body
            return {"ok": True, "status": status, "body": parsed}
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode("utf-8"))
        return {"ok": False, "status": e.code, "body": {"message": err.get("message", str(e))}}
    except Exception as e:
        return {"ok": False, "status": 500, "body": {"message": str(e)}}

def _clone_repo(repo_url, target_dir, branch='main'):
    """Clone a GitHub repository"""
    try:
        # Remove existing directory if exists
        if target_dir.exists():
            shutil.rmtree(target_dir)
        
        # Clone with depth=1 for speed
        cmd = ['git', 'clone', '--depth', '1', '--branch', branch, repo_url, str(target_dir)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return False, result.stderr
        return True, None
    except subprocess.TimeoutExpired:
        return False, "Clone timeout"
    except Exception as e:
        return False, str(e)

def _detect_language(path):
    """Detect primary language of project"""
    extensions = {
        '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript',
        '.jsx': 'JavaScript', '.tsx': 'TypeScript', '.vue': 'Vue',
        '.html': 'HTML', '.css': 'CSS', '.scss': 'CSS', '.sass': 'CSS',
        '.java': 'Java', '.kt': 'Kotlin', '.go': 'Go', '.rs': 'Rust',
        '.cpp': 'C++', '.cc': 'C++', '.cxx': 'C++', '.c': 'C', '.h': 'C',
        '.cs': 'C#', '.php': 'PHP', '.rb': 'Ruby', '.rb': 'Ruby',
        '.swift': 'Swift', '.dart': 'Dart', '.r': 'R', '.pl': 'Perl',
        '.lua': 'Lua', '.sh': 'Shell', '.bash': 'Shell', '.zsh': 'Shell',
        '.sql': 'SQL', '.json': 'JSON', '.yaml': 'YAML', '.yml': 'YAML',
        '.toml': 'TOML', '.ini': 'INI', '.cfg': 'INI', '.conf': 'INI',
        '.dockerfile': 'Dockerfile', '.dockerignore': 'Docker',
    }
    try:
        for ext, lang in extensions.items():
            if any(f.endswith(ext) for f in Path(path).rglob('*') if f.is_file()):
                return lang
    except Exception:
        pass
    return 'Unknown'

@csrf_exempt
@require_http_methods(["GET"])
def api_list(request):
    """List projects - only the current user's projects"""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard
    from agent_app import project_store
    from agent_app.permissions import current_user
    from agent_app.projects.security import get_user_guid

    user = current_user(request)
    user_id = str(user.id) if user else None
    user_guid = get_user_guid(request) if user else None
    projects = project_store.list_projects(user_id=user_id, user_guid=user_guid)
    return JsonResponse({"ok": True, "projects": projects})

@csrf_exempt
@require_http_methods(["POST"])
def api_create(request):
    """Create a new empty project"""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard
    from agent_app.permissions import current_user
    user = current_user(request)
    if not user:
        return JsonResponse({"ok": False, "error": "Authentication required"}, status=401)
    
    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        data = {}
    
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    user_id = str(user.id)
    
    # If no name given, AI generates a short one from description
    if not name and description:
        try:
            from agent_app.views import llm_create_resilient
            import re as _re
            resp = llm_create_resilient(
                model=getattr(settings, 'NVIDIA_INTENT_MODEL', None) or 'nvidia/nemotron-3-super-120b-a12b',
                messages=[
                    {"role": "system", "content": "Generate a short project name (max 3 words, lowercase, hyphenated, English or Turkish). Return ONLY the name, nothing else."},
                    {"role": "user", "content": description},
                ],
                temperature=0.2,
                max_tokens=20,
                reasoning_effort="none",
            )
            raw = (resp.choices[0].message.content or '').strip()
            # Sanitize: keep only safe chars
            raw = _re.sub(r'[^a-zA-Z0-9ğüşöçıİĞÜŞÖÇ\- ]', '', raw).strip().lower()
            raw = _re.sub(r'\s+', '-', raw)
            raw = _re.sub(r'-+', '-', raw).strip('-')
            name = raw[:40] if raw else 'yeni-proje'
        except Exception:
            name = 'yeni-proje'
    if not name:
        name = 'yeni-proje'
    
    # Create project directory
    project_id = str(uuid.uuid4())[:8]
    project_dir = _projects_dir() / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Create AI-named app folder inside project
    app_dir = project_dir / name
    app_dir.mkdir(exist_ok=True)
    
    # Create project metadata
    project = {
        'id': project_id,
        'name': name,
        'app_dir': name,
        'path': str(project_dir),
        'app_path': str(app_dir),
        'owner_guid': user_id,
        'user_id': user_id,
        'status': 'ready',
        'language': 'Unknown',
        'description': data.get('description', ''),
        'created_at': datetime.utcnow().isoformat() + 'Z',
        'updated_at': datetime.utcnow().isoformat() + 'Z',
    }
    
    # Save project.json
    with open(project_dir / 'project.json', 'w', encoding='utf-8') as f:
        json.dump(project, f, ensure_ascii=False, indent=2)
    
    # Create .project_meta.json for security module compatibility
    meta = {
        "id": project_id,
        "guid": project_id,
        "name": name,
        "owner_guid": user_id,
        "created": datetime.utcnow().isoformat() + "Z",
        "version": 1
    }
    with open(project_dir / '.project_meta.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    # Ensure security files (.gitignore, .env.example, SECURITY.md)
    try:
        from agent_app.project_scaffold import ensure_project_security
        ensure_project_security(project_dir)
    except Exception:
        pass
    
    with PROJECTS_LOCK:
        PROJECTS[project_id] = project
    
    # Set as current project in session
    request.session['current_project_id'] = project_id
    request.session['current_project_guid'] = project_id
    request.session.modified = True
    
    return JsonResponse({"ok": True, "project_id": project_id, "project": project})

@csrf_exempt
@require_http_methods(["POST"])
def api_import(request):
    """Import a project from GitHub"""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard

    from agent_app import project_store
    from agent_app.permissions import current_user

    user = current_user(request)
    user_id = str(user.id) if user else None

    try:
        data = json.loads(request.body)
        repo_url = (data.get('repo_url') or '').strip()
        branch = (data.get('branch') or 'main').strip()
        project_name = (data.get('project_name') or '').strip()

        if not repo_url:
            return JsonResponse({"ok": False, "error": "repo_url required"}, status=400)

        # Normalize URL
        if repo_url.startswith('git@github.com:'):
            repo_url = repo_url.replace('git@github.com:', 'https://github.com/')
        if repo_url.endswith('.git'):
            repo_url = repo_url[:-4]

        # Extract repo name from URL if not provided
        if not project_name:
            project_name = repo_url.rstrip('/').split('/')[-1]

        # Use project_store to create project metadata
        project = project_store.create_project(
            user_id=user_id,
            title=project_name,
            description=f"GitHub'dan içe aktarıldı: {repo_url}",
            status="importing"
        )
        project_id = project["id"]
        target_dir = Path(project["path"])

        # Update metadata with repo details
        project["original_url"] = repo_url
        project["branch"] = branch
        project["remote_url"] = repo_url
        project_store.write_project(project)

        # Start clone in background
        def clone_task():
            # Clone into a temp directory then move to target to avoid deleting metadata
            with tempfile.TemporaryDirectory(dir=target_dir.parent) as tmp_dir:
                tmp_path = Path(tmp_dir)
                success, error = _clone_repo(repo_url, tmp_path, branch)
                
                if success:
                    # Move everything from tmp to target EXCEPT the metadata files
                    for item in tmp_path.iterdir():
                        dest = target_dir / item.name
                        if dest.exists():
                            if dest.is_dir(): shutil.rmtree(dest)
                            else: dest.unlink()
                        shutil.move(str(item), str(dest))
                    
                    project["status"] = "ready"
                    project["language"] = _detect_language(target_dir)
                    project["updated_at"] = project_store.utcnow()
                else:
                    project["status"] = "error"
                    project["last_error"] = error or "Bilinmeyen hata"

                project_store.write_project(project)

        threading.Thread(target=clone_task, daemon=True).start()

        return JsonResponse({"ok": True, "project_id": project_id})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["GET"])
def api_get_project(request, project_id):
    """Get project details and set as current project"""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard

    from agent_app import project_store
    from agent_app.permissions import current_user

    user = current_user(request)
    user_id = str(user.id) if user else None
    project = project_store.read_project(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    owner = project.get("owner_guid") or project.get("user_id")
    if not project_store.owner_matches(owner, user_id=user_id, request=request):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    with PROJECTS_LOCK:
        PROJECTS[project_id] = dict(project)

    request.session["current_project_id"] = project_id
    request.session["current_project_guid"] = project_id
    request.session["active_project_explicit"] = True
    request.session.modified = True
    return JsonResponse({"ok": True, "project": project})

@csrf_exempt
@require_http_methods(["POST"])
def api_delete_project(request, project_id):
    """Delete a project. Only the owner can delete."""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    # Extra ownership check via project.json
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    meta_file = _get_project_dir(project_id) / 'project.json'
    if meta_file.exists() and user_id:
        try:
            meta = json.loads(meta_file.read_text())
            owner = meta.get('user_id') or meta.get('owner_guid')
            if owner and str(owner) != str(user_id):
                return JsonResponse({"ok": False, "error": "Access denied"}, status=403)
        except Exception:
            pass
    with PROJECTS_LOCK:
        if project_id not in PROJECTS:
            return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
        project = PROJECTS.pop(project_id)
    
    # Delete project directory
    project_dir = _get_project_dir(project_id)
    if project_dir.exists():
        try:
            shutil.rmtree(project_dir)
        except Exception:
            pass
    
    return JsonResponse({"ok": True})

@csrf_exempt
@require_http_methods(["GET"])
def api_file_list(request, project_id):
    """List files in project"""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    project_path = Path(project['path'])
    if not project_path.exists():
        return JsonResponse({"ok": False, "error": "Project directory not found"}, status=404)
    
    def build_tree(path, base_path):
        items = []
        try:
            for item in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                rel = item.relative_to(base_path)
                if item.is_dir():
                    children = list(item.iterdir())
                    items.append({
                        'name': item.name,
                        'path': str(rel).replace('\\', '/'),
                        'type': 'dir',
                        'children': [_build_tree(item, item)] if item.iterdir() else []
                    })
                else:
                    stat = item.stat()
                    items.append({
                        'name': item.name,
                        'path': str(rel).replace('\\', '/'),
                        'type': 'file',
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
        except Exception:
            pass
        return items
    
    def _build_tree(path, base):
        items = []
        try:
            for item in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                rel = item.relative_to(base)
                if item.is_dir():
                    children = _build_tree(item, base)
                    return {
                        'name': item.name,
                        'path': str(item.relative_to(base)).replace('\\', '/'),
                        'type': 'dir',
                        'children': children
                    }
                else:
                    stat = item.stat()
                    return {
                        'name': item.name,
                        'path': str(item.relative_to(base)).replace('\\', '/'),
                        'type': 'file',
                        'size': item.stat().st_size,
                        'modified': datetime.fromtimestamp(item.stat().st_mtime).isoformat()
                    }
            return items
        except Exception:
            return []
        
    try:
        items = _build_tree(project_path, project_path)
        return JsonResponse({"ok": True, "files": items})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["GET"])
def api_file_read(request, project_id):
    """Read file content"""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    path = request.GET.get('path', '')
    if not path:
        return JsonResponse({"ok": False, "error": "path required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Path traversal korumasi: proje dizini disina cikis engellenir
    safe_ok, safe_result = is_safe_path(project_id, path)
    if not safe_ok:
        return JsonResponse({"ok": False, "error": safe_result, "code": "unsafe_path"}, status=400)
    file_path = safe_result
    if not file_path.exists() or not file_path.is_file():
        return JsonResponse({"ok": False, "error": "File not found"}, status=404)
    
    try:
        content = file_path.read_text(encoding='utf-8')
        return JsonResponse({"ok": True, "content": content, "path": path})
    except UnicodeDecodeError:
        return JsonResponse({"ok": False, "error": "Binary file cannot be displayed"}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_file_write(request, project_id):
    """Write file content. Only owner can write."""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    # Extra ownership check
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    meta_file = _get_project_dir(project_id) / 'project.json'
    if meta_file.exists() and user_id:
        try:
            meta = json.loads(meta_file.read_text())
            owner = meta.get('user_id') or meta.get('owner_guid')
            if owner and str(owner) != str(user_id):
                return JsonResponse({"ok": False, "error": "Access denied"}, status=403)
        except Exception:
            pass
    try:
        data = json.loads(request.body)
        path = data.get('path', '')
        content = data.get('content', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not path:
        return JsonResponse({"ok": False, "error": "path required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Path traversal korumasi: proje dizini disina cikis engellenir
    safe_ok, safe_result = is_safe_path(project_id, path)
    if not safe_ok:
        return JsonResponse({"ok": False, "error": safe_result, "code": "unsafe_path"}, status=400)
    file_path = safe_result
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        file_path.write_text(content, encoding='utf-8')
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_file_delete(request, project_id):
    """Delete a file. Only owner can delete."""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    # Extra ownership check
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    meta_file = _get_project_dir(project_id) / 'project.json'
    if meta_file.exists() and user_id:
        try:
            meta = json.loads(meta_file.read_text())
            owner = meta.get('user_id') or meta.get('owner_guid')
            if owner and str(owner) != str(user_id):
                return JsonResponse({"ok": False, "error": "Access denied"}, status=403)
        except Exception:
            pass
    try:
        data = json.loads(request.body)
        path = data.get('path', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not path:
        return JsonResponse({"ok": False, "error": "path required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Path traversal korumasi: proje dizini disina cikis engellenir
    safe_ok, safe_result = is_safe_path(project_id, path)
    if not safe_ok:
        return JsonResponse({"ok": False, "error": safe_result, "code": "unsafe_path"}, status=400)
    file_path = safe_result
    if not file_path.exists():
        return JsonResponse({"ok": False, "error": "File not found"}, status=404)
    
    try:
        file_path.unlink()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_terminal(request, project_id):
    """Execute terminal command"""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    try:
        data = json.loads(request.body)
        cmd = data.get('cmd', '')
        cwd = data.get('cwd', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not cmd:
        return JsonResponse({"ok": False, "error": "cmd required"}, status=400)
    
    # Terminal komut beyaz listesi (shell injection korumasi)
    cmd_ok, cmd_result = check_command(cmd)
    if not cmd_ok:
        return JsonResponse(
            {"ok": False, "error": cmd_result, "code": "command_not_allowed"},
            status=400,
        )
    cmd = cmd_result
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Calisma dizini proje icinde olmali (path traversal korumasi)
    safe_dir_ok, safe_dir = is_safe_path(project_id, cwd or '')
    if not safe_dir_ok:
        return JsonResponse(
            {"ok": False, "error": "Gecersiz calisma dizini", "code": "unsafe_path"},
            status=400,
        )
    work_dir = safe_dir
    
    if not work_dir.exists():
        work_dir = Path(project['path'])
    
    try:
        # Run command (beyaz listeye gecirilmis, shell injection yok)
        result = subprocess.run(
            cmd, shell=False, cwd=str(work_dir),
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'TERM': 'xterm-256color'}
        )
        return JsonResponse({
            "ok": True,
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
            "cwd": str(work_dir)
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({"ok": False, "error": "Command timeout"}, status=408)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

# Git API
@csrf_exempt
@require_http_methods(["GET"])
def api_git_status(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    work_dir = Path(project['path'])
    try:
        result = subprocess.run(['git', 'status', '--short'], cwd=project['path'], capture_output=True, text=True, timeout=10)
        return JsonResponse({"ok": True, "stdout": result.stdout, "stderr": result.stderr})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_git_commit(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    try:
        data = json.loads(request.body)
        message = data.get('message', '').strip()
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not message:
        return JsonResponse({"ok": False, "error": "Message required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    try:
        subprocess.run(['git', 'add', '-A'], cwd=project['path'], check=True, timeout=10)
        result = subprocess.run(['git', 'commit', '-m', message], cwd=project['path'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return JsonResponse({"ok": True, "stdout": result.stdout})
        else:
            return JsonResponse({"ok": False, "error": result.stderr}, status=500)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_git_push(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    try:
        result = subprocess.run(['git', 'push'], cwd=project['path'], capture_output=True, text=True, timeout=30)
        return JsonResponse({"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_git_pull(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    try:
        result = subprocess.run(['git', 'pull'], cwd=project['path'], capture_output=True, text=True, timeout=30)
        return JsonResponse({"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["GET"])
def api_git_log(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    try:
        result = subprocess.run(['git', 'log', '--oneline', '-20'], cwd=project['path'], capture_output=True, text=True, timeout=10)
        return JsonResponse({"ok": True, "stdout": result.stdout, "stderr": result.stderr})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["GET"])
def api_project_files(request, project_id):
    """Alias for file_list"""
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    return api_file_list(request, project_id)

@csrf_exempt
@require_http_methods(["POST"])
def api_file_write(request, project_id):
    _guard = require_feature(request, "projects_editor")
    if _guard is not None:
        return _guard
    # Ownership check
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    meta_file = _get_project_dir(project_id) / 'project.json'
    if meta_file.exists() and user_id:
        try:
            meta = json.loads(meta_file.read_text())
            owner = meta.get('user_id') or meta.get('owner_guid')
            if owner and str(owner) != str(user_id):
                return JsonResponse({"ok": False, "error": "Access denied"}, status=403)
        except Exception:
            pass
    try:
        data = json.loads(request.body)
        path = data.get('path', '')
        content = data.get('content', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not path:
        return JsonResponse({"ok": False, "error": "path required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Path traversal korumasi: proje dizini disina cikis engellenir
    safe_ok, safe_result = is_safe_path(project_id, path)
    if not safe_ok:
        return JsonResponse({"ok": False, "error": safe_result, "code": "unsafe_path"}, status=400)
    file_path = safe_result
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        file_path.write_text(content, encoding='utf-8')
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_file_delete(request, project_id):
    _guard = require_feature(request, "projects_editor")
    if _guard is not None:
        return _guard
    # Ownership check
    from agent_app.permissions import current_user
    user = current_user(request)
    user_id = str(user.id) if user else None
    meta_file = _get_project_dir(project_id) / 'project.json'
    if meta_file.exists() and user_id:
        try:
            meta = json.loads(meta_file.read_text())
            owner = meta.get('user_id') or meta.get('owner_guid')
            if owner and str(owner) != str(user_id):
                return JsonResponse({"ok": False, "error": "Access denied"}, status=403)
        except Exception:
            pass
    try:
        data = json.loads(request.body)
        path = data.get('path', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not path:
        return JsonResponse({"ok": False, "error": "path required"}, status=400)
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    # Path traversal korumasi: proje dizini disina cikis engellenir
    safe_ok, safe_result = is_safe_path(project_id, path)
    if not safe_ok:
        return JsonResponse({"ok": False, "error": safe_result, "code": "unsafe_path"}, status=400)
    file_path = safe_result
    if not file_path.exists():
        return JsonResponse({"ok": False, "error": "File not found"}, status=404)
    
    try:
        file_path.unlink()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def api_terminal(request, project_id):
    _guard = require_project_access(request, project_id)
    if _guard is not None:
        return _guard
    try:
        data = json.loads(request.body)
        cmd = data.get('cmd', '')
        cwd = data.get('cwd', '')
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    if not cmd:
        return JsonResponse({"ok": False, "error": "cmd required"}, status=400)
    
    # Command whitelist (shell injection protection)
    cmd_ok, cmd_result = check_command(cmd)
    if not cmd_ok:
        return JsonResponse({"ok": False, "error": cmd_result, "code": "command_not_allowed"}, status=400)
    cmd = cmd_result
    
    with PROJECTS_LOCK:
        project = PROJECTS.get(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    
    work_dir = Path(project['path'])
    if cwd:
        cwd_path = Path(cwd)
        if cwd_path.is_absolute():
            work_dir = Path(cwd)
        else:
            work_dir = work_dir / cwd
    
    if not work_dir.exists():
        work_dir = Path(project['path'])
    
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=project['path'],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'TERM': 'xterm-256color'}
        )
        return JsonResponse({
            "ok": True,
            "output": result.stdout,
            "error": result.stderr,
            "returncode": result.returncode,
            "cwd": str(work_dir)
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({"ok": False, "error": "Command timeout"}, status=408)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_whatsapp_integration(request, project_id):
    """Proje bazlı WhatsApp rapor entegrasyonu."""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard

    from agent_app import project_store
    from agent_app.permissions import current_user
    from agent_app.project_integrations import (
        get_project_whatsapp_config,
        save_project_whatsapp_config,
        whatsapp_connection_state,
    )

    user = current_user(request)
    user_id = str(user.id) if user else None
    project = project_store.read_project(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    owner = project.get("owner_guid") or project.get("user_id")
    if not project_store.owner_matches(owner, user_id=user_id, request=request):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    if request.method == "GET":
        return JsonResponse({
            "ok": True,
            "integration": get_project_whatsapp_config(project),
            "connection": whatsapp_connection_state(user),
            "project": {"id": project.get("id"), "name": project.get("name")},
        })

    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    cfg = save_project_whatsapp_config(project_id, data)
    return JsonResponse({
        "ok": True,
        "integration": cfg,
        "connection": whatsapp_connection_state(user),
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_whatsapp_test(request, project_id):
    """Proje WhatsApp raporunu test et."""
    _guard = require_auth(request)
    if _guard is not None:
        return _guard

    from agent_app import project_store
    from agent_app.permissions import current_user
    from agent_app.project_integrations import send_project_whatsapp_message

    user = current_user(request)
    user_id = str(user.id) if user else None
    project = project_store.read_project(project_id)
    if not project:
        return JsonResponse({"ok": False, "error": "Project not found"}, status=404)
    owner = project.get("owner_guid") or project.get("user_id")
    if not project_store.owner_matches(owner, user_id=user_id, request=request):
        return JsonResponse({"ok": False, "error": "Access denied"}, status=403)

    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        data = {}
    message = str(data.get("message") or "").strip()
    if not message:
        message = (
            f"EMOG · {(project.get('name') or project_id)}\n\n"
            "✅ WhatsApp rapor bağlantısı test edildi.\n"
            "Görev raporları bu numaraya gönderilecek."
        )
    ok, detail = send_project_whatsapp_message(project_id, user_id, message)
    # Bağlı olmama / servis hatası beklenen durumlardır; HTTP 400 yerine
    # 200 + ok:false döndür. Aksi halde tarayıcı konsolunda kırmızı 400
    # görünür ve kullanıcı bunu gerçek bir hata sanır.
    return JsonResponse({"ok": ok, "detail": detail})

