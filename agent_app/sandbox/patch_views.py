"""
Security Patch Pipeline API Views
REST endpoints for managing security patches.
"""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .security_pipeline import (
    get_patch_pipeline, SecurityPatch, PatchType, PatchStatus, TestResult
)


@csrf_exempt
@require_http_methods(["POST"])
def patch_submit(request):
    """
    Submit a new security patch for testing.
    
    POST /sandbox/patches/
    {
        "name": "Fix SQL injection in user search",
        "description": "Sanitize user input in search query",
        "patch_type": "security",
        "target_files": ["app/views.py", "app/models.py"],
        "diff_content": "diff --git a/app/views.py...",
        "test_commands": ["python -m pytest tests/test_search.py -v"],
        "rollback_commands": ["git checkout HEAD -- app/views.py app/models.py"]
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    # Required fields
    name = data.get("name", "").strip()
    description = data.get("description", "").strip()
    patch_type = data.get("patch_type", "security")
    target_files = data.get("target_files", [])
    diff_content = data.get("diff_content", "")
    test_commands = data.get("test_commands", [])
    rollback_commands = data.get("rollback_commands", [])
    
    if not name or not diff_content:
        return JsonResponse({"ok": False, "error": "Name and diff_content required"}, status=400)
    
    if not target_files:
        return JsonResponse({"ok": False, "error": "Target files required"}, status=400)
    
    try:
        patch_type_enum = PatchType(patch_type)
    except ValueError:
        return JsonResponse({"ok": False, "error": f"Invalid patch_type: {patch_type}"}, status=400)
    
    pipeline = get_patch_pipeline()
    
    try:
        patch = SecurityPatch(
            id="",
            name=name,
            description=description,
            patch_type=patch_type_enum,
            target_files=target_files,
            diff_content=diff_content,
            test_commands=test_commands,
            rollback_commands=rollback_commands,
        )
        
        patch_id = pipeline.submit_patch(patch)
        
        return JsonResponse({
            "ok": True,
            "patch_id": patch_id,
            "status": "submitted",
        })
        
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Failed to submit patch: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def patch_status(request, patch_id):
    """
    Get patch status.
    
    GET /sandbox/patches/<patch_id>/
    """
    pipeline = get_patch_pipeline()
    patch = pipeline.get_patch_status(patch_id)
    
    if not patch:
        return JsonResponse({"ok": False, "error": "Patch not found"}, status=404)
    
    return JsonResponse({
        "ok": True,
        "patch": {
            "id": patch.id,
            "name": patch.name,
            "description": patch.description,
            "patch_type": patch.patch_type.value,
            "target_files": patch.target_files,
            "status": patch.status.value,
            "created_at": patch.created_at,
            "tested_at": patch.tested_at,
            "deployed_at": patch.deployed_at,
            "test_results": patch.test_results,
        }
    })


@require_http_methods(["GET"])
def patch_test_results(request, patch_id):
    """
    Get test results for a patch.
    
    GET /sandbox/patches/<patch_id>/results/
    """
    pipeline = get_patch_pipeline()
    results = pipeline.get_test_results(patch_id)
    
    return JsonResponse({
        "ok": True,
        "results": [
            {
                "patch_id": r.patch_id,
                "success": r.success,
                "tests_run": r.tests_run,
                "tests_passed": r.tests_passed,
                "tests_failed": r.tests_failed,
                "output": r.output,
                "error": r.error,
                "execution_time_ms": r.execution_time_ms,
                "timestamp": r.timestamp,
            }
            for r in results
        ]
    })


@csrf_exempt
@require_http_methods(["POST"])
def patch_deploy(request, patch_id):
    """
    Deploy a tested patch.
    
    POST /sandbox/patches/<patch_id>/deploy/
    """
    pipeline = get_patch_pipeline()
    
    success = pipeline.deploy_patch(patch_id)
    
    if success:
        return JsonResponse({"ok": True, "message": "Patch deployed successfully"})
    else:
        return JsonResponse({"ok": False, "error": "Deployment failed or patch not ready"}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def patch_rollback(request, patch_id):
    """
    Rollback a deployed patch.
    
    POST /sandbox/patches/<patch_id>/rollback/
    """
    pipeline = get_patch_pipeline()
    
    success = pipeline.rollback_patch(patch_id)
    
    if success:
        return JsonResponse({"ok": True, "message": "Patch rolled back successfully"})
    else:
        return JsonResponse({"ok": False, "error": "Rollback failed"}, status=400)


@require_http_methods(["GET"])
def patch_list(request):
    """
    List all patches.
    
    GET /sandbox/patches/
    Query params: status (optional)
    """
    status_str = request.GET.get("status")
    status = None
    if status_str:
        try:
            status = PatchStatus(status_str)
        except ValueError:
            return JsonResponse({"ok": False, "error": f"Invalid status: {status_str}"}, status=400)
    
    pipeline = get_patch_pipeline()
    patches = pipeline.list_patches(status)
    
    return JsonResponse({
        "ok": True,
        "patches": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "patch_type": p.patch_type.value,
                "target_files": p.target_files,
                "status": p.status.value,
                "created_at": p.created_at,
                "tested_at": p.tested_at,
                "deployed_at": p.deployed_at,
            }
            for p in patches
        ]
    })