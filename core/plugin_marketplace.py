"""
13. Extensible Skill/Plugin Marketplace
Dynamically acquires new functional capabilities/modules from external ecosystem.
"""
import asyncio
import hashlib
import time
import json
import importlib.util
import sys
from typing import Dict, List, Any, Optional, Callable, Type
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from abc import ABC, abstractmethod
import subprocess
import tempfile
import inspect


class PluginStatus(Enum):
    AVAILABLE = "available"
    INSTALLED = "installed"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


class PluginCategory(Enum):
    CODE_GENERATION = "code_generation"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    ANALYSIS = "analysis"
    INTEGRATION = "integration"
    UI = "ui"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DATA = "data"
    UTILITY = "utility"


@dataclass
class PluginMetadata:
    name: str
    version: str
    description: str
    author: str
    category: PluginCategory
    entry_point: str  # module:function
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)  # filesystem, network, exec, etc.
    config_schema: Dict = field(default_factory=dict)
    homepage: str = ""
    repository: str = ""
    license: str = "MIT"
    tags: List[str] = field(default_factory=list)
    min_core_version: str = "1.0.0"
    max_core_version: str = "99.9.9"


@dataclass
class Plugin:
    metadata: PluginMetadata
    status: PluginStatus = PluginStatus.AVAILABLE
    instance: Any = None
    installed_at: Optional[float] = None
    config: Dict = field(default_factory=dict)
    error: Optional[str] = None


