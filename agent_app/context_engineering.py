"""AST tabanli kod iskeleti ve git-diff tabanli akilli baglam penceresi.

Harici bagimlilik yoktur; yalnizca Python standart kutuphanesi kullanilir.

    get_code_outline()        -> bir .py dosyasinin sinif/fonksiyon imzalari
    build_repo_map()          -> tum .py dosyalarinin iskelet haritasi
    get_changed_files()       -> git diff / ls-files ile degisen dosyalar
    get_file_diff()           -> tek dosya icin unified diff
    build_active_context()    -> degisen/ilgili dosyalarin akilli penceresi
    build_smart_context()     -> iskelet + aktif dosya baglami
    get_smart_project_context() -> LLM istemine hazir metin

Ayar: Django ``settings.CONTEXT_ENGINEERING`` (ortam degiskenleriyle ezilebilir).
Django yapilandirilmamissa modul varsayilanlarla calisir.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_DEFAULT_IGNORE_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", ".idea", ".vscode", "dist", "build", "out", "target",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next",
    "coverage", "htmlcov", "task_runs", "migrations",
}

_DEFAULTS: Dict[str, object] = {
    "enabled": True,
    "use_git": True,
    "include_diffs": True,
    "project_context_max_chars": 14000,
    "repo_map_max_chars": 5000,
    "repo_map_max_files": 400,
    "active_context_max_chars": 5000,
    "smart_context_max_chars": 10000,
    "max_file_chars": 4000,
    "max_diff_chars": 6000,
    "active_file_threshold_lines": 200,
    "max_active_files": 8,
    "git_timeout": 8,
}

_PRIORITY_FILE_NAMES = (
    "main.py", "app.py", "manage.py", "server.py", "run.py",
    "wsgi.py", "asgi.py", "__main__.py",
)

_OUTLINE_CACHE: Dict[Tuple[str, bool], Tuple[float, int, str]] = {}
_CACHE_LOCK = threading.Lock()

_PY_PATH_RE = re.compile(r"[A-Za-z0-9_./\\-]+\.py\b")


def get_setting(key: str, default=None):
    """Django settings.CONTEXT_ENGINEERING degerini oku; yoksa varsayilana don."""
    try:
        from django.conf import settings

        data = getattr(settings, "CONTEXT_ENGINEERING", None) or {}
        if key in data:
            return data[key]
    except Exception:
        pass
    if default is not None:
        return default
    return _DEFAULTS.get(key)


def _cfg(key: str, default=None):
    return get_setting(key, default)


def truncate_text(text: str, limit: int, label: str = "icerik") -> str:
    """Metni butceye sigdir; kesildiginde bunu acikca belirt."""
    if text is None:
        return ""
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n... ({label} kesildi, {len(text)} karakter)"


# ----------------------------------------------------------------------
# AST iskeleti
# ----------------------------------------------------------------------
def _safe_unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "..."


def _format_args(args: ast.arguments) -> str:
    parts: List[str] = []
    positional = list(getattr(args, "posonlyargs", []) or []) + list(args.args or [])
    defaults = list(args.defaults or [])
    first_default = len(positional) - len(defaults)

    for index, arg in enumerate(positional):
        name = arg.arg
        if arg.annotation is not None:
            name += ": " + _safe_unparse(arg.annotation)
        if index >= first_default and defaults:
            default = defaults[index - first_default]
            name += "=" + _safe_unparse(default)
        parts.append(name)

    if args.vararg is not None:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs or [], args.kw_defaults or []):
        name = arg.arg
        if arg.annotation is not None:
            name += ": " + _safe_unparse(arg.annotation)
        if default is not None:
            name += "=" + _safe_unparse(default)
        parts.append(name)

    if args.kwarg is not None:
        parts.append("**" + args.kwarg.arg)

    return ", ".join(parts)


def _decorator_marker(node) -> str:
    short_names = {
        "property", "staticmethod", "classmethod", "abstractmethod",
        "cached_property", "setter", "deleter",
    }
    markers: List[str] = []
    for decorator in node.decorator_list or []:
        try:
            text = ast.unparse(decorator)
        except Exception:
            continue
        short = text.split("(", 1)[0].strip()
        if short in short_names:
            markers.append("@" + short)
    return (" ".join(markers) + " ") if markers else ""


def _function_lines(node, indent: str = "", include_lineno: bool = True) -> List[str]:
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = ""
    if node.returns is not None:
        returns = " -> " + _safe_unparse(node.returns)
    line = f"{indent}{_decorator_marker(node)}{keyword} {node.name}({_format_args(node.args)}){returns}: ..."
    if include_lineno:
        line += f"  # L{node.lineno}"
    return [line]


def _class_lines(node: ast.ClassDef, include_lineno: bool = True) -> List[str]:
    bases = ", ".join(_safe_unparse(base) for base in (node.bases or []))
    header = f"class {node.name}" + (f"({bases})" if bases else "") + ":"
    if include_lineno:
        header += f"  # L{node.lineno}"

    lines = [header]
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.extend(_function_lines(child, indent="    ", include_lineno=include_lineno))
        elif isinstance(child, ast.ClassDef):
            extra = f"  # L{child.lineno}" if include_lineno else ""
            lines.append(f"    class {child.name}: ...{extra}")
    if len(lines) == 1:
        lines.append("    ...")
    return lines


def outline_from_source(source: str, filename: str = "<ast>", include_lineno: bool = True) -> str:
    """Kaynak metinden yalnizca ust seviye sinif/fonksiyon iskeletini cikar."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return f"# AST sozdizimi hatasi: {exc.msg} (satir {exc.lineno})"

    lines: List[str] = []
    doc = ast.get_docstring(tree)
    if doc:
        first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        if first:
            lines.append(f'"""{first[:140]}"""')

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            lines.extend(_class_lines(node, include_lineno=include_lineno))
            lines.append("")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.extend(_function_lines(node, include_lineno=include_lineno))

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def get_code_outline(
    file_path,
    max_lines: Optional[int] = None,
    include_lineno: Optional[bool] = None,
) -> str:
    """Bir .py dosyasinin AST iskeletini (imzalar) dondur. Sonuc onbelleklenir."""
    path = Path(file_path)
    if include_lineno is None:
        include_lineno = bool(_cfg("outline_include_lineno", True))

    try:
        stat = path.stat()
    except OSError as exc:
        return f"# okunamadi: {exc}"

    cache_key = (str(path.resolve()), include_lineno)
    with _CACHE_LOCK:
        cached = _OUTLINE_CACHE.get(cache_key)
        if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            cached_text = cached[2]
            return _clip_outline(cached_text, max_lines)

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"# okunamadi: {exc}"

    text = outline_from_source(source, filename=str(path), include_lineno=include_lineno)
    with _CACHE_LOCK:
        _OUTLINE_CACHE[cache_key] = (stat.st_mtime, stat.st_size, text)
    return _clip_outline(text, max_lines)


