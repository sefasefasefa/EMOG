"""
Project-Sandbox Integration Layer
Connects the sandbox execution service with project terminals and file operations.
"""
import os
import json
import threading
from typing import Dict, Any, Optional, List
from pathlib import Path
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .docker_sandbox import get_sandbox, SandboxExecution, ResourceLimits, SandboxStatus
from .db_isolation import get_db_isolation


class ProjectSandboxIntegration:
    """
    Integrates project operations with sandbox execution.
    Provides safe execution environment for project commands.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._project_sessions: Dict[str, Dict[str, Any]] = {}
    
    def get_project_sandbox(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get or create sandbox session for a project"""
        with self._lock:
            if project_id not in self._project_sessions:
                self._project_sessions[project_id] = {
                    'project_id': project_id,
                    'sandbox': None,
                    'working_dir': None,
                    'active_executions': {},
                }
            return self._project_sessions[project_id]
    
    def execute_in_project_sandbox(self, 
                                   project_id: str,
                                   command: List[str],
                                   working_dir: str = None,
                                   environment: Dict[str, str] = None,
                                   stdin_data: str = None,
                                   resource_limits: ResourceLimits = None) -> Dict[str, Any]:
        """
        Execute a command in the project's sandbox environment.
        """
        from .projects.views import PROJECTS, PROJECTS_LOCK
        from pathlib import Path
        
        # Get project info
        with PROJECTS_LOCK:
            project = PROJECTS.get(project_id)
        
        if not project:
            return {"ok": False, "error": "Project not found"}
        
        # Determine working directory
        work_dir = working_dir or project.get('path', '/workspace')
        work_dir_path = Path(work_dir)
        if not work_dir_path.is_absolute():
            work_dir_path = Path(project['path']) / work_dir
        
        if not work_dir_path.exists():
            work_dir_path = Path(project['path'])
        
        # Prepare sandbox execution
        sandbox = get_sandbox()
        
        execution = SandboxExecution(
            id="",
            image="python",  # Default, could be configured per project
            command=command,
            environment=environment or {},
            working_dir=str(work_dir_path),
            resource_limits=resource_limits or ResourceLimits(),
            stdin_data=stdin_data,
        )
        
        # Set up volume mapping for project files
        execution.volumes = {
            str(Path(project['path'])): {"bind": "/workspace", "mode": "rw"},
        }
        
        # Add any additional volumes
        if project.get('extra_volumes'):
            execution.volumes.update(project['extra_volumes'])
        
        # Execute in sandbox
        result = sandbox.run(execution)
        
        return {
            "ok": True,
            "execution": {
                "id": result.id,
                "status": result.status.value,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "started_at": result.started_at.isoformat() if result.started_at else None,
                "completed_at": result.completed_at.isoformat() if result.completed_at else None,
                "error": result.error,
                "stats": result.stats,
            }
        }
    
    def execute_project_command(self, 
                                project_id: str,
                                command: str,
                                cwd: str = None,
                                environment: Dict[str, str] = None) -> Dict[str, Any]:
        """
        Execute a shell command in the project's sandbox.
        """
        return self.execute_in_project_sandbox(
            project_id=project_id,
            command=["sh", "-c", command],
            working_dir=cwd,
            environment=environment,
        )


# Global instance
_project_sandbox_integration: Optional[ProjectSandboxIntegration] = None
_integration_lock = threading.Lock()


def get_project_sandbox_integration() -> ProjectSandboxIntegration:
    global _project_sandbox_integration
    with _integration_lock:
        if _project_sandbox_integration is None:
            _project_sandbox_integration = ProjectSandboxIntegration()
        return _project_sandbox_integration


