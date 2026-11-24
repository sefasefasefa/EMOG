import os
import json
import threading
from pathlib import Path
from typing import Dict, List, Any, Optional
from django.conf import settings

# Thread-local storage for project context (strict isolation)
_project_context = threading.local()


def set_project_context(project_guid: str, user_guid: str = None, request=None):
    """Set the current project context for this thread with strict isolation."""
    if project_guid:
        # Try user-scoped path first: projects/<user_guid>/<project_guid>
        if user_guid:
            from agent_app.projects.security import project_dir
            pdir = project_dir(user_guid, project_guid)
            if pdir.exists():
                _project_context.project_guid = project_guid
                _project_context.user_guid = user_guid
                _project_context.project_root = pdir.resolve()
                return True

        # Fallback: flat layout projects/<project_guid> with ownership check
        base = Path(settings.BASE_DIR)
        flat_pdir = base / 'projects' / str(project_guid)
        if flat_pdir.exists():
            # Verify ownership via project.json
            meta_file = flat_pdir / 'project.json'
            owner_ok = True
            if meta_file.exists() and user_guid:
                try:
                    meta = json.loads(meta_file.read_text())
                    owner = meta.get('owner_guid') or meta.get('user_id')
                    if owner:
                        if request is None:
                            # Worker/pipeline: request yok; kimlik user_guid'dan dogrulanir.
                            if not _owner_matches(owner, user_guid):
                                owner_ok = False
                        else:
                            from agent_app.projects.security import _is_owner
                            if not _is_owner(owner, request):
                                owner_ok = False
                except Exception:
                    pass
            if owner_ok:
                # Auto-record owner if not set
                if meta_file.exists() and user_guid:
                    try:
                        meta = json.loads(meta_file.read_text())
                        if not meta.get('owner_guid') and not meta.get('user_id'):
                            meta['owner_guid'] = user_guid
                            meta_file.write_text(json.dumps(meta, indent=2))
                    except Exception:
                        pass
                _project_context.project_guid = project_guid
                _project_context.user_guid = user_guid
                _project_context.project_root = flat_pdir.resolve()
                return True

    _project_context.project_guid = None
    _project_context.user_guid = None
    _project_context.project_root = None
    return False


def _owner_matches(owner_value, user_guid) -> bool:
    """Request'siz (worker/pipeline) sahiplik kontrolu.

    user_guid numeric user.id veya hash'li guid olabilir; owner de ayni ikisinden
    biriyle yazilmis olabilir (sha256("user_<id>")[:16] veya dogrudan id/uuid).
    Bu yuzden her ikisi de aday kabul edilir.
    """
    if owner_value is None:
        return True
    candidates = {str(user_guid)}
    try:
        import hashlib
        candidates.add(hashlib.sha256(f"user_{user_guid}".encode()).hexdigest()[:16])
    except Exception:
        pass
    return str(owner_value) in candidates


def clear_project_context():
    """Clear the project context for this thread."""
    _project_context.project_guid = None
    _project_context.user_guid = None
    _project_context.project_root = None


def get_project_root() -> Optional[Path]:
    """Get the current project root, or None if no project context."""
    return getattr(_project_context, 'project_root', None)


def get_project_guid() -> Optional[str]:
    """Get the current project GUID, or None if no project context."""
    return getattr(_project_context, 'project_guid', None)


def get_user_guid() -> Optional[str]:
    """Get the current user GUID, or None if no project context."""
    return getattr(_project_context, 'user_guid', None)


def _validate_and_resolve(relative_path: str) -> tuple:
    """Validate path and return (ok, resolved_path_or_error)."""
    root = get_project_root()
    if not root:
        return False, "No project context set"
    
    from agent_app.projects.security import is_safe_path
    ok, result = is_safe_path(root, relative_path)
    if not ok:
        return False, result
    return True, result


# Directories/files to ignore
IGNORE_DIRS = {
    '__pycache__', '.git', '.venv', 'venv', 'env', 'node_modules',
    '.idea', '.vscode', 'dist', 'build', '.pytest_cache',
    'task_runs', 'migrations', '__pycache__'
}
IGNORE_FILES = {
    '.pyc', '.pyo', '.pyd', '.so', '.dll', '.exe', '.bin',
    '.db', '.sqlite3', '.log', '.tmp', '.temp',
    'db.sqlite3', 'omni_health.log', 'omni_proxy.log',
    # Multimedia - treat as binary, don't try to read as text
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.ico', '.svg',
    '.mp4', '.webm', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v',
    '.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.tar', '.gz', '.rar', '.7z',
}
IGNORE_PREFIXES = {'.', '~'}

# Scaffold/AI command files - hidden from file tree UI (user's private AI instructions)
SCAFFOLD_HIDDEN_FILES = {
    'ERROR_TRACKING_STANDARD.md',
    'FORM_VALIDATION_STANDARD.md',
    'SECURITY.md',
    'WHATSAPP_STANDARD.md',
    'API_STANDARD.md',
    'DATABASE_STANDARD.md',
    'TESTING_STANDARD.md',
    'DEPLOYMENT_STANDARD.md',
}

# EMOG Self-Protection: Core files that emog must NEVER modify
# These are the project's own critical files - modifying them breaks emog itself
EMOG_CORE_PATHS = frozenset({
    # Django project
    'emog_project/settings.py',
    'emog_project/urls.py',
    'emog_project/wsgi.py',
    'emog_project/asgi.py',
    # Main app
    'agent_app/views.py',
    'agent_app/urls.py',
    'agent_app/models.py',
    'agent_app/project_scanner.py',
    'agent_app/model_router.py',
    'agent_app/conversation_history.py',
    'agent_app/auth_views.py',
    'agent_app/auth_middleware.py',
    'agent_app/binding.py',
    'agent_app/binding_middleware.py',
    'agent_app/permissions.py',
    'agent_app/tokens.py',
    'agent_app/security.py',
    'agent_app/tasks/__init__.py',
    'agent_app/tasks/llm.py',
    'agent_app/tasks/memory.py',
    'agent_app/sandbox.py',
    'agent_app/celery_app.py',
    'agent_app/run_self_healing_loop.py',
    # Project scaffolding
    'manage.py',
    # Config
    '.env',
    'requirements.txt',
    'docker-compose.yml',
    'Dockerfile',
})

MAX_FILE_SIZE = 200000  # 200KB
MAX_TOTAL_CONTEXT = 500000  # 500KB total context


def should_ignore(path: Path, name: str) -> bool:
    if name in IGNORE_DIRS:
        return True
    if any(name.startswith(p) for p in IGNORE_PREFIXES):
        return True
    if name in IGNORE_FILES:
        return True
    if any(name.endswith(ext) for ext in IGNORE_FILES):
        return True
    if name in SCAFFOLD_HIDDEN_FILES:
        return True
    return False


def get_file_tree(root: Path, max_depth: int = 4, current_depth: int = 0) -> Dict[str, Any]:
    if current_depth >= max_depth:
        return {"type": "dir", "truncated": True}
    
    result = {"type": "dir", "children": {}}
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError):
        return {"type": "dir", "error": "Permission denied"}
    
    for entry in entries:
        if should_ignore(entry, entry.name):
            continue
        if entry.is_dir():
            result["children"][entry.name] = get_file_tree(entry, max_depth, current_depth + 1)
        else:
            try:
                size = entry.stat().st_size
                result["children"][entry.name] = {"type": "file", "size": size}
            except OSError:
                result["children"][entry.name] = {"type": "file", "size": 0}
    return result


def format_file_tree(tree: Dict[str, Any], prefix: str = "") -> List[str]:
    lines = []
    children = tree.get("children", {})
    items = list(children.items())
    for i, (name, info) in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "
        if info.get("type") == "dir":
            lines.append(f"{prefix}{connector}{name}/")
            if "children" in info:
                lines.extend(format_file_tree(info, prefix + extension))
            elif info.get("truncated"):
                lines.append(f"{prefix}{extension}    ... (max depth)")
        else:
            size = info.get("size", 0)
            size_str = f" ({size} bytes)" if size else ""
            lines.append(f"{prefix}{connector}{name}{size_str}")
    return lines


def read_file_safe(path: Path, max_size: int = MAX_FILE_SIZE) -> Optional[str]:
    try:
        if path.stat().st_size > max_size:
            return f"[File too large: {path.stat().st_size} bytes, max {max_size}]"
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except (OSError, UnicodeDecodeError) as e:
        return f"[Error reading file: {e}]"


def get_key_files_content(
    root: Path,
    max_total: int = MAX_TOTAL_CONTEXT,
    max_file_size: int = MAX_FILE_SIZE,
) -> Dict[str, str]:
    """Read important project files for context."""
    key_patterns = [
        '*.py', '*.js', '*.ts', '*.jsx', '*.tsx',
        '*.json', '*.yaml', '*.yml', '*.toml', '*.ini',
        '*.md', '*.txt', '*.html', '*.css', '*.scss',
        'Dockerfile', 'docker-compose.yml', 'requirements.txt',
        'pyproject.toml', 'setup.py', 'package.json'
    ]
    
    key_files = {}
    total_size = 0
    
    for pattern in key_patterns:
        for path in root.rglob(pattern):
            if any(part in IGNORE_DIRS for part in path.parts):
                continue
            if path.name in IGNORE_FILES:
                continue
            try:
                rel = path.relative_to(root)
                content = read_file_safe(path, max_size=max_file_size)
                if content and not content.startswith('['):
                    key_files[str(rel)] = content
                    total_size += len(content)
                    if total_size > max_total:
                        break
            except (ValueError, OSError):
                continue
        if total_size > max_total:
            break
    
    return key_files


def scan_project(root: Optional[Path] = None, max_key_chars: int = None) -> Dict[str, Any]:
    """Scan project and return context for LLM. Never falls back to BASE_DIR."""
    if root is None:
        root = get_project_root()
    if root is None:
        return {"root": None, "file_tree": "", "key_files": {}, "file_count": 0}
    
    tree = get_file_tree(root)
    tree_lines = format_file_tree(tree)
    key_files = get_key_files_content(root, max_total=max_key_chars or MAX_TOTAL_CONTEXT)
    
    return {
        "root": str(root),
        "file_tree": "\n".join(tree_lines),
        "key_files": key_files,
        "file_count": sum(1 for _ in root.rglob('*') if _.is_file() and not any(p in IGNORE_DIRS for p in _.parts)),
    }


