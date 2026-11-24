"""
Audit Logging and Monitoring for Quarantined Executions
Provides comprehensive audit trails, metrics collection, and alerting for sandbox activities.
"""
import os
import json
import time
import threading
import logging
import hashlib
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from collections import defaultdict
import uuid


class AuditEventType(Enum):
    """Types of audit events"""
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_KILLED = "execution_killed"
    COMMAND_EXECUTED = "command_executed"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    DB_QUERY = "db_query"
    DB_TRANSACTION = "db_transaction"
    PATCH_SUBMITTED = "patch_submitted"
    PATCH_TESTED = "patch_tested"
    PATCH_DEPLOYED = "patch_deployed"
    PATCH_ROLLED_BACK = "patch_rolled_back"
    SANDBOX_CREATED = "sandbox_created"
    SANDBOX_DESTROYED = "sandbox_destroyed"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    SECURITY_VIOLATION = "security_violation"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"


class AuditSeverity(Enum):
    """Severity levels for audit events"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Audit event record"""
    id: str
    event_type: AuditEventType
    severity: AuditSeverity
    timestamp: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    execution_id: Optional[str] = None
    project_id: Optional[str] = None
    sandbox_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    action: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    outcome: str = "success"  # success, failure, partial
    error_message: Optional[str] = None
    duration_ms: Optional[float] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class AlertRule:
    """Alert rule for monitoring"""
    id: str
    name: str
    description: str
    event_types: List[AuditEventType]
    severity_threshold: AuditSeverity
    condition: Callable[[AuditEvent], bool]  # Custom condition function
    actions: List[str] = field(default_factory=list)  # email, webhook, slack, etc.
    enabled: bool = True
    cooldown_seconds: int = 300  # Prevent alert spam


