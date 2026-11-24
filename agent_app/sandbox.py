"""Sandbox helpers: run model/user-generated code inside an isolated work directory.

Generated scripts are written to ``task_runs/sandbox/<run_id>/generated_task.py`` and
executed with that directory as the working directory. A small guard prologue is prepended
to the script so filesystem operations (open/remove/rename/mkdir/os.chdir) only work under
the run's own sandbox directory — the project root and sibling runs stay unreachable.
"""

import os
from pathlib import Path

# Project root, independent of Django settings so this module can be used by
# standalone scripts (agent_runner.py) that do not call django.setup().
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sandbox_root():
    """Return the root directory that holds per-run sandboxes."""
    return _PROJECT_ROOT / "task_runs" / "sandbox"


def run_sandbox_dir(run_id):
    """Return the isolated working directory for a single run."""
    return sandbox_root() / str(run_id)


# Guard prologue prepended to every generated script. Placeholder _SANDBOX_YOLU_
# is replaced with the absolute path of the run's sandbox directory at write time.
RUN_GUARD_SOURCE = r'''
# ==== EMOG SANDBOX GUARD ====
import builtins as _builtins, io as _io, os as _os
_SANDBOX = _SANDBOX_YOLU_
def _emog_sandbox_abs(path):
    try:
        return _os.path.abspath(_os.path.normpath(_os.fspath(path)))
    except Exception:
        return None
def _emog_inside(path):
    p = _emog_sandbox_abs(path)
    if not p:
        return False
    try:
        return _os.path.commonpath([p, _SANDBOX]) == _SANDBOX
    except ValueError:
        return False
def _emog_check(path):
    # An already-open file descriptor cannot be redirected; allow it (no path to check).
    if isinstance(path, int):
        return
    if not _emog_inside(path):
        raise PermissionError("EMOG sandbox: erisim reddedildi - kod yalnizca kendi run dizinine ulasabilir")
_EMOG_ORIG_OPEN = _builtins.open
def _emog_open(file, mode='r', *args, **kwargs):
    _emog_check(file)
    return _EMOG_ORIG_OPEN(file, mode, *args, **kwargs)
_builtins.open = _emog_open
_io.open = _emog_open
_EMOG_ORIG_OS_OPEN = _os.open
def _emog_os_open(path, flags, *args, **kwargs):
    _emog_check(path)
    return _EMOG_ORIG_OS_OPEN(path, flags, *args, **kwargs)
_os.open = _emog_os_open
_EMOG_ORIG_CHDIR = _os.chdir
def _emog_chdir(path):
    _emog_check(path)
    _EMOG_ORIG_CHDIR(path)
_os.chdir = _emog_chdir
def _emog_wrap_os(name):
    _orig = getattr(_os, name)
    def _wrapped(path, *a, **k):
        _emog_check(path)
        return _orig(path, *a, **k)
    return _wrapped
for _emog_fname in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "rename", "replace"):
    if hasattr(_os, _emog_fname):
        setattr(_os, _emog_fname, _emog_wrap_os(_emog_fname))
# Subprocess / shell kaçışlarını kapat (model "echo x > ../manage.py" gibi yapamaz)
import subprocess as _subprocess  # noqa: F401
_EMOG_ORIG_SUBPROCESS_POPEN = _subprocess.Popen
_EMOG_ORIG_SUBPROCESS_RUN = _subprocess.run
_EMOG_ORIG_SUBPROCESS_CALL = getattr(_subprocess, "call", None)
_EMOG_ORIG_OS_SYSTEM = _os.system
_EMOG_ORIG_OS_POPEN = _os.popen
def _emog_block_subprocess(*args, **kwargs):
    raise RuntimeError("EMOG sandbox: subprocess/shell calistirma engellendi")
def _emog_wrap_system_or_popen():
    _orig = _EMOG_ORIG_OS_SYSTEM
    def _wrapped(*a, **k):
        raise RuntimeError("EMOG sandbox: shell/os.system engellendi")
    return _wrapped
# subprocess — tüm varyantları engelle
_subprocess.Popen = _emog_block_subprocess
_subprocess.run = _emog_block_subprocess
if _EMOG_ORIG_SUBPROCESS_CALL is not None:
    _subprocess.call = _emog_block_subprocess
_os.system = _emog_block_subprocess
_os.popen = _emog_block_subprocess
# Genel kaçış: __import__ ile "subprocess" / "os" dinamik yüklemelerini denetle
_EMOG_ORIG_IMPORT = _builtins.__import__
def _emog_import(name, *args, **kwargs):
    if isinstance(name, str) and name.strip() in ("subprocess", "concurrent.futures"):
        raise ImportError("EMOG sandbox: '" + name.strip() + "' import'u engellendi")
    return _EMOG_ORIG_IMPORT(name, *args, **kwargs)
_builtins.__import__ = _emog_import
# (opsiyonel, read-only erişime izin): os.listdir/stat/scandir gibi
# meta veriyi sızdırma riski düşük olduğu için kapatılmadı. Dilersen açılabilir.
# ==== END GUARD ====
'''


def write_generated_script(code, run_id):
    """Write ``generated_task.py`` (guard prologue + user/model code) into the run's sandbox dir.

    Returns the absolute path of the written script.
    """
    directory = run_sandbox_dir(run_id)
    os.makedirs(directory, exist_ok=True)
    guard = RUN_GUARD_SOURCE.replace("_SANDBOX_YOLU_", repr(os.path.abspath(directory)))
    script_path = os.path.join(directory, "generated_task.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(guard + "\n\n")
        f.write(code)
    return script_path