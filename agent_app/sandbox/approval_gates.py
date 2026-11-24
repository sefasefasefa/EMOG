"""
Critical Approval Gates - Destructive Action Confirmation System
Requires explicit user confirmation before executing destructive actions.
"""
import os
import json
import time
import threading
import hashlib
import secrets
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path


class ActionType(Enum):
    """Types of actions that require approval"""
    DELETE_FILE = "delete_file"
    DELETE_DIRECTORY = "delete_directory"
    DELETE_PROJECT = "delete_project"
    DB_DROP_TABLE = "db_drop_table"
    DB_TRUNCATE_TABLE = "db_truncate_table"
    DB_DELETE_RECORDS = "db_delete_records"
    GIT_RESET_HARD = "git_reset_hard"
    GIT_FORCE_PUSH = "git_force_push"
    DOCKER_REMOVE = "docker_remove"
    SANDBOX_KILL = "sandbox_kill"
    PATCH_DEPLOY = "patch_deploy"
    PATCH_ROLLBACK = "patch_rollback"
    SYSTEM_COMMAND = "system_command"
    FILE_OVERWRITE = "file_overwrite"
    DB_EXECUTE_WRITE = "db_execute_write"
    TERMINAL_RM_RF = "terminal_rm_rf"


class ApprovalStatus(Enum):
    """Approval request status"""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class ApprovalRequest:
    """Approval request for a destructive action"""
    id: str
    action_type: ActionType
    description: str
    details: Dict[str, Any]
    requested_by: str
    requested_at: str
    expires_at: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    denied_by: Optional[str] = None
    denied_at: Optional[str] = None
    denial_reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action_type": self.action_type.value,
            "description": self.description,
            "details": self.details,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "denied_by": self.denied_by,
            "denied_at": self.denied_at,
            "denial_reason": self.denial_reason,
            "metadata": self.metadata,
        }