def get_project_context(
    root: Optional[Path] = None,
    max_chars: int = None,
    task_text: str = "",
) -> str:
    """Get formatted project context string for LLM prompt.

    Buyuk Python dosyalari ham halde gonderilmez; AST iskeleti kullanilir.
    Git diff varsa yalnizca degisen dosyalarin diff'i baglama eklenir.
    """
    if root is None:
        root = get_project_root()
    if root is None:
        return "No project selected. Ask the user to select or create a project first."

    try:
        from agent_app import context_engineering as ce
    except Exception:
        ce = None

    if ce is not None:
        total = int(max_chars or ce.get_setting("project_context_max_chars", 14000))
    else:
        total = int(max_chars or 14000)
    tree_budget = max(800, int(total * 0.18))
    skeleton_budget = max(800, int(total * 0.34))
    active_budget = max(800, int(total * 0.28))
    key_budget = max(800, total - tree_budget - skeleton_budget - active_budget)

    ctx = scan_project(root, max_key_chars=max(2000, key_budget * 2))

    parts = ["=== PROJECT STRUCTURE ===", _clip_text(ctx["file_tree"], tree_budget, "dosya agaci")]

    if ce is not None and ce.get_setting("enabled", True):
        skeleton = ce.build_repo_map(root, max_chars=skeleton_budget)
        if skeleton:
            parts += ["", "=== PYTHON SKELETON (AST) ===", skeleton]
        active = ce.build_active_context(root, task_text=task_text, max_chars=active_budget)
        if active:
            parts += ["", active]

    parts += ["", "=== KEY FILES ==="]
    used = 0
    threshold = int(ce.get_setting("active_file_threshold_lines", 200)) if ce is not None else 200
    omitted = 0

    for index, (fname, content) in enumerate(ctx["key_files"].items()):
        remaining = key_budget - used
        if remaining < 400:
            omitted = len(ctx["key_files"]) - index
            break
        if ce is not None and fname.endswith(".py") and content.count("\n") + 1 > threshold:
            rendered = ce.get_code_outline(root / fname)
        else:
            rendered = content[:3000]
            if len(content) > 3000:
                rendered += f"\n... (truncated, {len(content)} total chars)"
        block = f"\n--- {fname} ---\n{rendered}"
        if len(block) > remaining:
            block = _clip_text(block, remaining, "dosya")
        parts.append(block)
        used += len(block)

    if omitted > 0:
        parts.append(f"\n... (+{omitted} dosya butce nedeniyle gizlendi)")

    return "\n".join(parts)


def _clip_text(text: str, limit: int, label: str) -> str:
    if text is None:
        return ""
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n... ({label} kesildi, {len(text)} karakter)"


def tool_code_outline(path: str = ".", root: Path = None) -> Dict[str, Any]:
    """Bir .py dosyasinin veya dizinin AST iskeletini dondur (token tasarrufu)."""
    if root is None:
        root = get_project_root()
    if not root:
        return {"ok": False, "error": "No project context set. Select a project first."}

    root_path = Path(root).resolve()
    target = (root_path / path).resolve()
    if not str(target).startswith(str(root_path)):
        return {"ok": False, "error": "Path traversal not allowed"}
    if not target.exists():
        return {"ok": False, "error": f"Path not found: {path}"}

    try:
        from agent_app import context_engineering as ce

        if target.is_dir():
            rel = target.relative_to(root_path).as_posix() or "."
            outline = ce.build_repo_map(
                target, max_chars=ce.get_setting("repo_map_max_chars", 5000)
            )
            return {"ok": True, "path": rel, "type": "directory", "outline": outline}
        if target.suffix.lower() != ".py":
            return {"ok": False, "error": "code_outline yalnizca .py dosyalari icin desteklenir"}
        rel = target.relative_to(root_path).as_posix()
        outline = ce.get_code_outline(target)
        return {"ok": True, "path": rel, "type": "file", "outline": outline}
    except Exception as exc:
        return {"ok": False, "error": f"outline hatasi: {exc}"}


# Tool definitions for LLM
FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the project. Returns file content or error.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "code_outline",
            "description": (
                "Return a compact AST outline (classes, methods and function signatures) "
                "for a Python file or a whole directory. Use this before reading large "
                "files so you can locate the right function without spending tokens."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path relative to project root",
                        "default": ".",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"},
                    "content": {"type": "string", "description": "File content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory (non-recursive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path relative to project root", "default": "."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "Search for pattern in project files. Returns matching file paths and line snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search"},
                    "path": {"type": "string", "description": "Directory to search (relative to root)", "default": "."},
                    "include": {"type": "string", "description": "File pattern (glob)", "default": "**/*"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Make a targeted edit to a file by replacing old_string with new_string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"},
                    "old_string": {"type": "string", "description": "Exact string to replace"},
                    "new_string": {"type": "string", "description": "Replacement string"}
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the project directory. Use for tests, builds, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 60}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Apply a unified diff patch to one or more files. Use for atomic multi-file edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "Unified diff format patch content"}
                },
                "required": ["patch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a git-format patch from a string or file. Handles git-style patches with metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "Git patch content (from git diff or .patch file)"},
                    "strip": {"type": "integer", "description": "Number of leading path components to strip (like git apply -pN)", "default": 1}
                },
                "required": ["patch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_dir",
            "description": "Delete a directory and all its contents recursively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from project root"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source path relative to project root"},
                    "dst": {"type": "string", "description": "Destination path relative to project root"}
                },
                "required": ["src", "dst"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file or directory to a new location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Source path relative to project root"},
                    "dst": {"type": "string", "description": "Destination path relative to project root"}
                },
                "required": ["src", "dst"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run tests in the project. Auto-detects test framework (pytest, jest, go test, cargo test, etc.) or runs custom command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Custom test command (optional, auto-detected if omitted)"},
                    "path": {"type": "string", "description": "Directory to run tests in (relative to project root)", "default": "."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lint_code",
            "description": "Run linter/formatter on code. Auto-detects language and tool (ruff/black for Python, eslint/prettier for JS/TS, cargo fmt for Rust, gofmt for Go).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory to lint (relative to project root)", "default": "."},
                    "fix": {"type": "boolean", "description": "Auto-fix issues if possible", "default": True},
                    "tool": {"type": "string", "description": "Specific tool to use (optional, auto-detected)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff for uncommitted changes, staged changes, or between commits/branches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "description": "Show staged changes only", "default": False},
                    "path": {"type": "string", "description": "Limit to specific file/directory (optional)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status - modified, staged, untracked files.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent git commit history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of commits to show", "default": 20},
                    "oneline": {"type": "boolean", "description": "Compact one-line format", "default": True},
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Create a git commit with all staged changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message"},
                    "add_all": {"type": "boolean", "description": "Stage all changes before commit", "default": True}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push commits to remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "Remote name", "default": "origin"},
                    "branch": {"type": "string", "description": "Branch name (optional, uses current)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "Pull latest changes from remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {"type": "string", "description": "Remote name", "default": "origin"},
                    "branch": {"type": "string", "description": "Branch name (optional, uses current)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "Make an HTTP request to any URL. Use for API testing, webhooks, fetching data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL to request"},
                    "method": {"type": "string", "description": "HTTP method", "default": "GET", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]},
                    "headers": {"type": "object", "description": "Request headers as key-value pairs"},
                    "body": {"type": "string", "description": "Request body (JSON string for POST/PUT)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and extract content from a URL (HTML, markdown, JSON). Use for documentation, web scraping, API discovery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "format": {"type": "string", "description": "Output format", "default": "text", "enum": ["text", "html", "json", "markdown"]},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "database_query",
            "description": "Execute a SQL query on the project's database (SQLite/PostgreSQL/MySQL). Read-only by default.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL query to execute"},
                    "params": {"type": "array", "description": "Query parameters for parameterized queries"},
                    "readonly": {"type": "boolean", "description": "Enforce read-only (no INSERT/UPDATE/DELETE)", "default": True}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_build",
            "description": "Build a Docker image from a Dockerfile in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to Dockerfile directory (relative to project root)", "default": "."},
                    "tag": {"type": "string", "description": "Image tag (name:tag)"},
                    "dockerfile": {"type": "string", "description": "Dockerfile name", "default": "Dockerfile"},
                    "build_args": {"type": "object", "description": "Build arguments as key-value pairs"}
                },
                "required": ["tag"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "docker_run",
            "description": "Run a Docker container from an image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image": {"type": "string", "description": "Image name (name:tag or image ID)"},
                    "command": {"type": "string", "description": "Command to run in container (optional)"},
                    "ports": {"type": "array", "description": "Port mappings as ['host:container', ...]"},
                    "env": {"type": "object", "description": "Environment variables"},
                    "volumes": {"type": "array", "description": "Volume mounts as ['host:container', ...]"},
                    "detach": {"type": "boolean", "description": "Run in background", "default": False},
                    "remove": {"type": "boolean", "description": "Remove container after exit", "default": True}
                },
                "required": ["image"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_dependencies",
            "description": "Analyze project dependencies for vulnerabilities, outdated packages, and license issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project root or specific manifest file (package.json, requirements.txt, Cargo.toml, etc.)", "default": "."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_code",
            "description": "Generate a summary of a code file or directory - purpose, key functions, dependencies, complexity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File or directory to summarize (relative to project root)"},
                    "detail": {"type": "string", "description": "Detail level", "default": "medium", "enum": ["brief", "medium", "detailed"]}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web with multiple providers at once (Brave/Tavily/Serper/Bing/Exa/SerpApi/Kagi/"
                "Google CSE/You/SearXNG if an API key is configured, plus free DuckDuckGo/Mojeek/Wikipedia/"
                "GitHub/StackExchange/Hacker News/arXiv/Crossref/PubMed/Semantic Scholar). "
                "Returns de-duplicated results with title, url and snippet. "
                "Use this BEFORE deciding how to implement anything unfamiliar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (Turkish or English)"},
                    "count": {"type": "integer", "description": "Max results per provider", "default": 8},
                    "providers": {
                        "type": "array",
                        "description": "Optional provider name filter (default: all available)",
                        "items": {"type": "string"},
                    },
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": (
                "Run a multi-step research pass on a topic: generates several queries, searches across all "
                "providers, reads the most relevant pages, and returns a structured domain dossier "
                "(how it works, must-have features, pitfalls, tech stack, UX principles, quality bar). "
                "Use for open-ended 'how does X work / what does X need' questions. Slower than web_search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Research question or topic"},
                    "depth": {
                        "type": "string",
                        "description": "Research depth",
                        "default": "standard",
                        "enum": ["quick", "standard", "deep"],
                    },
                },
                "required": ["question"]
            }
        }
    }
]

