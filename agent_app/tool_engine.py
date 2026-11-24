"""
EMOG Tool Engine — Araç ve Otomasyon Sistemi (Bölüm 12)

NEURAL CORE'un dış dünyayla çalışmasını sağlayan merkezi a motoru.

Tüm araçlar standart bir arayüz üzerinden sisteme tanıtılır. Yeni bir API,
yöntem veya otomasyon aracı eklendiğinde yalnızca `ToolEngine.register()` ile
kaydedilmesi yeterlidir; sistemin diğer bölümleri değişmez.

Araç kategorileri:
  web_research     -> Web araştırması
  api_system       -> API sistemleri
  code_execution   -> Kod çalıştırma
  file_management  -> Dosya yönetimi
  database         -> Veritabanları
  browser_automation -> Tarayıcı otomasyonu
  local_program    -> Yerel programlar
  virtual_machine  -> Sanal makineler
  simulation_environment -> Simülasyon ortamları
  data_analysis    -> Veri analiz araçları

Her araç:
  - benzersiz bir ada sahip,
  - bir kategoride tanımlı,
  - JSON şemalı parametreleri bildirir,
  - onay gerektirebilir veya gerektirmez,
  - bir handler işleviyle çalıştırılır.

Kullanıcı bazlı yalıtım ve onay akışı `neural_core.tools.ToolRegistry`
üzerinden sürdürülür.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from django.conf import settings

from agent_app.project_scanner import (
    TOOL_HANDLERS,
    execute_tool as _scanner_execute_tool,
    get_project_root,
)
from agent_app.neural_core.tools import ToolRegistry as NeuralToolRegistry

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    WEB_RESEARCH = "web_research"
    API_SYSTEM = "api_system"
    CODE_EXECUTION = "code_execution"
    FILE_MANAGEMENT = "file_management"
    DATABASE = "database"
    BROWSER_AUTOMATION = "browser_automation"
    LOCAL_PROGRAM = "local_program"
    VIRTUAL_MACHINE = "virtual_machine"
    SIMULATION_ENVIRONMENT = "simulation_environment"
    DATA_ANALYSIS = "data_analysis"
    SYSTEM = "system"


@dataclass
class ToolSpec:
    name: str
    category: str
    description: str
    handler: Callable
    parameters: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    requires_approval: bool = False
    timeout_seconds: float = 60.0
    cost_estimate_usd: float = 0.0
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["handler"] = self.handler.__name__ if self.handler else None
        d["category"] = self.category if isinstance(self.category, str) else self.category.value
        return d


class ToolResult:
    def __init__(
        self,
        ok: bool,
        tool: str,
        data: Any = None,
        error: Optional[str] = None,
        latency_ms: float = 0.0,
        approval_required: bool = False,
        approval_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.ok = ok
        self.tool = tool
        self.data = data
        self.error = error
        self.latency_ms = latency_ms
        self.approval_required = approval_required
        self.approval_id = approval_id
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "approval_required": self.approval_required,
            "approval_id": self.approval_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class ToolEngine:
    def __init__(self):
        self._specs: Dict[str, ToolSpec] = {}
        self._execution_log: List[Dict[str, Any]] = []
        self._max_log = 1000

        self._register_scanner_tools()

    def register(self, spec: ToolSpec) -> None:
        if not spec.name:
            raise ValueError("araç ismi boş olamaz")
        if not spec.handler:
            raise ValueError(f"araç '{spec.name}' için handler belirtilmedi")
        self._specs[spec.name] = spec
        logger.info("ToolEngine: araç kaydedildi %s (%s)", spec.name, spec.category)

    def register_handler(
        self,
        name: str,
        handler: Callable,
        category: str = "system",
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        required: Optional[List[str]] = None,
        requires_approval: bool = False,
        timeout_seconds: float = 60.0,
        cost_estimate_usd: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.register(ToolSpec(
            name=name,
            category=category,
            description=description or handler.__doc__ or "",
            handler=handler,
            parameters=parameters or {},
            required=required or [],
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
            cost_estimate_usd=cost_estimate_usd,
            metadata=metadata or {},
        ))

    def unregister(self, name: str) -> bool:
        if name in self._specs:
            del self._specs[name]
            logger.info("ToolEngine: araç kaldırıldı %s", name)
            return True
        return False

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def list_tools(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for spec in self._specs.values():
            if not spec.enabled and not include_disabled:
                continue
            out.append({
                "name": spec.name,
                "category": spec.category if isinstance(spec.category, str) else spec.category.value,
                "description": spec.description,
                "parameters": spec.parameters,
                "required": spec.required,
                "requires_approval": spec.requires_approval,
                "timeout_seconds": spec.timeout_seconds,
                "cost_estimate_usd": spec.cost_estimate_usd,
                "enabled": spec.enabled,
                "metadata": spec.metadata,
            })
        return out

    def get_categories(self) -> List[str]:
        return sorted({s.category if isinstance(s.category, str) else s.category.value for s in self._specs.values()})

    def _register_scanner_tools(self) -> None:
        category_map = {
            "read_file": ToolCategory.FILE_MANAGEMENT,
            "write_file": ToolCategory.FILE_MANAGEMENT,
            "list_files": ToolCategory.FILE_MANAGEMENT,
            "code_outline": ToolCategory.SYSTEM,
            "edit_file": ToolCategory.FILE_MANAGEMENT,
            "grep_files": ToolCategory.SYSTEM,
            "patch_file": ToolCategory.FILE_MANAGEMENT,
            "apply_patch": ToolCategory.FILE_MANAGEMENT,
            "delete_file": ToolCategory.FILE_MANAGEMENT,
            "delete_dir": ToolCategory.FILE_MANAGEMENT,
            "move_file": ToolCategory.FILE_MANAGEMENT,
            "copy_file": ToolCategory.FILE_MANAGEMENT,
            "run_command": ToolCategory.CODE_EXECUTION,
            "run_tests": ToolCategory.CODE_EXECUTION,
            "lint_code": ToolCategory.SYSTEM,
            "git_diff": ToolCategory.SYSTEM,
            "git_status": ToolCategory.SYSTEM,
            "git_log": ToolCategory.SYSTEM,
            "git_commit": ToolCategory.SYSTEM,
            "git_push": ToolCategory.SYSTEM,
            "git_pull": ToolCategory.SYSTEM,
            "http_request": ToolCategory.API_SYSTEM,
            "fetch_url": ToolCategory.WEB_RESEARCH,
            "database_query": ToolCategory.DATABASE,
            "docker_build": ToolCategory.VIRTUAL_MACHINE,
            "docker_run": ToolCategory.VIRTUAL_MACHINE,
            "analyze_dependencies": ToolCategory.DATA_ANALYSIS,
            "summarize_code": ToolCategory.DATA_ANALYSIS,
            "generate_tests": ToolCategory.DATA_ANALYSIS,
            "explain_code": ToolCategory.DATA_ANALYSIS,
            "secret_scan": ToolCategory.SYSTEM,
            "sast_scan": ToolCategory.SYSTEM,
            "generate_readme": ToolCategory.SYSTEM,
            "changelog_generate": ToolCategory.SYSTEM,
            "github_actions_validate": ToolCategory.SYSTEM,
            "dependency_graph": ToolCategory.DATA_ANALYSIS,
            "profile_code": ToolCategory.DATA_ANALYSIS,
            "benchmark": ToolCategory.DATA_ANALYSIS,
            "api_docs_generate": ToolCategory.API_SYSTEM,
            "rollback_deploy": ToolCategory.SYSTEM,
            "export_data": ToolCategory.DATA_ANALYSIS,
            "data_diff": ToolCategory.DATA_ANALYSIS,
            "android_build": ToolCategory.LOCAL_PROGRAM,
            "ios_build": ToolCategory.LOCAL_PROGRAM,
            "wasm_build": ToolCategory.LOCAL_PROGRAM,
            "analyze_image": ToolCategory.SYSTEM,
            "deep_research": ToolCategory.WEB_RESEARCH,
        }

        for name, handler in TOOL_HANDLERS.items():
            cat = category_map.get(name, ToolCategory.SYSTEM)
            requires_approval = name in (
                "write_file", "delete_file", "delete_dir", "run_command",
                "docker_build", "docker_run", "git_push", "git_commit",
                "patch_file", "apply_patch", "move_file", "copy_file",
            )
            self.register_handler(
                name=name,
                handler=handler,
                category=cat.value,
                description=(handler.__doc__ or "").strip() or f"{name} aracı",
                parameters=self._infer_parameters(handler),
                required=[],
                requires_approval=requires_approval,
            )

    @staticmethod
    def _infer_parameters(handler: Callable) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        try:
            sig = inspect.signature(handler)
            for pname, p in sig.parameters.items():
                if pname == "root":
                    continue
                ptype = "string"
                if p.annotation is not inspect.Parameter.empty:
                    ann = p.annotation
                    if ann is int:
                        ptype = "integer"
                    elif ann is float:
                        ptype = "number"
                    elif ann is bool:
                        ptype = "boolean"
                    elif ann is dict or ann is Dict:
                        ptype = "object"
                    elif ann is list or ann is List:
                        ptype = "array"
                props[pname] = {"type": ptype, "description": f"{pname} parametresi"}
        except Exception:
            pass
        return {"type": "object", "properties": props}

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user: Any,
        project_root: Optional[str] = None,
        force_approval: bool = False,
    ) -> ToolResult:
        start = time.time()
        spec = self.get_spec(tool_name)
        if not spec:
            return ToolResult(
                ok=False, tool=tool_name, error=f"Bilinmeyen araç: {tool_name}",
            )
        if not spec.enabled:
            return ToolResult(
                ok=False, tool=tool_name, error=f"Araç devre dışı: {tool_name}",
            )

        root_path = project_root or str(get_project_root() or settings.BASE_DIR)
        root = Path(root_path).resolve()

        needs_approval = spec.requires_approval or force_approval
        if needs_approval:
            try:
                registry = NeuralToolRegistry(user)
                if tool_name == "write_file":
                    approval = registry.request_write(
                        path=arguments.get("path", ""),
                        content=arguments.get("content", ""),
                    )
                elif tool_name == "run_command":
                    approval = registry.request_command(
                        command=arguments.get("command", ""),
                        cwd=arguments.get("cwd", "."),
                    )
                else:
                    approval = registry.request_write(
                        path=arguments.get("path", ""),
                        content=json.dumps(arguments, ensure_ascii=False),
                    )
                return ToolResult(
                    ok=True,
                    tool=tool_name,
                    data={
                        "approval_required": True,
                        "approval_id": str(approval.id),
                        "status": approval.status,
                        "message": f"'{tool_name}' aracı için onay bekleniyor.",
                    },
                    approval_required=True,
                    approval_id=str(approval.id),
                    latency_ms=round((time.time() - start) * 1000, 2),
                )
            except Exception as exc:
                logger.warning("ToolEngine: onay oluşturma hatası %s: %s", tool_name, exc)
                return ToolResult(
                    ok=False, tool=tool_name,
                    error=f"Onay oluşturulamadı: {exc}",
                    latency_ms=round((time.time() - start) * 1000, 2),
                )

        try:
            result = spec.handler(**dict(arguments or {}), root=root)
            if not isinstance(result, dict):
                result = {"ok": True, "data": result}
            latency = round((time.time() - start) * 1000, 2)
            self._log_execution(tool_name, user, arguments, result, latency)
            return ToolResult(
                ok=bool(result.get("ok", False)),
                tool=tool_name,
                data=result.get("data") or result,
                error=result.get("error"),
                latency_ms=latency,
                approval_required=False,
                metadata={"root": str(root)},
            )
        except TypeError as exc:
            latency = round((time.time() - start) * 1000, 2)
            return ToolResult(
                ok=False,
                tool=tool_name,
                error=f"Geçersiz araç argümanları ({tool_name}): {exc}",
                latency_ms=latency,
                metadata={"root": str(root)},
            )
        except Exception as exc:
            latency = round((time.time() - start) * 1000, 2)
            logger.exception("ToolEngine: aracı çalıştırma hatası %s", tool_name)
            return ToolResult(
                ok=False,
                tool=tool_name,
                error=str(exc),
                latency_ms=latency,
                metadata={"root": str(root)},
            )

    def approve_and_execute(
        self,
        approval_id: str,
        approve: bool,
        user: Any,
    ) -> Dict[str, Any]:
        registry = NeuralToolRegistry(user)
        return registry.decide(approval_id=approval_id, approve=approve, user=user)

    def _log_execution(
        self, tool_name: str, user: Any, arguments: Dict[str, Any],
        result: Dict[str, Any], latency_ms: float,
    ) -> None:
        entry = {
            "id": str(uuid.uuid4()),
            "tool": tool_name,
            "user_id": str(getattr(user, "id", None) or getattr(user, "pk", None) or "anonymous"),
            "arguments": arguments,
            "ok": result.get("ok", False),
            "error": result.get("error"),
            "latency_ms": latency_ms,
            "timestamp": time.time(),
        }
        self._execution_log.append(entry)
        if len(self._execution_log) > self._max_log:
            self._execution_log = self._execution_log[-self._max_log:]

    def get_execution_history(
        self, user_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        logs = self._execution_log
        if user_id:
            logs = [e for e in logs if e.get("user_id") == user_id]
        return list(reversed(logs))[:limit]

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._execution_log)
        if total == 0:
            return {"total_executions": 0}
        successes = sum(1 for e in self._execution_log if e.get("ok"))
        by_tool: Dict[str, int] = {}
        for e in self._execution_log:
            by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1
        return {
            "total_executions": total,
            "success_rate": round(successes / total, 3),
            "registered_tools": len(self._specs),
            "categories": self.get_categories(),
            "by_tool": by_tool,
        }


# Global instance
tool_engine = ToolEngine()
