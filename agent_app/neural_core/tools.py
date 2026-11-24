"""Bilgisayar kontrolü: izinli dosya ve komut araçları.

Okuma ve listeleme doğrudan yapılabilir. Yazma ve komut çalıştırma her
zaman `NeuralToolApproval` kaydı oluşturur; yalnızca kayıt sahibinin
onayıyla yürütülür.

İzolasyon: her kullanıcı yalnızca kendi alanına erişir.

  projects/<user_guid>/...
  task_runs/neural/<user_guid>/...

Kullanıcıya ait olmayan bir GUID içeren yollar reddedilir; `..` ve kök
dışı mutlak yollar `ValueError` ile engellenir.
"""
import os
import re
import shlex
import subprocess
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import config as neural_config
from .models import NeuralToolApproval

# Satır içi kod çalıştıran bayraklar: komut beyaz listesinde olsa bile
# keyfi kod yürütmeyi engeller.
INLINE_CODE_FLAGS = {
    "python": ("-c", "--command"),
    "python3": ("-c", "--command"),
    "py": ("-c", "--command"),
    "node": ("-e", "--eval", "-p", "--print"),
    "perl": ("-e",),
    "ruby": ("-e",),
    "php": ("-r",),
    "git": ("-c", "--exec-path", "--upload-pack", "--receive-pack"),
}

# Kurulum/çalıştırma sırasında üçüncü taraf kodu çalıştıran alt komutlar.
BLOCKED_SUBCOMMANDS = {
    "pip": {"install", "uninstall", "download", "wheel"},
    "pip3": {"install", "uninstall", "download", "wheel"},
    "npm": {"install", "i", "ci", "exec", "x", "run", "run-script", "start", "test", "publish"},
    "yarn": {"add", "install", "run", "exec", "publish"},
    "pnpm": {"add", "install", "run", "exec", "publish"},
    "git": {"filter-branch", "filter-repo", "submodule", "daemon"},
}

# Kabuk yorumlayıcısına kaçış riski taşıyan karakterler. `shell=False`
# olsa da alt süreç bunları kendi yorumlayabilir.
SHELL_METACHARACTERS = (";", "|", "&", "`", "$(", "\n", "\r")


