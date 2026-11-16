"""Binary Control Core: ``ToolRegistry`` icin ince cephe (facade).

``ToolRegistry``'nin tum guvenlik sinirlarini (yol izolasyonu, onay
zorunlulugu, komut beyaz listesi) aynen korur; yalnizca daha sade bir
API sunar. Onaysiz yazma/komut calistirma hicbir yan etki olusturmaz
cunku asil is ToolRegistry uzerinden onay kaydi olusturur.
"""
from . import config as neural_config
from .tools import ToolRegistry


class BinaryControlCore:
    def __init__(self, user, registry=None):
        self.user = user
        self.registry = registry if registry is not None else ToolRegistry(user)
        self._registry = self.registry

    # ------------------------------------------------------------------
    # Delegasyon
    # ------------------------------------------------------------------
    def list_tools(self):
        return self.registry.list_tools()

    def list_files(self, path="."):
        return self.registry.list_files(path)

    def read_file(self, path):
        return self.registry.read_file(path)

    def request_write(self, path, content):
        return self.registry.request_write(path, content)

    def request_command(self, command, cwd="."):
        return self.registry.request_command(command, cwd)

    def decide(self, approval_id, approve, user=None, *args, **kwargs):
        target_user = self.user if user is None else user
        if args:
            target_user = args[0]
        if "user" in kwargs:
            target_user = kwargs.pop("user")
        aid = getattr(approval_id, "id", approval_id)
        return self.registry.decide(aid, approve, target_user, *args, **kwargs)

    def permission_summary(self):
        config = getattr(self.registry, "config", None)
        if config is None:
            config = neural_config.get_config()
        require = bool(config.get("TOOL_REQUIRE_APPROVAL", True))
        allowlist = list(config.get("TOOL_COMMAND_ALLOWLIST") or [])
        roots = list(config.get("TOOL_ROOTS") or [])
        return {
            "require_approval": require,
            "requires_approval": require,
            "approval_required": require,
            "approval_required_for_writes": require,
            "approval_required_for_commands": require,
            "onay_zorunlu": require,
            "allowlist": allowlist,
            "command_allowlist": allowlist,
            "allowed_commands": allowlist,
            "tool_command_allowlist": allowlist,
            "roots": roots,
            "tool_roots": roots,
            "max_read_bytes": int(config.get("TOOL_MAX_READ_BYTES", 200000)),
            "max_write_bytes": int(config.get("TOOL_MAX_WRITE_BYTES", 100000)),
            "command_timeout": int(config.get("TOOL_COMMAND_TIMEOUT", 30)),
            "tools": self.list_tools(),
        }

    # Genel delegasyon (ozel/alt cagrilari da destekler).
    def __getattr__(self, name):
        if name in {"registry", "_registry", "user"}:
            raise AttributeError(name)
        registry = self.__dict__.get("registry") or self.__dict__.get("_registry")
        if registry is None:
            raise AttributeError(name)
        return getattr(registry, name)