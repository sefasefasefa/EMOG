"""
Docker-based Sandbox Execution Service
Provides isolated execution environment with resource limits, network isolation,
and security constraints for untrusted code execution.
"""
import os
import json
import uuid
import tempfile
import shutil
import threading
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    docker = None
    DOCKER_AVAILABLE = False


class SandboxStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    KILLED = "killed"


@dataclass
class ResourceLimits:
    """Resource limits for sandbox container"""
    cpu_quota: int = 50000      # CPU quota (microseconds per 100ms period)
    cpu_period: int = 100000    # CPU period (microseconds)
    memory_limit: str = "256m"  # Memory limit (e.g., "256m", "512m")
    memory_swap: str = "512m"   # Swap limit
    pids_limit: int = 100       # Maximum number of processes
    disk_quota: str = "1g"      # Disk quota (via tmpfs)
    network_mode: str = "none"  # Network isolation ("none", "bridge")
    read_only_rootfs: bool = True
    timeout_seconds: int = 300  # Execution timeout


@dataclass
class SandboxExecution:
    """Sandbox execution request/result"""
    id: str
    image: str
    command: List[str]
    environment: Dict[str, str] = field(default_factory=dict)
    working_dir: str = "/workspace"
    volumes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    stdin_data: Optional[str] = None
    
    # Result fields
    status: SandboxStatus = SandboxStatus.CREATED
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)


