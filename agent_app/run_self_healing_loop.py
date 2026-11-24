import subprocess
import sys
import threading
import uuid
import time
import asyncio
from datetime import datetime
import os
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from django import forms
from django.shortcuts import render
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from openai import OpenAI

from .project_scanner import (
    get_project_context, FILE_TOOLS, TOOL_HANDLERS, execute_tool,
    scan_project, tool_read_file, tool_write_file, tool_list_files,
    tool_grep_files, tool_edit_file, tool_run_command
)
from .models import Conversation, Node, Checkpoint
from .conversation_history import ConversationHistoryService, ConversationManager
from .model_router import get_model_router, select_model_for_task, escalate_model_tier

logger = logging.getLogger(__name__)

# Configure OpenAI client using NVIDIA settings from Django settings
omni_cfg = getattr(settings, "OMNIROUTE", {})
base_url = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1") or "https://integrate.api.nvidia.com/v1"
base_url = base_url.rstrip('/')
# For NVIDIA API (integrate.api.nvidia.com/v1), keep /v1 in base_url.
if base_url.endswith('/v1') and 'nvidia' not in base_url:
    base_url = base_url[:-3]
client = OpenAI(
    base_url=base_url,
    api_key=omni_cfg.get("API_KEY", os.environ.get("NVIDIA_API_KEY", "")),
    timeout=omni_cfg.get("TIMEOUT", 30),
)

# Default max_tokens for chat completions
DEFAULT_MAX_TOKENS = omni_cfg.get("MAX_TOKENS", 2048)


