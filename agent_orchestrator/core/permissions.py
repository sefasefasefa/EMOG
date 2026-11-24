"""
Permission System for Local Machine Access
"""
import json
import os
import secrets
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


def _jwt_secret():
    """
    JWT imzalama anahtari - YALNIZCA ortam degiskeninden.

    Guvenlik: API anahtarlari ve sirlar koda gomulmez.
    EMOG_JWT_SECRET tanimli degilse hata firlatilir; bilinen bir
    varsayilan deger ile imzalama/token forging YAPILAMAZ.
    """
    import os
    import sys
    from pathlib import Path

    secret = os.environ.get("EMOG_JWT_SECRET") or os.environ.get("SECRET_KEY")

    if not secret or len(secret) < 32:
        raise RuntimeError(
            "EMOG_JWT_SECRET tanimli degil veya cok kisa (min 32 karakter).\n"
            "API anahtarlari koda gomulmez - .env dosyaniza ekleyin:\n"
            "  EMOG_JWT_SECRET=<50+ karakter rastgele>\n"
            "Uretmek icin: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )

    lowered = secret.lower()
    if any(p in lowered for p in ("change-in-production", "change-me", "dev-secret", "example")):
        raise RuntimeError(
            "EMOG_JWT_SECRET zayif/ornek bir deger. Gercek bir sirlar."
        )

    return secret



class PermissionLevel(Enum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    FULL = "full"


class PermissionCategory(Enum):
    FILE_SYSTEM = "file_system"
    MOUSE_CONTROL = "mouse_control"
    KEYBOARD_CONTROL = "keyboard_control"
    SCREEN_CAPTURE = "screen_capture"
    BROWSER_AUTOMATION = "browser_automation"
    SHELL_COMMANDS = "shell_commands"
    NETWORK_ACCESS = "network_access"
    SYSTEM_INFO = "system_info"
    PROCESS_CONTROL = "process_control"
    CLIPBOARD = "clipboard"


@dataclass
class Permission:
    category: PermissionCategory
    level: PermissionLevel = PermissionLevel.NONE
    paths: List[str] = field(default_factory=list)  # Specific paths for file system
    conditions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionProfile:
    name: str
    description: str
    permissions: Dict[PermissionCategory, Permission] = field(default_factory=dict)
    is_default: bool = False


class PermissionManager:
    DEFAULT_PROFILES = {
        "research_only": PermissionProfile(
            name="Research Only",
            description="Web search, academic papers, API calls - no local machine access",
            permissions={
                PermissionCategory.NETWORK_ACCESS: Permission(PermissionCategory.NETWORK_ACCESS, PermissionLevel.FULL),
                PermissionCategory.FILE_SYSTEM: Permission(PermissionCategory.FILE_SYSTEM, PermissionLevel.READ, paths=["./data", "./downloads"]),
            },
            is_default=True,
        ),
        "automation_assistant": PermissionProfile(
            name="Automation Assistant",
            description="Browser automation, file operations, screen capture - no shell commands",
            permissions={
                PermissionCategory.NETWORK_ACCESS: Permission(PermissionCategory.NETWORK_ACCESS, PermissionLevel.FULL),
                PermissionCategory.FILE_SYSTEM: Permission(PermissionCategory.FILE_SYSTEM, PermissionLevel.WRITE, paths=["./data", "./downloads", "./workspace"]),
                PermissionCategory.BROWSER_AUTOMATION: Permission(PermissionCategory.BROWSER_AUTOMATION, PermissionLevel.FULL),
                PermissionCategory.SCREEN_CAPTURE: Permission(PermissionCategory.SCREEN_CAPTURE, PermissionLevel.FULL),
                PermissionCategory.MOUSE_CONTROL: Permission(PermissionCategory.MOUSE_CONTROL, PermissionLevel.FULL),
                PermissionCategory.KEYBOARD_CONTROL: Permission(PermissionCategory.KEYBOARD_CONTROL, PermissionLevel.FULL),
                PermissionCategory.CLIPBOARD: Permission(PermissionCategory.CLIPBOARD, PermissionLevel.FULL),
            },
        ),
        "full_assistant": PermissionProfile(
            name="Full Assistant",
            description="Complete computer control including shell commands",
            permissions={
                PermissionCategory.FILE_SYSTEM: Permission(PermissionCategory.FILE_SYSTEM, PermissionLevel.FULL),
                PermissionCategory.MOUSE_CONTROL: Permission(PermissionCategory.MOUSE_CONTROL, PermissionLevel.FULL),
                PermissionCategory.KEYBOARD_CONTROL: Permission(PermissionCategory.KEYBOARD_CONTROL, PermissionLevel.FULL),
                PermissionCategory.SCREEN_CAPTURE: Permission(PermissionCategory.SCREEN_CAPTURE, PermissionLevel.FULL),
                PermissionCategory.BROWSER_AUTOMATION: Permission(PermissionCategory.BROWSER_AUTOMATION, PermissionLevel.FULL),
                PermissionCategory.SHELL_COMMANDS: Permission(PermissionCategory.SHELL_COMMANDS, PermissionLevel.FULL),
                PermissionCategory.NETWORK_ACCESS: Permission(PermissionCategory.NETWORK_ACCESS, PermissionLevel.FULL),
                PermissionCategory.SYSTEM_INFO: Permission(PermissionCategory.SYSTEM_INFO, PermissionLevel.FULL),
                PermissionCategory.PROCESS_CONTROL: Permission(PermissionCategory.PROCESS_CONTROL, PermissionLevel.FULL),
                PermissionCategory.CLIPBOARD: Permission(PermissionCategory.CLIPBOARD, PermissionLevel.FULL),
            },
        ),
    }

    def __init__(self, config_dir: Optional[str] = None):
        self.config_dir = Path(config_dir or os.path.expanduser("~/.emog/permissions"))
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.permissions_file = self.config_dir / "permissions.json"
        self.grants_file = self.config_dir / "grants.json"
        self._load_grants()

    def _load_grants(self) -> None:
        if self.grants_file.exists():
            with open(self.grants_file) as f:
                self.grants = json.load(f)
        else:
            self.grants = {}

    def _save_grants(self) -> None:
        with open(self.grants_file, "w") as f:
            json.dump(self.grants, f, indent=2)

    def get_profile(self, profile_name: str) -> Dict[str, Any]:
        if profile_name in self.DEFAULT_PROFILES:
            profile = self.DEFAULT_PROFILES[profile_name]
            return {
                "name": profile.name,
                "description": profile.description,
                "permissions": {
                    cat.value: {
                        "level": perm.level.value,
                        "paths": perm.paths,
                        "conditions": perm.conditions,
                    }
                    for cat, perm in profile.permissions.items()
                }
            }
        return {}

    def list_profiles(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "description": profile.description,
                "is_default": profile.is_default,
            }
            for name, profile in self.DEFAULT_PROFILES.items()
        ]

    def check_permission(self, agent_id: str, category: PermissionCategory, level: PermissionLevel, path: str = "") -> bool:
        """Check if agent has specific permission"""
        grant = self.grants.get(agent_id)
        if not grant:
            return False

        perm_data = grant.get(category.value)
        if not perm_data:
            return False

        if perm_data.get("level") == PermissionLevel.NONE.value:
            return False

        required_level = PermissionLevel(level)
        granted_level = PermissionLevel(perm_data.get("level", PermissionLevel.NONE.value))

        if granted_level.value < required_level.value:
            return False

        # Check path restrictions for file system
        if category == PermissionCategory.FILE_SYSTEM and path:
            allowed_paths = perm_data.get("paths", [])
            if allowed_paths:
                path_obj = Path(path).resolve()
                allowed = any(path_obj.is_relative_to(Path(p).resolve()) for p in allowed_paths)
                if not allowed:
                    return False

        return True

    def grant_permissions(self, agent_id: str, profile_name: str, custom_overrides: Optional[Dict] = None) -> bool:
        """Grant permissions to an agent based on a profile"""
        if profile_name not in self.DEFAULT_PROFILES:
            return False

        profile = self.DEFAULT_PROFILES[profile_name]
        grant = {}

        for cat, perm in profile.permissions.items():
            grant[cat.value] = {
                "level": perm.level.value,
                "paths": perm.paths,
                "conditions": perm.conditions,
            }

        if custom_overrides:
            for cat, override in custom_overrides.items():
                if cat in grant:
                    grant[cat].update(override)

        self.grants[agent_id] = grant
        self._save_grants()
        return True

    def revoke_permissions(self, agent_id: str) -> bool:
        if agent_id in self.grants:
            del self.grants[agent_id]
            self._save_grants()
            return True
        return False

    def get_agent_permissions(self, agent_id: str) -> Dict[str, Any]:
        return self.grants.get(agent_id, {})

    def generate_agent_token(self, agent_id: str, profile_name: str, expires_days: int = 30) -> str:
        """Generate a token for agent authentication"""
        import jwt
        import datetime

        payload = {
            "agent_id": agent_id,
            "profile": profile_name,
            "exp": datetime.datetime.utcnow() + datetime.timedelta(days=expires_days),
            "iat": datetime.datetime.utcnow(),
        }
        secret = _jwt_secret()
        return jwt.encode(payload, secret, algorithm="HS256")

    def verify_agent_token(self, token: str) -> Optional[Dict]:
        import jwt
        try:
            secret = _jwt_secret()
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            return payload
        except jwt.InvalidTokenError:
            return None


# Global instance
_permission_manager = None


def get_permission_manager(config_dir: Optional[str] = None) -> PermissionManager:
    global _permission_manager
    if _permission_manager is None:
        _permission_manager = PermissionManager(config_dir)
    return _permission_manager