class PluginInterface(ABC):
    """Base interface for all plugins."""
    
    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        pass
    
    @abstractmethod
    async def initialize(self, config: Dict) -> bool:
        """Initialize plugin with config."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        pass
    
    @abstractmethod
    async def execute(self, action: str, params: Dict) -> Any:
        """Execute plugin action."""
        pass
    
    def get_schema(self) -> Dict:
        """Return JSON schema for plugin config."""
        return self.metadata.config_schema


class PluginRegistry:
    """Manages plugin discovery, installation, and lifecycle."""
    
    def __init__(self, plugins_dir: str = "./plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(exist_ok=True)
        
        self.plugins: Dict[str, Plugin] = {}
        self._hook_registry: Dict[str, List[Callable]] = {}
        self._command_registry: Dict[str, Callable] = {}
    
    def register_hook(self, hook_name: str, callback: Callable) -> None:
        """Register a hook callback."""
        if hook_name not in self._hook_registry:
            self._hook_registry[hook_name] = []
        self._hook_registry[hook_name].append(callback)
    
    async def trigger_hook(self, hook_name: str, *args, **kwargs) -> List[Any]:
        """Trigger all callbacks for a hook."""
        results = []
        for callback in self._hook_registry.get(hook_name, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    result = await callback(*args, **kwargs)
                else:
                    result = callback(*args, **kwargs)
                results.append(result)
            except Exception as e:
                results.append({"error": str(e)})
        return results
    
    def register_command(self, command: str, handler: Callable) -> None:
        """Register a command handler."""
        self._command_registry[command] = handler
    
    async def execute_command(self, command: str, params: Dict) -> Any:
        """Execute a registered command."""
        handler = self._command_registry.get(command)
        if not handler:
            raise ValueError(f"Command not found: {command}")
        
        if asyncio.iscoroutinefunction(handler):
            return await handler(params)
        return handler(params)
    
    async def discover_plugins(self, source: str = "local") -> List[PluginMetadata]:
        """Discover available plugins."""
        plugins = []
        
        if source == "local":
            # Scan local plugins directory
            for plugin_dir in self.plugins_dir.iterdir():
                if plugin_dir.is_dir() and (plugin_dir / "plugin.json").exists():
                    try:
                        with open(plugin_dir / "plugin.json", 'r') as f:
                            meta = PluginMetadata(**json.load(f))
                            plugins.append(meta)
                    except Exception:
                        pass
        
        elif source == "marketplace":
            # Would fetch from remote marketplace API
            pass
        
        return plugins
    
    async def install_plugin(self, name: str, source: str = "local", config: Dict = None) -> bool:
        """Install a plugin."""
        if name in self.plugins:
            return False
        
        plugin_dir = self.plugins_dir / name
        
        if source == "local" and plugin_dir.exists():
            # Load from local directory
            return await self._load_plugin(plugin_dir, config)
        elif source == "github":
            # Clone from GitHub
            return await self._install_from_github(name, config)
        elif source == "pypi":
            # Install from PyPI
            return await self._install_from_pypi(name, config)
        
        return False
    
    async def _load_plugin(self, plugin_dir: Path, config: Dict = None) -> bool:
        """Load plugin from directory."""
        try:
            # Read metadata
            with open(plugin_dir / "plugin.json", 'r') as f:
                meta_data = json.load(f)
            metadata = PluginMetadata(**meta_data)
            
            # Load module
            spec = importlib.util.spec_from_file_location(
                f"plugin_{metadata.name}",
                plugin_dir / metadata.entry_point.split(':')[0] + ".py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            
            # Get plugin class
            class_name = metadata.entry_point.split(':')[1] if ':' in metadata.entry_point else "Plugin"
            plugin_class = getattr(module, class_name)
            
            # Instantiate
            instance = plugin_class()
            await instance.initialize(config or {})
            
            plugin = Plugin(
                metadata=metadata,
                status=PluginStatus.INSTALLED,
                instance=instance,
                installed_at=time.time(),
                config=config or {}
            )
            
            self.plugins[metadata.name] = plugin
            
            # Register hooks and commands
            await self._register_plugin_hooks(plugin)
            
            return True
            
        except Exception as e:
            self.plugins[metadata.name] = Plugin(
                metadata=metadata,
                status=PluginStatus.ERROR,
                error=str(e)
            )
            return False
    
    async def _register_plugin_hooks(self, plugin: Plugin) -> None:
        """Register plugin hooks and commands."""
        instance = plugin.instance
        
        # Register hooks
        if hasattr(instance, 'hooks'):
            for hook_name, callback in instance.hooks.items():
                self.register_hook(hook_name, callback)
        
        # Register commands
        if hasattr(instance, 'commands'):
            for cmd_name, handler in instance.commands.items():
                self.register_command(cmd_name, handler)
    
    async def _install_from_github(self, repo: str, config: Dict = None) -> bool:
        """Install plugin from GitHub."""
        try:
            repo_url = f"https://github.com/{repo}.git"
            with tempfile.TemporaryDirectory() as tmpdir:
                # Clone repo
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, tmpdir],
                    capture_output=True, timeout=60
                )
                if result.returncode != 0:
                    return False
                
                plugin_dir = Path(tmpdir)
                return await self._load_plugin(plugin_dir, config)
        except Exception:
            return False
    
    async def _install_from_pypi(self, package: str, config: Dict = None) -> bool:
        """Install plugin from PyPI."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                capture_output=True, timeout=120
            )
            if result.returncode != 0:
                return False
            
            # Try to load
            package_name = package.split('==')[0].split('>=')[0].split('<=')[0]
            return await self._load_plugin(Path(package_name), config)
        except Exception:
            return False
    
    async def uninstall_plugin(self, name: str) -> bool:
        """Uninstall a plugin."""
        if name not in self.plugins:
            return False
        
        plugin = self.plugins[name]
        
        if plugin.status == PluginStatus.ACTIVE:
            await plugin.instance.shutdown()
        
        del self.plugins[name]
        return True
    
    async def activate_plugin(self, name: str) -> bool:
        """Activate an installed plugin."""
        if name not in self.plugins:
            return False
        
        plugin = self.plugins[name]
        if plugin.status == PluginStatus.ERROR:
            return False
        
        if plugin.status != PluginStatus.ACTIVE:
            await plugin.instance.initialize(plugin.config)
            plugin.status = PluginStatus.ACTIVE
        
        return True
    
    async def deactivate_plugin(self, name: str) -> bool:
        """Deactivate a plugin."""
        if name not in self.plugins:
            return False
        
        plugin = self.plugins[name]
        if plugin.status == PluginStatus.ACTIVE:
            await plugin.instance.shutdown()
            plugin.status = PluginStatus.INSTALLED
        
        return True
    
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """Get plugin by name."""
        return self.plugins.get(name)
    
    def list_plugins(self, status: PluginStatus = None) -> List[Plugin]:
        """List all plugins."""
        plugins = list(self.plugins.values())
        if status:
            plugins = [p for p in plugins if p.status == status]
        return plugins
    
    async def execute_plugin_action(self, name: str, action: str, params: Dict) -> Any:
        """Execute action on a plugin."""
        plugin = self.plugins.get(name)
        if not plugin or not plugin.instance:
            raise ValueError(f"Plugin not found or not initialized: {name}")
        
        return await plugin.instance.execute(action, params)


# Example base plugin class
class BasePlugin(PluginInterface):
    """Base class for creating plugins."""
    
    metadata = PluginMetadata(
        name="base",
        version="1.0.0",
        description="Base plugin",
        author="EMOG",
        category=PluginCategory.UTILITY,
        entry_point="base:BasePlugin"
    )
    
    def __init__(self):
        self.config = {}
        self.hooks = {}
        self.commands = {}
    
    async def initialize(self, config: Dict) -> bool:
        self.config = config
        return True
    
    async def shutdown(self) -> None:
        pass
    
    async def execute(self, action: str, params: Dict) -> Any:
        handler = getattr(self, f"action_{action}", None)
        if handler:
            if asyncio.iscoroutinefunction(handler):
                return await handler(params)
            return handler(params)
        raise ValueError(f"Action not found: {action}")