# API Views for project-sandbox integration
@csrf_exempt
@require_http_methods(["POST"])
def project_sandbox_execute(request, project_id):
    """
    Execute command in project's sandbox.
    
    POST /projects/<project_id>/sandbox/execute/
    {
        "command": ["python", "script.py"],
        "cwd": "/workspace/src",
        "environment": {"VAR": "value"},
        "stdin": "input",
        "resource_limits": {
            "memory_limit": "256m",
            "timeout_seconds": 60
        }
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    command = data.get("command", [])
    if not command:
        return JsonResponse({"ok": False, "error": "Command required"}, status=400)
    
    cwd = data.get("cwd")
    environment = data.get("environment", {})
    stdin_data = data.get("stdin")
    
    limits_data = data.get("resource_limits", {})
    resource_limits = ResourceLimits(
        memory_limit=limits_data.get("memory_limit", "256m"),
        timeout_seconds=limits_data.get("timeout_seconds", 60),
    )
    
    integration = get_project_sandbox_integration()
    
    try:
        result = integration.execute_in_project_sandbox(
            project_id=project_id,
            command=command,
            working_dir=cwd,
            environment=environment,
            stdin_data=stdin_data,
            resource_limits=resource_limits,
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def project_sandbox_shell(request, project_id):
    """
    Execute shell command in project's sandbox.
    
    POST /projects/<project_id>/sandbox/shell/
    {
        "command": "python script.py",
        "cwd": "/workspace/src"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    command = data.get("command", "").strip()
    if not command:
        return JsonResponse({"ok": False, "error": "Command required"}, status=400)
    
    cwd = data.get("cwd")
    environment = data.get("environment", {})
    
    integration = get_project_sandbox_integration()
    
    try:
        result = integration.execute_project_command(
            project_id=project_id,
            command=command,
            cwd=cwd,
            environment=environment,
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def project_sandbox_file_execute(request, project_id):
    """
    Execute a file in project's sandbox.
    
    POST /projects/<project_id>/sandbox/file-execute/
    {
        "file_path": "script.py",
        "args": ["arg1", "arg2"],
        "stdin": "input"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    file_path = data.get("file_path", "").strip()
    args = data.get("args", [])
    stdin_data = data.get("stdin")
    
    if not file_path:
        return JsonResponse({"ok": False, "error": "file_path required"}, status=400)
    
    # Determine interpreter from file extension
    from pathlib import Path
    ext = Path(file_path).suffix.lower()
    interpreter_map = {
        '.py': 'python3',
        '.js': 'node',
        '.ts': 'ts-node',
        '.sh': 'bash',
        '.rb': 'ruby',
        '.pl': 'perl',
        '.php': 'php',
    }
    interpreter = interpreter_map.get(ext, 'python3')
    
    # Build command
    if args:
        command = [interpreter, file_path] + args
    else:
        command = [interpreter, file_path]
    
    integration = get_project_sandbox_integration()
    
    try:
        result = integration.execute_in_project_sandbox(
            project_id=project_id,
            command=command,
            stdin_data=stdin_data,
        )
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


# Database integration views for projects
@csrf_exempt
@require_http_methods(["POST"])
def project_db_query(request, project_id):
    """
    Execute database query in project context.
    
    POST /projects/<project_id>/db/query/
    {
        "query": "SELECT * FROM users WHERE id = ?",
        "params": [1],
        "allow_write": false
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    query = data.get("query", "").strip()
    params = data.get("params", [])
    allow_write = data.get("allow_write", False)
    
    if not query:
        return JsonResponse({"ok": False, "error": "Query required"}, status=400)
    
    isolation = get_db_isolation()
    
    try:
        result = isolation.execute_safe(query, tuple(params), allow_write=allow_write)
        return JsonResponse({
            "ok": True,
            "result": {
                "columns": result.columns,
                "rows": result.rows,
                "rowcount": result.rowcount,
                "execution_time_ms": result.execution_time_ms,
            }
        })
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Query failed: {str(e)}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def project_db_transaction(request, project_id):
    """
    Execute multiple queries in a transaction.
    
    POST /projects/<project_id>/db/transaction/
    {
        "queries": [
            ["INSERT INTO users (name) VALUES (?)", ["John"]],
            ["SELECT * FROM users WHERE name = ?", ["John"]]
        ],
        "read_only": false
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    queries = data.get("queries", [])
    read_only = data.get("read_only", False)
    
    if not queries:
        return JsonResponse({"ok": False, "error": "Queries required"}, status=400)
    
    isolation = get_db_isolation()
    
    try:
        query_tuples = [(q[0], tuple(q[1])) if isinstance(q, list) and len(q) == 2 
                       else (q, ()) for q in queries]
        results = isolation.execute_in_transaction(query_tuples, read_only=read_only)
        
        return JsonResponse({
            "ok": True,
            "results": [
                {
                    "columns": r.columns,
                    "rows": r.rows,
                    "rowcount": r.rowcount,
                }
                for r in results
            ]
        })
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Transaction failed: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def project_db_schema(request, project_id):
    """
    Get database schema for project.
    
    GET /projects/<project_id>/db/schema/
    """
    isolation = get_db_isolation()
    
    try:
        schema = isolation.get_schema()
        return JsonResponse({"ok": True, "schema": schema})
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Schema retrieval failed: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def project_db_tables(request, project_id):
    """
    List all tables in project database.
    
    GET /projects/<project_id>/db/tables/
    """
    isolation = get_db_isolation()
    
    try:
        schema = isolation.get_schema()
        tables = [
            {
                "name": name,
                "columns": info["columns"],
                "create_sql": info["create_sql"]
            }
            for name, info in schema.items()
        ]
        return JsonResponse({"ok": True, "tables": tables})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)