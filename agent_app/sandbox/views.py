"""
Sandbox Execution API Views
REST endpoints for secure code execution in isolated containers.
"""
import json
import uuid
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .docker_sandbox import get_sandbox, SandboxExecution, ResourceLimits, SandboxStatus
from ..permissions import require_auth


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def sandbox_execute(request):
    """
    Execute code in isolated sandbox.
    
    POST /sandbox/execute/
    {
        "image": "python",
        "command": ["python3", "-c", "print('hello')"],
        "environment": {"VAR": "value"},
        "stdin": "input data",
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
    
    # Required fields
    image = data.get("image", "python")
    command = data.get("command", [])
    
    if not command:
        return JsonResponse({"ok": False, "error": "Command required"}, status=400)
    
    # Optional fields
    environment = data.get("environment", {})
    stdin_data = data.get("stdin")
    working_dir = data.get("working_dir", "/workspace")
    
    # Resource limits
    limits_data = data.get("resource_limits", {})
    resource_limits = ResourceLimits(
        cpu_quota=limits_data.get("cpu_quota", 50000),
        cpu_period=limits_data.get("cpu_period", 100000),
        memory_limit=limits_data.get("memory_limit", "256m"),
        memory_swap=limits_data.get("memory_swap", "512m"),
        pids_limit=limits_data.get("pids_limit", 100),
        timeout_seconds=limits_data.get("timeout_seconds", 60),
    )
    
    # Volumes (host_path -> container_path with mode)
    volumes = {}
    for vol in data.get("volumes", []):
        host_path = vol.get("host_path")
        container_path = vol.get("container_path", "/workspace")
        mode = vol.get("mode", "rw")
        if host_path:
            volumes[host_path] = {"bind": container_path, "mode": mode}
    
    # Create sandbox
    sandbox = get_sandbox()
    
    try:
        execution = sandbox.prepare_execution(
            image=image,
            command=command,
            environment=environment,
            working_dir=working_dir,
            resource_limits=resource_limits,
            stdin_data=stdin_data,
        )
        
        # Override volumes if provided
        if volumes:
            execution.volumes = volumes
        
        # Run execution
        result = sandbox.run(execution)
        
        return JsonResponse({
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
        })
        
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Execution failed: {str(e)}"}, status=500)


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def sandbox_execute_async(request):
    """
    Start async sandbox execution.
    
    POST /sandbox/execute/async/
    Same payload as /sandbox/execute/
    Returns execution_id for polling.
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    image = data.get("image", "python")
    command = data.get("command", [])
    
    if not command:
        return JsonResponse({"ok": False, "error": "Command required"}, status=400)
    
    environment = data.get("environment", {})
    stdin_data = data.get("stdin")
    working_dir = data.get("working_dir", "/workspace")
    
    limits_data = data.get("resource_limits", {})
    resource_limits = ResourceLimits(
        cpu_quota=limits_data.get("cpu_quota", 50000),
        cpu_period=limits_data.get("cpu_period", 100000),
        memory_limit=limits_data.get("memory_limit", "256m"),
        memory_swap=limits_data.get("memory_swap", "512m"),
        pids_limit=limits_data.get("pids_limit", 100),
        timeout_seconds=limits_data.get("timeout_seconds", 60),
    )
    
    sandbox = get_sandbox()
    
    try:
        execution = sandbox.prepare_execution(
            image=image,
            command=command,
            environment=environment,
            working_dir=working_dir,
            resource_limits=resource_limits,
        )
        
        # Start async execution
        execution_id = sandbox.run_async(execution)
        
        return JsonResponse({
            "ok": True,
            "execution_id": execution_id,
            "status": "started"
        })
        
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Failed to start: {str(e)}"}, status=500)


@require_auth
@require_http_methods(["GET"])
def sandbox_status(request, execution_id):
    """
    Get status of async execution.
    
    GET /sandbox/status/<execution_id>/
    """
    sandbox = get_sandbox()
    execution = sandbox.get_execution(execution_id)
    
    if not execution:
        return JsonResponse({"ok": False, "error": "Execution not found"}, status=404)
    
    return JsonResponse({
        "ok": True,
        "execution": {
            "id": execution.id,
            "status": execution.status.value,
            "exit_code": execution.exit_code,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "error": execution.error,
            "stats": execution.stats,
        }
    })


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def sandbox_kill(request, execution_id):
    """
    Kill a running execution.
    
    POST /sandbox/kill/<execution_id>/
    """
    sandbox = get_sandbox()
    success = sandbox.kill_execution(execution_id)
    
    if success:
        return JsonResponse({"ok": True, "message": "Execution killed"})
    else:
        return JsonResponse({"ok": False, "error": "Execution not found or already completed"}, status=404)


@require_auth
@require_http_methods(["GET"])
def sandbox_list(request):
    """
    List all active executions.
    
    GET /sandbox/list/
    """
    sandbox = get_sandbox()
    executions = sandbox.list_executions()
    
    return JsonResponse({
        "ok": True,
        "executions": [
            {
                "id": e.id,
                "image": e.image,
                "command": e.command,
                "status": e.status.value,
                "started_at": e.started_at.isoformat() if e.started_at else None,
            }
            for e in executions
        ]
    })


@require_auth
@require_http_methods(["GET"])
def sandbox_images(request):
    """
    List allowed sandbox images.
    
    GET /sandbox/images/
    """
    sandbox = get_sandbox()
    
    return JsonResponse({
        "ok": True,
        "images": sandbox.ALLOWED_IMAGES,
        "docker_available": sandbox.is_available()
    })


@csrf_exempt
@require_auth
@require_http_methods(["POST"])
def sandbox_python(request):
    """
    Convenience endpoint for Python execution.
    
    POST /sandbox/python/
    {
        "code": "print('hello')",
        "stdin": "input"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    code = data.get("code", "")
    stdin_data = data.get("stdin")
    
    if not code:
        return JsonResponse({"ok": False, "error": "Code required"}, status=400)
    
    sandbox = get_sandbox()
    
    try:
        execution = sandbox.prepare_execution(
            image="python",
            command=["python3", "-c", code],
            stdin_data=stdin_data,
            resource_limits=ResourceLimits(timeout_seconds=30),
        )
        
        result = sandbox.run(execution)
        
        return JsonResponse({
            "ok": True,
            "result": {
                "status": result.status.value,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "execution_time_ms": (
                    (result.completed_at - result.started_at).total_seconds() * 1000
                    if result.started_at and result.completed_at else None
                ),
            }
        })
        
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)