def _clip_outline(text: str, max_lines: Optional[int]) -> str:
    if not max_lines or max_lines <= 0:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... (+{len(lines) - max_lines} satir kesildi)"


def clear_cache() -> None:
    """AST iskeleti onbellegini temizle (testler ve dosya izleyicileri icin)."""
    with _CACHE_LOCK:
        _OUTLINE_CACHE.clear()


# ----------------------------------------------------------------------
# Dosya kesfi
# ----------------------------------------------------------------------
def _is_relevant_rel(rel_path: str) -> bool:
    rel = str(rel_path or "").replace("\\", "/").strip()
    if not rel:
        return False
    parts = rel.split("/")
    for part in parts:
        if part in _DEFAULT_IGNORE_DIRS:
            return False
        if part.startswith(".") and part not in (".", ".."):
            return False
    return True


def iter_python_files(root, max_files: int = 2000) -> List[Path]:
    """Proje agacindaki .py dosyalarini (gurultu dizinleri haric) listele."""
    root_path = Path(root)
    if not root_path.exists():
        return []
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in _DEFAULT_IGNORE_DIRS and not name.startswith(".")
        )
        for name in sorted(filenames):
            if not name.endswith(".py") or name.startswith("."):
                continue
            found.append(Path(dirpath) / name)
            if len(found) >= max_files:
                return found
    return found