class TaskForm(forms.Form):
    task = forms.CharField(
        label="Görev",
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "placeholder": "Örnek: 1'den 100'e kadar asal sayıları listeleyen Python fonksiyonunu yaz.",
            }
        ),
    )
    model = forms.ChoiceField(
        label="Model",
        required=False,
        help_text="OmniRoute modeli; 'Auto' seçili ise ayarlardaki varsayılan kullanılır.",
    )

    def __init__(self, *args, model_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if model_choices is None:
            model_choices = []
        # ensure 'auto' is the first/top option
        auto_label = f"Auto (use default: {getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto')})"
        choices = [("auto", auto_label)] + [(m, m) for m in model_choices]
        self.fields["model"].choices = choices
        # default to 'auto'
        self.fields["model"].initial = "auto"


def extract_code_block(text):
    if "```python" in text:
        parts = text.split("```python")
        if len(parts) > 1:
            return parts[1].split("```")[0].strip()
    elif "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            return parts[1].strip()
    return None


def run_self_healing_loop(initial_prompt, model_name=None, max_iterations=5, preferred_tier=None):
    """
    Tool-calling agent loop: LLM can read/write/list/grep/edit files and run commands.
    """
    # Initialize escalation counter for model tier escalation
    if not hasattr(run_self_healing_loop, '_escalation_count'):
        run_self_healing_loop._escalation_count = 0

    # model name may be provided in the prompt wrapper (we set default later)
    # allow callers to pass a wrapper dict: {"__meta__": {...}, "prompt": "..."}
    prompt_text = None
    if isinstance(initial_prompt, dict) and isinstance(initial_prompt.get("__meta__"), dict):
        prompt_text = initial_prompt.get("prompt") or initial_prompt.get("task") or ""
    else:
        prompt_text = initial_prompt

    # Get project context
    project_ctx = get_project_context()

    # Build system prompt with project context and tool definitions
    system_prompt = f"""You are an autonomous senior developer agent working on a real project.
You have access to the project files and can use tools to read, write, edit, search, and run commands.

=== PROJECT CONTEXT ===
{project_ctx}

=== AVAILABLE TOOLS ===
You can call these functions by responding with a tool_calls block:
- read_file(path): Read a file's content
- code_outline(path): AST outline (classes/functions) for a Python file or directory
- write_file(path, content): Write content to a file (creates dirs)
- list_files(path): List files in a directory
- grep_files(pattern, path, include): Search for regex pattern in files
- edit_file(path, old_string, new_string): Replace exact string in file
- run_command(command, timeout): Run shell command in project root

When you need to use a tool, respond with a JSON tool_calls array. Example:
{{
  "tool_calls": [
    {{"name": "read_file", "arguments": {{"path": "agent_app/views.py"}}}}
  ]
}}

The system will execute the tool and return results. Continue until the task is complete.
Then respond with a final message summarizing what you did.

IMPORTANT:
- Always use tools to explore the codebase before making changes
- Make minimal, focused changes
- Run tests/commands to verify your changes work
- Never assume file contents - always read first
- Use edit_file for precise changes, write_file for new files
"""

    conversation_history = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]

    # Use model router for intelligent model selection based on task complexity
    router = get_model_router()
    
    # Determine model tier from task complexity
    if not model_name:
        current_tier, model_config = select_model_for_task(prompt_text)
        model_name = model_config.get("model_id", getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', 'auto/best-mode'))
    else:
        # User explicitly specified a model, use it but get its tier info
        model_name = model_name
        # Find which tier this model belongs to
        router = get_model_router()
        current_tier = "standard"
        for tier_name, tier_info in router.tiers.items():
            if tier_info.get("model_id") == model_name:
                current_tier = tier_name
                break
        if not current_tier:
            current_tier = "standard"

    # logging: optional run_id may be set in the outer scope by callers.
    run_id = None
    console = False
    # callers may pass in a thread-local override via attributes on the prompt tuple
    if isinstance(initial_prompt, dict) and initial_prompt.get("__meta__"):
        meta = initial_prompt.pop("__meta__")
        run_id = meta.get("run_id")
        console = meta.get("console", False)

    def _get_run_dir():
        dirp = os.path.join(settings.BASE_DIR, "task_runs")
        try:
            os.makedirs(dirp, exist_ok=True)
        except Exception:
            pass
        return dirp

    def _ensure_run_id():
        nonlocal run_id
        if not run_id:
            run_id = uuid.uuid4().hex
        open(os.path.join(_get_run_dir(), f"{run_id}.log"), "a", encoding="utf-8").close()
        return run_id

    def _append_log(entry_type, text):
        try:
            rid = _ensure_run_id()
            path = os.path.join(_get_run_dir(), f"{rid}.log")
            obj = {"ts": datetime.utcnow().isoformat() + "Z", "type": entry_type, "text": str(text)}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            if console:
                print(f"[{entry_type}] {text}", flush=True)
        except Exception:
            pass

    # Tool-calling loop
    for i in range(max_iterations):
        try:
            _append_log("info", f"Iteration {i+1}/{max_iterations}")
            _append_log("status", "Düşünüyor...")
            
            # Build messages with tools
            response = client.chat.completions.create(
                model=model_name,
                messages=conversation_history,
                temperature=0.2,
                tools=FILE_TOOLS,
                tool_choice="auto",
                max_tokens=DEFAULT_MAX_TOKENS,
                reasoning_effort="none",
            )
            
            message = response.choices[0].message
            content = message.content or ""
            tool_calls = message.tool_calls or []
            
            _append_log("chat_response", content)
            if tool_calls:
                _append_log("tool_calls", json.dumps([{"name": tc.function.name, "args": tc.function.arguments} for tc in tool_calls], ensure_ascii=False))
            
            # If no tool calls, treat as final response
            if not tool_calls:
                if content.strip():
                    _append_log("final_response", content)
                    _append_log("status", "Tamamlandı")
                    return {"success": True, "output": content.strip(), "run_id": run_id}
                # Empty response, continue
                continue
            
            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                func_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                
                tool_status_map = {
                    "read_file": "Dosya okunuyor...",
                    "write_file": "Dosya yazılıyor...",
                    "list_files": "Dosya ağacı taranıyor...",
                    "run_command": "Komut çalıştırılıyor...",
                    "grep_search": "Kodda arama yapılıyor...",
                    "analyze_dependencies": "Bağımlılıklar analiz ediliyor...",
                    "analyze_image": "Görsel analiz ediliyor...",
                    "web_search": "İnternette araştırılıyor...",
                }
                status_msg = tool_status_map.get(func_name, f"{func_name} çalıştırılıyor...")
                _append_log("status", status_msg)
                
                _append_log("tool_call", f"{func_name}({json.dumps(args, ensure_ascii=False)[:500]})")
                result = execute_tool(func_name, args)
                try:
                    result_json = json.dumps(result, ensure_ascii=False, default=str)[:5000]
                except Exception:
                    result_json = '{"ok": false, "error": "Serialization failed"}'
                _append_log("tool_result", result_json)
                
                tool_results.append({
                    "tool_call_id": tc.id,
                    "role": "tool",
                    "name": func_name,
                    "content": result_json
                })
            
            # Add assistant message with tool calls to history
            conversation_history.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in tool_calls
                ]
            })
            
            # Add tool results to history
            conversation_history.extend(tool_results)
            _append_log("status", "Sonuçlar işleniyor...")
        
        except Exception as exc:
            try:
                err_text = str(exc)[:1000]
            except Exception:
                err_text = type(exc).__name__
            _append_log("exception", err_text)
            if "not a valid" in err_text or "Unknown built-in" in err_text or "invalid_request_error" in err_text:
                try:
                    omni_cfg = getattr(settings, "OMNIROUTE", {})
                    base = omni_cfg.get("BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
                    timeout = omni_cfg.get("TIMEOUT", 5)
                    candidates = [base + "/models", base, base + "/v1/models"]
                    model_ids = []
                    for url in candidates:
                        try:
                            with urllib.request.urlopen(url, timeout=timeout) as resp:
                                body = resp.read(65536).decode(errors="replace")
                                parsed = None
                                try:
                                    parsed = json.loads(body)
                                except Exception:
                                    try:
                                        parsed = json.loads(json.loads(body))
                                    except Exception:
                                        parsed = None
                                if isinstance(parsed, dict):
                                    data = parsed.get("data") or parsed.get("models") or parsed
                                    if isinstance(data, list):
                                        for it in data:
                                            if isinstance(it, dict) and it.get("id"):
                                                model_ids.append(it.get("id"))
                                    elif isinstance(parsed.get("data"), str):
                                        try:
                                            dd = json.loads(parsed.get("data"))
                                            if isinstance(dd, list):
                                                for it in dd:
                                                    if isinstance(it, dict) and it.get("id"):
                                                        model_ids.append(it.get("id"))
                                        except Exception:
                                            pass
                                    elif isinstance(parsed, list):
                                        for it in parsed:
                                            if isinstance(it, dict) and it.get("id"):
                                                model_ids.append(it.get("id"))
                                                if model_ids:
                                                    break
                        except Exception:
                            continue
                    suggestion_text = ""
                    if model_ids:
                        top = model_ids[:8]
                        suggestion_text = "\nÖnerilen modeller: " + ", ".join(top)
                    else:
                        suggestion_text = "\nModel listesi alınamadı; lütfen `/omni/models/` endpoint'ini kontrol edin."
                    return {"success": False, "output": f"Hata oluştu: {err_text}\n{suggestion_text}"}
                except Exception:
                    return {"success": False, "output": f"Hata oluştu: {err_text}"}
            
            # Auto-escalate model tier on model-related errors
            if current_tier and settings.MODEL_ROUTING.get("auto_escalate", True):
                max_escalations = settings.MODEL_ROUTING.get("max_escalations", 2)
                escalation_count = getattr(run_self_healing_loop, '_escalation_count', 0)
                if escalation_count < max_escalations:
                    new_tier = escalate_model_tier(current_tier, f"Model error: {err_text}")
                    if new_tier != current_tier:
                        current_tier = new_tier
                        model_config = router.tiers.get(current_tier, {})
                        model_name = model_config.get("model_id")
                        _append_log("info", f"Escalated due to: {err_text[:100]}")
                        run_self_healing_loop._escalation_count = escalation_count + 1
                        continue  # Retry with higher tier model
            return {"success": False, "output": f"Hata oluştu: {err_text}"}
    
    return {
        "success": False,
        "output": "Maksimum iterasyon sayısına ulaşıldı ancak görev tam olarak çözülemedi.",
    }


def index(request):
    # Do NOT fetch model list during page render to avoid blocking the UI.
    # The frontend will populate model choices via AJAX using `/omni/models/`.
    # Keep a local placeholder; only fetch cached model ids when handling POST to
    # avoid blocking the initial GET render.
    form = TaskForm(model_choices=[])
    model_choices = []
    result = None

    # Ensure current_project_id is set in session for file tree
    if not request.session.get('current_project_id'):
        try:
            from .projects.views import PROJECTS, PROJECTS_LOCK
            with PROJECTS_LOCK:
                if PROJECTS:
                    # Get most recently updated project
                    latest_project = max(PROJECTS.values(), key=lambda p: p.get('updated_at', ''))
                    request.session['current_project_id'] = latest_project['id']
                    request.session.modified = True
        except Exception:
            pass

    if request.method == "POST":
        # try to fetch a short-lived cached model list for the POST handling
        try:
            from .views import get_model_ids_cached
            model_choices = get_model_ids_cached() or []
        except Exception:
            model_choices = []
        form = TaskForm(request.POST, model_choices=model_choices)
        if form.is_valid():
            task = form.cleaned_data["task"]
            model = form.cleaned_data.get("model")
            # interpret 'auto' as None to use settings default
            if model == "auto" or not model:
                model = None
            result = run_self_healing_loop(task, model_name=model)
    
    # Determine provider type
    use_nvidia = getattr(settings, "OMNIROUTE", {}).get("BASE_URL", "").startswith("https://integrate.api.nvidia.com")
    
    return render(
        request,
        "agent_app/chat.html",
        {
            "form": form, 
            "result": result, 
            "default_model": getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "auto/best-chat"),
            "provider": "nvidia" if use_nvidia else "omniroute"
        },
    )


def landing_page(request):
    """Render the landing page with Replit-style layout."""
    return render(request, "agent_app/landing.html")


def runs_page(request):
    """Render the full recent-runs history screen."""
    return render(request, "agent_app/runs.html")


def projects_page(request):
    """Render the projects management page."""
    return render(request, "agent_app/projects.html")