# Example plugins
class CodeGeneratorPlugin(BasePlugin):
    """Plugin for code generation."""
    
    metadata = PluginMetadata(
        name="code_generator",
        version="1.0.0",
        description="Generate code from specifications",
        author="EMOG",
        category=PluginCategory.CODE_GENERATION,
        entry_point="plugin_marketplace:CodeGeneratorPlugin",
        permissions=["filesystem", "exec"],
        config_schema={
            "type": "object",
            "properties": {
                "default_language": {"type": "string", "default": "python"},
                "template_dir": {"type": "string"},
            }
        }
    )
    
    async def action_generate(self, params: Dict) -> Dict:
        spec = params.get("spec", "")
        language = params.get("language", self.config.get("default_language", "python"))
        
        # Would use LLM to generate code
        return {
            "code": f"# Generated {language} code for: {spec}",
            "language": language,
            "files": {}
        }
    
    async def action_generate_from_template(self, params: Dict) -> Dict:
        template = params.get("template", "")
        variables = params.get("variables", {})
        
        # Simple template rendering
        import string
        template_obj = string.Template(template)
        rendered = template_obj.safe_substitute(variables)
        
        return {"rendered": rendered}


class TestGeneratorPlugin(BasePlugin):
    """Plugin for test generation."""
    
    metadata = PluginMetadata(
        name="test_generator",
        version="1.0.0",
        description="Generate tests from code",
        author="EMOG",
        category=PluginCategory.TESTING,
        entry_point="plugin_marketplace:TestGeneratorPlugin",
        permissions=["filesystem"],
    )
    
    async def action_generate_unit_tests(self, params: Dict) -> Dict:
        code = params.get("code", "")
        language = params.get("language", "python")
        
        return {
            "tests": f"# Generated unit tests for {language} code",
            "framework": "pytest" if language == "python" else "jest"
        }
    
    async def action_generate_integration_tests(self, params: Dict) -> Dict:
        return {"tests": "# Generated integration tests"}


class DeploymentPlugin(BasePlugin):
    """Plugin for deployment automation."""
    
    metadata = PluginMetadata(
        name="deployment",
        version="1.0.0",
        description="Deploy applications to various platforms",
        author="EMOG",
        category=PluginCategory.DEPLOYMENT,
        entry_point="plugin_marketplace:DeploymentPlugin",
        permissions=["network", "exec", "filesystem"],
        config_schema={
            "type": "object",
            "properties": {
                "default_target": {"type": "string", "enum": ["docker", "vercel", "netlify", "kubernetes"]},
                "docker_registry": {"type": "string"},
            }
        }
    )
    
    async def action_deploy(self, params: Dict) -> Dict:
        target = params.get("target", self.config.get("default_target", "docker"))
        project_path = params.get("project_path", ".")
        
        return {"status": "deployed", "target": target, "url": f"https://{target}.example.com"}
    
    async def action_build(self, params: Dict) -> Dict:
        return {"status": "built", "image": "myapp:latest"}


class SecurityAuditPlugin(BasePlugin):
    """Plugin for security auditing."""
    
    metadata = PluginMetadata(
        name="security_audit",
        version="1.0.0",
        description="Automated security auditing",
        author="EMOG",
        category=PluginCategory.SECURITY,
        entry_point="plugin_marketplace:SecurityAuditPlugin",
        permissions=["filesystem", "network"],
    )
    
    async def action_scan(self, params: Dict) -> Dict:
        path = params.get("path", ".")
        return {"vulnerabilities": [], "summary": "No vulnerabilities found"}
    
    async def action_fix(self, params: Dict) -> Dict:
        return {"fixed": 0, "details": []}


# Plugin marketplace singleton
_plugin_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    global _plugin_registry
    if _plugin_registry is None:
        _plugin_registry = PluginRegistry()
    return _plugin_registry


async def initialize_plugins() -> PluginRegistry:
    """Initialize plugin system with default plugins."""
    registry = get_plugin_registry()
    
    # Register built-in plugins
    builtin_plugins = [
        CodeGeneratorPlugin,
        TestGeneratorPlugin,
        DeploymentPlugin,
        SecurityAuditPlugin,
    ]
    
    for plugin_class in builtin_plugins:
        plugin = plugin_class()
        metadata = plugin.metadata
        
        plugin_obj = Plugin(
            metadata=metadata,
            status=PluginStatus.INSTALLED,
            instance=plugin,
            installed_at=time.time()
        )
        
        await plugin.initialize({})
        plugin_obj.status = PluginStatus.ACTIVE
        
        registry.plugins[metadata.name] = plugin_obj
        await registry._register_plugin_hooks(plugin_obj)
    
    return registry