class AuditLogger:
    """
    Centralized audit logging system with multiple backends.
    """
    
    def __init__(self, log_dir: str = None, enable_console: bool = True):
        self.log_dir = Path(log_dir or os.environ.get("AUDIT_LOG_DIR", "./audit_logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self._lock = threading.Lock()
        self._event_buffer: List[AuditEvent] = []
        self._buffer_size = 100
        self._flush_interval = 5  # seconds
        self._last_flush = time.time()
        
        # Alert rules
        self._alert_rules: Dict[str, AlertRule] = {}
        self._alert_lock = threading.Lock()
        
        # Metrics collectors
        self._metrics: Dict[str, Any] = defaultdict(lambda: defaultdict(int))
        self._metrics_lock = threading.Lock()
        
        # Setup logging
        self._setup_logging(enable_console)
        
        # Start background flusher
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        
        # Default alert rules
        self._setup_default_alerts()
    
    def _setup_logging(self, enable_console: bool):
        """Setup logging handlers"""
        self.logger = logging.getLogger("audit")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        
        # File handler with rotation
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            self.log_dir / "audit.log",
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=10,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s'
        )
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)
        
        # JSON file handler for structured logs
        json_handler = RotatingFileHandler(
            self.log_dir / "audit.jsonl",
            maxBytes=50 * 1024 * 1024,
            backupCount=10,
            encoding='utf-8'
        )
        json_handler.setLevel(logging.DEBUG)
        json_handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(json_handler)
        
        if enable_console:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(message)s'
            ))
            self.logger.addHandler(console_handler)
    
    def _setup_default_alerts(self):
        """Setup default alert rules"""
        # Critical security violations
        self.add_alert_rule(AlertRule(
            id="security_violation_alert",
            name="Security Violation Alert",
            description="Alert on security violations",
            event_types=[AuditEventType.SECURITY_VIOLATION],
            severity_threshold=AuditSeverity.CRITICAL,
            condition=lambda e: True,
            actions=["log", "webhook"],
        ))
        
        # Resource limit exceeded
        self.add_alert_rule(AlertRule(
            id="resource_limit_alert",
            name="Resource Limit Exceeded",
            description="Alert when resource limits are exceeded",
            event_types=[AuditEventType.RESOURCE_LIMIT_EXCEEDED],
            severity_threshold=AuditSeverity.WARNING,
            condition=lambda e: True,
            actions=["log", "email"],
        ))
        
        # Failed executions
        self.add_alert_rule(AlertRule(
            id="execution_failure_alert",
            name="Execution Failure Alert",
            description="Alert on execution failures",
            event_types=[AuditEventType.EXECUTION_FAILED],
            severity_threshold=AuditSeverity.ERROR,
            condition=lambda e: True,
            actions=["log"],
        ))
        
        # Resource limit exceeded
        self.add_alert_rule(AlertRule(
            id="resource_exhaustion_alert",
            name="Resource Exhaustion Alert",
            description="Alert when resources are exhausted",
            event_types=[
                AuditEventType.EXECUTION_FAILED,
                AuditEventType.RESOURCE_LIMIT_EXCEEDED
            ],
            severity_threshold=AuditSeverity.ERROR,
            condition=lambda e: e.details.get("exit_code") == -1 or "timeout" in str(e.error_message).lower(),
            actions=["log", "webhook"],
        ))
    
    def log_event(self, event: AuditEvent):
        """Log an audit event"""
        with self._lock:
            self._event_buffer.append(event)
            
            # Update metrics
            self._update_metrics(event)
            
            # Check alert rules
            self._check_alerts(event)
            
            # Flush if buffer full or interval exceeded
            if len(self._event_buffer) >= self._buffer_size or \
               time.time() - self._last_flush > self._flush_interval:
                self._flush()
    
    def _update_metrics(self, event: AuditEvent):
        """Update internal metrics"""
        with self._metrics_lock:
            key = f"{event.event_type.value}"
            self._metrics[key]["count"] += 1
            self._metrics[key]["last_seen"] = event.timestamp
            
            if event.outcome == "failure":
                self._metrics[key]["failures"] += 1
            
            if event.duration_ms:
                self._metrics[key]["total_duration_ms"] = \
                    self._metrics[key].get("total_duration_ms", 0) + event.duration_ms
    
    def _check_alerts(self, event: AuditEvent):
        """Check if event triggers any alert rules"""
        with self._alert_lock:
            for rule in self._alert_rules.values():
                if not rule.enabled:
                    continue
                if event.event_type not in rule.event_types:
                    continue
                if self._severity_value(event.severity) < self._severity_value(rule.severity_threshold):
                    continue
                if not rule.condition(event):
                    continue
                
                # Check cooldown
                # (Simplified - in production would track last alert time per rule)
                self._trigger_alert(rule, event)
    
    def _severity_value(self, severity: AuditSeverity) -> int:
        values = {
            AuditSeverity.DEBUG: 0,
            AuditSeverity.INFO: 1,
            AuditSeverity.WARNING: 2,
            AuditSeverity.ERROR: 3,
            AuditSeverity.CRITICAL: 4,
        }
        return values.get(severity, 0)
    
    def _trigger_alert(self, rule: AlertRule, event: AuditEvent):
        """Trigger alert actions"""
        alert_data = {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "event": event.to_dict(),
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        for action in rule.actions:
            try:
                if action == "log":
                    self.logger.warning(f"ALERT: {rule.name} - {event.event_type.value}")
                elif action == "webhook":
                    self._send_webhook(rule, alert_data)
                elif action == "email":
                    self._send_email(rule, alert_data)
            except Exception as e:
                self.logger.error(f"Alert action failed: {e}")
    
    def _send_webhook(self, rule: AlertRule, alert_data: Dict):
        """Send alert via webhook"""
        webhook_url = os.environ.get("ALERT_WEBHOOK_URL")
        if webhook_url:
            try:
                import urllib.request
                req = urllib.request.Request(
                    webhook_url,
                    data=json.dumps(alert_data).encode(),
                    headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                self.logger.error(f"Webhook failed: {e}")
    
    def _send_email(self, rule: AlertRule, alert_data: Dict):
        """Send alert via email (placeholder)"""
        # Implementation would use SMTP
        pass
    
    def _flush(self):
        """Flush event buffer to disk"""
        if not self._event_buffer:
            return
        
        with self._lock:
            events = self._event_buffer[:]
            self._event_buffer.clear()
            self._last_flush = time.time()
        
        # Write to JSONL file
        try:
            with open(self.log_dir / "audit.jsonl", "a", encoding="utf-8") as f:
                for event in events:
                    f.write(event.to_json() + "\n")
        except Exception as e:
            self.logger.error(f"Failed to flush audit events: {e}")
            # Re-add to buffer
            with self._lock:
                self._event_buffer.extend(events)
    
    def _flush_loop(self):
        """Background flush loop"""
        while True:
            time.sleep(self._flush_interval)
            if time.time() - self._last_flush > self._flush_interval:
                self._flush()
    
    def log(self, 
            event_type: AuditEventType,
            action: str,
            outcome: str = "success",
            severity: AuditSeverity = AuditSeverity.INFO,
            user_id: str = None,
            session_id: str = None,
            execution_id: str = None,
            project_id: str = None,
            sandbox_id: str = None,
            resource_type: str = None,
            resource_id: str = None,
            details: Dict = None,
            error_message: str = None,
            duration_ms: float = None,
            **metadata) -> AuditEvent:
        """Convenience method to log an audit event"""
        event = AuditEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            severity=severity,
            timestamp=datetime.utcnow().isoformat(),
            user_id=user_id,
            session_id=session_id,
            execution_id=execution_id,
            project_id=project_id,
            sandbox_id=sandbox_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            details=details or {},
            outcome=outcome,
            error_message=error_message,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        self.log_event(event)
        return event
    
    def add_alert_rule(self, rule: AlertRule):
        """Add an alert rule"""
        with self._alert_lock:
            self._alert_rules[rule.id] = rule
    
    def remove_alert_rule(self, rule_id: str):
        """Remove an alert rule"""
        with self._alert_lock:
            self._alert_rules.pop(rule_id, None)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        with self._metrics_lock:
            return dict(self._metrics)
    
    def get_events(self, 
                   start_time: datetime = None,
                   end_time: datetime = None,
                   event_types: List[AuditEventType] = None,
                   user_id: str = None,
                   project_id: str = None,
                   limit: int = 1000) -> List[AuditEvent]:
        """Query audit events"""
        events = []
        
        # Read from JSONL file
        log_file = self.log_dir / "audit.jsonl"
        if not log_file.exists():
            return []
        
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if len(events) >= limit:
                        break
                    try:
                        event_data = json.loads(line)
                        event = AuditEvent(**event_data)
                        
                        # Filter
                        event_time = datetime.fromisoformat(event.timestamp)
                        if start_time and event_time < start_time:
                            continue
                        if end_time and event_time > end_time:
                            continue
                        if event_types and event.event_type not in event_types:
                            continue
                        if user_id and event.user_id != user_id:
                            continue
                        if project_id and event.project_id != project_id:
                            continue
                        
                        events.append(event)
                    except Exception:
                        continue
        except Exception as e:
            self.logger.error(f"Failed to read audit log: {e}")
        
        return events
    
    def get_statistics(self, 
                       start_time: datetime = None,
                       end_time: datetime = None) -> Dict[str, Any]:
        """Get audit statistics"""
        events = self.get_events(start_time=start_time, end_time=end_time, limit=10000)
        
        stats = {
            "total_events": len(events),
            "by_type": defaultdict(int),
            "by_severity": defaultdict(int),
            "by_outcome": defaultdict(int),
            "by_user": defaultdict(int),
            "by_project": defaultdict(int),
            "avg_duration_ms": 0,
            "total_duration_ms": 0,
            "failure_rate": 0,
        }
        
        total_duration = 0
        duration_count = 0
        failure_count = 0
        
        for event in events:
            stats["by_type"][event.event_type.value] += 1
            stats["by_severity"][event.severity.value] += 1
            stats["by_outcome"][event.outcome] += 1
            if event.user_id:
                stats["by_user"][event.user_id] += 1
            if event.project_id:
                stats["by_project"][event.project_id] += 1
            
            if event.duration_ms:
                total_duration += event.duration_ms
                duration_count += 1
            if event.outcome == "failure":
                failure_count += 1
        
        if duration_count > 0:
            stats["avg_duration_ms"] = total_duration / duration_count
        stats["total_duration_ms"] = total_duration
        
        if len(events) > 0:
            stats["failure_rate"] = failure_count / len(events)
        
        return stats


# Middleware for Django to automatically log requests
class AuditMiddleware:
    """Django middleware for automatic audit logging"""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.audit_logger = get_audit_logger()
    
    def __call__(self, request):
        start_time = time.time()
        
        # Log request start
        execution_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        response = self.get_response(request)
        
        # Log request completion
        duration_ms = (time.time() - start_time) * 1000
        
        # Determine outcome
        if response.status_code >= 500:
            outcome = "failure"
            severity = AuditSeverity.ERROR
        elif response.status_code >= 400:
            outcome = "failure"
            severity = AuditSeverity.WARNING
        else:
            outcome = "success"
            severity = AuditSeverity.INFO
        
        # Log the event
        get_audit_logger().log(
            event_type=AuditEventType.COMMAND_EXECUTED,
            action=f"{request.method} {request.path}",
            outcome=outcome,
            severity=severity,
            user_id=getattr(request.user, 'id', None) if hasattr(request, 'user') else None,
            session_id=request.session.session_key if hasattr(request, 'session') else None,
            execution_id=execution_id,
            details={
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
            },
            duration_ms=duration_ms,
        )
        
        return response


# Global instance
_audit_logger: Optional[AuditLogger] = None
_audit_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """Get or create global audit logger instance"""
    global _audit_logger
    with _audit_lock:
        if _audit_logger is None:
            _audit_logger = AuditLogger()
        return _audit_logger