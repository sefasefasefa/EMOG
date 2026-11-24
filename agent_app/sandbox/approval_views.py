"""
Approval Gates API Views
REST endpoints for managing approval gates for destructive actions.
"""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .approval_gates import (
    get_approval_gate, ApprovalGate, ActionType, ApprovalStatus, ApprovalRequest
)


@csrf_exempt
@require_http_methods(["POST"])
def approval_request(request):
    """
    Request approval for a destructive action.
    
    POST /sandbox/approval/request/
    {
        "action_type": "delete_file",
        "description": "Delete user data file",
        "details": {"path": "/workspace/user_data.txt"},
        "requested_by": "user123",
        "ttl_hours": 24
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    # Required fields
    action_type_str = data.get("action_type", "").strip()
    description = data.get("description", "").strip()
    details = data.get("details", {})
    requested_by = data.get("requested_by", "").strip()
    ttl_hours = data.get("ttl_hours")
    metadata = data.get("metadata", {})
    
    if not action_type_str:
        return JsonResponse({"ok": False, "error": "action_type required"}, status=400)
    if not description:
        return JsonResponse({"ok": False, "error": "description required"}, status=400)
    if not requested_by:
        return JsonResponse({"ok": False, "error": "requested_by required"}, status=400)
    
    try:
        action_type = ActionType(action_type_str)
    except ValueError:
        return JsonResponse({"ok": False, "error": f"Invalid action_type: {action_type_str}"}, status=400)
    
    if ttl_hours is not None:
        try:
            ttl_hours = int(ttl_hours)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "ttl_hours must be integer"}, status=400)
    
    gate = get_approval_gate()
    
    try:
        request_obj = gate.request_approval(
            action_type=action_type,
            description=description,
            details=details,
            requested_by=requested_by,
            ttl_hours=ttl_hours,
            metadata=metadata,
        )
        
        return JsonResponse({
            "ok": True,
            "approval": request_obj.to_dict(),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Failed to create approval request: {str(e)}"}, status=500)


@require_http_methods(["GET"])
def approval_status(request, request_id):
    """
    Get status of an approval request.
    
    GET /sandbox/approval/<request_id>/
    """
    gate = get_approval_gate()
    request_obj = gate.check_approval(request_id)
    
    if not request_obj:
        return JsonResponse({"ok": False, "error": "Approval request not found"}, status=404)
    
    return JsonResponse({
        "ok": True,
        "approval": request_obj.to_dict(),
    })


@csrf_exempt
@require_http_methods(["POST"])
def approval_approve(request, request_id):
    """
    Approve a pending approval request.
    
    POST /sandbox/approval/<request_id>/approve/
    {
        "approved_by": "user123"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    approved_by = data.get("approved_by", "").strip()
    if not approved_by:
        return JsonResponse({"ok": False, "error": "approved_by required"}, status=400)
    
    gate = get_approval_gate()
    
    success = gate.approve(request_id, approved_by)
    
    if success:
        request_obj = gate.check_approval(request_id)
        return JsonResponse({
            "ok": True,
            "message": "Approval granted",
            "approval": request_obj.to_dict() if request_obj else None,
        })
    else:
        return JsonResponse({"ok": False, "error": "Failed to approve - request not found, not pending, or expired"}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def approval_deny(request, request_id):
    """
    Deny a pending approval request.
    
    POST /sandbox/approval/<request_id>/deny/
    {
        "denied_by": "user123",
        "reason": "Not authorized"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    denied_by = data.get("denied_by", "").strip()
    reason = data.get("reason", "").strip()
    
    if not denied_by:
        return JsonResponse({"ok": False, "error": "denied_by required"}, status=400)
    
    gate = get_approval_gate()
    
    success = gate.deny(request_id, denied_by, reason)
    
    if success:
        request_obj = gate.check_approval(request_id)
        return JsonResponse({
            "ok": True,
            "message": "Approval denied",
            "approval": request_obj.to_dict() if request_obj else None,
        })
    else:
        return JsonResponse({"ok": False, "error": "Failed to deny - request not found, not pending, or expired"}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def approval_cancel(request, request_id):
    """
    Cancel a pending approval request.
    
    POST /sandbox/approval/<request_id>/cancel/
    {
        "cancelled_by": "user123"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    cancelled_by = data.get("cancelled_by", "").strip()
    if not cancelled_by:
        return JsonResponse({"ok": False, "error": "cancelled_by required"}, status=400)
    
    gate = get_approval_gate()
    
    success = gate.cancel(request_id, cancelled_by)
    
    if success:
        request_obj = gate.check_approval(request_id)
        return JsonResponse({
            "ok": True,
            "message": "Approval cancelled",
            "approval": request_obj.to_dict() if request_obj else None,
        })
    else:
        return JsonResponse({"ok": False, "error": "Failed to cancel - request not found or not pending"}, status=400)


@require_http_methods(["GET"])
def approval_list(request):
    """
    List approval requests.
    
    GET /sandbox/approval/
    Query params: status (optional), user_id (optional)
    """
    status_str = request.GET.get("status")
    user_id = request.GET.get("user_id")
    
    status_filter = None
    if status_str:
        try:
            status_filter = ApprovalStatus(status_str)
        except ValueError:
            return JsonResponse({"ok": False, "error": f"Invalid status: {status_str}"}, status=400)
    
    gate = get_approval_gate()
    approvals = gate.get_pending_approvals(user_id)
    
    if status_filter:
        approvals = [a for a in approvals if a.status == status_filter]
    
    return JsonResponse({
        "ok": True,
        "approvals": [a.to_dict() for a in approvals],
    })


@csrf_exempt
@require_http_methods(["POST"])
def approval_cleanup(request):
    """
    Clean up expired approval requests.
    
    POST /sandbox/approval/cleanup/
    """
    gate = get_approval_gate()
    gate.cleanup_expired()
    return JsonResponse({"ok": True, "message": "Cleanup completed"})


@csrf_exempt
@require_http_methods(["POST"])
def approval_execute(request):
    """
    Execute an action with approval gate.
    
    POST /sandbox/approval/execute/
    {
        "action_type": "delete_file",
        "description": "Delete user data file",
        "details": {"path": "/workspace/user_data.txt"},
        "requested_by": "user123",
        "ttl_hours": 24
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    action_type_str = data.get("action_type", "").strip()
    description = data.get("description", "").strip()
    details = data.get("details", {})
    requested_by = data.get("requested_by", "").strip()
    ttl_hours = data.get("ttl_hours")
    metadata = data.get("metadata", {})
    
    if not action_type_str:
        return JsonResponse({"ok": False, "error": "action_type required"}, status=400)
    if not description:
        return JsonResponse({"ok": False, "error": "description required"}, status=400)
    if not requested_by:
        return JsonResponse({"ok": False, "error": "requested_by required"}, status=400)
    
    try:
        action_type = ActionType(action_type_str)
    except ValueError:
        return JsonResponse({"ok": False, "error": f"Invalid action_type: {action_type_str}"}, status=400)
    
    if ttl_hours is not None:
        try:
            ttl_hours = int(ttl_hours)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "ttl_hours must be integer"}, status=400)
    
    gate = get_approval_gate()
    
    # For this example, we'll use a simple action handler
    def action_handler(details):
        # In real implementation, this would execute the actual action
        # For now, just return success
        return {"message": f"Action executed: {details}"}
    
    result = gate.execute_with_approval(
        action_type=action_type,
        description=description,
        details=details,
        requested_by=requested_by,
        action_handler=action_handler,
        ttl_hours=ttl_hours,
        metadata=metadata,
    )
    
    return JsonResponse(result)