class DockerSandbox:
    """
    Docker-based sandbox for secure code execution.
    Provides isolation, resource limits, and security constraints.
    """
    
    # Pre-approved safe images
    ALLOWED_IMAGES = {
        "python": "python:3.11-slim",
        "python3": "python:3.11-slim",
        "node": "node:20-slim",
        "nodejs": "node:20-slim",
        "bash": "bash:5.2",
        "sh": "alpine:3.19",
        "go": "golang:1.22-alpine",
        "rust": "rust:1.78-slim",
        "gcc": "gcc:13",
        "clang": "llvm/clang:17",
        "sqlite": "alpine:3.19",
        "postgres": "postgres:16-alpine",
        "redis": "redis:7-alpine",
    }
    
    # Dangerous commands/patterns to block
    BLOCKED_PATTERNS = [
        r"rm\s+-rf\s+/",
        r"dd\s+if=.*of=/dev/",
        r"mkfs\.",
        r"fdisk",
        r"parted",
        r">\s*/dev/",
        r"chmod\s+777",
        r"chown\s+root",
        r"passwd",
        r"su\s+",
        r"sudo\s+",
        r"systemctl",
        r"service\s+",
        r"mount\s+",
        r"umount\s+",
        r"iptables",
        r"nc\s+-l",
        r"netcat\s+-l",
        r"socat",
        r"cryptsetup",
        r"gpg\s+--gen-key",
    ]
    
    def __init__(self, docker_url: str = None, default_limits: ResourceLimits = None):
        self.docker_url = docker_url or os.environ.get("DOCKER_URL", "unix:///var/run/docker.sock")
        self.default_limits = default_limits or ResourceLimits()
        self._client: Optional[docker.DockerClient] = None
        self._lock = threading.Lock()
        self._active_executions: Dict[str, SandboxExecution] = {}
        self._init_client()
    
    def _init_client(self):
        """Initialize Docker client"""
        if not DOCKER_AVAILABLE:
            print("Warning: Docker SDK not available. Sandbox will use fallback.")
            return
        try:
            self._client = docker.DockerClient(
                base_url=self.docker_url,
                timeout=30,
                max_pool_size=10,
            )
            # Test connection
            self._client.ping()
            # Pre-pull allowed images
            self._ensure_images()
        except Exception as e:
            print(f"Warning: Docker not available: {e}")
            self._client = None
    
    def is_available(self) -> bool:
        """Check if Docker is available"""
        if not self._client:
            return False
        try:
            self._client.ping()
            return True
        except Exception:
            return False
    
    def _ensure_images(self):
        """Pre-pull allowed images"""
        if not self._client:
            return
        for name, image in self.ALLOWED_IMAGES.items():
            try:
                self._client.images.pull(image)
            except Exception:
                pass  # Image might already exist
    
    def validate_image(self, image: str) -> str:
        """Validate and resolve image name"""
        if image in self.ALLOWED_IMAGES:
            return self.ALLOWED_IMAGES[image]
        # Allow full image names for custom images (with validation)
        if "/" in image and not any(blocked in image for blocked in ["rm", "dd", "mkfs"]):
            return image
        raise ValueError(f"Image not allowed: {image}. Allowed: {list(self.ALLOWED_IMAGES.keys())}")
    
    def validate_command(self, command: List[str]) -> bool:
        """Validate command for dangerous patterns"""
        cmd_str = " ".join(command)
        import re
        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, cmd_str, re.IGNORECASE):
                return False
        return True
    
    def prepare_execution(self, 
                         image: str,
                         command: List[str],
                         environment: Dict[str, str] = None,
                         working_dir: str = "/workspace",
                         volumes: Dict[str, Dict[str, Any]] = None,
                         resource_limits: ResourceLimits = None,
                         stdin_data: str = None) -> SandboxExecution:
        """Prepare a sandbox execution"""
        
        # Validate
        resolved_image = self.validate_image(image)
        if not self.validate_command(command):
            raise ValueError(f"Command contains blocked patterns: {' '.join(command)}")
        
        # Create execution object
        exec_id = uuid.uuid4().hex[:12]
        execution = SandboxExecution(
            id=exec_id,
            image=resolved_image,
            command=command,
            environment=environment or {},
            working_dir=working_dir,
            volumes=volumes or {},
            resource_limits=resource_limits or self.default_limits,
            stdin_data=stdin_data,
        )
        
        # Add default environment
        execution.environment.setdefault("HOME", "/tmp")
        execution.environment.setdefault("TMPDIR", "/tmp")
        execution.environment.setdefault("PYTHONUNBUFFERED", "1")
        
        # Add default volumes for workspace
        workspace_dir = tempfile.mkdtemp(prefix=f"sandbox_{exec_id}_")
        execution.volumes.setdefault(workspace_dir, {"bind": "/workspace", "mode": "rw"})
        
        with self._lock:
            self._active_executions[exec_id] = execution
        
        return execution
    
    def run(self, execution: SandboxExecution) -> SandboxExecution:
        """Run sandbox execution"""
        if not self._client:
            execution.status = SandboxStatus.FAILED
            execution.error = "Docker not available"
            execution.completed_at = datetime.utcnow()
            return execution
        
        execution.status = SandboxStatus.RUNNING
        execution.started_at = datetime.utcnow()
        
        container = None
        try:
            # Prepare container config
            container_config = self._build_container_config(execution)
            
            # Create and start container
            container = self._client.containers.create(**container_config)
            container.start()
            
            # Handle stdin if provided
            if execution.stdin_data:
                self._send_stdin(container, execution.stdin_data)
            
            # Wait with timeout
            result = container.wait(timeout=execution.resource_limits.timeout_seconds)
            execution.exit_code = result.get("StatusCode", -1)
            
            # Get logs
            execution.stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            execution.stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            
            # Get stats
            execution.stats = self._get_container_stats(container)
            
            execution.status = SandboxStatus.COMPLETED if execution.exit_code == 0 else SandboxStatus.FAILED
            
        except docker.errors.ContainerError as e:
            execution.status = SandboxStatus.FAILED
            execution.error = str(e)
            execution.exit_code = e.exit_status
            execution.stderr = str(e)
        except docker.errors.ImageNotFound:
            execution.status = SandboxStatus.FAILED
            execution.error = f"Image not found: {execution.image}"
        except docker.errors.APIError as e:
            execution.status = SandboxStatus.FAILED
            execution.error = f"Docker API error: {e}"
        except Exception as e:
            execution.status = SandboxStatus.FAILED
            execution.error = f"Execution error: {e}"
        finally:
            execution.completed_at = datetime.utcnow()
            # Cleanup container
            if container:
                try:
                    container.remove(force=True, v=True)
                except Exception:
                    pass
            # Cleanup workspace
            self._cleanup_workspace(execution)
            with self._lock:
                self._active_executions.pop(execution.id, None)
        
        return execution
    
    def run_async(self, execution: SandboxExecution) -> str:
        """Run execution asynchronously, return execution ID"""
        thread = threading.Thread(target=self.run, args=(execution,), daemon=True)
        thread.start()
        return execution.id
    
    def _build_container_config(self, execution: SandboxExecution) -> Dict[str, Any]:
        """Build Docker container configuration"""
        limits = execution.resource_limits
        
        # Host config with resource limits
        host_config = self._client.api.create_host_config(
            cpu_quota=limits.cpu_quota,
            cpu_period=limits.cpu_period,
            mem_limit=limits.memory_limit,
            memswap_limit=limits.memory_swap,
            pids_limit=limits.pids_limit,
            network_mode=limits.network_mode,
            read_only=limits.read_only_rootfs,
            tmpfs={"/tmp": "size=100m,noexec,nosuid,nodev", "/workspace": "size=500m,exec"},
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"],
            ulimits=[
                docker.types.Ulimit(name="nofile", soft=1024, hard=1024),
                docker.types.Ulimit(name="nproc", soft=100, hard=100),
            ],
        )
        
        # Environment
        env = [
            f"{k}={v}" for k, v in execution.environment.items()
        ]
        
        # Volumes
        volumes = {}
        for host_path, config in execution.volumes.items():
            volumes[host_path] = config
        
        config = {
            "image": execution.image,
            "command": execution.command,
            "environment": env,
            "working_dir": execution.working_dir,
            "host_config": host_config,
            "volumes": volumes,
            "stdin_open": execution.stdin_data is not None,
            "tty": False,
            "detach": True,
            "remove": False,  # We'll remove manually after getting logs
        }
        return config
    
    def _send_stdin(self, container, data: str):
        """Send stdin data to container"""
        try:
            sock = container.attach_socket(params={"stdin": 1, "stream": 1})
            sock._sock.send(data.encode())
            sock.close()
        except Exception:
            pass
    
    def _get_container_stats(self, container) -> Dict[str, Any]:
        """Get container resource usage stats"""
        try:
            stats = container.stats(stream=False, decode=True)
            return {
                "memory_usage": stats.get("memory_stats", {}).get("usage", 0),
                "memory_limit": stats.get("memory_stats", {}).get("limit", 0),
                "cpu_usage": stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0),
                "pids": stats.get("pids_stats", {}).get("current", 0),
            }
        except Exception:
            return {}
    
    def _cleanup_workspace(self, execution: SandboxExecution):
        """Cleanup temporary workspace"""
        for host_path in execution.volumes.keys():
            try:
                if os.path.exists(host_path):
                    shutil.rmtree(host_path, ignore_errors=True)
            except Exception:
                pass
    
    def kill_execution(self, execution_id: str) -> bool:
        """Kill a running execution"""
        with self._lock:
            execution = self._active_executions.get(execution_id)
        if not execution:
            return False
        
        if not self._client:
            return False
        
        try:
            containers = self._client.containers.list(
                filters={"label": f"sandbox_id={execution_id}"}
            )
            for container in containers:
                container.kill()
                container.remove(force=True, v=True)
            execution.status = SandboxStatus.KILLED
            return True
        except Exception:
            return False
        
    def get_execution(self, execution_id: str) -> Optional[SandboxExecution]:
        """Get execution by ID"""
        with self._lock:
            return self._active_executions.get(execution_id)
    
    def list_executions(self) -> List[SandboxExecution]:
        """List all active executions"""
        with self._lock:
            return list(self._active_executions.values())