# Tool implementations
def tool_read_file(path: str, root: Path = None) -> Dict[str, Any]:
    try:
        ok, result = _validate_and_resolve(path)
        if not ok:
            return {"ok": False, "error": result}
        full_path = result
        if full_path.is_dir():
            return {"ok": False, "error": "Bu bir dizin, dosya değil."}
        if not full_path.is_file():
            return {"ok": False, "error": "Dosya bulunamadı"}
        content = read_file_safe(full_path)
        if content is None:
            return {"ok": False, "error": "File not found or unreadable"}
        return {"ok": True, "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_write_file(path: str, content: str, root: Path = None) -> Dict[str, Any]:
    try:
        # Input validation
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "Geçersiz dosya yolu"}
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        
        ok, result = _validate_and_resolve(path)
        if not ok:
            return {"ok": False, "error": str(result)}
        full_path = result
        
        # Self-protection: prevent modifying emog's own core files
        if path in EMOG_CORE_PATHS:
            return {"ok": False, "error": f"YASAK: '{path}' emog'un kendi çekirdek dosyasıdır. Bu dosyayı değiştirmek emog'u bozar."}
        
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"ok": True, "path": path, "bytes": len(content.encode('utf-8', errors='ignore'))}
    except RecursionError:
        return {"ok": False, "error": "Recursion limit aşıldı - dosya yazma başarısız"}
    except Exception as e:
        try:
            err_msg = str(e)[:500]
        except Exception:
            err_msg = "Bilinmeyen hata"
        return {"ok": False, "error": err_msg}


def tool_list_files(path: str, root: Path = None) -> Dict[str, Any]:
    try:
        ok, result = _validate_and_resolve(path or ".")
        if not ok:
            return {"ok": False, "error": result}
        full_path = result
        
        if not full_path.exists() or not full_path.is_dir():
            return {"ok": False, "error": "Not a directory"}
        entries = []
        for entry in sorted(full_path.iterdir()):
            if should_ignore(entry, entry.name):
                continue
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None
            })
        return {"ok": True, "path": path or ".", "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_grep_files(pattern: str, path: str = ".", include: str = "**/*", root: Path = None) -> Dict[str, Any]:
    try:
        ok, result = _validate_and_resolve(path or ".")
        if not ok:
            return {"ok": False, "error": result}
        search_root = result
        
        import re
        regex = re.compile(pattern)
        matches = []
        for file_path in search_root.rglob(include):
            if file_path.is_file() and not any(p in IGNORE_DIRS for p in file_path.parts):
                try:
                    rel = file_path.relative_to(search_root)
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                matches.append({
                                    "file": str(rel),
                                    "line": i,
                                    "text": line.rstrip()[:200]
                                })
                                if len(matches) >= 50:
                                    break
                        if len(matches) >= 50:
                            break
                except (OSError, UnicodeDecodeError):
                    continue
        return {"ok": True, "matches": matches, "count": len(matches)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_edit_file(path: str, old_string: str, new_string: str, root: Path = None) -> Dict[str, Any]:
    try:
        ok, result = _validate_and_resolve(path)
        if not ok:
            return {"ok": False, "error": result}
        full_path = result
        
        # Self-protection: prevent modifying emog's own core files
        if path in EMOG_CORE_PATHS:
            return {"ok": False, "error": f"YASAK: '{path}' emog'un kendi çekirdek dosyasıdır. Bu dosyayı değiştirmek emog'u bozar."}
        
        content = read_file_safe(full_path)
        if content is None:
            return {"ok": False, "error": "File not found"}
        if old_string not in content:
            return {"ok": False, "error": "old_string not found in file"}
        new_content = content.replace(old_string, new_string)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return {"ok": True, "path": path, "replacements": content.count(old_string)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Dangerous commands/patterns that are NEVER allowed
FORBIDDEN_COMMAND_PATTERNS = (
    # System destruction
    "rm -rf /", "rm -rf /*", ":(){ :|:& };:", "mkfs", "dd if=", "shutdown", "reboot", "halt", "poweroff",
    # Disk/fs manipulation
    "> /dev/sd", "fdisk", "parted", "format", "cryptsetup",
    # Permission escalation
    "chmod -R 777 /", "chown -R root", "setuid", "setgid", "sudo su", "su -", "passwd",
    # Network exfiltration/attacks
    "curl", "wget", "nc ", "netcat", "ncat", "socat", "telnet", "ssh ", "scp ", "rsync ",
    # Code injection
    "python -c", "python3 -c", "perl -e", "ruby -e", "node -e", "php -r", "bash -c", "sh -c", "eval ",
    "base64 -d", "base64 -D", "xxd -r",
    # Process manipulation
    "kill -9 1", "killall", "pkill -9", "renice",
    # Kernel/modules
    "insmod", "rmmod", "modprobe", "kexec",
    # Container escape
    "docker run --privileged", "kubectl", "crictl",
)

# Dangerous shell metacharacters for injection
SHELL_INJECTION_PATTERNS = (
    "`", "$(", "${",
)

# System paths that should never be accessed
FORBIDDEN_PATHS = (
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/ssh/",
    "/root/", "/home/*/.ssh/", "/home/*/.aws/", "/home/*/.config/gcloud/",
    "/var/lib/docker/", "/var/run/docker.sock", "/proc/", "/sys/", "/dev/",
    "/boot/", "/lib/modules/", "/usr/src/linux", "/bin/", "/sbin/", "/usr/bin/sudo",
)

def _is_command_safe(command: str) -> tuple[bool, str]:
    """Check if command is safe to execute."""
    if not command or not str(command).strip():
        return False, "Boş komut"
    
    text = str(command).strip().lower()
    
    # Check forbidden patterns
    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if pattern.lower() in text:
            return False, f"YASAKLI KOMUT: '{pattern}' - sistem güvenliği için engellendi"
    
    # Check for shell injection attempts (but allow some for normal usage)
    # Be more careful - only flag suspicious patterns
    suspicious_count = sum(1 for p in SHELL_INJECTION_PATTERNS if p in command)
    if suspicious_count > 3:  # Allow some but not excessive chaining
        return False, "Şüpheli komut zinciri tespit edildi (injection girişimi olabilir)"
    
    # Check for forbidden paths in command
    for path in FORBIDDEN_PATHS:
        if path.lower() in text:
            return False, f"YASAKLI YOL: '{path}' - sistem dosyalarına erişim engellendi"
    
    return True, ""

def _terminate_process_tree(pid: int) -> None:
    """cmd'i degil, npx/node gibi TORUN surecleri de oldur (yetim kalmasinlar)."""
    import subprocess as sp
    import os
    try:
        if os.name == "nt":
            sp.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True,
                   creationflags=getattr(sp, "CREATE_NO_WINDOW", 0))
        else:
            try:
                os.killpg(os.getpgid(pid), 9)
            except Exception:
                pass
    except Exception:
        pass


def _command_absolute_paths(command: str):
    """Komut metni icindeki mutlak Windows yollarini cikarir.

    'cd C:\\...\\projects\\EMOG && npm ...' gibi cagrilarda proje koku
    disina cikilir; bu yuzden komut icindeki mutlak yollar denetlenir.
    """
    import re

    found = []
    for groups in re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', command or ""):
        token = next((g for g in groups if g), "")
        if not token:
            continue
        if len(token) >= 3 and token[1] == ":" and token[2] in "\\/":
            found.append(token)
        elif token.startswith("\\\\"):
            found.append(token)
    return found


def tool_run_command(command: str, timeout: int = 60, root: Path = None,
                     path: str = None, cwd: str = None, **kwargs) -> Dict[str, Any]:
    # Safety check FIRST
    safe, error = _is_command_safe(command)
    if not safe:
        return {"ok": False, "error": error, "code": "command_blocked"}
    
    if root is None:
        root = get_project_root()
    if root is None:
        return {"ok": False, "error": "No project context set. Select a project first."}

    # Model run_command icine 'path'/'cwd' gonderebilir. Bu bir calisma dizini
    # olabilir; proje dizini disina cikmasina izin verilmez. ('.' = proje kok)
    workdir = Path(root)
    rel = path if path not in (None, "", ".", "./") else (cwd if cwd not in (None, "", ".", "./") else kwargs.get("dir"))
    if rel not in (None, "", ".", "./"):
        from agent_app.projects.security import is_safe_path
        ok_path, resolved = is_safe_path(root, rel)
        if not ok_path:
            return {"ok": False, "error": str(resolved)}
        workdir = resolved

    # Model bazen timeout'u metin gonderir; communicate() numeric ister
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 60
    if timeout <= 0:
        timeout = 60

    # Komut icinde mutlak yol ile proje disina cikma engeli.
    # 'cd C:\\...\\projects\\EMOG && npm ...' gibi cagrilarda dosyalar proje
    # koku disina yaziliyor, dogrulama ise koke bakiyordu.
    try:
        root_resolved = Path(root).resolve()
        for raw in _command_absolute_paths(command):
            candidate = Path(str(raw).strip().strip('"').strip("'"))
            if not candidate.is_absolute():
                continue
            try:
                candidate.resolve().relative_to(root_resolved)
            except Exception:
                return {
                    "ok": False,
                    "error": ("Komut proje disina bir yol kullaniyor (mutlak yol yasak): %s. "
                              "Proje kokune gorel yol kullan (orn. 'ts/src')." % str(candidate)[:140]),
                    "code": "path_escape",
                }
    except Exception:
        pass

    import subprocess
    import os
    # Non-interactive calistirma: stdin EOF ver, CI bayragi koy ki
    # create-next-app / npm gibi araclar soru sormasın, prompt'ta asilmasin.
    env = os.environ.copy()
    env.setdefault("CI", "1")
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        out, err = proc.communicate(timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (out or "")[-5000:],
            "stderr": (err or "")[-5000:]
        }
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return {
            "ok": False,
            "error": f"Command timed out after {timeout}s",
            "stdout": (out or "")[-5000:],
            "stderr": (err or "")[-5000:]
        }
    except Exception as e:
        _terminate_process_tree(proc.pid)
        return {"ok": False, "error": str(e)}


# ===== NEW TOOL IMPLEMENTATIONS =====