class ToolRegistry:
    def __init__(self, user):
        self.user = user
        self.config = neural_config.get_config()
        raw_guid = str(getattr(user, "id", "") or "")
        # GUID yalnızca güvenli karakterlerden oluşur; bozuksa hash'le.
        guid = re.sub(r"[^A-Za-z0-9_-]", "", raw_guid)
        if not guid:
            import hashlib

            guid = hashlib.sha256(raw_guid.encode("utf-8")).hexdigest()[:32]
        self.user_guid = guid

    # ------------------------------------------------------------------
    # Yol güvenliği
    # ------------------------------------------------------------------
    def _root_paths(self):
        """Kullanıcıya özel araç kökleri (sıra korunur)."""
        base = Path(settings.BASE_DIR).resolve()
        roots = []
        for root in self.config.get("TOOL_ROOTS") or ["projects", "task_runs"]:
            name = str(root).strip("/\\")
            if name == "projects":
                roots.append((base / "projects" / self.user_guid).resolve())
            elif name == "task_runs":
                roots.append((base / "task_runs" / "neural" / self.user_guid).resolve())
            else:
                roots.append((base / name / self.user_guid).resolve())
        return roots

    def _is_inside_roots(self, path):
        return any(
            path == root or path.is_relative_to(root)
            for root in self._root_paths()
        )

    def _resolve(self, path):
        """Yolu kullanıcının köklerine çöz; sahiplik dışı erişimi reddet."""
        base = Path(settings.BASE_DIR).resolve()
        raw_text = str(path if path not in (None, "") else ".").replace("\\", "/")
        raw = Path(raw_text)

        if raw.is_absolute():
            candidate = raw
        else:
            parts = [part for part in raw.parts if part not in ("", ".")]
            first = parts[0] if parts else ""
            if first == "projects":
                rest = parts[1:]
                if rest and rest[0] != self.user_guid:
                    raise ValueError("başka kullanıcının proje alanına erişim reddedildi")
                rest = rest[1:] if rest else rest
                candidate = (base / "projects" / self.user_guid).joinpath(*rest)
            elif first == "task_runs":
                rest = parts[1:]
                if rest and rest[0] == "neural":
                    rest = rest[1:]
                    if rest and rest[0] != self.user_guid:
                        raise ValueError("başka kullanıcının görev alanına erişim reddedildi")
                    rest = rest[1:] if rest else rest
                candidate = (base / "task_runs" / "neural" / self.user_guid).joinpath(*rest)
            else:
                roots = self._root_paths()
                if not roots:
                    raise ValueError("tanımlı araç kökü yok")
                candidate = roots[0].joinpath(*parts)
        resolved = candidate.resolve()
        if not self._is_inside_roots(resolved):
            raise ValueError("yol izin verilen araç köklerinin dışında")
        return resolved

    def _display(self, resolved):
        try:
            return resolved.relative_to(Path(settings.BASE_DIR).resolve()).as_posix()
        except ValueError:
            return resolved.as_posix()

    # ------------------------------------------------------------------
    # Salt okunur araçlar
    # ------------------------------------------------------------------
    def list_tools(self):
        return [
            {"name": "list_files", "requires_approval": False, "description": "Kendi alanındaki dosyaları listeler"},
            {"name": "read_file", "requires_approval": False, "description": "Kendi alanındaki dosyayı okur"},
            {"name": "write_file", "requires_approval": True, "description": "Onayla kendi alanına dosya yazar"},
            {"name": "run_command", "requires_approval": True, "description": "Onayla beyaz listedeki komutu çalıştırır"},
        ]

    def list_files(self, path="."):
        target = self._resolve(path)
        if not target.exists():
            if target in self._root_paths():
                return {"path": self._display(target), "entries": []}
            raise FileNotFoundError("dizin bulunamadı")
        if not target.is_dir():
            raise ValueError("hedef dizin değil")
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                resolved_item = item.resolve()
            except OSError:
                continue
            # Kök dışına işaret eden sembolik bağlantıları listeleme.
            if not self._is_inside_roots(resolved_item):
                continue
            entries.append({
                "name": item.name,
                "path": self._display(resolved_item),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
            })
            if len(entries) >= 500:
                break
        return {"path": self._display(target), "entries": entries}

    def read_file(self, path):
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError("dosya bulunamadı")
        size = target.stat().st_size
        limit = int(self.config.get("TOOL_MAX_READ_BYTES", 200000))
        if size > limit:
            raise ValueError(f"dosya çok büyük ({size} > {limit} bayt)")
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": self._display(target), "size": size, "content": content}

    # ------------------------------------------------------------------
    # Onay gerektiren araçlar
    # ------------------------------------------------------------------
    def request_write(self, path, content):
        target = self._resolve(path)
        content = "" if content is None else str(content)
        limit = int(self.config.get("TOOL_MAX_WRITE_BYTES", 100000))
        if len(content.encode("utf-8")) > limit:
            raise ValueError(f"içerik çok büyük ({limit} bayt sınırı)")
        return NeuralToolApproval.objects.create(
            user=self.user,
            tool="write_file",
            args={"path": self._display(target), "content": content},
        )

    def request_command(self, command, cwd="."):
        command = (command or "").strip()
        if not command:
            raise ValueError("komut boş olamaz")
        if len(command) > 4000:
            raise ValueError("komut çok uzun (en fazla 4000 karakter)")
        parts = _split_command(command)
        if not parts:
            raise ValueError("komut ayrıştırılamadı")
        self._validate_command(parts)
        target_dir = self._resolve(cwd)
        if target_dir.exists() and not target_dir.is_dir():
            raise ValueError("çalışma dizini bir dosya")
        return NeuralToolApproval.objects.create(
            user=self.user,
            tool="run_command",
            args={"command": command, "cwd": self._display(target_dir)},
        )

    def decide(self, approval_id, approve, user):
        """Onay kaydını atomik olarak sahiplen; onayda aracı çalıştır.

        İki eşzamanlı istek aynı kaydı çalıştıramaz: `pending` → `executing`
        geçişi tek satırlık koşullu UPDATE ile yapılır.
        """
        approval = NeuralToolApproval.objects.filter(id=approval_id, user=user).first()
        if approval is None:
            raise LookupError("onay kaydı bulunamadı")

        now = timezone.now()
        target_status = "executing" if approve else "rejected"
        claimed = NeuralToolApproval.objects.filter(
            id=approval.id, user=user, status="pending"
        ).update(status=target_status, decided_at=now)
        if not claimed:
            approval.refresh_from_db()
            return {"ok": False, "status": approval.status, "output": approval.output}

        approval.refresh_from_db()
        if not approve:
            return {"ok": False, "status": approval.status, "output": "reddedildi"}

        try:
            if approval.tool == "write_file":
                output = self._execute_write(approval.args or {})
            elif approval.tool == "run_command":
                output, returncode = self._execute_command(approval.args or {})
                if returncode != 0:
                    approval.status = "failed"
                    approval.output = output
                    approval.save(update_fields=["status", "output", "decided_at"])
                    return {"ok": False, "status": approval.status, "output": output}
            else:
                raise ValueError(f"bilinmeyen araç: {approval.tool}")
        except Exception as exc:
            approval.status = "failed"
            approval.output = str(exc)[:20000]
            approval.save(update_fields=["status", "output", "decided_at"])
            return {"ok": False, "status": approval.status, "output": approval.output}

        approval.status = "approved"
        approval.output = output[:20000]
        approval.save(update_fields=["status", "output", "decided_at"])
        return {"ok": True, "status": approval.status, "output": approval.output}

    def _execute_write(self, args):
        target = self._resolve(args.get("path") or "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args.get("content") or ""), encoding="utf-8")
        return f"yazıldı: {self._display(target)}"

    def _execute_command(self, args):
        command = str(args.get("command") or "")
        parts = _split_command(command)
        self._validate_command(parts)
        cwd = self._resolve(args.get("cwd") or ".")
        if not cwd.exists():
            cwd.mkdir(parents=True, exist_ok=True)
        timeout = int(self.config.get("TOOL_COMMAND_TIMEOUT", 30))
        completed = subprocess.run(
            parts,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return output[:20000], completed.returncode

    # ------------------------------------------------------------------
    # Komut doğrulama
    # ------------------------------------------------------------------
    def _validate_command(self, parts):
        if not parts:
            raise ValueError("komut ayrıştırılamadı")
        allowed = set(self.config.get("TOOL_COMMAND_ALLOWLIST") or [])
        name = _executable_name(parts[0])
        if name not in {item.lower() for item in allowed} and parts[0] not in allowed:
            raise ValueError(f"komut beyaz listede değil: {parts[0]}")

        flags = INLINE_CODE_FLAGS.get(name, ())
        args = parts[1:]
        for index, arg in enumerate(args):
            flag = arg.split("=", 1)[0]
            if flag in flags:
                raise ValueError("satır içi kod çalıştırma reddedildi")
            if flag == "-m" and name in ("python", "python3", "py"):
                module = args[index + 1] if index + 1 < len(args) else ""
                if module not in ("pytest", "unittest"):
                    raise ValueError("python -m yalnızca pytest/unittest için kullanılabilir")

        blocked = BLOCKED_SUBCOMMANDS.get(name)
        if blocked:
            subcommand = next((arg for arg in args if not arg.startswith("-")), "")
            if subcommand in blocked:
                raise ValueError(f"alt komut güvenli değil: {subcommand}")

        self._validate_command_arguments(args)

    def _validate_command_arguments(self, args):
        roots = self._root_paths()
        for raw in args:
            value = raw
            if value.startswith("-") and "=" in value:
                value = value.split("=", 1)[1]
            if not value:
                continue
            if any(token in value for token in SHELL_METACHARACTERS):
                raise ValueError("komut argümanında kabuk metakarakteri reddedildi")
            normalized = value.replace("\\", "/")
            if ".." in Path(normalized).parts:
                raise ValueError("komut argümanında yol kaçışı reddedildi")
            if normalized.startswith("~"):
                raise ValueError("komut argümanında ev dizini kısaltması reddedildi")
            candidate = Path(normalized)
            if candidate.is_absolute():
                resolved = candidate.resolve()
                if not any(
                    resolved == root or resolved.is_relative_to(root)
                    for root in roots
                ):
                    raise ValueError("komut argümanı izin verilen araç köklerinin dışında")


def _executable_name(value):
    name = Path(value).name
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _split_command(command):
    if os.name == "nt":
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = command.split()
        cleaned = []
        for part in parts:
            if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"'):
                part = part[1:-1]
            cleaned.append(part)
        return cleaned
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()
