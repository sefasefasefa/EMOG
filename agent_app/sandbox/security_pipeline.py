"""
Security Patch Testing Pipeline
Automatically tests security patches in isolated environment before deployment.
"""
import os
import json
import tempfile
import shutil
import subprocess
import threading
import time
from typing import Dict, Any, List, Optional
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


class PatchStatus(Enum):
    PENDING = "pending"
    TESTING = "testing"
    PASSED = "passed"
    FAILED = "failed"
    DEPLOYED = "deployed"
    ROLLED_BACK = "rolled_back"


class PatchType(Enum):
    SECURITY = "security"
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    DEPENDENCY = "dependency"
    CONFIG = "config"


@dataclass
class SecurityPatch:
    """Security patch to be tested and deployed"""
    id: str
    name: str
    description: str
    patch_type: PatchType
    target_files: List[str]
    diff_content: str
    test_commands: List[str] = field(default_factory=list)
    rollback_commands: List[str] = field(default_factory=list)
    status: PatchStatus = PatchStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tested_at: Optional[str] = None
    deployed_at: Optional[str] = None
    test_results: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Result of a patch test"""
    patch_id: str
    success: bool
    tests_run: int
    tests_passed: int
    tests_failed: int
    output: str
    error: Optional[str] = None
    execution_time_ms: float = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class SecurityPatchPipeline:
    """
    Pipeline for testing and deploying security patches.
    Runs tests in isolated Docker containers before deployment.
    """
    
    def __init__(self, docker_url: str = None, patches_dir: str = None):
        self.docker_url = docker_url or os.environ.get("DOCKER_URL", "unix:///var/run/docker.sock")
        self.patches_dir = patches_dir or os.path.join(tempfile.gettempdir(), "emog_patches")
        self._client: Optional[docker.DockerClient] = None
        self._lock = threading.Lock()
        self._patches: Dict[str, SecurityPatch] = {}
        self._test_results: Dict[str, List[TestResult]] = {}
        self._init_directories()
        self._init_client()
    
    def _init_directories(self):
        """Initialize patch directories"""
        os.makedirs(self.patches_dir, exist_ok=True)
        os.makedirs(os.path.join(self.patches_dir, "pending"), exist_ok=True)
        os.makedirs(os.path.join(self.patches_dir, "tested"), exist_ok=True)
        os.makedirs(os.path.join(self.patches_dir, "deployed"), exist_ok=True)
        os.makedirs(os.path.join(self.patches_dir, "failed"), exist_ok=True)
    
    def _init_client(self):
        """Initialize Docker client"""
        if not DOCKER_AVAILABLE:
            print("Warning: Docker SDK not available. Pipeline will use fallback.")
            return
        try:
            self._client = docker.DockerClient(
                base_url=self.docker_url,
                timeout=30,
                max_pool_size=10,
            )
            self._client.ping()
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
    
    def submit_patch(self, patch: SecurityPatch) -> str:
        """Submit a new security patch for testing"""
        patch.id = patch.id or f"patch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{patch.name[:20]}"
        
        # Save patch to disk
        patch_dir = os.path.join(self.patches_dir, "pending", patch.id)
        os.makedirs(patch_dir, exist_ok=True)
        
        with open(os.path.join(patch_dir, "patch.json"), "w") as f:
            json.dump({
                "id": patch.id,
                "name": patch.name,
                "description": patch.description,
                "patch_type": patch.patch_type.value,
                "target_files": patch.target_files,
                "diff_content": patch.diff_content,
                "test_commands": patch.test_commands,
                "rollback_commands": patch.rollback_commands,
                "status": patch.status.value,
                "created_at": patch.created_at,
                "metadata": patch.metadata,
            }, f, indent=2)
        
        # Save diff content
        with open(os.path.join(patch_dir, "patch.diff"), "w") as f:
            f.write(patch.diff_content)
        
        with self._lock:
            self._patches[patch.id] = patch
        
        # Auto-start testing
        threading.Thread(target=self.test_patch, args=(patch.id,), daemon=True).start()
        
        return patch.id
    
    def test_patch(self, patch_id: str) -> TestResult:
        """Test a patch in isolated environment"""
        with self._lock:
            patch = self._patches.get(patch_id)
        
        if not patch:
            # Load from disk
            patch = self._load_patch(patch_id)
            if not patch:
                return TestResult(
                    patch_id=patch_id,
                    success=False,
                    tests_run=0,
                    tests_passed=0,
                    tests_failed=0,
                    output="",
                    error=f"Patch not found: {patch_id}"
                )
        
        patch.status = PatchStatus.TESTING
        self._save_patch(patch)
        
        start_time = time.time()
        tests_run = 0
        tests_passed = 0
        tests_failed = 0
        output_parts = []
        error = None
        
        try:
            if self.is_available() and self._client:
                result = self._test_in_docker(patch)
            else:
                result = self._test_in_fallback(patch)
            
            tests_run = result.tests_run
            tests_passed = result.tests_passed
            tests_failed = result.tests_failed
            output_parts.append(result.output)
            error = result.error
            
        except Exception as e:
            error = f"Test execution failed: {str(e)}"
            tests_failed += 1
        
        execution_time = (time.time() - start_time) * 1000
        success = tests_failed == 0 and tests_run > 0
        
        result = TestResult(
            patch_id=patch_id,
            success=success,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            output="\n".join(output_parts),
            error=error,
            execution_time_ms=execution_time
        )
        
        # Update patch status
        patch.status = PatchStatus.PASSED if success else PatchStatus.FAILED
        patch.tested_at = datetime.utcnow().isoformat()
        patch.test_results = {
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "execution_time_ms": execution_time,
            "error": error,
        }
        self._save_patch(patch)
        
        # Store test result
        with self._lock:
            if patch_id not in self._test_results:
                self._test_results[patch_id] = []
            self._test_results[patch_id].append(result)
        
        # Move patch to appropriate directory
        self._move_patch(patch)
        
        return result
    
    def _test_in_docker(self, patch: SecurityPatch) -> TestResult:
        """Test patch in Docker container"""
        tests_run = 0
        tests_passed = 0
        tests_failed = 0
        output_parts = []
        error = None
        
        # Create test container
        test_dir = tempfile.mkdtemp(prefix=f"patch_test_{patch.id}_")
        
        try:
            # Write patch and test files
            self._prepare_test_environment(test_dir, patch)
            
            # Build test image
            dockerfile = self._generate_dockerfile(patch)
            dockerfile_path = os.path.join(test_dir, "Dockerfile")
            with open(dockerfile_path, "w") as f:
                f.write(dockerfile)
            
            # Build image
            image_tag = f"emog_patch_test_{patch.id}"
            self._client.images.build(
                path=test_dir,
                tag=image_tag,
                rm=True,
                forcerm=True,
            )
            
            # Run tests
            for i, cmd in enumerate(patch.test_commands):
                tests_run += 1
                try:
                    container = self._client.containers.run(
                        image_tag,
                        command=["sh", "-c", cmd],
                        detach=True,
                        remove=True,
                        mem_limit="256m",
                        cpu_quota=50000,
                        network_mode="none",
                        security_opt=["no-new-privileges:true"],
                    )
                    
                    # Wait for completion
                    exit_code = container.wait(timeout=60)
                    logs = container.logs().decode("utf-8", errors="replace")
                    
                    if exit_code["StatusCode"] == 0:
                        tests_passed += 1
                        output_parts.append(f"Test {i+1} PASSED: {cmd}\n{logs}")
                    else:
                        tests_failed += 1
                        output_parts.append(f"Test {i+1} FAILED: {cmd}\n{logs}")
                        error = f"Test {i+1} failed with exit code {exit_code['StatusCode']}"
                    
                except Exception as e:
                    tests_failed += 1
                    error = f"Test {i+1} exception: {str(e)}"
                    output_parts.append(f"Test {i+1} ERROR: {cmd}\n{str(e)}")
            
            return TestResult(
                patch_id=patch.id,
                success=tests_failed == 0 and tests_run > 0,
                tests_run=tests_run,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                output="\n".join(output_parts),
                error=error,
            )
            
        finally:
            # Cleanup
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
            except Exception:
                pass
    
    def _test_in_fallback(self, patch: SecurityPatch) -> TestResult:
        """Test patch using subprocess fallback"""
        tests_run = 0
        tests_passed = 0
        tests_failed = 0
        output_parts = []
        error = None
        
        test_dir = tempfile.mkdtemp(prefix=f"patch_test_{patch.id}_")
        
        try:
            self._prepare_test_environment(test_dir, patch)
            
            for i, cmd in enumerate(patch.test_commands):
                tests_run += 1
                try:
                    result = subprocess.run(
                        cmd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=test_dir,
                    )
                    
                    if result.returncode == 0:
                        tests_passed += 1
                        output_parts.append(f"Test {i+1} PASSED: {cmd}\n{result.stdout}")
                    else:
                        tests_failed += 1
                        output_parts.append(f"Test {i+1} FAILED: {cmd}\n{result.stderr}")
                        error = f"Test {i+1} failed with exit code {result.returncode}"
                        
                except subprocess.TimeoutExpired:
                    tests_failed += 1
                    error = f"Test {i+1} timed out"
                    output_parts.append(f"Test {i+1} TIMEOUT: {cmd}")
                except Exception as e:
                    tests_failed += 1
                    error = f"Test {i+1} exception: {str(e)}"
                    output_parts.append(f"Test {i+1} ERROR: {cmd}\n{str(e)}")
            
            return TestResult(
                patch_id=patch.id,
                success=tests_failed == 0 and tests_run > 0,
                tests_run=tests_run,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                output="\n".join(output_parts),
                error=error,
            )
            
        finally:
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
            except Exception:
                pass
    
    def _prepare_test_environment(self, test_dir: str, patch: SecurityPatch):
        """Prepare test environment with patch applied"""
        # Copy target files
        for target_file in patch.target_files:
            if os.path.exists(target_file):
                dst = os.path.join(test_dir, target_file)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(target_file, dst)
        
        # Apply patch
        patch_file = os.path.join(test_dir, "patch.diff")
        with open(patch_file, "w") as f:
            f.write(patch.diff_content)
        
        # Apply using patch command
        subprocess.run(
            ["patch", "-p1", "-i", patch_file],
            cwd=test_dir,
            capture_output=True,
            timeout=30,
        )
    
    def _generate_dockerfile(self, patch: SecurityPatch) -> str:
        """Generate Dockerfile for testing"""
        base_image = "python:3.11-slim"
        
        # Determine base image from patch type
        if patch.patch_type == PatchType.DEPENDENCY:
            base_image = "python:3.11-slim"
        elif "node" in " ".join(patch.test_commands).lower():
            base_image = "node:20-slim"
        
        dockerfile = f"""