# Fallback for when Docker is not available
class FallbackSandbox:
    """Fallback sandbox using subprocess with basic isolation"""
    
    # Same allowed images as DockerSandbox for consistency
    ALLOWED_IMAGES = {
        "python": "python3",
        "python3": "python3",
        "node": "node",
        "nodejs": "node",
        "bash": "bash",
        "sh": "sh",
        "go": "go",
        "rust": "rustc",
        "gcc": "gcc",
        "clang": "clang",
        "sqlite": "sqlite3",
    }
    
    def __init__(self):
        pass
    
    def is_available(self) -> bool:
        return True
    
    def validate_image(self, image: str) -> str:
        # Map to local commands
        local_map = {
            "python": "python",
            "python3": "python",
            "node": "node",
            "nodejs": "node",
            "bash": "bash",
            "sh": "sh",
            "go": "go",
            "rust": "rustc",
            "gcc": "gcc",
            "clang": "clang",
            "sqlite": "sqlite3",
        }
        return local_map.get(image, "python")
    
    def validate_command(self, command: List[str]) -> bool:
        # Basic validation
        return True
    
    def prepare_execution(self, image: str, command: List[str], **kwargs) -> SandboxExecution:
        resolved = self.validate_image(image)
        # Replace first command with local equivalent
        new_cmd = [resolved] + command[1:] if command else [resolved]
        
        exec_id = uuid.uuid4().hex[:12]
        execution = SandboxExecution(
            id=exec_id,
            image=resolved,
            command=new_cmd,
            **kwargs
        )
        return execution
    
    def run(self, execution: SandboxExecution) -> SandboxExecution:
        """Run using subprocess with basic limits"""
        import subprocess
        
        # Try to import resource module (not available on Windows)
        try:
            import resource
            HAS_RESOURCE = True
        except ImportError:
            HAS_RESOURCE = False
        
        execution.status = SandboxStatus.RUNNING
        execution.started_at = datetime.utcnow()
        
        # Ensure working directory exists
        try:
            os.makedirs(execution.working_dir, exist_ok=True)
        except Exception:
            pass
        
        try:
            # Set resource limits for child process (Unix only)
            preexec_fn = None
            try:
                import resource
                HAS_RESOURCE = True
            except ImportError:
                HAS_RESOURCE = False
            
            if HAS_RESOURCE:
                def preexec():
                    # Limit CPU time (seconds)
                    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
                    # Limit memory (256MB)
                    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
                    # Limit processes
                    resource.setrlimit(resource.RLIMIT_NPROC, (100, 100))
                    # Limit file size
                    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
                preexec_fn = preexec
            
            result = subprocess.run(
                execution.command,
                capture_output=True,
                text=True,
                timeout=execution.resource_limits.timeout_seconds,
                preexec_fn=preexec_fn,
                cwd=execution.working_dir,
                env={**os.environ, **execution.environment},
                input=execution.stdin_data,
            )
            
            execution.exit_code = result.returncode
            execution.stdout = result.stdout
            execution.stderr = result.stderr
            execution.status = SandboxStatus.COMPLETED if result.returncode == 0 else SandboxStatus.FAILED
            
        except subprocess.TimeoutExpired:
            execution.status = SandboxStatus.TIMEOUT
            execution.error = "Execution timeout"
        except FileNotFoundError as e:
            execution.status = SandboxStatus.FAILED
            execution.error = f"Command not found: {e}"
            execution.exit_code = -1
        except Exception as e:
            execution.status = SandboxStatus.FAILED
            execution.error = f"{type(e).__name__}: {str(e)}"
            execution.exit_code = -1
        finally:
            execution.completed_at = datetime.utcnow()
        
        return execution
    
    def run_async(self, execution: SandboxExecution) -> str:
        import threading
        thread = threading.Thread(target=self.run, args=(execution,), daemon=True)
        thread.start()
        return execution.id
        
    def get_execution(self, execution_id: str) -> Optional[SandboxExecution]:
        """Get execution by ID (not tracked in fallback - returns None)"""
        return None
    
    def list_executions(self) -> List[SandboxExecution]:
        """List all active executions (empty in fallback)"""
        return []
    
    def kill_execution(self, execution_id: str) -> bool:
        """Kill a running execution (not supported in fallback)"""
        return False
    
    def is_available(self) -> bool:
        return True


# Factory function
def create_sandbox() -> DockerSandbox:
    """Create appropriate sandbox instance"""
    sandbox = DockerSandbox()
    if sandbox.is_available():
        return sandbox
    return FallbackSandbox()


# Global instance
_sandbox: Optional[DockerSandbox] = None


def get_sandbox() -> DockerSandbox:
    """Get or create global sandbox instance"""
    global _sandbox
    if _sandbox is None:
        _sandbox = create_sandbox()
    return _sandbox