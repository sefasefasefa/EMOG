"""
15. Local/Browser Execution
Runs simple code/tests in browser via WebAssembly/WebContainers.
Eliminates server costs and latency.
"""
import asyncio
import hashlib
import time
import uuid
import tempfile
import subprocess
import json
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal
import asyncio.subprocess


class ExecutionEngine(Enum):
    PYODIDE = "pyodide"          # Python in browser via WebAssembly
    WEBCONTAINERS = "webcontainers"  # Node.js/deno in browser
    QUICKJS = "quickjs"          # JavaScript engine
    WASM_PYTHON = "wasm_python"  # Standalone WASM Python
    NATIVE = "native"            # Native subprocess fallback


class ExecutionStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ExecutionRequest:
    id: str
    code: str
    language: str  # python, javascript, typescript
    engine: ExecutionEngine = ExecutionEngine.PYODIDE
    dependencies: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)  # filename -> content
    timeout: int = 30
    stdin: str = ""
    env_vars: Dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    request_id: str
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    execution_time: float = 0.0
    memory_used: int = 0
    artifacts: Dict[str, str] = field(default_factory=dict)  # filename -> base64 content
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


class BrowserExecutor:
    """
    Executes code in browser-like environments.
    Supports Pyodide (Python), WebContainers (Node.js), QuickJS.
    """
    
    def __init__(self, cache_dir: str = "./execution_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        self._running: Dict[str, asyncio.subprocess.Process] = {}
        self._results: Dict[str, ExecutionResult] = {}
        self._engine_scripts: Dict[ExecutionEngine, str] = {}
        self._init_engines()
    
    def _init_engines(self) -> None:
        """Initialize engine runtime scripts."""
        # Pyodide initialization script
        self._engine_scripts[ExecutionEngine.PYODIDE] = """
// Pyodide Python execution environment
let pyodide = null;
let output = { stdout: "", stderr: "" };

async function initPyodide() {
    if (!pyodide) {
        pyodide = await loadPyodide({
            indexURL: "https://cdn.jsdelivr.net/pyodide/v0.25.0/full/"
        });
    }
    return pyodide;
}

async function runPython(code, stdin = "") {
    if (!pyodide) await initPyodide();
    
    // Capture stdout/stderr
    const originalStdout = sys.stdout;
    const originalStderr = sys.stderr;
    
    sys.stdout = { write: (text) => output.stdout += text };
    sys.stderr = { write: (text) => output.stderr += text };
    
    if (stdin) {
        sys.stdin = { read: () => stdin };
    }
    
    try {
        await pyodide.runPythonAsync(code);
        return { success: true, output };
    } catch (err) {
        output.stderr += err.toString();
        return { success: false, output, error: err.toString() };
    } finally {
        sys.stdout = originalStdout;
        sys.stderr = originalStderr;
    }
}

// Export for use
self.runPython = runPython;
self.pyodideReady = initPyodide().then(() => true);
"""
        
        # WebContainers initialization
        self._engine_scripts[ExecutionEngine.WEBCONTAINERS] = """
// WebContainers Node.js execution
import { WebContainer } from '@webcontainer/api';

let webcontainer = null;

async function initWebContainer() {
    if (!webcontainer) {
        webcontainer = await WebContainer.boot();
    }
    return webcontainer;
}

async function runNode(code, files = {}) {
    if (!webcontainer) await initWebContainer();
    
    // Write files
    for (const [filename, content] of Object.entries(files)) {
        await webcontainer.fs.writeFile(filename, content);
    }
    
    // Run code
    const process = await webcontainer.spawn('node', ['-e', code]);
    
    return new Promise((resolve) => {
        let stdout = '', stderr = '';
        process.output.pipeTo(new WritableStream({
            write(chunk) { stdout += new TextDecoder().decode(chunk); }
        }));
        process.output.pipeTo(new WritableStream({
            write(chunk) { stderr += new TextDecoder().decode(chunk); }
        }));
        
        process.exit.then((code) => {
            resolve({ exitCode: code, stdout, stderr });
        });
    });
}

self.runNode = runNode;
"""
        
        # QuickJS script
        self._engine_scripts[ExecutionEngine.QUICKJS] = """
// QuickJS JavaScript execution
const { QuickJS } = require('quickjs-emscripten');

let runtime = null;
let context = null;

function initQuickJS() {
    if (!runtime) {
        const QuickJS = require('quickjs-emscripten');
        runtime = QuickJS();
        context = runtime.newContext();
    }
    return { runtime, context };
}

function runJS(code) {
    const { context } = initQuickJS();
    
    const result = context.evalCode(code, { type: 'global' });
    
    if (result.error) {
        return { success: false, error: result.error.stack || result.error.message };
    }
    
    return { success: true, result: result.value };

self.runJS = runJS;
"""
    
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute code with specified engine."""
        result = ExecutionResult(request_id=request.id)
        self._results[request.id] = result
        
        start_time = time.time()
        result.started_at = start_time
        result.status = ExecutionStatus.RUNNING
        
        try:
            if request.engine == ExecutionEngine.PYODIDE:
                result = await self._execute_pyodide(request, result)
            elif request.engine == ExecutionEngine.WEBCONTAINERS:
                result = await self._execute_webcontainer(request, result)
            elif request.engine == ExecutionEngine.QUICKJS:
                result = await self._execute_quickjs(request, result)
            elif request.engine == ExecutionEngine.NATIVE:
                result = await self._execute_native(request, result)
            else:
                result.status = ExecutionStatus.FAILED
                result.error = f"Unknown engine: {request.engine}"
                
        except asyncio.TimeoutError:
            result.status = ExecutionStatus.TIMEOUT
            result.error = f"Execution timed out after {request.timeout}s"
        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
        
        result.completed_at = time.time()
        result.execution_time = result.completed_at - start_time
        
        return result
    
    async def _execute_pyodide(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute Python via Pyodide (requires browser environment)."""
        # This would run in browser via WebAssembly
        # For server-side, we use native Python as fallback
        return await self._execute_native(request, result)
    
    async def _execute_webcontainer(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute Node.js via WebContainers."""
        # Would run in browser via WebContainers API
        # Server-side fallback to Node.js
        return await self._execute_native(request, result)
    
    async def _execute_quickjs(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute JavaScript via QuickJS."""
        # For server-side execution
        try:
            # Write code to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
                f.write(request.code)
                temp_file = f.name
            
            proc = await asyncio.create_subprocess_exec(
                'node', temp_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=request.stdin.encode()),
                    timeout=request.timeout
                )
                
                result.stdout = stdout.decode()
                result.stderr = stderr.decode()
                result.return_code = proc.returncode or 0
                result.status = ExecutionStatus.COMPLETED if proc.returncode == 0 else ExecutionStatus.FAILED
                
            finally:
                Path(temp_file).unlink(missing_ok=True)
                
        except asyncio.TimeoutError:
            result.status = ExecutionStatus.TIMEOUT
            result.error = "Timeout"
        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
        
        return result
    
    async def _execute_native(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute code natively (fallback)."""
        if request.language == "python":
            return await self._execute_python_native(request, result)
        elif request.language in ["javascript", "typescript"]:
            return await self._execute_js_native(request, result)
        else:
            result.status = ExecutionStatus.FAILED
            result.error = f"Unsupported language: {request.language}"
            return result
    
    async def _execute_python_native(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute Python code natively."""
        # Write code to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            # Add dependencies installation if needed
            if request.dependencies:
                f.write("# dependencies:\n")
                for dep in request.dependencies:
                    f.write(f"# pip install {dep}\n")
            
            f.write(request.code)
            temp_file = f.name
        
        try:
            # Prepare environment
            env = os.environ.copy()
            env.update(request.env_vars)
            
            # Run with timeout
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-X', 'utf8', temp_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=request.stdin.encode()),
                    timeout=request.timeout
                )
                
                result.stdout = stdout.decode()
                result.stderr = stderr.decode()
                result.return_code = proc.returncode or 0
                result.status = ExecutionStatus.COMPLETED if proc.returncode == 0 else ExecutionStatus.FAILED
                
            finally:
                Path(temp_file).unlink(missing_ok=True)
                
        except asyncio.TimeoutError:
            result.status = ExecutionStatus.TIMEOUT
            result.error = f"Timeout after {request.timeout}s"
        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
        
        return result
    
    async def _execute_js_native(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        """Execute JavaScript/TypeScript natively."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(request.code)
            temp_file = f.name
        
        try:
            proc = await asyncio.create_subprocess_exec(
                'node', temp_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=request.stdin.encode()),
                    timeout=request.timeout
                )
                
                result.stdout = stdout.decode()
                result.stderr = stderr.decode()
                result.return_code = proc.returncode or 0
                result.status = ExecutionStatus.COMPLETED if proc.returncode == 0 else ExecutionStatus.FAILED
                
            finally:
                Path(temp_file).unlink(missing_ok=True)
                
        except asyncio.TimeoutError:
            result.status = ExecutionStatus.TIMEOUT
            result.error = "Timeout"
        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error = str(e)
        
        return result
    
    def get_result(self, request_id: str) -> Optional[ExecutionResult]:
        """Get execution result."""
        return self._results.get(request_id)
    
    def cancel(self, request_id: str) -> bool:
        """Cancel running execution."""
        if request_id in self._running:
            self._running[request_id].terminate()
            return True
        return False


class CodeSandbox:
    """
    High-level code sandbox with multiple execution strategies.
    Automatically selects best engine based on code and environment.
    """
    
    def __init__(self, prefer_browser: bool = False):
        self.executor = BrowserExecutor()
        self.prefer_browser = prefer_browser
        self._execution_history: List[ExecutionResult] = []
    
    async def run(self, code: str, language: str = "python", **kwargs) -> ExecutionResult:
        """Run code with automatic engine selection."""
        # Determine best engine
        engine = self._select_engine(language, kwargs)
        
        request = ExecutionRequest(
            id=f"exec_{uuid.uuid4().hex[:8]}",
            code=code,
            language=language,
            engine=engine,
            **kwargs
        )
        
        result = await self.executor.execute(request)
        self._execution_history.append(result)
        
        # Keep history limited
        if len(self._execution_history) > 100:
            self._execution_history = self._execution_history[-100:]
        
        return result
    
    def _select_engine(self, language: str, kwargs: Dict) -> ExecutionEngine:
        """Select best execution engine."""
        # For browser environments, prefer WebAssembly
        if self.prefer_browser:
            if language == "python":
                return ExecutionEngine.PYODIDE
            else:
                return ExecutionEngine.WEBCONTAINERS
        
        # Server-side defaults
        if language == "python":
            return ExecutionEngine.NATIVE  # Uses native Python
        elif language in ["javascript", "typescript"]:
            return ExecutionEngine.QUICKJS
        else:
            return ExecutionEngine.NATIVE
    
    async def run_with_files(self, main_code: str, files: Dict[str, str], language: str = "python", **kwargs) -> ExecutionResult:
        """Run code with additional files."""
        request = ExecutionRequest(
            id=f"exec_{uuid.uuid4().hex[:8]}",
            code=main_code,
            language=language,
            files=files,
            **kwargs
        )
        
        # Write files to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename, content in files.items():
                file_path = Path(tmpdir) / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)
            
            # Add temp dir to Python path or Node modules
            if kwargs.get('engine') == ExecutionEngine.NATIVE:
                env = os.environ.copy()
                env['PYTHONPATH'] = tmpdir + ':' + env.get('PYTHONPATH', '')
                kwargs['env_vars'] = {**kwargs.get('env_vars', {}), **env}
            
            return await self.executor.execute(ExecutionRequest(
                id=f"exec_{uuid.uuid4().hex[:8]}",
                code=main_code,
                language=language,
                **kwargs
            ))
    
    def get_history(self, limit: int = 20) -> List[ExecutionResult]:
        """Get execution history."""
        return self._execution_history[-limit:]
    
    def get_stats(self) -> Dict:
        """Get execution statistics."""
        if not self._execution_history:
            return {"total": 0}
        
        total = len(self._execution_history)
        successful = sum(1 for r in self._execution_history if r.status == ExecutionStatus.COMPLETED)
        failed = sum(1 for r in self._execution_history if r.status == ExecutionStatus.FAILED)
        timed_out = sum(1 for r in self._execution_history if r.status == ExecutionStatus.TIMEOUT)
        
        avg_time = sum(r.execution_time for r in self._execution_history) / total
        
        return {
            "total_executions": total,
            "successful": successful,
            "failed": failed,
            "timed_out": timed_out,
            "success_rate": successful / total,
            "avg_execution_time": avg_time,
        }


# Pyodide HTML template for browser execution
PYODIDE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Pyodide Python Runner</title>
    <script src="https://cdn.jsdelivr.net/pyodide/v0.25.0/full/pyodide.js"></script>
</head>
<body>
    <textarea id="code" rows="20" cols="80"></textarea>
    <button onclick="runCode()">Run</button>
    <pre id="output"></pre>
    <script>
        let pyodide = null;
        
        async function initPyodide() {
            pyodide = await loadPyodide({
                indexURL: "https://cdn.jsdelivr.net/pyodide/v0.25.0/full/"
            });
        }
        
        async function runCode() {
            const code = document.getElementById('code').value;
            const output = document.getElementById('output');
            output.textContent = "Running...";
            
            try {
                if (!window.pyodide) {
                    await initPyodide();
                }
                
                // Capture output
                const originalStdout = sys.stdout;
                const originalStderr = sys.stderr;
                
                let stdout = "", stderr = "";
                sys.stdout = { write: (text) => stdout += text };
                sys.stderr = { write: (text) => stderr += text };
                
                await pyodide.runPythonAsync(code);
                
                output.textContent = stdout + (stderr ? "\\nSTDERR:\\n" + stderr : "");
                
                sys.stdout = originalStdout;
                sys.stderr = originalStderr;
            } catch (err) {
                output.textContent = "Error: " + err.toString();
            }
        }
        
        // Auto-init
        initPyodide().then(() => console.log("Pyodide ready"));
    </script>
</body>
</html>
"""