FROM {base_image}

WORKDIR /test

# Install patch tool
RUN apt-get update && apt-get install -y patch git && rm -rf /var/lib/apt/lists/*

# Copy test environment
COPY . /test/

# Set non-root user
RUN useradd -m -s /bin/bash testuser && chown -R testuser:testuser /test
USER testuser

CMD ["sh", "-c", "echo 'Ready for testing'"]
"""
        return dockerfile
    
    def _load_patch(self, patch_id: str) -> Optional[SecurityPatch]:
        """Load patch from disk"""
        patch_dir = os.path.join(self.patches_dir, "pending", patch_id)
        if not os.path.exists(patch_dir):
            # Check other directories
            for subdir in ["tested", "deployed", "failed"]:
                patch_dir = os.path.join(self.patches_dir, subdir, patch_id)
                if os.path.exists(patch_dir):
                    break
            else:
                return None
        
        patch_file = os.path.join(patch_dir, "patch.json")
        if not os.path.exists(patch_file):
            return None
        
        with open(patch_file, "r") as f:
            data = json.load(f)
        
        diff_file = os.path.join(patch_dir, "patch.diff")
        if os.path.exists(diff_file):
            with open(diff_file, "r") as f:
                diff_content = f.read()
        else:
            diff_content = ""
        
        return SecurityPatch(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            patch_type=PatchType(data["patch_type"]),
            target_files=data["target_files"],
            diff_content=diff_content,
            test_commands=data.get("test_commands", []),
            rollback_commands=data.get("rollback_commands", []),
            status=PatchStatus(data["status"]),
            created_at=data["created_at"],
            metadata=data.get("metadata", {}),
        )
    
    def _save_patch(self, patch: SecurityPatch):
        """Save patch to disk"""
        for subdir in ["pending", "tested", "deployed", "failed"]:
            patch_dir = os.path.join(self.patches_dir, subdir, patch.id)
            if os.path.exists(patch_dir):
                patch_file = os.path.join(patch_dir, "patch.json")
                if os.path.exists(patch_file):
                    with open(patch_file, "w") as f:
                        json.dump({
                            "id": patch.id,
                            "name": patch.name,
                            "description": patch.description,
                            "patch_type": patch.patch_type.value,
                            "target_files": patch.target_files,
                            "diff_content": patch.diff_content,
                            "test_commands": patch.test_commands,
                            "rollback_commands": patch.rollback_commands,
                            "status": patch.status.value,
                            "created_at": patch.created_at,
                            "tested_at": patch.tested_at,
                            "deployed_at": patch.deployed_at,
                            "test_results": patch.test_results,
                            "metadata": patch.metadata,
                        }, f, indent=2)
                    break
    
    def _move_patch(self, patch: SecurityPatch):
        """Move patch to appropriate directory based on status"""
        src_dirs = ["pending", "tested", "deployed", "failed"]
        dst_dir = patch.status.value
        
        for src_dir in src_dirs:
            src = os.path.join(self.patches_dir, src_dir, patch.id)
            if os.path.exists(src):
                dst = os.path.join(self.patches_dir, dst_dir, patch.id)
                if os.path.exists(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.move(src, dst)
                break
    
    def deploy_patch(self, patch_id: str) -> bool:
        """Deploy a tested patch to production"""
        with self._lock:
            patch = self._patches.get(patch_id)
        
        if not patch:
            patch = self._load_patch(patch_id)
            if not patch:
                return False
        
        if patch.status != PatchStatus.PASSED:
            return False
        
        try:
            # Apply patch to target files
            for target_file in patch.target_files:
                if os.path.exists(target_file):
                    # Backup original
                    backup = f"{target_file}.backup.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
                    shutil.copy2(target_file, backup)
                    
                    # Apply patch
                    subprocess.run(
                        ["patch", "-p1", "-i", os.path.join(self.patches_dir, "tested", patch.id, "patch.diff")],
                        cwd=os.path.dirname(target_file) or ".",
                        check=True,
                        timeout=30,
                    )
            
            patch.status = PatchStatus.DEPLOYED
            patch.deployed_at = datetime.utcnow().isoformat()
            self._save_patch(patch)
            self._move_patch(patch)
            return True
            
        except Exception as e:
            patch.status = PatchStatus.FAILED
            self._save_patch(patch)
            return False
    
    def rollback_patch(self, patch_id: str) -> bool:
        """Rollback a deployed patch"""
        with self._lock:
            patch = self._patches.get(patch_id)
        
        if not patch:
            patch = self._load_patch(patch_id)
            if not patch:
                return False
        
        try:
            # Restore from backups
            for target_file in patch.target_files:
                backups = [f for f in os.listdir(os.path.dirname(target_file) or ".")
                          if f.startswith(os.path.basename(target_file) + ".backup.")]
                if backups:
                    latest_backup = max(backups)
                    shutil.copy2(os.path.join(os.path.dirname(target_file) or ".", backups[-1]), target_file)
            
            # Run rollback commands
            for cmd in patch.rollback_commands:
                subprocess.run(cmd, shell=True, timeout=60)
            
            patch.status = PatchStatus.ROLLED_BACK
            self._save_patch(patch)
            return True
            
        except Exception as e:
            return False
    
    def get_patch_status(self, patch_id: str) -> Optional[SecurityPatch]:
        """Get patch status"""
        with self._lock:
            if patch_id in self._patches:
                return self._patches[patch_id]
        return self._load_patch(patch_id)
    
    def get_test_results(self, patch_id: str) -> List[TestResult]:
        """Get test results for a patch"""
        with self._lock:
            return self._test_results.get(patch_id, [])
    
    def list_patches(self, status: Optional[PatchStatus] = None) -> List[SecurityPatch]:
        """List all patches"""
        with self._lock:
            patches = list(self._patches.values())
        
        # Also load from disk
        for subdir in ["pending", "tested", "deployed", "failed"]:
            dir_path = os.path.join(self.patches_dir, subdir)
            if os.path.exists(dir_path):
                for patch_id in os.listdir(dir_path):
                    if patch_id not in self._patches:
                        patch = self._load_patch(patch_id)
                        if patch:
                            with self._lock:
                                self._patches[patch.id] = patch
        
        with self._lock:
            patches = list(self._patches.values())
        
        if status:
            patches = [p for p in patches if p.status == status]
        
        return patches


# Global instance
_patch_pipeline: Optional[SecurityPatchPipeline] = None
_pipeline_lock = threading.Lock()


def get_patch_pipeline() -> SecurityPatchPipeline:
    """Get or create global patch pipeline instance"""
    global _patch_pipeline
    with _pipeline_lock:
        if _patch_pipeline is None:
            _patch_pipeline = SecurityPatchPipeline()
        return _patch_pipeline