# ----------------------------------------------------------------------
# Git entegrasyonu
# ----------------------------------------------------------------------
def _run_git(args: Sequence[str], cwd, timeout: Optional[int] = None) -> Optional[str]:
    if not _cfg("use_git", True):
        return None
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(timeout or _cfg("git_timeout", 8)),
            creationflags=flags,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_git_repo(root) -> bool:
    out = _run_git(["rev-parse", "--is-inside-work-tree"], root)
    return bool(out and out.strip().lower() == "true")


def get_changed_files(root) -> List[str]:
    """Git'e gore degisen/eklenen .py dosyalarini proje koku goreli dondur."""
    root_path = Path(root)
    if not is_git_repo(root_path):
        return []

    found: List[str] = []
    seen = set()
    commands = (
        ["diff", "--relative", "--name-only", "HEAD"],
        ["diff", "--relative", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard", "*.py"],
    )
    for args in commands:
        output = _run_git(args, root_path)
        if not output:
            continue
        for line in output.splitlines():
            rel = line.strip().replace("\\", "/")
            if not rel.endswith(".py") or rel in seen:
                continue
            if not _is_relevant_rel(rel):
                continue
            seen.add(rel)
            found.append(rel)
    return found


def get_file_diff(root, rel_path: str, max_chars: Optional[int] = None) -> str:
    """Tek dosya icin HEAD'e gore unified diff dondur; diff yoksa bos string."""
    rel = str(rel_path or "").replace("\\", "/")
    if not rel:
        return ""
    for args in (
        ["diff", "--relative", "--no-ext-diff", "HEAD", "--", rel],
        ["diff", "--relative", "--no-ext-diff", "--", rel],
    ):
        output = _run_git(args, root)
        if output and output.strip():
            limit = int(max_chars or _cfg("max_diff_chars", 6000))
            return truncate_text(output, limit, "diff")
    return ""


def _main_files(root, limit: int = 3) -> List[str]:
    """Git degisikligi yoksa baglama alinacak kucuk bir baslangic dosya seti."""
    root_path = Path(root)
    scored: List[Tuple[int, float, str]] = []
    for path in iter_python_files(root_path, max_files=800):
        try:
            rel = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if not _is_relevant_rel(rel):
            continue
        priority = 0 if path.name.lower() in _PRIORITY_FILE_NAMES else 1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        scored.append((priority, -mtime, rel))
    scored.sort()
    return [rel for _, _, rel in scored[:limit]]


# ----------------------------------------------------------------------
# Baglam pencereleri
# ----------------------------------------------------------------------
def _resolve_root(root):
    if root is not None:
        path = Path(root)
        return path if path.exists() else None
    try:
        from agent_app.project_scanner import get_project_root

        current = get_project_root()
        if current:
            return Path(current)
    except Exception:
        pass
    return None


def _mentioned_files(root, text: str) -> List[str]:
    root_path = Path(root)
    found: List[str] = []
    seen = set()
    for match in _PY_PATH_RE.findall(text or ""):
        rel = match.strip().replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel in seen or not _is_relevant_rel(rel):
            continue
        if not (root_path / rel).is_file():
            continue
        seen.add(rel)
        found.append(rel)
    return found


def _file_window(path: Path, rel: str, budget: int) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"# okunamadi: {exc}"
    line_count = source.count("\n") + 1
    threshold = int(_cfg("active_file_threshold_lines", 200))
    if path.suffix.lower() == ".py" and line_count > threshold:
        outline = get_code_outline(path)
        return f"[AST iskeleti - {line_count} satir]\n{outline}"
    limit = min(int(_cfg("max_file_chars", 4000)), max(0, budget))
    return truncate_text(source, limit, "dosya")


def build_repo_map(root, max_chars: Optional[int] = None, max_files: Optional[int] = None) -> str:
    """Tum .py dosyalarinin AST iskelet haritasini butceli olarak uret."""
    if not _cfg("enabled", True):
        return ""
    root_path = Path(root)
    if not root_path.exists():
        return ""

    budget = int(max_chars or _cfg("repo_map_max_chars", 5000))
    file_limit = int(max_files or _cfg("repo_map_max_files", 400))

    ordered: List[str] = []
    seen = set()
    for rel in get_changed_files(root_path):
        if rel not in seen and (root_path / rel).is_file():
            seen.add(rel)
            ordered.append(rel)
    for path in iter_python_files(root_path, max_files=file_limit * 2):
        try:
            rel = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if rel in seen or not _is_relevant_rel(rel):
            continue
        seen.add(rel)
        ordered.append(rel)

    ordered = ordered[:file_limit]
    chunks: List[str] = []
    used = 0
    included = 0
    for rel in ordered:
        path = root_path / rel
        try:
            if path.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        outline = get_code_outline(path)
        if not outline.strip():
            continue
        block = f"### {rel}\n{outline}"
        if used + len(block) + 2 > budget:
            break
        chunks.append(block)
        used += len(block) + 2
        included += 1

    if not chunks:
        return ""
    hidden = len(ordered) - included
    if hidden > 0:
        chunks.append(f"... (+{hidden} .py dosyasi butce nedeniyle gizlendi)")
    return "\n\n".join(chunks)


def build_active_context(
    root,
    task_text: str = "",
    max_chars: Optional[int] = None,
    max_files: Optional[int] = None,
) -> str:
    """Degisen ve gorevde adi gecen dosyalari yalnizca gerektigi kadar bagla."""
    if not _cfg("enabled", True):
        return ""
    root_path = _resolve_root(root)
    if root_path is None:
        return ""

    budget = int(max_chars or _cfg("active_context_max_chars", 5000))
    file_limit = int(max_files or _cfg("max_active_files", 8))

    targets: List[str] = []
    seen = set()
    for rel in get_changed_files(root_path):
        if rel not in seen:
            seen.add(rel)
            targets.append(rel)
    for rel in _mentioned_files(root_path, task_text):
        if rel not in seen:
            seen.add(rel)
            targets.append(rel)
    if not targets:
        for rel in _main_files(root_path, limit=3):
            if rel not in seen:
                seen.add(rel)
                targets.append(rel)
    targets = targets[:file_limit]
    if not targets:
        return ""

    chunks: List[str] = ["=== ACTIVE FILES (degisen / gorevle ilgili) ==="]
    remaining = budget
    include_diffs = bool(_cfg("include_diffs", True))

    for rel in targets:
        path = root_path / rel
        if not path.is_file():
            continue
        diff = ""
        if include_diffs:
            diff = get_file_diff(root_path, rel, max_chars=min(remaining, int(_cfg("max_diff_chars", 6000))))
        if diff:
            block = f"--- DIFF: {rel} ---\n{diff}"
        else:
            block = f"--- FILE: {rel} ---\n{_file_window(path, rel, remaining)}"
        if len(block) > remaining:
            block = truncate_text(block, remaining, "aktif dosya")
        chunks.append(block)
        remaining -= len(block)
        if remaining <= 200:
            break

    if len(chunks) == 1:
        return ""
    return "\n\n".join(chunks)


def build_smart_context(
    root,
    task_text: str = "",
    max_chars: Optional[int] = None,
) -> str:
    """Iskelet haritasi + aktif dosya penceresini birlestir."""
    if not _cfg("enabled", True):
        return ""
    root_path = _resolve_root(root)
    if root_path is None:
        return ""

    budget = int(max_chars or _cfg("smart_context_max_chars", 10000))
    skeleton_budget = int(budget * 0.55)
    active_budget = max(500, budget - skeleton_budget)

    sections: List[str] = []
    skeleton = build_repo_map(root_path, max_chars=skeleton_budget)
    if skeleton:
        sections.append("=== PYTHON SKELETON (AST) ===")
        sections.append(skeleton)
    active = build_active_context(root_path, task_text=task_text, max_chars=active_budget)
    if active:
        sections.append("")
        sections.append(active)
    return "\n".join(sections)


def get_smart_project_context(
    root=None,
    task_text: str = "",
    max_chars: Optional[int] = None,
) -> str:
    """LLM sistem istemine eklenmeye hazir, butceli proje baglami."""
    return build_smart_context(root, task_text=task_text, max_chars=max_chars)
