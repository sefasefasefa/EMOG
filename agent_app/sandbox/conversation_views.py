"""
Conversation Time Travel API Views
REST endpoints for Git-like conversation history with branching and rollback.
"""
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .conversation_time_travel import (
    get_conversation_time_travel, ConversationTimeTravel, 
    Conversation, ConversationNode, ConversationBranch, NodeType
)


@csrf_exempt
@require_http_methods(["POST"])
def conversation_create(request):
    """
    Create a new conversation with Git-like history.
    
    POST /sandbox/conversations/
    {
        "title": "My Project",
        "user_id": "user123",
        "metadata": {"project": "my-project"}
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    title = data.get("title", "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title required"}, status=400)
    
    user_id = data.get("user_id")
    metadata = data.get("metadata", {})
    
    try:
        time_travel = get_conversation_time_travel()
        conversation = time_travel.create_conversation(
            title=title,
            user_id=user_id,
            metadata=metadata
        )
        
        return JsonResponse({
            "ok": True,
            "conversation": conversation.to_dict(),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_http_methods(["GET"])
def conversation_list(request):
    """
    List all conversations.
    
    GET /sandbox/conversations/
    """
    time_travel = get_conversation_time_travel()
    
    # Get all conversations from database
    conn = sqlite3.connect(time_travel.db_path, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, user_id, created_at, updated_at FROM conversations ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    conversations = []
    for row in rows:
        conversations.append({
            "id": row[0],
            "title": row[1],
            "user_id": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        })
    
    return JsonResponse({
        "ok": True,
        "conversations": conversations,
    })


@require_http_methods(["GET"])
def conversation_detail(request, conversation_id):
    """
    Get conversation details with full tree structure.
    
    GET /sandbox/conversations/<conversation_id>/
    """
    time_travel = get_conversation_time_travel()
    conversation = time_travel.get_conversation(conversation_id)
    
    if not conversation:
        return JsonResponse({"ok": False, "error": "Conversation not found"}, status=404)
    
    # Get branches
    conn = sqlite3.connect(time_travel.db_path, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM branches WHERE conversation_id = ?", (conversation_id,))
    branches = [ConversationBranch(
        id=row[0], conversation_id=row[1], name=row[2],
        head_node_id=row[3], base_node_id=row[4],
        created_at=row[5], updated_at=row[6],
        metadata=json.loads(row[7]) if row[7] else {}
    ) for row in cursor.fetchall()]
    conn.close()
    
    return JsonResponse({
        "ok": True,
        "conversation": conversation.to_dict(),
        "branches": [b.to_dict() for b in branches],
    })


@csrf_exempt
@require_http_methods(["POST"])
def conversation_add_message(request, conversation_id):
    """
    Add a message to the conversation (creates a new node in the tree).
    
    POST /sandbox/conversations/<conversation_id>/messages/
    {
        "content": "Hello, how are you?",
        "role": "user",
        "parent_node_id": "optional-parent-node-id",
        "node_type": "user_message",
        "model": "gpt-4",
        "tokens_used": 100
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    content = data.get("content", "").strip()
    role = data.get("role", "user")
    parent_node_id = data.get("parent_node_id")
    node_type_str = data.get("node_type", "user_message")
    model = data.get("model")
    tokens_used = data.get("tokens_used", 0)
    metadata = data.get("metadata", {})
    
    if not content:
        return JsonResponse({"ok": False, "error": "Content required"}, status=400)
    
    try:
        node_type = NodeType(node_type_str)
    except ValueError:
        return JsonResponse({"ok": False, "error": f"Invalid node_type: {node_type_str}"}, status=400)
    
    try:
        time_travel = get_conversation_time_travel()
        node = time_travel.add_message(
            conversation_id=conversation_id,
            content=content,
            role=role,
            parent_node_id=parent_node_id,
            node_type=node_type,
            model=model,
            tokens_used=tokens_used,
            metadata=metadata,
        )
        
        return JsonResponse({
            "ok": True,
            "node": node.to_dict() if hasattr(node, 'to_dict') else node.__dict__,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_http_methods(["GET"])
def conversation_history(request, conversation_id):
    """
    Get conversation history for current branch.
    
    GET /sandbox/conversations/<conversation_id>/history/
    Query params: branch (optional)
    """
    branch_name = request.GET.get("branch")
    
    time_travel = get_conversation_time_travel()
    history = time_travel.get_conversation_history(conversation_id, branch_name)
    
    return JsonResponse({
        "ok": True,
        "history": [n.to_dict() if hasattr(n, 'to_dict') else n.__dict__ for n in history],
    })


@require_http_methods(["GET"])
def conversation_node_path(request, node_id):
    """
    Get path from root to a specific node (for context reconstruction).
    
    GET /sandbox/nodes/<node_id>/path/
    """
    time_travel = get_conversation_time_travel()
    path = time_travel.get_path_to_root(node_id)
    
    return JsonResponse({
        "ok": True,
        "path": [n.to_dict() if hasattr(n, 'to_dict') else n.__dict__ for n in path],
    })


@csrf_exempt
@require_http_methods(["POST"])
def conversation_branch_create(request, conversation_id):
    """
    Create a new branch from a specific node.
    
    POST /sandbox/conversations/<conversation_id>/branches/
    {
        "name": "feature-branch",
        "base_node_id": "optional-node-id",
        "metadata": {}
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    name = data.get("name", "").strip()
    base_node_id = data.get("base_node_id")
    metadata = data.get("metadata", {})
    
    if not name:
        return JsonResponse({"ok": False, "error": "Branch name required"}, status=400)
    
    try:
        time_travel = get_conversation_time_travel()
        branch = time_travel.create_branch(
            conversation_id=conversation_id,
            branch_name=name,
            base_node_id=base_node_id,
            metadata=metadata,
        )
        
        return JsonResponse({
            "ok": True,
            "branch": branch.to_dict(),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def conversation_branch_checkout(request, conversation_id):
    """
    Switch to a different branch.
    
    POST /sandbox/conversations/<conversation_id>/branches/checkout/
    {
        "branch_id": "branch-uuid"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    branch_id = data.get("branch_id")
    if not branch_id:
        return JsonResponse({"ok": False, "error": "branch_id required"}, status=400)
    
    try:
        time_travel = get_conversation_time_travel()
        success = time_travel.checkout_branch(conversation_id, branch_id)
        
        if success:
            return JsonResponse({"ok": True, "message": "Branch switched successfully"})
        else:
            return JsonResponse({"ok": False, "error": "Branch not found or invalid"}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def conversation_branch_rollback(request, conversation_id):
    """
    Rollback branch to a specific node (time travel).
    
    POST /sandbox/conversations/<conversation_id>/branches/rollback/
    {
        "branch_id": "branch-uuid",
        "target_node_id": "node-uuid"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    branch_id = data.get("branch_id")
    target_node_id = data.get("target_node_id")
    
    if not branch_id or not target_node_id:
        return JsonResponse({"ok": False, "error": "branch_id and target_node_id required"}, status=400)
    
    try:
        time_travel = get_conversation_time_travel()
        success = time_travel.rollback_to_node(conversation_id, branch_id, target_node_id)
        
        if success:
            return JsonResponse({"ok": True, "message": "Rollback completed"})
        else:
            return JsonResponse({"ok": False, "error": "Rollback failed - invalid node or branch"}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_http_methods(["GET"])
def conversation_branch_history(request, conversation_id, branch_id):
    """
    Get branch history (audit log).
    
    GET /sandbox/conversations/<conversation_id>/branches/<branch_id>/history/
    """
    time_travel = get_conversation_time_travel()
    history = time_travel.get_branch_history(branch_id)
    
    return JsonResponse({
        "ok": True,
        "history": history,
    })


@csrf_exempt
@require_http_methods(["POST"])
def conversation_branch_merge(request, conversation_id):
    """
    Merge one branch into another.
    
    POST /sandbox/conversations/<conversation_id>/branches/merge/
    {
        "source_branch_id": "source-uuid",
        "target_branch_id": "target-uuid",
        "strategy": "fast_forward"
    }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    source_branch_id = data.get("source_branch_id")
    target_branch_id = data.get("target_branch_id")
    strategy = data.get("strategy", "fast_forward")
    
    if not source_branch_id or not target_branch_id:
        return JsonResponse({"ok": False, "error": "source_branch_id and target_branch_id required"}, status=400)
    
    try:
        time_travel = get_conversation_time_travel()
        success, message = time_travel.merge_branch(
            conversation_id=conversation_id,
            source_branch_id=source_branch_id,
            target_branch_id=target_branch_id,
            merge_strategy=strategy,
        )
        
        return JsonResponse({
            "ok": success,
            "message": message,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)