"""
5. Live Environment Setup & Execution
Compiles/deploys projects directly to web, manages live environment processes.
"""
import asyncio
import subprocess
import tempfile
import os
import shutil
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
import docker


class DeploymentTarget(Enum):
    LOCAL = "local"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    CLOUD_RUN = "cloud_run"
    VERCEL = "vercel"
    NETLIFY = "netlify"


class EnvironmentStatus(Enum):
    CREATING = "creating"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class EnvironmentConfig:
    name: str
    target: DeploymentTarget
    project_path: str
    config: Dict = field(default_factory=dict)
    env_vars: Dict[str, str] = field(default_factory=dict)
    ports: List[int] = field(default_factory=list)
    volumes: List[str] = field(default_factory=list)


@dataclass
class DeploymentResult:
    success: bool
    environment_id: str
    url: Optional[str] = None
    logs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class LiveEnvironmentManager:
    """
    Manages live deployment environments.
    Compiles, deploys, and monitors projects in real-time.
    """
    
    def __init__(self, base_dir: str = "./deployments"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.environments: Dict[str, Dict] = {}
        self.docker_client = None
        self._init_docker()
    
    def _init_docker(self) -> None:
        """Initialize Docker client if available."""
        try:
            self.docker_client = docker.from_env()
        except Exception:
            self.docker_client = None
    
    async def create_environment(self, config: EnvironmentConfig) -> DeploymentResult:
        """Create and start a new deployment environment."""
        env_id = f"{config.name}-{int(time.time())}"
        env_dir = self.base_dir / env_id
        
        try:
            # Prepare project
            await self._prepare_project(config, env_dir)
            
            # Deploy based on target
            if config.target == DeploymentTarget.DOCKER:
                result = await self._deploy_docker(config, env_dir, env_id)
            elif config.target == DeploymentTarget.LOCAL:
                result = await self._deploy_local(config, env_dir, env_id)
            elif config.target == DeploymentTarget.VERCEL:
                result = await self._deploy_vercel(config, env_dir, env_id)
            elif config.target == DeploymentTarget.NETLIFY:
                result = await self._deploy_netlify(config, env_dir, env_id)
            else:
                raise ValueError(f"Unsupported target: {config.target}")
            
            # Store environment info
            self.environments[env_id] = {
                "config": config,
                "directory": str(env_dir),
                "status": EnvironmentStatus.RUNNING if result.success else EnvironmentStatus.ERROR,
                "created_at": time.time(),
                "result": result,
                "process": result.metadata.get("process"),
            }
            
            return result
            
        except Exception as e:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error=str(e),
                logs=[f"Failed to create environment: {e}"]
            )
    
    async def _prepare_project(self, config: EnvironmentConfig, env_dir: Path) -> None:
        """Copy and prepare project for deployment."""
        # Copy project files
        if os.path.exists(config.project_path):
            shutil.copytree(config.project_path, env_dir, dirs_exist_ok=True)
        else:
            raise ValueError(f"Project path not found: {config.project_path}")
        
        # Generate deployment files
        await self._generate_dockerfile(config, env_dir)
        await self._generate_docker_compose(config, env_dir)
        await self._generate_env_file(config, env_dir)
    
    async def _generate_dockerfile(self, config: EnvironmentConfig, env_dir: Path) -> None:
        """Generate Dockerfile based on project type."""
        # Detect project type
        project_type = self._detect_project_type(env_dir)
        
        dockerfiles = {
            "python": '''FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py"]''',
            "node": '''FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]''',
            "static": '''FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80''',
        }
        
        dockerfile = dockerfiles.get(project_type, dockerfiles["python"])
        (env_dir / "Dockerfile").write_text(dockerfile)
    
    async def _generate_docker_compose(self, config: EnvironmentConfig, env_dir: Path) -> None:
        """Generate docker-compose.yml."""
        compose = {
            "version": "3.8",
            "services": {
                config.name: {
                    "build": ".",
                    "ports": [f"{p}:{p}" for p in config.ports] or ["8000:8000"],
                    "environment": config.env_vars,
                    "volumes": config.volumes,
                    "restart": "unless-stopped",
                }
            }
        }
        
        # Add dependent services
        if "database" in config.config:
            compose["services"]["db"] = {
                "image": "postgres:15",
                "environment": {
                    "POSTGRES_DB": config.config.get("db_name", "app"),
                    "POSTGRES_USER": config.config.get("db_user", "app"),
                    "POSTGRES_PASSWORD": config.config.get("db_pass", "secret"),
                },
                "volumes": ["db_data:/var/lib/postgresql/data"],
            }
        
        if "cache" in config.config:
            compose["services"]["redis"] = {
                "image": "redis:7-alpine",
                "volumes": ["redis_data:/data"],
            }
        
        import yaml
        (env_dir / "docker-compose.yml").write_text(yaml.dump(compose))
    
    async def _generate_env_file(self, config: EnvironmentConfig, env_dir: Path) -> None:
        """Generate .env file."""
        env_content = "\n".join(f"{k}={v}" for k, v in config.env_vars.items())
        (env_dir / ".env").write_text(env_content)
    
    def _detect_project_type(self, project_dir: Path) -> str:
        """Detect project type from files."""
        if (project_dir / "package.json").exists():
            return "node"
        if (project_dir / "requirements.txt").exists() or (project_dir / "pyproject.toml").exists():
            return "python"
        if (project_dir / "index.html").exists():
            return "static"
        return "python"
    
    async def _deploy_docker(self, config: EnvironmentConfig, env_dir: Path, env_id: str) -> DeploymentResult:
        """Deploy using Docker Compose."""
        if not self.docker_client:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error="Docker not available"
            )
        
        logs = []
        
        try:
            # Build
            logs.append("Building Docker image...")
            image, build_logs = self.docker_client.images.build(
                path=str(env_dir),
                tag=f"{env_id}:latest",
                rm=True
            )
            for chunk in build_logs:
                if "stream" in chunk:
                    logs.append(chunk["stream"].strip())
            
            # Run container
            logs.append("Starting container...")
            container = self.docker_client.containers.run(
                image.id,
                detach=True,
                name=env_id,
                ports={f"{p}/tcp": p for p in config.ports} or {"8000/tcp": 8000},
                environment=config.env_vars,
                volumes={str(v.split(":")[0]): {"bind": v.split(":")[1], "mode": "rw"} for v in config.volumes} if config.volumes else {},
                remove=True,
            )
            
            # Wait for health
            await asyncio.sleep(3)
            
            # Get URL
            port = config.ports[0] if config.ports else 8000
            url = f"http://localhost:{port}"
            
            return DeploymentResult(
                success=True,
                environment_id=env_id,
                url=url,
                logs=logs,
                metadata={"container_id": container.id, "image_id": image.id}
            )
            
        except Exception as e:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error=str(e),
                logs=logs
            )
    
    async def _deploy_local(self, config: EnvironmentConfig, env_dir: Path, env_id: str) -> DeploymentResult:
        """Deploy locally using subprocess."""
        logs = []
        
        try:
            # Install dependencies
            if (env_dir / "requirements.txt").exists():
                logs.append("Installing Python dependencies...")
                proc = await asyncio.create_subprocess_exec(
                    "pip", "install", "-r", "requirements.txt",
                    cwd=env_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                logs.append(stdout.decode()[:500])
            
            # Start process
            logs.append("Starting application...")
            if (env_dir / "main.py").exists():
                cmd = ["python", "main.py"]
            elif (env_dir / "server.js").exists():
                cmd = ["node", "server.js"]
            else:
                cmd = ["python", "-m", "http.server", "8000"]
            
            env = os.environ.copy()
            env.update(config.env_vars)
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=env_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            await asyncio.sleep(2)
            
            port = config.ports[0] if config.ports else 8000
            url = f"http://localhost:{port}"
            
            return DeploymentResult(
                success=True,
                environment_id=env_id,
                url=url,
                logs=logs,
                metadata={"process": process}
            )
            
        except Exception as e:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error=str(e),
                logs=logs
            )
    
    async def _deploy_vercel(self, config: EnvironmentConfig, env_dir: Path, env_id: str) -> DeploymentResult:
        """Deploy to Vercel."""
        logs = ["Vercel deployment requires Vercel CLI and authentication"]
        
        try:
            # Check for vercel.json
            vercel_config = {
                "buildCommand": config.config.get("build_command", "npm run build"),
                "outputDirectory": config.config.get("output_dir", "dist"),
                "framework": config.config.get("framework", "nextjs"),
            }
            (env_dir / "vercel.json").write_text(json.dumps(vercel_config))
            
            # Run vercel deploy
            proc = await asyncio.create_subprocess_exec(
                "vercel", "deploy", "--prod", "--yes",
                cwd=env_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            output = stdout.decode()
            logs.append(output[:1000])
            
            # Extract URL from output
            import re
            url_match = re.search(r'https://[\w\-]+\.vercel\.app', output)
            url = url_match.group(0) if url_match else None
            
            return DeploymentResult(
                success=proc.returncode == 0,
                environment_id=env_id,
                url=url,
                logs=logs,
                metadata={"vercel_output": output}
            )
            
        except FileNotFoundError:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error="Vercel CLI not installed",
                logs=logs
            )
    
    async def _deploy_netlify(self, config: EnvironmentConfig, env_dir: Path, env_id: str) -> DeploymentResult:
        """Deploy to Netlify."""
        logs = ["Netlify deployment requires Netlify CLI and authentication"]
        
        try:
            netlify_config = {
                "build": config.config.get("build_command", "npm run build"),
                "publish": config.config.get("publish_dir", "dist"),
            }
            (env_dir / "netlify.toml").write_text(f"""
[build]
  command = "{netlify_config['build']}"
  publish = "{netlify_config['publish']}"
""")
            
            proc = await asyncio.create_subprocess_exec(
                "netlify", "deploy", "--prod", "--dir", netlify_config["publish"],
                cwd=env_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            output = stdout.decode()
            logs.append(output[:1000])
            
            import re
            url_match = re.search(r'https://[\w\-]+\.netlify\.app', output)
            url = url_match.group(0) if url_match else None
            
            return DeploymentResult(
                success=proc.returncode == 0,
                environment_id=env_id,
                url=url,
                logs=logs
            )
            
        except FileNotFoundError:
            return DeploymentResult(
                success=False,
                environment_id=env_id,
                error="Netlify CLI not installed",
                logs=logs
            )
    
    async def stop_environment(self, env_id: str) -> bool:
        """Stop and clean up environment."""
        if env_id not in self.environments:
            return False
        
        env = self.environments[env_id]
        
        try:
            if env["config"].target == DeploymentTarget.DOCKER:
                if self.docker_client:
                    try:
                        container = self.docker_client.containers.get(env_id)
                        container.stop()
                        container.remove()
                    except docker.errors.NotFound:
                        pass
            
            elif env["config"].target == DeploymentTarget.LOCAL:
                process = env.get("process")
                if process and process.returncode is None:
                    process.terminate()
                    await process.wait()
            
            # Clean up directory
            shutil.rmtree(env["directory"], ignore_errors=True)
            
            del self.environments[env_id]
            return True
            
        except Exception:
            return False
    
    async def get_environment_status(self, env_id: str) -> Optional[Dict]:
        """Get environment status."""
        if env_id not in self.environments:
            return None
        
        env = self.environments[env_id]
        
        # Check if process/container still running
        is_running = False
        if env["config"].target == DeploymentTarget.DOCKER and self.docker_client:
            try:
                container = self.docker_client.containers.get(env_id)
                is_running = container.status == "running"
            except docker.errors.NotFound:
                is_running = False
        elif env["config"].target == DeploymentTarget.LOCAL:
            process = env.get("process")
            is_running = process and process.returncode is None
        
        return {
            "id": env_id,
            "name": env["config"].name,
            "status": EnvironmentStatus.RUNNING.value if is_running else EnvironmentStatus.STOPPED.value,
            "url": env["result"].url,
            "created_at": env["created_at"],
            "uptime": time.time() - env["created_at"] if is_running else 0,
        }
    
    async def list_environments(self) -> List[Dict]:
        """List all environments."""
        results = []
        for env_id in self.environments:
            status = await self.get_environment_status(env_id)
            if status:
                results.append(status)
        return results
    
    async def get_logs(self, env_id: str, lines: int = 100) -> List[str]:
        """Get environment logs."""
        if env_id not in self.environments:
            return []
        
        env = self.environments[env_id]
        logs = env["result"].logs[-lines:]
        
        # Add live logs from container/process
        if env["config"].target == DeploymentTarget.DOCKER and self.docker_client:
            try:
                container = self.docker_client.containers.get(env_id)
                container_logs = container.logs(tail=lines).decode().split("\n")
                logs.extend([l for l in container_logs if l])
            except Exception:
                pass
        
        return logs