def tool_patch_file(patch: str, root: Path = None) -> Dict[str, Any]:
    """Apply a unified diff patch to one or more files."""
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False, encoding='utf-8') as f:
            f.write(patch)
            patch_file = f.name
        try:
            proc = subprocess.run(
                ['patch', '-p1', '--no-backup-if-mismatch', '-i', patch_file],
                cwd=root, capture_output=True, text=True, timeout=60
            )
            return {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode
            }
        finally:
            import os
            try: os.unlink(patch_file)
            except: pass
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Patch timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_apply_patch(patch: str, strip: int = 1, root: Path = None) -> Dict[str, Any]:
    """Apply a git-format patch."""
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False, encoding='utf-8') as f:
            f.write(patch)
            patch_file = f.name
        try:
            proc = subprocess.run(
                ['git', 'apply', f'-p{strip}', '--no-add', '--whitespace=nowarn', patch_file],
                cwd=root, capture_output=True, text=True, timeout=60
            )
            return {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode
            }
        finally:
            import os
            try: os.unlink(patch_file)
            except: pass
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Git apply timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_delete_file(path: str, root: Path) -> Dict[str, Any]:
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        if full_path.is_dir():
            return {"ok": False, "error": "Path is a directory, use delete_dir"}
        full_path.unlink()
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_delete_dir(path: str, root: Path) -> Dict[str, Any]:
    import shutil
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "Directory not found"}
        if not full_path.is_dir():
            return {"ok": False, "error": "Path is not a directory"}
        shutil.rmtree(full_path)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_move_file(src: str, dst: str, root: Path) -> Dict[str, Any]:
    import shutil
    try:
        src_path = (root / src).resolve()
        dst_path = (root / dst).resolve()
        if not str(src_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Source path traversal not allowed"}
        if not str(dst_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Destination path traversal not allowed"}
        if not src_path.exists():
            return {"ok": False, "error": "Source not found"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
        return {"ok": True, "src": src, "dst": dst}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_copy_file(src: str, dst: str, root: Path) -> Dict[str, Any]:
    import shutil
    try:
        src_path = (root / src).resolve()
        dst_path = (root / dst).resolve()
        if not str(src_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Source path traversal not allowed"}
        if not str(dst_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Destination path traversal not allowed"}
        if not src_path.exists():
            return {"ok": False, "error": "Source not found"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
        return {"ok": True, "src": src, "dst": dst}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_run_tests(command: str = "", path: str = ".", timeout: int = 120, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if not command:
            # Auto-detect test framework
            if (work_dir / 'pytest.ini').exists() or (work_dir / 'pyproject.toml').exists() or any(work_dir.rglob('test_*.py')):
                command = 'python -m pytest -v'
            elif (work_dir / 'package.json').exists():
                import json
                pkg = json.loads((work_dir / 'package.json').read_text())
                if 'test' in pkg.get('scripts', {}):
                    command = 'npm test'
            elif (work_dir / 'Cargo.toml').exists():
                command = 'cargo test'
            elif (work_dir / 'go.mod').exists():
                command = 'go test ./...'
            else:
                return {"ok": False, "error": "Could not detect test framework, provide custom command"}
        
        proc = subprocess.run(
            command, shell=True, cwd=work_dir,
            capture_output=True, text=True, timeout=timeout, encoding='utf-8'
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-10000:] if proc.stdout else "",
            "stderr": proc.stderr[-10000:] if proc.stderr else "",
            "command": command
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Tests timed out after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_lint_code(path: str = ".", fix: bool = True, tool: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if not tool:
            # Auto-detect
            if (work_dir / 'pyproject.toml').exists() or any(work_dir.rglob('*.py')):
                tool = 'ruff'
            elif (work_dir / 'package.json').exists():
                tool = 'eslint'
            elif (work_dir / 'Cargo.toml').exists():
                tool = 'cargo'
            elif (work_dir / 'go.mod').exists():
                tool = 'gofmt'
            else:
                return {"ok": False, "error": "Could not detect language/tool"}
        
        commands = {
            'ruff': ['ruff', 'check', '--fix' if fix else ''] if fix else ['ruff', 'check'],
            'black': ['black'] if fix else ['black', '--check'],
            'eslint': ['npx', 'eslint', '--fix' if fix else ''] if fix else ['npx', 'eslint'],
            'prettier': ['npx', 'prettier', '--write' if fix else '--check'] if fix else ['npx', 'prettier', '--check'],
            'cargo': ['cargo', 'fmt'] if fix else ['cargo', 'fmt', '--check'],
            'gofmt': ['gofmt', '-w'] if fix else ['gofmt', '-d'],
        }
        
        if tool not in commands:
            return {"ok": False, "error": f"Unsupported tool: {tool}"}
        
        cmd = commands[tool]
        if tool in ['ruff', 'eslint', 'prettier'] and not fix:
            cmd = cmd[:-1]
        
        proc = subprocess.run(
            cmd, shell=False, cwd=work_dir,
            capture_output=True, text=True, timeout=60
        )
        return {
            "ok": proc.returncode == 0 or (fix and proc.returncode in [0, 1]),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-5000:] if proc.stdout else "",
            "stderr": proc.stderr[-5000:] if proc.stderr else "",
            "tool": tool
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Lint timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_diff(staged: bool = False, path: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = root
        if path:
            work_dir = (root / path).resolve()
            if not str(work_dir).startswith(str(root.resolve())):
                return {"ok": False, "error": "Path traversal not allowed"}
        
        cmd = ['git', 'diff']
        if staged:
            cmd.append('--staged')
        if path:
            cmd.append('--')
            cmd.append(path)
        
        proc = subprocess.run(
            cmd, cwd=work_dir, capture_output=True, text=True, timeout=30
        )
        return {
            "ok": True,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_status(root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        proc = subprocess.run(
            ['git', 'status', '--porcelain'], cwd=root,
            capture_output=True, text=True, timeout=30
        )
        return {
            "ok": True,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_log(limit: int = 20, oneline: bool = True, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        cmd = ['git', 'log', f'-{limit}']
        if oneline:
            cmd.append('--oneline')
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=30
        )
        return {
            "ok": True,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_commit(message: str, add_all: bool = True, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        if add_all:
            subprocess.run(['git', 'add', '-A'], cwd=root, capture_output=True, timeout=30)
        proc = subprocess.run(
            ['git', 'commit', '-m', message], cwd=root,
            capture_output=True, text=True, timeout=30
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_push(remote: str = "origin", branch: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        cmd = ['git', 'push', remote]
        if branch:
            cmd.append(branch)
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=60
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_git_pull(remote: str = "origin", branch: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        cmd = ['git', 'pull', remote]
        if branch:
            cmd.append(branch)
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, timeout=60
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# SSRF Protection: Block internal/private IPs and localhost
def _is_url_safe(url: str) -> tuple[bool, str]:
    """Check if URL is safe (no SSRF to internal networks)."""
    try:
        import urllib.parse
        import ipaddress
        import socket
        
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        
        if not hostname:
            return False, "Geçersiz URL"
        
        # Block localhost/loopback
        if hostname.lower() in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            return False, "YASAK: localhost/loopback adresine erişim engellendi (SSRF koruması)"
        
        # Resolve and check IP
        try:
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            
            # Block private ranges
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                return False, f"YASAK: Özel/İç ağ IP'sine erişim engellendi: {ip} (SSRF koruması)"
            
            # Block metadata services (cloud)
            if ip in ('169.254.169.254', '169.254.170.2', '100.100.100.200'):
                return False, f"YASAK: Cloud metadata servisine erişim engellendi: {ip}"
                
        except socket.gaierror:
            pass  # DNS resolution failed, allow but will fail later
            
        return True, ""
    except Exception as e:
        return False, f"URL güvenlik kontrolü hatası: {e}"

def tool_http_request(url: str, method: str = "GET", headers: dict = None, body: str = "", timeout: int = 30, root: Path = None) -> Dict[str, Any]:
    # SSRF Protection
    safe, error = _is_url_safe(url)
    if not safe:
        return {"ok": False, "error": error, "code": "ssrf_blocked"}
    
    import urllib.request
    import urllib.error
    import json
    try:
        req_headers = headers or {}
        if body and 'Content-Type' not in req_headers:
            req_headers['Content-Type'] = 'application/json'
        data = body.encode('utf-8') if body else None
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode('utf-8', errors='replace')
            try:
                parsed = json.loads(resp_body)
            except:
                parsed = resp_body
            return {
                "ok": True,
                "status": resp.getcode(),
                "headers": dict(resp.headers),
                "body": parsed
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode('utf-8', errors='replace')}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_fetch_url(url: str, format: str = "text", timeout: int = 30, root: Path = None) -> Dict[str, Any]:
    # SSRF Protection
    safe, error = _is_url_safe(url)
    if not safe:
        return {"ok": False, "error": error, "code": "ssrf_blocked"}
    
    import urllib.request
    import urllib.error
    from bs4 import BeautifulSoup
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'EMOG-Agent/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            content_type = resp.headers.get('Content-Type', '')
            
            if format == 'json':
                try:
                    import json
                    data = json.loads(content.decode('utf-8'))
                    return {"ok": True, "data": data}
                except:
                    pass
            elif format == 'html':
                return {"ok": True, "data": content.decode('utf-8', errors='replace')}
            elif format == 'markdown':
                soup = BeautifulSoup(content, 'html.parser')
                text = soup.get_text(separator='\n', strip=True)
                return {"ok": True, "data": text}
            else:
                return {"ok": True, "data": content.decode('utf-8', errors='replace')}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# SQL Injection Protection
SQL_FORBIDDEN_PATTERNS = (
    ";--", "/*", "*/", "@@", "char(", "nchar(", "varchar(", "nvarchar(",
    "exec(", "execute(", "sp_", "xp_", "waitfor delay", "benchmark(",
    "sleep(", "pg_sleep(", "load_file(", "into outfile", "into dumpfile",
    "union select", "union all select", "information_schema", "sys.tables",
    "sys.columns", "sysobjects", "syscolumns", "master..", "msdb..",
)

def _is_sql_safe(query: str) -> tuple[bool, str]:
    """Basic SQL injection detection."""
    if not query:
        return False, "Boş sorgu"
    
    query_lower = query.lower().strip()
    
    # Must start with SELECT for readonly mode
    if not query_lower.startswith('select'):
        return False, "Sadece SELECT sorgularına izin verilir (readonly mod)"
    
    # Check for forbidden patterns
    for pattern in SQL_FORBIDDEN_PATTERNS:
        if pattern in query_lower:
            return False, f"YASAKLI SQL KALIBI: '{pattern}' - SQL injection koruması"
    
    # Limit query length
    if len(query) > 5000:
        return False, "Sorgu çok uzun (max 5000 karakter)"
    
    return True, ""

def tool_database_query(query: str, params: list = None, readonly: bool = True, root: Path = None) -> Dict[str, Any]:
    # SQL Injection Protection
    safe, error = _is_sql_safe(query)
    if not safe:
        return {"ok": False, "error": error, "code": "sql_injection_blocked"}
    
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    import sqlite3
    import os
    try:
        # Find database file
        db_files = list(root.rglob('*.sqlite3')) + list(root.rglob('*.sqlite')) + list(root.rglob('*.db'))
        if not db_files:
            return {"ok": False, "error": "No database file found in project"}
        
        db_path = db_files[0]
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if readonly:
            # Check for write operations
            query_lower = query.strip().lower()
            if any(kw in query_lower for kw in ['insert', 'update', 'delete', 'drop', 'create', 'alter', 'truncate']):
                return {"ok": False, "error": "Write operations not allowed in readonly mode"}
        
        cursor.execute(query, params or [])
        if query.strip().lower().startswith('select'):
            rows = [dict(row) for row in cursor.fetchall()]
            return {"ok": True, "rows": rows, "count": len(rows)}
        else:
            conn.commit()
            return {"ok": True, "rowcount": cursor.rowcount}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: conn.close()
        except: pass


# Docker Security: Block container escape vectors
DOCKER_FORBIDDEN_FLAGS = (
    "--privileged", "--pid=host", "--network=host", "--ipc=host", "--uts=host",
    "--cap-add=ALL", "--cap-add=CAP_SYS_ADMIN", "--cap-add=CAP_DAC_OVERRIDE",
    "--device=", "/dev/", "/proc/", "/sys/", "/var/run/docker.sock",
    "docker.sock", "host.docker.internal",
)

def _is_docker_safe(cmd_list: list, volumes: list = None) -> tuple[bool, str]:
    """Check docker command for escape vectors."""
    cmd_str = " ".join(cmd_list).lower()
    
    # Check for forbidden flags
    for flag in DOCKER_FORBIDDEN_FLAGS:
        if flag.lower() in cmd_str:
            return False, f"YASAKLI DOCKER PARAMETRESİ: '{flag}' - container escape koruması"
    
    # Check volumes for host path mounts
    if volumes:
        for vol in volumes:
            vol_lower = vol.lower()
            if any(p in vol_lower for p in ["/etc", "/root", "/home", "/var/run", "/proc", "/sys", "/dev", "docker.sock"]):
                return False, f"YASAKLI VOLUME: '{vol}' - host sistem dosyalarına erişim engellendi"
    
    return True, ""

def tool_docker_build(path: str = ".", tag: str = "", dockerfile: str = "Dockerfile", build_args: dict = None, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if not tag:
            return {"ok": False, "error": "tag is required"}
        
        cmd = ['docker', 'build', '-t', tag, '-f', dockerfile]
        if build_args:
            for k, v in build_args.items():
                cmd.extend(['--build-arg', f'{k}={v}'])
        cmd.append('.')
        
        # Safety check
        safe, error = _is_docker_safe(cmd)
        if not safe:
            return {"ok": False, "error": error, "code": "docker_escape_blocked"}
        
        proc = subprocess.run(
            cmd, cwd=work_dir, capture_output=True, text=True, timeout=300
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-5000:] if proc.stdout else "",
            "stderr": proc.stderr[-5000:] if proc.stderr else ""
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Docker build timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_docker_run(image: str, command: str = "", ports: list = None, env: dict = None, 
                   volumes: list = None, detach: bool = False, remove: bool = True, 
                   root: Path = None) -> Dict[str, Any]:
    import subprocess
    try:
        cmd = ['docker', 'run']
        if remove:
            cmd.append('--rm')
        if detach:
            cmd.append('-d')
        if ports:
            for p in ports:
                cmd.extend(['-p', p])
        if env:
            for k, v in env.items():
                cmd.extend(['-e', f'{k}={v}'])
        if volumes:
            for v in volumes:
                cmd.extend(['-v', v])
        cmd.append(image)
        if command:
            cmd.extend(command.split())
        
        # Safety check
        safe, error = _is_docker_safe(cmd, volumes)
        if not safe:
            return {"ok": False, "error": error, "code": "docker_escape_blocked"}
        
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Docker run timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_analyze_dependencies(path: str = ".", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import json
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        results = {}
        
        # Python
        req_files = list(work_dir.rglob('requirements*.txt')) + [work_dir / 'pyproject.toml', work_dir / 'setup.py']
        for f in req_files:
            if f.exists():
                try:
                    proc = subprocess.run(['pip-audit', '-r', str(f), '--format=json'], capture_output=True, text=True, timeout=60, cwd=work_dir)
                    if proc.returncode == 0:
                        results['python'] = json.loads(proc.stdout)
                except:
                    pass
        
        # Node.js
        pkg_json = work_dir / 'package.json'
        if pkg_json.exists():
            try:
                proc = subprocess.run(['npm', 'audit', '--json'], capture_output=True, text=True, timeout=60, cwd=work_dir)
                results['nodejs'] = json.loads(proc.stdout)
            except:
                pass
        
        # Rust
        cargo_toml = work_dir / 'Cargo.toml'
        if cargo_toml.exists():
            try:
                proc = subprocess.run(['cargo', 'audit', '--json'], capture_output=True, text=True, timeout=60, cwd=work_dir)
                results['rust'] = json.loads(proc.stdout)
            except:
                pass
        
        # Go
        go_mod = work_dir / 'go.mod'
        if go_mod.exists():
            try:
                proc = subprocess.run(['go', 'list', '-m', '-json', 'all'], capture_output=True, text=True, timeout=60, cwd=work_dir)
                results['go'] = proc.stdout
            except:
                pass
        
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_summarize_code(path: str, detail: str = "medium", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if not full_path.exists():
            return {"ok": False, "error": "Path not found"}
        
        if full_path.is_file():
            content = full_path.read_text(encoding='utf-8', errors='replace')
            lines = content.splitlines()
            return {
                "ok": True,
                "type": "file",
                "path": path,
                "lines": len(lines),
                "size": len(content),
                "summary": f"File: {path} ({len(lines)} lines, {len(content)} bytes)"
            }
        else:
            files = list(full_path.rglob('*'))
            file_count = sum(1 for f in files if f.is_file())
            return {
                "ok": True,
                "type": "directory",
                "path": path,
                "files": file_count,
                "summary": f"Directory: {path} ({file_count} files)"
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== ADVANCED TOOL IMPLEMENTATIONS =====

def tool_generate_tests(path: str, framework: str = "auto", coverage_target: int = 80, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        content = full_path.read_text(encoding='utf-8', errors='replace')
        
        if framework == "auto":
            if full_path.suffix == '.py':
                framework = "pytest"
            elif full_path.suffix in ['.js', '.ts', '.jsx', '.tsx']:
                framework = "jest"
            elif full_path.suffix == '.go':
                framework = "go test"
            elif full_path.suffix == '.rs':
                framework = "cargo test"
            else:
                framework = "pytest"
        
        from agent_app.project_scanner import get_project_context
        project_ctx = get_project_context(max_chars=3000)
        
        system_prompt = f"""You are an expert software engineer. Generate comprehensive unit tests for the given code.
Project context: {project_ctx[:2000]}

Target framework: {framework}
Target coverage: {coverage_target}%

Generate complete, runnable test files. Follow best practices:
- Test edge cases, error conditions, happy paths
- Use appropriate mocking
- Follow framework conventions
- Aim for {coverage_target}% coverage"""
        
        user_prompt = f"""Generate tests for this file ({path}):

```{full_path.suffix[1:] if full_path.suffix else 'text'}
{content[:8000]}
```"""
        
        from agent_app.views import client
        try:
            response = client.chat.completions.create(
                model=getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-chat'),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=4000,
                reasoning_effort="none"
            )
            test_content = response.choices[0].message.content or ""
        except Exception as e:
            return {"ok": False, "error": f"AI generation failed: {e}"}
        
        test_dir = full_path.parent / "tests"
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / f"test_{full_path.name}"
        test_file.write_text(test_content, encoding='utf-8')
        
        return {"ok": True, "test_file": str(test_file.relative_to(root)), "framework": framework, "content_preview": test_content[:500]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_explain_code(path: str, detail: str = "medium", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        content = full_path.read_text(encoding='utf-8', errors='replace')
        
        detail_prompts = {
            "brief": "Give a 2-3 sentence summary of what this code does.",
            "medium": "Explain what this code does, its main functions, and key logic. Include inputs/outputs and side effects.",
            "detailed": "Provide a comprehensive explanation: purpose, architecture, key functions, data flow, dependencies, design patterns, and potential issues."
        }
        
        prompt = f"""{detail_prompts.get(detail, detail_prompts['medium'])}

File: {path}
```{full_path.suffix[1:] if full_path.suffix else 'text'}
{content[:10000]}
```"""
        
        from agent_app.views import client
        try:
            response = client.chat.completions.create(
                model=getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-chat'),
                messages=[
                    {"role": "system", "content": "You are an expert code explainer. Be clear, concise, and technical."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            explanation = response.choices[0].message.content or ""
        except Exception as e:
            return {"ok": False, "error": f"AI generation failed: {e}"}
        
        return {"ok": True, "path": path, "explanation": explanation, "detail": detail}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_secret_scan(path: str = ".", include_history: bool = True, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    import json
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        results = {}
        
        try:
            cmd = ['trufflehog', 'filesystem', '--json', str(work_dir)]
            if not include_history:
                cmd.insert(2, '--no-history')
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=work_dir)
            if proc.stdout:
                findings = [json.loads(line) for line in proc.stdout.strip().split('\n') if line]
                results['trufflehog'] = findings
        except FileNotFoundError:
            results['trufflehog'] = "trufflehog not installed"
        except Exception as e:
            results['trufflehog'] = f"Error: {e}"
        
        try:
            proc = subprocess.run(['gitleaks', 'detect', '--source', str(work_dir), '--report-format', 'json', '--verbose'], 
                                capture_output=True, text=True, timeout=120, cwd=work_dir)
            if proc.stdout:
                results['gitleaks'] = json.loads(proc.stdout)
        except FileNotFoundError:
            results['gitleaks'] = "gitleaks not installed"
        except Exception as e:
            results['gitleaks'] = f"Error: {e}"
        
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_sast_scan(path: str = ".", config: str = "auto", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    import json
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        cmd = ['semgrep', 'scan', '--json', f'--config={config}', str(work_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=work_dir)
        
        if proc.stdout:
            try:
                results = json.loads(proc.stdout)
            except:
                results = {"raw": proc.stdout}
        else:
            results = {"stderr": proc.stderr, "returncode": proc.returncode}
        
        return {"ok": True, "results": results}
    except FileNotFoundError:
        return {"ok": False, "error": "semgrep not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_generate_readme(path: str = ".", overwrite: bool = False, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        readme_path = work_dir / "README.md"
        if readme_path.exists() and not overwrite:
            return {"ok": False, "error": "README.md already exists. Use overwrite=true to replace."}
        
        from agent_app.project_scanner import get_project_context
        project_ctx = get_project_context(max_chars=3000)
        
        key_files = []
        for ext in ['.py', '.js', '.ts', '.go', '.rs', '.java', '.cpp', '.c', '.h']:
            key_files.extend([str(f.relative_to(work_dir)) for f in work_dir.rglob(f'*{ext}')][:20])
        
        file_contents = {}
        for f in key_files[:10]:
            try:
                fp = work_dir / f
                if fp.is_file():
                    file_contents[f] = fp.read_text(encoding='utf-8', errors='replace')[:2000]
            except:
                pass
        
        prompt = f"""Generate a comprehensive README.md for this project.

Project context: {project_ctx[:2000]}
Key files: {key_files[:20]}
File contents: {file_contents}

Generate a professional README with:
1. Project title and description
2. Features
3. Installation instructions
4. Usage examples
4. Configuration
5. API documentation (if applicable)
5. Contributing guidelines
5. License

Use proper Markdown formatting with badges, code blocks, and clear sections."""
        
        from agent_app.views import client
        try:
            response = client.chat.completions.create(
                model=getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-chat'),
                messages=[
                    {"role": "system", "content": "You are a technical writer creating professional README.md files."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=4000
            )
            readme_content = response.choices[0].message.content or ""
        except Exception as e:
            return {"ok": False, "error": f"AI generation failed: {e}"}
        
        readme_path.write_text(readme_content, encoding='utf-8')
        return {"ok": True, "readme_path": str(readme_path.relative_to(root))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_changelog_generate(path: str = ".", since: str = "", until: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        cmd = ['git', 'log', '--pretty=format:%h|%s|%an|%ad', '--date=short']
        if since:
            cmd.extend(['--since', since])
        if until:
            cmd.extend(['--until', until])
        
        proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr}
        
        commits = []
        for line in proc.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('|', 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "message": parts[1], "author": parts[2], "date": parts[3]})
        
        categories = {"feat": [], "fix": [], "docs": [], "refactor": [], "test": [], "chore": [], "other": []}
        for c in commits:
            msg = c['message'].lower()
            cat = 'other'
            for prefix in ['feat:', 'fix:', 'docs:', 'refactor:', 'test:', 'chore:']:
                if msg.startswith(prefix):
                    cat = prefix[:-1]
                    break
            categories[cat].append(c)
        
        lines = ["# Changelog\n"]
        for cat, items in categories.items():
            if not items:
                continue
            title = cat.capitalize()
            if cat == 'feat':
                title = 'Features'
            elif cat == 'fix':
                title = 'Bug Fixes'
            elif cat == 'refactor':
                title = 'Refactoring'
            lines.append(f"\n## {title}\n")
            for item in items:
                lines.append(f"- {item['message']} ({item['hash'][:7]}) by {item['author']} on {item['date']}")
        
        changelog = '\n'.join(lines)
        changelog_path = Path(work_dir) / "CHANGELOG.md"
        changelog_path.write_text(changelog, encoding='utf-8')
        
        return {"ok": True, "changelog_path": str(changelog_path.relative_to(root)), "commits_processed": len(commits)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_github_actions_validate(path: str = ".github/workflows", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import yaml
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if not work_dir.exists():
            return {"ok": False, "error": "Workflows directory not found"}
        
        issues = []
        warnings = []
        
        for wf_file in work_dir.rglob('*.yml'):
            try:
                with open(wf_file, 'r') as f:
                    content = yaml.safe_load(f)
                
                if 'name' not in content:
                    warnings.append(f"{wf_file.name}: Missing 'name'")
                if 'on' not in content:
                    issues.append(f"{wf_file.name}: Missing 'on' trigger")
                if 'jobs' not in content:
                    issues.append(f"{wf_file.name}: Missing 'jobs'")
                
                content_str = yaml.dump(content)
                if '${{ secrets.' not in content_str and ('password' in content_str.lower() or 'token' in content_str.lower() or 'key' in content_str.lower()):
                    warnings.append(f"{wf_file.name}: Possible hardcoded secrets")
                
                if 'uses:' in content_str:
                    for line in content_str.split('\n'):
                        if 'uses:' in line and '@' not in line and 'actions/' in line:
                            warnings.append(f"{wf_file.name}: Action not pinned to SHA")
                            
            except yaml.YAMLError as e:
                issues.append(f"{wf_file.name}: YAML syntax error - {e}")
            except Exception as e:
                issues.append(f"{wf_file.name}: {e}")
        
        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "files_checked": len(list(Path(path).rglob('*.yml')))
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_dependency_graph(path: str = ".", format: str = "json", include_external: bool = False, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        graph = {"nodes": [], "edges": []}
        node_map = {}
        
        for py_file in work_dir.rglob('*.py'):
            try:
                rel = py_file.relative_to(work_dir)
                content = py_file.read_text(encoding='utf-8', errors='replace')
                imports = []
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('import ') or line.startswith('from '):
                        parts = line.split()
                        if len(parts) >= 2:
                            imp = parts[1].split('.')[0]
                            imports.append(imp)
                
                node_id = str(rel)
                if node_id not in node_map:
                    node_map[node_id] = len(graph["nodes"])
                    graph["nodes"].append({"id": node_id, "label": str(rel), "type": "file"})
                
                for imp in imports:
                    if include_external or not imp.startswith('_'):
                        if imp not in node_map:
                            node_map[imp] = len(graph["nodes"])
                            graph["nodes"].append({"id": imp, "label": imp, "type": "module"})
                        graph["edges"].append({"from": node_map[node_id], "to": node_map[imp]})
            except:
                pass
        
        if format == "dot":
            lines = ['digraph G {']
            for node in graph["nodes"]:
                lines.append(f'  "{node["id"]}" [label="{node["label"]}"];')
            for edge in graph["edges"]:
                lines.append(f'  "{graph["nodes"][edge["from"]]["id"]}" -> "{graph["nodes"][edge["to"]]["id"]}";')
            lines.append('}')
            output = '\n'.join(lines)
        elif format == "mermaid":
            lines = ['graph TD']
            for node in graph["nodes"]:
                lines.append(f'  {node["id"]}["{node["label"]}"]')
            for edge in graph["edges"]:
                lines.append(f'  {graph["nodes"][edge["from"]]["id"]} --> {graph["nodes"][edge["to"]]["id"]}')
            output = '\n'.join(lines)
        else:
            output = graph
        
        return {"ok": True, "graph": output, "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_profile_code(command: str, duration: int = 30, output_format: str = "json", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    import tempfile
    import os
    try:
        work_dir = root
        
        if command.startswith('python ') or command.startswith('python3 '):
            with tempfile.NamedTemporaryFile(suffix='.prof', delete=False) as f:
                prof_file = f.name
            try:
                cmd = f'python -m cProfile -o {prof_file} {command}'
                proc = subprocess.run(cmd, shell=True, cwd=work_dir, capture_output=True, text=True, timeout=duration + 30)
                
                return {
                    "ok": proc.returncode == 0,
                    "stdout": proc.stdout[-5000:],
                    "stderr": proc.stderr[-5000:],
                    "profile_file": prof_file
                }
            finally:
                try: os.unlink(prof_file)
                except: pass
        else:
            return {"ok": False, "error": "Only Python profiling supported currently"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Profiling timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_benchmark(snippets: list, iterations: int = 1000, language: str = "python", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import timeit
    import json
    try:
        results = []
        for snippet in snippets:
            name = snippet.get('name', 'unnamed')
            code = snippet.get('code', '')
            
            if language == "python":
                for _ in range(10):
                    try: exec(code)
                    except: pass
                
                timer = timeit.Timer(code)
                times = timer.repeat(repeat=5, number=iterations)
                avg = sum(times) / len(times)
                min_t = min(times)
                max_t = max(times)
                
                results.append({
                    "name": name,
                    "iterations": iterations,
                    "avg_ms": avg * 1000,
                    "min_ms": min_t * 1000,
                    "max_ms": max_t * 1000,
                    "ops_per_sec": iterations / avg if avg > 0 else 0
                })
            else:
                results.append({"name": name, "error": f"Language {language} not supported"})
        
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_api_docs_generate(path: str = ".", format: str = "json", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        openapi_spec = {"openapi": "3.0.0", "info": {"title": "API", "version": "1.0.0"}, "paths": {}}
        found = False
        
        for py_file in work_dir.rglob('*.py'):
            try:
                content = py_file.read_text(encoding='utf-8', errors='replace')
                if 'FastAPI' in content or 'Flask' in content or '@app.route' in content or '@app.get' in content:
                    found = True
                    break
            except:
                pass
        
        if not found:
            pkg = work_dir / 'package.json'
            if pkg.exists():
                import json
                pkg_data = json.loads(pkg.read_text())
                if any('express' in str(v) for v in {**pkg_data.get('dependencies', {}), **pkg_data.get('devDependencies', {})}.values()):
                    found = True
        
        if format == "yaml":
            import yaml
            output = yaml.dump(openapi_spec, allow_unicode=True)
        else:
            import json
            output = json.dumps(openapi_spec, indent=2)
        
        return {"ok": True, "spec": output, "detected_framework": "FastAPI/Flask/Express" if found else "unknown"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_rollback_deploy(steps: int = 1, deploy_command: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = root
        
        for i in range(steps):
            proc = subprocess.run(['git', 'revert', '--no-edit', f'HEAD~{i}'], cwd=work_dir, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                return {"ok": False, "error": f"Revert failed at step {i+1}: {proc.stderr}"}
        
        if deploy_command:
            proc = subprocess.run(deploy_command, shell=True, cwd=work_dir, capture_output=True, text=True, timeout=300)
            return {
                "ok": proc.returncode == 0,
                "reverted": steps,
                "deploy_stdout": proc.stdout[-5000:],
                "deploy_stderr": proc.stderr[-5000:],
                "deploy_returncode": proc.returncode
            }
        
        return {"ok": True, "reverted": steps, "message": "Reverted commits. Run deploy command manually."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_export_data(source: str, format: str = "csv", output_path: str = "", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import csv
    import json
    import sqlite3
    try:
        import pandas as pd
    except ImportError:
        return {"ok": False, "error": "pandas not installed"}
    try:
        work_dir = root
        
        if source.endswith('.sql') or source.strip().lower().startswith('select'):
            db_files = list(root.rglob('*.sqlite3')) + list(root.rglob('*.sqlite')) + list(root.rglob('*.db'))
            if not db_files:
                return {"ok": False, "error": "No database found"}
            conn = sqlite3.connect(str(db_files[0]))
            df = pd.read_sql_query(source, conn)
            conn.close()
        elif source.endswith('.csv'):
            df = pd.read_csv(work_dir / source)
        elif source.endswith('.json'):
            df = pd.read_json(work_dir / source)
        else:
            return {"ok": False, "error": "Unsupported source format"}
        
        out_path = Path(output_path) if output_path else root / f"export_{int(__import__('time').time())}.{format}"
        if format == 'csv':
            df.to_csv(out_path, index=False)
        elif format == 'json':
            df.to_json(out_path, orient='records', indent=2)
        elif format == 'parquet':
            df.to_parquet(out_path)
        elif format == 'xlsx':
            df.to_excel(out_path, index=False)
        else:
            return {"ok": False, "error": f"Unsupported format: {format}"}
        
        return {"ok": True, "output_path": str(out_path.relative_to(root)), "rows": len(df), "columns": list(df.columns)}
    except ImportError:
        return {"ok": False, "error": "pandas not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_data_diff(source_a: str, source_b: str, key_columns: list = None, root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    try:
        import pandas as pd
        import sqlite3
        
        def load_source(src):
            if src.endswith('.sql') or src.strip().lower().startswith('select'):
                db_files = list(root.rglob('*.sqlite3')) + list(root.rglob('*.sqlite')) + list(root.rglob('*.db'))
                if not db_files:
                    return None
                conn = sqlite3.connect(str(db_files[0]))
                return pd.read_sql_query(src, conn)
            elif src.endswith('.csv'):
                return pd.read_csv(root / src)
            elif src.endswith('.json'):
                return pd.read_json(root / src)
            return None
        
        df_a = load_source(source_a)
        df_b = load_source(source_b)
        
        if df_a is None or df_b is None:
            return {"ok": False, "error": "Could not load one or both sources"}
        
        if key_columns:
            merged = df_a.merge(df_b, on=key_columns, how='outer', indicator=True, suffixes=('_a', '_b'))
        else:
            merged = df_a.merge(df_b, how='outer', indicator=True, suffixes=('_a', '_b'))
        
        diff = merged[merged['_merge'] != 'both']
        return {
            "ok": True,
            "total_a": len(df_a),
            "total_b": len(df_b),
            "differences": len(diff),
            "diff_sample": diff.head(20).to_dict('records')
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_android_build(path: str = ".", variant: str = "release", output: str = "aab", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        gradle = work_dir / 'gradlew'
        if not gradle.exists():
            return {"ok": False, "error": "gradlew not found"}
        
        task = f"bundle{variant.capitalize()}" if output == "aab" else f"assemble{variant.capitalize()}"
        proc = subprocess.run(['./gradlew', task], cwd=work_dir, capture_output=True, text=True, timeout=600)
        
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-5000:],
            "stderr": proc.stderr[-5000:],
            "returncode": proc.returncode
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Build timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_ios_build(path: str = ".", scheme: str = "", configuration: str = "Release", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        xcodeproj = list(work_dir.rglob('*.xcodeproj'))
        xcworkspace = list(work_dir.rglob('*.xcworkspace'))
        
        if not xcodeproj and not xcworkspace:
            return {"ok": False, "error": "No Xcode project found"}
        
        project = xcworkspace[0] if xcworkspace else xcodeproj[0]
        
        if not scheme:
            proc = subprocess.run(['xcodebuild', '-list', '-project', str(project)], capture_output=True, text=True, timeout=30)
            for line in proc.stdout.split('\n'):
                if 'Schemes:' in line:
                    schemes = line.split(':')[1].strip().split(', ')
                    if schemes:
                        scheme = schemes[0]
                        break
        
        if not scheme:
            return {"ok": False, "error": "No scheme specified or found"}
        
        proc = subprocess.run([
            'xcodebuild', '-scheme', scheme, '-configuration', configuration,
            '-derivedDataPath', 'build', 'build'
        ], cwd=work_dir, capture_output=True, text=True, timeout=600)
        
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-5000:],
            "stderr": proc.stderr[-5000:],
            "returncode": proc.returncode
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Build timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_wasm_build(path: str = ".", language: str = "rust", target: str = "web", root: Path = None) -> Dict[str, Any]:
    if root is None:
        root = Path(settings.BASE_DIR)
    import subprocess
    try:
        work_dir = (root / path).resolve()
        if not str(work_dir).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        
        if language == "rust":
            cmd = ['wasm-pack', 'build', '--target', target]
            if not (work_dir / 'Cargo.toml').exists():
                return {"ok": False, "error": "Cargo.toml not found"}
        elif language == "go":
            cmd = ['tinygo', 'build', '-o', 'main.wasm', '-target', 'wasi', '.']
        elif language in ['cpp', 'c']:
            cmd = ['emcc', '-O3', '-s', f'EXPORTED_FUNCTIONS=["_main"]', '-o', 'main.js', 'main.cpp' if language=="cpp" else 'main.c']
        else:
            return {"ok": False, "error": f"Unsupported language: {language}"}
        
        proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=300)
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-5000:],
            "stderr": proc.stderr[-5000:],
            "returncode": proc.returncode
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Build timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== MULTIMEDIA TOOLS =====


def tool_analyze_image(path: str, root: Path = None) -> Dict[str, Any]:
    """Analyze image file: dimensions, format, mode, EXIF data."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        # Try PIL first
        try:
            from PIL import Image, ExifTags
            with Image.open(full_path) as img:
                info = {
                    "path": path,
                    "format": img.format,
                    "mode": img.mode,
                    "width": img.width,
                    "height": img.height,
                    "size_bytes": full_path.stat().st_size,
                }
                
                # EXIF data
                exif = img.getexif()
                if exif:
                    exif_data = {}
                    for tag_id, value in exif.items():
                        tag = ExifTags.TAGS.get(tag_id, tag_id)
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='replace')
                        exif_data[str(tag)] = value
                    info["exif"] = exif_data
                
                # Color analysis (dominant colors)
                try:
                    small = img.resize((50, 50))
                    pixels = list(small.getdata())
                    from collections import Counter
                    color_counts = Counter(pixels)
                    dominant = color_counts.most_common(5)
                    info["dominant_colors"] = [{"rgb": c[0], "count": c[1]} for c in dominant]
                except:
                    pass
                
                return {"ok": True, **info}
        except ImportError:
            # Fallback: basic file info
            stat = full_path.stat()
            return {
                "ok": True,
                "path": path,
                "size_bytes": stat.st_size,
                "note": "PIL not available, limited info"
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_analyze_video(path: str, root: Path = None) -> Dict[str, Any]:
    """Analyze video file: duration, resolution, codec, bitrate."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        import subprocess
        import json
        
        # Use ffprobe for detailed info
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', str(full_path)
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                format_info = data.get('format', {})
                streams = data.get('streams', [])
                
                video_streams = [s for s in streams if s.get('codec_type') == 'video']
                audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
                
                info = {
                    "path": path,
                    "size_bytes": full_path.stat().st_size,
                    "format": format_info.get('format_name'),
                    "duration": float(format_info.get('duration', 0)),
                    "bitrate": int(format_info.get('bit_rate', 0)),
                    "video_streams": len(video_streams),
                    "audio_streams": len(audio_streams),
                }
                
                if video_streams:
                    v = video_streams[0]
                    info["video"] = {
                        "codec": v.get('codec_name'),
                        "width": v.get('width'),
                        "height": v.get('height'),
                        "fps": eval(v.get('r_frame_rate', '0/1')) if v.get('r_frame_rate') else 0,
                        "pix_fmt": v.get('pix_fmt'),
                    }
                
                if audio_streams:
                    a = audio_streams[0]
                    info["audio"] = {
                        "codec": a.get('codec_name'),
                        "channels": a.get('channels'),
                        "sample_rate": a.get('sample_rate'),
                    }
                
                return {"ok": True, **info}
        except FileNotFoundError:
            return {"ok": False, "error": "ffprobe not installed"}
        except Exception as e:
            return {"ok": False, "error": f"ffprobe error: {e}"}
        
        # Fallback: basic file info
        stat = full_path.stat()
        return {
            "ok": True,
            "path": path,
            "size_bytes": stat.st_size,
            "note": "ffprobe not available, limited info"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_extract_frame(path: str, timestamp: float = 0, output_path: str = None, root: Path = None) -> Dict[str, Any]:
    """Extract a frame from video at given timestamp (seconds)."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        import subprocess
        
        if output_path is None:
            output_path = str(full_path.with_suffix('.jpg'))
        out_full = (root / output_path).resolve()
        if not str(out_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Output path traversal not allowed"}
        
        cmd = [
            'ffmpeg', '-y', '-ss', str(timestamp), '-i', str(full_path),
            '-vframes', '1', '-q:v', '2', str(out_full)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if proc.returncode == 0 and out_full.exists():
            return {
                "ok": True,
                "output": output_path,
                "timestamp": timestamp,
                "size_bytes": out_full.stat().st_size
            }
        else:
            return {"ok": False, "error": proc.stderr or "ffmpeg failed"}
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_convert_media(input_path: str, output_path: str, options: dict = None, root: Path = None) -> Dict[str, Any]:
    """Convert media file using ffmpeg (images, videos, audio)."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        in_full = (root / input_path).resolve()
        out_full = (root / output_path).resolve()
        
        if not str(in_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Input path traversal not allowed"}
        if not str(out_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Output path traversal not allowed"}
        if not in_full.exists():
            return {"ok": False, "error": "Input file not found"}
        
        import subprocess
        
        cmd = ['ffmpeg', '-y', '-i', str(in_full)]
        
        if options:
            # Video options
            if 'vcodec' in options:
                cmd.extend(['-c:v', options['vcodec']])
            if 'vf' in options:  # video filters (scale, crop, etc.)
                cmd.extend(['-vf', options['vf']])
            if 'crf' in options:
                cmd.extend(['-crf', str(options['crf'])])
            if 'preset' in options:
                cmd.extend(['-preset', options['preset']])
            
            # Audio options
            if 'acodec' in options:
                cmd.extend(['-c:a', options['acodec']])
            if 'ab' in options:
                cmd.extend(['-b:a', options['ab']])
            
            # General
            if 'ss' in options:  # start time
                cmd.extend(['-ss', str(options['ss'])])
            if 't' in options:   # duration
                cmd.extend(['-t', str(options['t'])])
        
        cmd.append(str(out_full))
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if proc.returncode == 0 and out_full.exists():
            return {
                "ok": True,
                "input": input_path,
                "output": output_path,
                "size_bytes": out_full.stat().st_size
            }
        else:
            return {"ok": False, "error": proc.stderr or "ffmpeg failed"}
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===== END NEW TOOL IMPLEMENTATIONS =====


def tool_web_search(query: str, count: int = 8, providers: list = None, root: Path = None) -> Dict[str, Any]:
    """Coklu saglayicida paralel web aramasi. Anahtarli saglayicilar once."""
    try:
        from agent_app.buildkit import search as search_mod

        limit = max(1, min(int(count or 8), 20))
        response = search_mod.search(query, count=limit, providers=providers)
        return {
            "ok": bool(response.results) or bool(response.providers),
            "query": query,
            "count": len(response.results),
            "results": response.results[:limit],
            "providers": response.providers,
            "error": None if response.results else "arama sonucu bulunamadi",
        }
    except Exception as e:
        return {"ok": False, "error": f"Web aramasi basarisiz: {e}"}


def tool_deep_research(question: str, depth: str = "standard", root: Path = None) -> Dict[str, Any]:
    """Cok adimli arastirma: sorgu uretimi -> arama -> sayfalari oku -> ozetle."""
    try:
        from agent_app.buildkit import research as research_mod
        from agent_app.buildkit.events import RunLogger

        depth_map = {"quick": (3, 4), "standard": (6, 8), "deep": (10, 14)}
        query_count, read_count = depth_map.get(depth, depth_map["standard"])
        agent = research_mod.DeepResearcher(RunLogger())
        agent.max_queries = query_count
        agent.max_deep_reads = read_count
        dossier = agent.run(question)
        dossier.pop("documents", None)
        return {"ok": True, "question": question, "depth": depth, "dossier": dossier}
    except Exception as e:
        return {"ok": False, "error": f"Derin arastirma basarisiz: {e}"}


TOOL_HANDLERS = {
    "read_file": tool_read_file,
    "code_outline": tool_code_outline,
    "write_file": tool_write_file,
    "list_files": tool_list_files,
    "grep_files": tool_grep_files,
    "edit_file": tool_edit_file,
    "run_command": tool_run_command,
    "patch_file": tool_patch_file,
    "apply_patch": tool_apply_patch,
    "delete_file": tool_delete_file,
    "delete_dir": tool_delete_dir,
    "move_file": tool_move_file,
    "copy_file": tool_copy_file,
    "run_tests": tool_run_tests,
    "lint_code": tool_lint_code,
    "git_diff": tool_git_diff,
    "git_status": tool_git_status,
    "git_log": tool_git_log,
    "git_commit": tool_git_commit,
    "git_push": tool_git_push,
    "git_pull": tool_git_pull,
    "http_request": tool_http_request,
    "fetch_url": tool_fetch_url,
    "database_query": tool_database_query,
    "docker_build": tool_docker_build,
    "docker_run": tool_docker_run,
    "analyze_dependencies": tool_analyze_dependencies,
    "summarize_code": tool_summarize_code,
    "generate_tests": tool_generate_tests,
    "explain_code": tool_explain_code,
    "secret_scan": tool_secret_scan,
    "sast_scan": tool_sast_scan,
    "generate_readme": tool_generate_readme,
    "changelog_generate": tool_changelog_generate,
    "github_actions_validate": tool_github_actions_validate,
    "dependency_graph": tool_dependency_graph,
    "profile_code": tool_profile_code,
    "benchmark": tool_benchmark,
    "api_docs_generate": tool_api_docs_generate,
    "rollback_deploy": tool_rollback_deploy,
    "export_data": tool_export_data,
    "data_diff": tool_data_diff,
    "android_build": tool_android_build,
    "ios_build": tool_ios_build,
    "wasm_build": tool_wasm_build,
    "secret_scan": tool_secret_scan,
    "sast_scan": tool_sast_scan,
    "analyze_image": tool_analyze_image,
    "analyze_video": tool_analyze_video,
    "extract_frame": tool_extract_frame,
    "convert_media": tool_convert_media,
    "web_search": tool_web_search,
    "deep_research": tool_deep_research,
}


def tool_analyze_image(path: str, root: Path = None) -> Dict[str, Any]:
    """Analyze image file: dimensions, format, mode, EXIF data."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        # Try PIL first
        try:
            from PIL import Image, ExifTags
            with Image.open(full_path) as img:
                info = {
                    "path": path,
                    "format": img.format,
                    "mode": img.mode,
                    "width": img.width,
                    "height": img.height,
                    "size_bytes": full_path.stat().st_size,
                }
                
                # EXIF data
                exif = img.getexif()
                if exif:
                    exif_data = {}
                    for tag_id, value in exif.items():
                        tag = ExifTags.TAGS.get(tag_id, tag_id)
                        if isinstance(value, bytes):
                            value = value.decode('utf-8', errors='replace')
                        exif_data[str(tag)] = value
                    info["exif"] = exif_data
                
                # Color analysis (dominant colors)
                try:
                    small = img.resize((50, 50))
                    pixels = list(small.getdata())
                    from collections import Counter
                    color_counts = Counter(pixels)
                    dominant = color_counts.most_common(5)
                    info["dominant_colors"] = [{"rgb": c[0], "count": c[1]} for c in dominant]
                except:
                    pass
                
                return {"ok": True, **info}
        except ImportError:
            # Fallback: basic file info
            stat = full_path.stat()
            return {
                "ok": True,
                "path": path,
                "size_bytes": stat.st_size,
                "note": "PIL not available, limited info"
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_analyze_video(path: str, root: Path = None) -> Dict[str, Any]:
    """Analyze video file: duration, resolution, codec, bitrate."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        import subprocess
        import json
        
        # Use ffprobe for detailed info
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', str(full_path)
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                format_info = data.get('format', {})
                streams = data.get('streams', [])
                
                video_streams = [s for s in streams if s.get('codec_type') == 'video']
                audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
                
                info = {
                    "path": path,
                    "size_bytes": full_path.stat().st_size,
                    "format": format_info.get('format_name'),
                    "duration": float(format_info.get('duration', 0)),
                    "bitrate": int(format_info.get('bit_rate', 0)),
                    "video_streams": len(video_streams),
                    "audio_streams": len(audio_streams),
                }
                
                if video_streams:
                    v = video_streams[0]
                    info["video"] = {
                        "codec": v.get('codec_name'),
                        "width": v.get('width'),
                        "height": v.get('height'),
                        "fps": eval(v.get('r_frame_rate', '0/1')) if v.get('r_frame_rate') else 0,
                        "pix_fmt": v.get('pix_fmt'),
                    }
                
                if audio_streams:
                    a = audio_streams[0]
                    info["audio"] = {
                        "codec": a.get('codec_name'),
                        "channels": a.get('channels'),
                        "sample_rate": a.get('sample_rate'),
                    }
                
                return {"ok": True, **info}
        except FileNotFoundError:
            return {"ok": False, "error": "ffprobe not installed"}
        except Exception as e:
            return {"ok": False, "error": f"ffprobe error: {e}"}
        
        # Fallback: basic file info
        stat = full_path.stat()
        return {
            "ok": True,
            "path": path,
            "size_bytes": stat.st_size,
            "note": "ffprobe not available, limited info"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_extract_frame(path: str, timestamp: float = 0, output_path: str = None, root: Path = None) -> Dict[str, Any]:
    """Extract a frame from video at given timestamp (seconds)."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        full_path = (root / path).resolve()
        if not str(full_path).startswith(str(root.resolve())):
            return {"ok": False, "error": "Path traversal not allowed"}
        if not full_path.exists():
            return {"ok": False, "error": "File not found"}
        
        import subprocess
        
        if output_path is None:
            output_path = str(full_path.with_suffix('.jpg'))
        out_full = (root / output_path).resolve()
        if not str(out_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Output path traversal not allowed"}
        
        cmd = [
            'ffmpeg', '-y', '-ss', str(timestamp), '-i', str(full_path),
            '-vframes', '1', '-q:v', '2', str(out_full)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if proc.returncode == 0 and out_full.exists():
            return {
                "ok": True,
                "output": output_path,
                "timestamp": timestamp,
                "size_bytes": out_full.stat().st_size
            }
        else:
            return {"ok": False, "error": proc.stderr or "ffmpeg failed"}
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_convert_media(input_path: str, output_path: str, options: dict = None, root: Path = None) -> Dict[str, Any]:
    """Convert media file using ffmpeg (images, videos, audio)."""
    if root is None:
        root = get_project_root() or Path(settings.BASE_DIR)
    try:
        in_full = (root / input_path).resolve()
        out_full = (root / output_path).resolve()
        
        if not str(in_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Input path traversal not allowed"}
        if not str(out_full).startswith(str(root.resolve())):
            return {"ok": False, "error": "Output path traversal not allowed"}
        if not in_full.exists():
            return {"ok": False, "error": "Input file not found"}
        
        import subprocess
        
        cmd = ['ffmpeg', '-y', '-i', str(in_full)]
        
        if options:
            # Video options
            if 'vcodec' in options:
                cmd.extend(['-c:v', options['vcodec']])
            if 'vf' in options:  # video filters (scale, crop, etc.)
                cmd.extend(['-vf', options['vf']])
            if 'crf' in options:
                cmd.extend(['-crf', str(options['crf'])])
            if 'preset' in options:
                cmd.extend(['-preset', options['preset']])
            
            # Audio options
            if 'acodec' in options:
                cmd.extend(['-c:a', options['acodec']])
            if 'ab' in options:
                cmd.extend(['-b:a', options['ab']])
            
            # General
            if 'ss' in options:  # start time
                cmd.extend(['-ss', str(options['ss'])])
            if 't' in options:   # duration
                cmd.extend(['-t', str(options['t'])])
        
        cmd.append(str(out_full))
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if proc.returncode == 0 and out_full.exists():
            return {
                "ok": True,
                "input": input_path,
                "output": output_path,
                "size_bytes": out_full.stat().st_size
            }
        else:
            return {"ok": False, "error": proc.stderr or "ffmpeg failed"}
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg not installed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def execute_tool(name: str, arguments: Dict[str, Any], root: Path = None) -> Dict[str, Any]:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    # Use project root if available (for user project isolation)
    if root is None:
        project_root = get_project_root()
        if project_root:
            root = project_root
        else:
            # SECURITY: Never fall back to BASE_DIR - deny instead
            return {"ok": False, "error": "No project context set. Select a project first."}
    # Arac hatasi (yanlis arguman, beklenmedik istisna) TUM pipeline'i yikmasin:
    # hatayi araca geri dondur ki ajan mesaji gorup kendini duzeltsin.
    try:
        return handler(**arguments, root=root)
    except TypeError as exc:
        return {
            "ok": False,
            "error": f"Gecersiz arac argumanlari ({name}): {exc}",
            "code": "bad_tool_args",
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Arac calistirma hatasi: %s", name)
        return {"ok": False, "error": f"{name} araci hatasi: {exc}"}