class ApprovalGate:
    """
    Manages approval gates for destructive actions.
    Requires explicit user confirmation before executing destructive actions.
    """
    
    # Actions that require approval (default policy)
    DEFAULT_APPROVAL_REQUIRED = {
        ActionType.DELETE_FILE: True,
        ActionType.DELETE_DIRECTORY: True,
        ActionType.DELETE_PROJECT: True,
        ActionType.DB_DROP_TABLE: True,
        ActionType.DB_TRUNCATE_TABLE: True,
        ActionType.DB_DELETE_RECORDS: True,
        ActionType.GIT_RESET_HARD: True,
        ActionType.GIT_FORCE_PUSH: True,
        ActionType.DOCKER_REMOVE: True,
        ActionType.SANDBOX_KILL: False,  # Allow killing sandbox
        ActionType.PATCH_DEPLOY: True,
        ActionType.PATCH_ROLLBACK: True,
        ActionType.SYSTEM_COMMAND: True,  # Always require approval for system commands
        ActionType.FILE_OVERWRITE: False,  # Allow overwrites by default
        ActionType.DB_EXECUTE_WRITE: True,
        ActionType.TERMINAL_RM_RF: True,
    }
    
    def __init__(self, approval_dir: str = None, default_ttl_hours: int = 24):
        self.approval_dir = Path(approval_dir or os.environ.get("APPROVAL_DIR", "./approvals"))
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl_hours = default_ttl_hours
        self._lock = threading.Lock()
        self._approvals: Dict[str, Any] = {}
        self._approval_policies: Dict[ActionType, bool] = self.DEFAULT_APPROVAL_REQUIRED.copy()
        self._approval_handlers: Dict[ActionType, Callable] = {}
        self._init_directories()
    
    def _init_directories(self):
        """Initialize approval directories"""
        for subdir in ["pending", "approved", "denied", "expired"]:
            (self.approval_dir / subdir).mkdir(parents=True, exist_ok=True)
    
    def is_approval_required(self, action_type: ActionType) -> bool:
        """Check if approval is required for an action type"""
        return self._approval_policies.get(action_type, True)
    
    def set_approval_policy(self, action_type: ActionType, required: bool):
        """Set approval policy for an action type"""
        with self._lock:
            self._approval_policies[action_type] = required
    
    def request_approval(self,
                        action_type: ActionType,
                        description: str,
                        details: Dict[str, Any],
                        requested_by: str,
                        ttl_hours: int = None,
                        metadata: Dict = None) -> ApprovalRequest:
        """
        Request approval for a destructive action.
        
        Returns:
            ApprovalRequest object with the approval details
        """
        if not self.is_approval_required(action_type):
            # Auto-approve if not required
            return ApprovalRequest(
                id=str(uuid.uuid4()),
                action_type=action_type,
                description=description,
                details=details,
                requested_by=requested_by,
                requested_at=datetime.utcnow().isoformat(),
                expires_at=(datetime.utcnow() + timedelta(hours=ttl_hours or self.default_ttl_hours)).isoformat(),
                status=ApprovalStatus.APPROVED,
                approved_by=requested_by,
                approved_at=datetime.utcnow().isoformat(),
                metadata=metadata or {},
            )
        
        # Create approval request
        ttl = ttl_hours or self.default_ttl_hours
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            action_type=action_type,
            description=description,
            details=details,
            requested_by=requested_by,
            requested_at=datetime.utcnow().isoformat(),
            expires_at=(datetime.utcnow() + timedelta(hours=ttl)).isoformat(),
            status=ApprovalStatus.PENDING,
            metadata=metadata or {},
        )
        
        with self._lock:
            self._approvals[request.id] = request
            self._save_request(request)
        
        return request
    
    def _save_request(self, request: ApprovalRequest):
        """Save approval request to disk"""
        status_dir = self.approval_dir / request.status.value
        status_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = status_dir / f"{request.id}.json"
        with open(file_path, "w") as f:
            json.dump(request.to_dict(), f, indent=2)
    
    def _load_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Load approval request from disk"""
        for subdir in ["pending", "approved", "denied", "expired"]:
            file_path = self.approval_dir / subdir / f"{request_id}.json"
            if file_path.exists():
                try:
                    with open(file_path, "r") as f:
                        data = json.load(f)
                    return ApprovalRequest(**data)
                except Exception:
                    continue
        return None
    
    def approve(self, request_id: str, approved_by: str) -> bool:
        """Approve a pending request"""
        with self._lock:
            request = self._approvals.get(request_id) or self._load_request(request_id)
            if not request:
                return False
            
            if request.status != ApprovalStatus.PENDING:
                return False
            
            if datetime.fromisoformat(request.expires_at) < datetime.utcnow():
                request.status = ApprovalStatus.EXPIRED
                self._save_request(request)
                return False
            
            request.status = ApprovalStatus.APPROVED
            request.approved_by = approved_by
            request.approved_at = datetime.utcnow().isoformat()
            self._save_request(request)
            self._approvals[request_id] = request
            return True
    
    def deny(self, request_id: str, denied_by: str, reason: str = "") -> bool:
        """Deny a pending request"""
        with self._lock:
            request = self._approvals.get(request_id) or self._load_request(request_id)
            if not request:
                return False
            
            if request.status != ApprovalStatus.PENDING:
                return False
            
            request.status = ApprovalStatus.DENIED
            request.denied_by = denied_by
            request.denied_at = datetime.utcnow().isoformat()
            request.denial_reason = reason
            self._save_request(request)
            self._approvals[request_id] = request
            return True
    
    def cancel(self, request_id: str, cancelled_by: str) -> bool:
        """Cancel a pending request"""
        with self._lock:
            request = self._approvals.get(request_id) or self._load_request(request_id)
            if not request:
                return False
            
            if request.status != ApprovalStatus.PENDING:
                return False
            
            request.status = ApprovalStatus.CANCELLED
            request.denied_by = cancelled_by
            request.denied_at = datetime.utcnow().isoformat()
            request.denial_reason = "Cancelled by user"
            self._save_request(request)
            self._approvals[request_id] = request
            return True
    
    def check_approval(self, request_id: str) -> Optional[ApprovalRequest]:
        """Check the status of an approval request"""
        with self._lock:
            request = self._approvals.get(request_id) or self._load_request(request_id)
            if not request:
                return None
            
            # Check expiration
            if request.status == ApprovalStatus.PENDING:
                if datetime.fromisoformat(request.expires_at) < datetime.utcnow():
                    request.status = ApprovalStatus.EXPIRED
                    self._save_request(request)
            
            return request
    
    def wait_for_approval(self, request_id: str, timeout_seconds: int = 300, poll_interval: int = 5) -> Optional[ApprovalRequest]:
        """Wait for approval decision with timeout"""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            request = self.check_approval(request_id)
            if request and request.status != ApprovalStatus.PENDING:
                return request
            time.sleep(poll_interval)
        
        # Timeout - check final status
        return self.check_approval(request_id)
    
    def register_action_handler(self, action_type: ActionType, handler: Callable):
        """Register a handler for an approved action"""
        with self._lock:
            self._approval_handlers[action_type] = handler
    
    def execute_with_approval(self,
                             action_type: ActionType,
                             description: str,
                             details: Dict[str, Any],
                             requested_by: str,
                             action_handler: Callable,
                             ttl_hours: int = None,
                             metadata: Dict = None) -> Dict[str, Any]:
        """
        Execute an action with approval gate.
        
        Returns:
            Dict with result or approval request info
        """
        # Check if approval is required
        if not self.is_approval_required(action_type):
            # Execute directly
            try:
                result = action_handler(details)
                return {
                    "ok": True,
                    "auto_approved": True,
                    "result": result,
                }
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"Execution failed: {str(e)}",
                }
        
        # Request approval
        request = self.request_approval(
            action_type=action_type,
            description=description,
            details=details,
            requested_by=requested_by,
            ttl_hours=ttl_hours,
            metadata={"handler": "registered"} if action_type in self._approval_handlers else {},
        )
        
        if request.status == ApprovalStatus.APPROVED:
            try:
                result = action_handler(details)
                return {
                    "ok": True,
                    "auto_approved": False,
                    "approval_id": request.id,
                    "result": result,
                }
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"Execution failed: {str(e)}",
                }
        
        # Return approval request info for client to poll
        return {
            "ok": True,
            "requires_approval": True,
            "approval_id": request.id,
            "status": request.status.value,
            "expires_at": request.expires_at,
            "message": f"Approval required for {action_type.value}: {description}",
        }
    
    def get_pending_approvals(self, user_id: str = None) -> List[ApprovalRequest]:
        """Get all pending approval requests"""
        with self._lock:
            pending = []
            for request in self._approvals.values():
                if request.status == ApprovalStatus.PENDING:
                    if user_id is None or request.requested_by == user_id:
                        pending.append(request)
            
            # Also load from disk
            pending_dir = self.approval_dir / "pending"
            if pending_dir.exists():
                for file_path in pending_dir.glob("*.json"):
                    try:
                        with open(file_path, "r") as f:
                            data = json.load(f)
                        request = ApprovalRequest(**data)
                        if request.status == ApprovalStatus.PENDING:
                            if user_id is None or request.requested_by == user_id:
                                # Check if already in list
                                if not any(r.id == request.id for r in pending):
                                    pending.append(request)
                    except Exception:
                        continue
            
            return pending
    
    def cleanup_expired(self):
        """Clean up expired approval requests"""
        with self._lock:
            for request in list(self._approvals.values()):
                if request.status == ApprovalStatus.PENDING:
                    if datetime.fromisoformat(request.expires_at) < datetime.utcnow():
                        request.status = ApprovalStatus.EXPIRED
                        self._save_request(request)
            
            # Also check disk
            pending_dir = self.approval_dir / "pending"
            if pending_dir.exists():
                for file_path in pending_dir.glob("*.json"):
                    try:
                        with open(file_path, "r") as f:
                            data = json.load(f)
                        if data.get("status") == "pending":
                            if datetime.fromisoformat(data["expires_at"]) < datetime.utcnow():
                                # Move to expired
                                expired_dir = self.approval_dir / "expired"
                                expired_dir.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(file_path), str(expired_dir / file_path.name))
                    except Exception:
                        continue


# Global instance
_approval_gate: Optional[ApprovalGate] = None
_approval_lock = threading.Lock()


def get_approval_gate() -> ApprovalGate:
    """Get or create global approval gate instance"""
    global _approval_gate
    with _approval_lock:
        if _approval_gate is None:
            _approval_gate = ApprovalGate()
        return _approval_gate