"""
System Health Monitoring - Prometheus Metrics & Auto-Refactoring
Provides comprehensive health monitoring, metrics collection, and automated code improvement.
"""
import os
import json
import time
import threading
import psutil
import platform
import socket
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from collections import defaultdict, deque
from functools import wraps
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt


class HealthStatus(Enum):
    """Health check status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class MetricType(Enum):
    """Types of metrics"""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class Metric:
    """A single metric data point"""
    name: str
    value: float
    metric_type: MetricType
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_prometheus(self) -> str:
        """Convert to Prometheus format"""
        labels_str = ",".join(f'{k}="{v}"' for k, v in self.labels.items())
        labels_part = f"{{{labels_str}}}" if labels_str else ""
        return f"{self.name}{labels_part} {self.value} {int(time.time() * 1000)}"


@dataclass
class HealthCheck:
    """Health check result"""
    name: str
    status: HealthStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: float = 0


class PrometheusMetricsCollector:
    """
    Prometheus-compatible metrics collector.
    Collects and exports metrics in Prometheus format.
    """
    
    def __init__(self, prefix: str = "emog"):
        self.prefix = prefix
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._summaries: Dict[str, List[float]] = defaultdict(list)
    
    def _make_key(self, name: str, labels: Dict[str, str]) -> str:
        """Create a unique key for a metric with labels"""
        labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{self.prefix}_{name}{{{labels_str}}}" if labels_str else f"{self.prefix}_{name}"
    
    def counter(self, name: str, value: float = 1, labels: Dict[str, str] = None):
        """Increment a counter metric"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = {"type": "counter", "value": 0, "labels": dict(labels)}
            self._metrics[key]["value"] += value
    
    def gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        """Set a gauge metric"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            self._metrics[key] = {"type": "gauge", "value": value, "labels": dict(labels)}
    
    def histogram(self, name: str, value: float, labels: Dict[str, str] = None, buckets: List[float] = None):
        """Record a histogram observation"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = []
            self._histograms[key].append(value)
            # Keep only last 1000 observations
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-1000:]
            
            # Also track as summary
            summary_key = f"{key}_sum"
            if summary_key not in self._summaries:
                self._summaries[summary_key] = []
            self._summaries[summary_key].append(value)
            if len(self._summaries[summary_key]) > 1000:
                self._summaries[summary_key] = self._summaries[summary_key][-1000:]
    
    def summary(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a summary observation (similar to histogram but simpler)"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            if key not in self._summaries:
                self._summaries[key] = []
            self._summaries[key].append(value)
            if len(self._summaries[key]) > 1000:
                self._summaries[key] = self._summaries[key][-1000:]
    
    def get_metrics(self) -> str:
        """Export all metrics in Prometheus format"""
        lines = []
        
        with self._lock:
            # Counters and gauges
            for key, metric in self._metrics.items():
                metric_type = metric["type"]
                labels_str = ",".join(f'{k}="{v}"' for k, v in metric["labels"].items())
                labels_part = f"{{{labels_str}}}" if labels_str else ""
                lines.append(f"# TYPE {self.prefix}_{key.replace(self.prefix + '_', '')} {metric_type}")
                lines.append(f"{self.prefix}_{key.replace(self.prefix + '_', '')}{{{labels_str}}} {metric['value']}")
            
            # Histograms
            for key, values in self._histograms.items():
                if not values:
                    continue
                metric_name = key.replace(self.prefix + "_", "")
                labels_str = ""
                if "{" in key:
                    metric_name = key.split("{")[0].replace(self.prefix + "_", "")
                    labels_str = key[key.find("{"):key.find("}")+1]
                
                # Calculate quantiles
                sorted_values = sorted(values)
                count = len(values)
                sum_val = sum(values)
                
                lines.append(f"# TYPE {self.prefix}_{metric_name} histogram")
                lines.append(f"{self.prefix}_{metric_name}_count{{{labels_str}}} {count}")
                lines.append(f"{self.prefix}_{metric_name}_sum{{{labels_str}}} {sum_val}")
                
                # Quantiles
                for q in [0.5, 0.9, 0.95, 0.99]:
                    idx = int(len(sorted_values) * q)
                    quantile_val = sorted_values[min(idx, len(sorted_values)-1)]
                    lines.append(f'{self.prefix}_{metric_name}{{{labels_str},quantile="{q}"}} {quantile_val}')
            
            # Summaries
            for key, values in self._summaries.items():
                if not values:
                    continue
                metric_name = key.replace(self.prefix + "_", "").replace("_sum", "")
                labels_str = ""
                if "{" in key:
                    metric_name = key.split("{")[0].replace(self.prefix + "_", "")
                    labels_str = key[key.find("{"):key.find("}")+1]
                
                lines.append(f"# TYPE {self.prefix}_{metric_name} summary")
                lines.append(f'{self.prefix}_{metric_name}_count{{{labels_str}}} {len(values)}')
                lines.append(f'{self.prefix}_{metric_name}_sum{{{labels_str}}} {sum(values)}')
        
        return "\n".join(lines) + "\n"
    
    def get_metric_value(self, name: str, labels: Dict[str, str] = None) -> Optional[float]:
        """Get current value of a metric"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            if key in self._metrics:
                return self._metrics[key]["value"]
            return None
    
    def get_histogram_stats(self, name: str, labels: Dict[str, str] = None) -> Dict[str, float]:
        """Get histogram statistics"""
        labels = labels or {}
        key = self._make_key(name, labels)
        
        with self._lock:
            if key not in self._histograms or not self._histograms[key]:
                return {}
            
            values = sorted(self._histograms[key])
            count = len(values)
            return {
                "count": count,
                "sum": sum(values),
                "min": values[0],
                "max": values[-1],
                "mean": sum(values) / count,
                "p50": values[int(count * 0.5)],
                "p90": values[int(count * 0.9)],
                "p95": values[int(count * 0.95)],
                "p99": values[int(count * 0.99)],
            }


class HealthMonitor:
    """
    Comprehensive health monitoring system.
    Checks system health, service dependencies, and resource usage.
    """
    
    def __init__(self):
        self._checks: Dict[str, Callable[[], HealthCheck]] = {}
        self._lock = threading.Lock()
        self._last_results: Dict[str, HealthCheck] = {}
    
    def register_check(self, name: str, check_func: Callable[[], HealthCheck], interval_seconds: int = 30):
        """Register a health check"""
        with self._lock:
            self._checks[name] = {
                "func": check_func,
                "interval": interval_seconds,
                "last_run": 0,
            }
    
    def run_check(self, name: str) -> HealthCheck:
        """Run a specific health check"""
        with self._lock:
            if name not in self._checks:
                return HealthCheck(
                    name=name,
                    status=HealthStatus.UNKNOWN,
                    message=f"Check '{name}' not registered"
                )
            
            check_info = self._checks[name]
            check_func = check_info["func"]
        
        start_time = time.time()
        try:
            result = check_func()
            duration_ms = (time.time() - start_time) * 1000
            result.duration_ms = duration_ms
            result.timestamp = datetime.utcnow().isoformat()
            return result
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return HealthCheck(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {str(e)}",
                details={"error": str(e)},
                duration_ms=duration_ms,
            )
    
    def run_all_checks(self) -> Dict[str, HealthCheck]:
        """Run all registered health checks"""
        results = {}
        with self._lock:
            check_names = list(self._checks.keys())
        
        for name in check_names:
            results[name] = self.run_check(name)
            with self._lock:
                self._last_results[name] = results[name]
        
        return results
    
    def get_overall_health(self) -> HealthStatus:
        """Get overall system health status"""
        results = self.run_all_checks()
        
        if not results:
            return HealthStatus.UNKNOWN
        
        statuses = [r.status for r in results.values()]
        
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        elif HealthStatus.HEALTHY in statuses:
            return HealthStatus.HEALTHY
        else:
            return HealthStatus.UNKNOWN
    
    def get_last_results(self) -> Dict[str, HealthCheck]:
        """Get last health check results"""
        with self._lock:
            return dict(self._last_results)


# Built-in health checks
def create_system_health_checks() -> Dict[str, Callable[[], HealthCheck]]:
    """Create standard system health checks"""
    
    def check_cpu() -> HealthCheck:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        if cpu_percent > 90:
            return HealthCheck("cpu", HealthStatus.UNHEALTHY, f"CPU usage critical: {cpu_percent}%")
        elif cpu_percent > 75:
            return HealthCheck("cpu", HealthStatus.DEGRADED, f"CPU usage high: {cpu_percent}%")
        return HealthCheck("cpu", HealthStatus.HEALTHY, f"CPU usage normal: {cpu_percent}%")
    
    def check_memory() -> HealthCheck:
        mem = psutil.virtual_memory()
        if mem.percent > 90:
            return HealthCheck("memory", HealthStatus.UNHEALTHY, f"Memory usage critical: {mem.percent}%")
        elif mem.percent > 75:
            return HealthCheck("memory", HealthStatus.DEGRADED, f"Memory usage high: {mem.percent}%")
        return HealthCheck("memory", HealthStatus.HEALTHY, f"Memory usage normal: {mem.percent}%")
    
    def check_disk() -> HealthCheck:
        disk = psutil.disk_usage("/")
        percent = (disk.used / disk.total) * 100
        if percent > 90:
            return HealthCheck("disk", HealthStatus.UNHEALTHY, f"Disk usage critical: {percent:.1f}%")
        elif percent > 75:
            return HealthCheck("disk", HealthStatus.DEGRADED, f"Disk usage high: {percent:.1f}%")
        return HealthCheck("disk", HealthStatus.HEALTHY, f"Disk usage normal: {percent:.1f}%")
    
    def check_network() -> HealthCheck:
        try:
            # Try to connect to a known host
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            return HealthCheck("network", HealthStatus.HEALTHY, "Network connectivity OK")
        except Exception:
            return HealthCheck("network", HealthStatus.UNHEALTHY, "Network connectivity failed")
    
    def check_database() -> HealthCheck:
        try:
            conn = sqlite3.connect("./db.sqlite3", timeout=2)
            conn.execute("SELECT 1")
            conn.close()
            return HealthCheck("database", HealthStatus.HEALTHY, "Database connection OK")
        except Exception as e:
            return HealthCheck("database", HealthStatus.UNHEALTHY, f"Database error: {e}")
    
    def check_redis() -> HealthCheck:
        try:
            import redis
            r = redis.Redis(host="localhost", port=6379, socket_timeout=2)
            r.ping()
            return HealthCheck("redis", HealthStatus.HEALTHY, "Redis connection OK")
        except Exception as e:
            return HealthCheck("redis", HealthStatus.DEGRADED, f"Redis unavailable: {e}")
    
    return {
        "cpu": check_cpu,
        "memory": check_memory,
        "disk": check_disk,
        "network": check_network,
        "database": check_database,
        "redis": check_redis,
    }


class AutoRefactorEngine:
    """
    Automated code refactoring engine.
    Analyzes code patterns and suggests/applies improvements.
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self._lock = threading.Lock()
        self._refactor_rules: List[Callable] = []
        self._refactor_history: List[Dict] = []
        self._load_default_rules()
    
    def _load_default_rules(self):
        """Load default refactoring rules"""
        self._refactor_rules = [
            self._fix_long_lines,
            self._fix_unused_imports,
            self._fix_bare_excepts,
            self._fix_missing_type_hints,
            self._fix_trailing_whitespace,
            self._fix_duplicate_code,
            self._optimize_imports,
            self._fix_magic_numbers,
        ]
    
    def analyze_file(self, file_path: Path) -> List[Dict]:
        """Analyze a Python file for issues"""
        issues = []
        
        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            
            for rule in self._refactor_rules:
                issues.extend(rule(file_path, lines))
                
        except Exception as e:
            issues.append({
                "type": "analysis_error",
                "message": str(e),
                "line": 0,
            })
        
        return issues
    
    def _fix_long_lines(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find lines exceeding 100 characters"""
        issues = []
        for i, line in enumerate(lines):
            if len(line) > 100 and not line.strip().startswith("#"):
                issues.append({
                    "type": "long_line",
                    "message": f"Line {i+1} exceeds 100 characters ({len(line)})",
                    "line": i + 1,
                    "severity": "warning",
                    "fixable": True,
                    "suggestion": "Break line into multiple lines",
                })
        return issues
    
    def _fix_unused_imports(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Detect unused imports (basic)"""
        issues = []
        imports = set()
        used_names = set()
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                # Extract imported names
                parts = stripped.split()
                if parts[0] == "import":
                    for part in parts[1:]:
                        name = part.split(".")[0].rstrip(",")
                        imports.add(name)
                elif parts[0] == "from":
                    if len(parts) >= 4 and parts[2] == "import":
                        for part in parts[3:]:
                            name = part.rstrip(",")
                            imports.add(name)
        
        # Check usage (simplified)
        content = " ".join(lines)
        for imp in imports:
            if imp not in content or content.count(imp) == 1:  # Only in import statement
                issues.append({
                    "type": "unused_import",
                    "message": f"Potentially unused import: {imp}",
                    "line": 0,
                    "severity": "warning",
                    "fixable": True,
                    "suggestion": f"Remove unused import: {imp}",
                })
        
        return issues
    
    def _fix_bare_excepts(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find bare except clauses"""
        issues = []
        for i, line in enumerate(lines):
            if re.search(r"^\s*except\s*:", line):
                issues.append({
                    "type": "bare_except",
                    "message": f"Bare except clause at line {i+1}",
                    "line": i + 1,
                    "severity": "error",
                    "fixable": True,
                    "suggestion": "Use 'except Exception:' or specific exception type",
                })
        return issues
    
    def _fix_missing_type_hints(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find functions missing type hints"""
        issues = []
        for i, line in enumerate(lines):
            if line.strip().startswith("def ") and "->" not in line and "(" in line:
                # Skip if it's a method in a class (could be intentional)
                if not any(l.strip().startswith("class ") for l in lines[:i]):
                    issues.append({
                        "type": "missing_type_hint",
                        "message": f"Function missing return type hint at line {i+1}",
                        "line": i + 1,
                        "severity": "info",
                        "fixable": True,
                        "suggestion": "Add return type hint",
                    })
        return issues
    
    def _fix_trailing_whitespace(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find trailing whitespace"""
        issues = []
        for i, line in enumerate(lines):
            if line.rstrip() != line and line.strip():
                issues.append({
                    "type": "trailing_whitespace",
                    "message": f"Trailing whitespace at line {i+1}",
                    "line": i + 1,
                    "severity": "style",
                    "fixable": True,
                    "suggestion": "Remove trailing whitespace",
                })
        return issues
    
    def _fix_duplicate_code(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find potential duplicate code blocks (simplified)"""
        issues = []
        # This is a simplified version - real implementation would use AST
        # For now, just flag if we see very similar consecutive blocks
        return issues
    
    def _optimize_imports(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Optimize imports (group and sort)"""
        issues = []
        import_lines = []
        for i, line in enumerate(lines):
            if line.strip().startswith(("import ", "from ")) and "import" in line:
                import_lines.append((i, line))
        
        if len(import_lines) > 1:
            issues.append({
                "type": "import_organization",
                "message": f"Multiple import statements could be organized",
                "line": import_lines[0][0] + 1,
                "severity": "style",
                "fixable": True,
                "suggestion": "Group and sort imports (stdlib, third-party, local)",
            })
        return issues
    
    def _fix_magic_numbers(self, file_path: Path, lines: List[str]) -> List[Dict]:
        """Find magic numbers that should be constants"""
        issues = []
        magic_pattern = re.compile(r"\b\d{3,}\b")  # Numbers with 3+ digits
        for i, line in enumerate(lines):
            matches = magic_pattern.findall(line)
            if matches and not line.strip().startswith("#"):
                issues.append({
                    "type": "magic_number",
                    "message": f"Magic number(s) found: {', '.join(matches)}",
                    "line": i + 1,
                    "severity": "info",
                    "fixable": True,
                    "suggestion": "Extract to named constant",
                })
        return issues
    
    def apply_fix(self, file_path: Path, issue: Dict) -> bool:
        """Apply a fix for an issue"""
        if not issue.get("fixable"):
            return False
        
        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            
            if issue["type"] == "trailing_whitespace":
                lines = [line.rstrip() for line in lines]
                file_path.write_text("\n".join(lines), encoding="utf-8")
                return True
            
            elif issue["type"] == "unused_import":
                # Would need more sophisticated handling
                pass
            
            elif issue["type"] == "bare_except":
                lines = [line.replace("except:", "except Exception:") if "except:" in line else line for line in lines]
                file_path.write_text("\n".join(lines), encoding="utf-8")
                return True
            
            elif issue["type"] == "trailing_whitespace":
                lines = [line.rstrip() for line in lines]
                file_path.write_text("\n".join(lines), encoding="utf-8")
                return True
            
            return False
            
        except Exception as e:
            print(f"Failed to apply fix: {e}")
            return False
    
    def refactor_file(self, file_path: Path, dry_run: bool = False) -> Dict[str, Any]:
        """Analyze and optionally fix a file"""
        issues = self.analyze_file(file_path)
        
        results = {
            "file": str(file_path),
            "issues_found": len(issues),
            "issues": issues,
            "fixes_applied": 0,
        }
        
        if not dry_run:
            for issue in issues:
                if self.apply_fix(Path(file_path), issue):
                    results["fixes_applied"] += 1
            
            # Record in history
            with self._lock:
                self._refactor_history.append({
                    "file": str(file_path),
                    "timestamp": datetime.utcnow().isoformat(),
                    "issues_fixed": results["fixes_applied"],
                    "total_issues": len(issues),
                })
        
        return results
    
    def refactor_project(self, pattern: str = "**/*.py", dry_run: bool = False) -> Dict[str, Any]:
        """Refactor all matching files in project"""
        files = list(self.project_root.glob(pattern))
        results = {
            "files_processed": 0,
            "total_issues": 0,
            "total_fixes": 0,
            "files": [],
        }
        
        for file_path in files:
            if file_path.is_file():
                result = self.refactor_file(file_path, dry_run)
                results["files_processed"] += 1
                results["total_issues"] += result["issues_found"]
                results["total_fixes"] += result["fixes_applied"]
                results["files"].append(result)
        
        return results
    
    def get_refactor_history(self) -> List[Dict]:
        """Get refactoring history"""
        with self._lock:
            return list(self._refactor_history)


# Global instances
_prometheus_metrics: Optional[PrometheusMetricsCollector] = None
_health_monitor: Optional[HealthMonitor] = None
_auto_refactor: Optional[AutoRefactorEngine] = None

_metrics_lock = threading.Lock()
_health_lock = threading.Lock()
_refactor_lock = threading.Lock()


def get_prometheus_metrics(prefix: str = "emog") -> PrometheusMetricsCollector:
    global _prometheus_metrics
    with _metrics_lock:
        if _prometheus_metrics is None:
            _prometheus_metrics = PrometheusMetricsCollector(prefix)
        return _prometheus_metrics


def get_health_monitor() -> HealthMonitor:
    global _health_monitor
    with _health_lock:
        if _health_monitor is None:
            _health_monitor = HealthMonitor()
            # Register default checks
            checks = create_system_health_checks()
            for name, check_func in checks.items():
                _health_monitor.register_check(name, check_func)
        return _health_monitor


def get_auto_refactor(project_root: str = ".") -> AutoRefactorEngine:
    global _auto_refactor
    with _refactor_lock:
        if _auto_refactor is None:
            _auto_refactor = AutoRefactorEngine(project_root)
        return _auto_refactor


# Prometheus metrics endpoint
@require_http_methods(["GET"])
def metrics_endpoint(request):
    """Prometheus metrics endpoint"""
    metrics = get_prometheus_metrics()
    return HttpResponse(metrics.get_metrics(), content_type="text/plain; version=0.0.4; charset=utf-8")


# Health check endpoint
@require_http_methods(["GET"])
def health_endpoint(request):
    """Health check endpoint"""
    monitor = get_health_monitor()
    overall = monitor.get_overall_health()
    results = monitor.run_all_checks()
    
    return JsonResponse({
        "status": overall.value,
        "checks": {name: {
            "status": check.status.value,
            "message": check.message,
            "details": check.details,
            "duration_ms": check.duration_ms,
        } for name, check in results.items()},
        "timestamp": datetime.utcnow().isoformat(),
    })


# Refactoring endpoints
@csrf_exempt
@require_http_methods(["POST"])
def refactor_file(request):
    """Refactor a specific file"""
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    file_path = data.get("file_path")
    dry_run = data.get("dry_run", True)
    
    if not file_path:
        return JsonResponse({"ok": False, "error": "file_path required"}, status=400)
    
    refactor = get_auto_refactor()
    result = refactor.refactor_file(Path(file_path), dry_run=dry_run)
    
    return JsonResponse({"ok": True, "result": result})


@csrf_exempt
@require_http_methods(["POST"])
def refactor_project(request):
    """Refactor entire project"""
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    pattern = data.get("pattern", "**/*.py")
    dry_run = data.get("dry_run", True)
    
    refactor = get_auto_refactor()
    result = refactor.refactor_project(pattern, dry_run)
    
    return JsonResponse({"ok": True, "result": result})


@require_http_methods(["GET"])
def refactor_history(request):
    """Get refactoring history"""
    refactor = get_auto_refactor()
    history = refactor.get_refactor_history()
    
    return JsonResponse({"ok": True, "history": history})


# Global instances
_prometheus_metrics: Optional[PrometheusMetricsCollector] = None
_health_monitor: Optional[HealthMonitor] = None
_auto_refactor: Optional[AutoRefactorEngine] = None

_metrics_lock = threading.Lock()
_health_lock = threading.Lock()
_refactor_lock = threading.Lock()


def get_prometheus_metrics(prefix: str = "emog") -> PrometheusMetricsCollector:
    global _prometheus_metrics
    with _metrics_lock:
        if _prometheus_metrics is None:
            _prometheus_metrics = PrometheusMetricsCollector(prefix)
        return _prometheus_metrics


def get_health_monitor() -> HealthMonitor:
    global _health_monitor
    with _health_lock:
        if _health_monitor is None:
            _health_monitor = HealthMonitor()
            # Register default checks
            checks = create_system_health_checks()
            for name, check_func in checks.items():
                _health_monitor.register_check(name, check_func)
        return _health_monitor


def get_auto_refactor(project_root: str = ".") -> AutoRefactorEngine:
    global _auto_refactor
    with _refactor_lock:
        if _auto_refactor is None:
            _auto_refactor = AutoRefactorEngine(project_root)
        return _auto_refactor