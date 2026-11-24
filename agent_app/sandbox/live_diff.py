"""
Live Diff Streaming - Streaming Diff like Cursor
Applies only changed lines as streaming diff with instant editor highlighting.
"""
import os
import json
import difflib
import threading
import time
import hashlib
import uuid
import asyncio
import io
from typing import Dict, Any, Optional, List, Callable, Generator, AsyncGenerator, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from collections import defaultdict, deque
from functools import wraps
import threading
from .docker_sandbox import ResourceLimits
from .project_integration import ProjectSandboxIntegration

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


class DiffOperation(Enum):
    EQUAL = "equal"
    INSERT = "insert"
    DELETE = "delete"
    REPLACE = "replace"


@dataclass
class DiffChunk:
    """A single diff chunk"""
    operation: DiffOperation
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)
    checksum: str = ""


@dataclass
class StreamingDiff:
    """Streaming diff result"""
    file_path: str
    chunks: List[DiffChunk] = field(default_factory=list)
    total_chunks: int = 0
    current_chunk: int = 0
    is_complete: bool = False
    checksum_old: str = ""
    checksum_new: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class DiffEngine:
    """
    Streaming Diff Engine - Computes and streams diffs like Cursor.
    Supports line-level and word-level diffs with streaming.
    """
    
    def __init__(self, chunk_size: int = 50, context_lines: int = 3):
        self.chunk_size = chunk_size
        self.context_lines = context_lines
        self._diff_cache: Dict[str, List[DiffChunk]] = {}
        self._lock = threading.Lock()
    
    def compute_diff(self, old_content: str, new_content: str, 
                    file_path: str = "") -> StreamingDiff:
        """
        Compute full diff between old and new content.
        Returns complete diff with all chunks.
        """
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        # Use difflib for efficient diff computation
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        chunks = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                op = DiffOperation.EQUAL
            elif tag == 'insert':
                op = DiffOperation.INSERT
            elif tag == 'delete':
                op = DiffOperation.DELETE
            else:  # replace
                op = DiffOperation.REPLACE
            
            old_lines_chunk = old_lines[i1:i2] if i1 != i2 else []
            new_lines_chunk = new_lines[j1:j2] if j1 != j2 else []
            
            chunk = DiffChunk(
                operation=op,
                old_start=i1,
                old_end=i2,
                new_start=j1,
                new_end=j2,
                old_lines=old_lines_chunk,
                new_lines=new_lines_chunk,
                checksum=hashlib.md5((''.join(old_lines_chunk) + ''.join(new_lines_chunk)).encode()).hexdigest()
            )
            chunks.append(chunk)
        
        checksum_old = hashlib.md5(old_content.encode()).hexdigest()
        checksum_new = hashlib.md5(new_content.encode()).hexdigest()
        
        return StreamingDiff(
            file_path=file_path,
            chunks=chunks,
            total_chunks=len(chunks),
            checksum_old=checksum_old,
            checksum_new=checksum_new
        )
    
    def stream_diff(self, old_content: str, new_content: str,
                   file_path: str = "", 
                   chunk_size: int = None) -> Generator[DiffChunk, None, None]:
        """
        Stream diff chunks one by one (generator).
        Yields chunks as they are computed for real-time streaming.
        """
        chunk_size = chunk_size or self.chunk_size
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        chunk_buffer = []
        chunk_count = 0
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                op = DiffOperation.EQUAL
            elif tag == 'insert':
                op = DiffOperation.INSERT
            elif tag == 'delete':
                op = DiffOperation.DELETE
            else:
                op = DiffOperation.REPLACE
            
            old_lines_chunk = old_lines[i1:i2] if i1 != i2 else []
            new_lines_chunk = new_lines[j1:j2] if j1 != j2 else []
            
            chunk = DiffChunk(
                operation=op,
                old_start=i1,
                old_end=i2,
                new_start=j1,
                new_end=j2,
                old_lines=old_lines_chunk,
                new_lines=new_lines_chunk,
                checksum=hashlib.md5((''.join(old_lines_chunk) + ''.join(new_lines_chunk)).encode()).hexdigest()
            )
            
            chunk_buffer.append(chunk)
            chunk_count += 1
            
            # Yield when buffer reaches chunk_size or at end of operation
            if chunk_count >= chunk_size or tag != 'equal':
                for chunk in chunk_buffer:
                    yield chunk
                chunk_buffer = []
                chunk_count = 0
        
        # Yield remaining
        for chunk in chunk_buffer:
            yield chunk
    
    async def async_stream_diff(self, old_content: str, new_content: str,
                               file_path: str = "",
                               chunk_size: int = None) -> AsyncGenerator[DiffChunk, None]:
        """Async version of stream_diff for non-blocking operation"""
        for chunk in self.stream_diff(old_content, new_content, file_path, chunk_size):
            yield chunk
            await asyncio.sleep(0)  # Yield control
    
    def apply_diff(self, content: str, diff: StreamingDiff, 
                  dry_run: bool = False) -> Tuple[str, List[DiffChunk]]:
        """
        Apply a streaming diff to content.
        Returns (new_content, applied_chunks)
        """
        lines = content.splitlines(keepends=True)
        applied = []
        offset = 0  # Line offset due to previous changes
        
        for chunk in diff.chunks:
            if chunk.operation == DiffOperation.EQUAL:
                continue
            
            old_start = chunk.old_start + offset
            old_end = chunk.old_end + offset
            
            if chunk.operation == DiffOperation.DELETE:
                # Remove lines
                if not dry_run:
                    del lines[old_start:old_end]
                offset -= (chunk.old_end - chunk.old_start)
                
            elif chunk.operation == DiffOperation.INSERT:
                # Insert new lines
                if not dry_run:
                    for i, line in enumerate(chunk.new_lines):
                        lines.insert(chunk.new_start + offset + i, line)
                offset += len(chunk.new_lines)
                
            elif chunk.operation == DiffOperation.REPLACE:
                # Replace lines
                if not dry_run:
                    del lines[old_start:old_end]
                    for i, line in enumerate(chunk.new_lines):
                        lines.insert(chunk.new_start + offset + i, line)
                offset += len(chunk.new_lines) - (chunk.old_end - chunk.old_start)
            
            applied.append(chunk)
        
        new_content = ''.join(lines)
        return new_content, applied
    
    def get_unified_diff(self, old_content: str, new_content: str, 
                        file_path: str = "") -> str:
        """Generate unified diff format (like git diff)"""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{file_path}" if file_path else "a",
            tofile=f"b/{file_path}" if file_path else "b",
            lineterm=""
        )
        return '\n'.join(diff)
    
    def get_side_by_side_diff(self, old_content: str, new_content: str) -> List[Dict]:
        """Generate side-by-side diff for UI display"""
        old_lines = old_content.splitlines(keepends=False)
        new_lines = new_content.splitlines(keepends=False)
        
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        result = []
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for k in range(i1, i2):
                    result.append({
                        'type': 'equal',
                        'old_line': i1 + 1,
                        'new_line': j1 + 1,
                        'content': old_lines[k]
                    })
            elif tag == 'delete':
                for k in range(i1, i2):
                    result.append({
                        'type': 'delete',
                        'old_line': i1 + 1,
                        'content': old_lines[k]
                    })
            elif tag == 'insert':
                for k in range(j1, j2):
                    result.append({
                        'type': 'insert',
                        'new_line': j1 + 1,
                        'content': new_lines[k]
                    })
            elif tag == 'replace':
                # Show as delete + insert
                for k in range(i1, i2):
                    result.append({
                        'type': 'delete',
                        'old_line': i1 + 1,
                        'content': old_lines[k]
                    })
                for k in range(j1, j2):
                    result.append({
                        'type': 'insert',
                        'new_line': j1 + 1,
                        'content': new_lines[k]
                    })
        
        return result


class LiveDiffStreamer:
    """
    Live diff streaming for real-time collaborative editing.
    Manages diff streams between editor and server.
    """
    
    def __init__(self):
        self._streams: Dict[str, asyncio.Queue] = {}
        self._sessions: Dict[str, Dict] = {}
        self._lock = threading.Lock()
        self._diff_engine = DiffEngine()
    
    def create_stream(self, session_id: str, file_path: str, 
                     initial_content: str) -> str:
        """Create a new diff stream session"""
        stream_id = hashlib.md5(f"{session_id}{time.time()}".encode()).hexdigest()[:16]
        
        with self._lock:
            self._streams[stream_id] = asyncio.Queue()
            self._sessions[stream_id] = {
                'session_id': session_id,
                'file_path': file_path,
                'content': '',
                'version': 0,
                'created_at': datetime.utcnow().isoformat(),
                'last_content': initial_content
            }
        
        return stream_id
    
    async def push_change(self, stream_id: str, new_content: str) -> StreamingDiff:
        """Push a content change and get diff"""
        with self._lock:
            session = self._sessions.get(stream_id)
        if not session:
            raise ValueError(f"Stream {stream_id} not found")
        
        old_content = session['last_content']
        session['last_content'] = new_content
        session['version'] += 1
        
        # Compute diff
        diff = self._diff_engine.compute_diff(old_content, new_content)
        
        # Put in stream queue
        queue = self._streams[stream_id]
        await queue.put(diff)
        
        return diff
    
    async def subscribe(self, stream_id: str) -> AsyncGenerator[StreamingDiff, None]:
        """Subscribe to diff stream"""
        with self._lock:
            if stream_id not in self._streams:
                raise ValueError(f"Stream {stream_id} not found")
            queue = self._streams[stream_id]
        
        while True:
            try:
                diff = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield diff
            except asyncio.TimeoutError:
                # Send heartbeat
                yield StreamingDiff(
                    file_path="",
                    chunks=[],
                    total_chunks=0,
                    is_complete=False,
                    timestamp=datetime.utcnow().isoformat()
                )
    
    def close_stream(self, stream_id: str):
        """Close a diff stream"""
        with self._lock:
            if stream_id in self._streams:
                del self._streams[stream_id]
            if stream_id in self._sessions:
                del self._sessions[stream_id]


class EditorDiffIntegration:
    """
    Integrates live diff with editor (VS Code style).
    Handles applying diffs, conflict resolution, and editor sync.
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self._diff_engine = DiffEngine()
        self._live_streamer = LiveDiffStreamer()
        self._file_versions: Dict[str, str] = {}
        self._lock = threading.Lock()
    
    def get_file_content(self, file_path: str) -> str:
        """Get current file content"""
        full_path = self.project_root / file_path
        if full_path.exists():
            return full_path.read_text(encoding='utf-8')
        return ""
    
    def compute_file_diff(self, file_path: str, new_content: str) -> StreamingDiff:
        """Compute diff for a file"""
        old_content = self.get_file_content(file_path)
        
        with self._lock:
            self._file_versions[file_path] = new_content
        
        return self._diff_engine.compute_diff(
            self._file_versions.get(file_path, ""), 
            new_content,
            file_path
        )
    
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
            resource_limits=resource_limits or ResourceLimits(timeout_seconds=60),
            stdin_data=stdin_data,
        )
        
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


# =============================================================================
# LSP / Live Diff API Views
# =============================================================================

@csrf_exempt
@require_http_methods(["POST"])
def lsp_diff_stream(request):
    """
    Stream diff for file changes (SSE endpoint).
    
    POST /sandbox/diff/stream/
    Example payload:
        file_path: "path/to/file.py"
        old_content: "old content..."
        new_content: "new content..."
        stream: true
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    file_path = data.get("file_path", "")
    old_content = data.get("old_content", "")
    new_content = data.get("new_content", "")
    stream_mode = data.get("stream", False)
    
    if not file_path:
        return JsonResponse({"ok": False, "error": "file_path required"}, status=400)
    if not new_content:
        return JsonResponse({"ok": False, "error": "new_content required"}, status=400)
    
    if stream_mode:
        def generate():
            for chunk in DiffEngine().stream_diff(old_content, new_content, file_path):
                yield f"data: {json.dumps(asdict(chunk))}\n\n"
        return StreamingHttpResponse(generate(), content_type="text/event-stream")
    
    diff = DiffEngine().compute_diff(old_content, new_content, file_path)
    return JsonResponse({
        "ok": True,
        "diff": {
            "file_path": diff.file_path,
            "chunks": [asdict(c) for c in diff.chunks],
            "total_chunks": diff.total_chunks,
            "checksum_old": diff.checksum_old,
            "checksum_new": diff.checksum_new,
            "timestamp": diff.timestamp,
            "is_complete": diff.is_complete,
        }
    })
@csrf_exempt
@require_http_methods(["POST"])
def apply_diff(request):
    """
    Apply a diff to a file.
    
    POST /sandbox/diff/apply/
    Example payload:
        file_path: "path/to/file.py"
        diff: { ... StreamingDiff object ... }
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    file_path = data.get("file_path", "")
    diff_data = data.get("diff", {})
    
    if not file_path or not diff_data:
        return JsonResponse({"ok": False, "error": "file_path and diff required"}, status=400)
    
    # Reconstruct diff object
    diff = StreamingDiff(
        file_path=diff_data.get("file_path", file_path),
        chunks=[DiffChunk(**c) for c in diff_data.get("chunks", [])],
        total_chunks=diff_data.get("total_chunks", 0),
        checksum_old=diff_data.get("checksum_old", ""),
        checksum_new=diff_data.get("checksum_new", ""),
        timestamp=diff_data.get("timestamp", ""),
        is_complete=diff_data.get("is_complete", True),
    )
    
    integration = EditorDiffIntegration()
    success = integration.apply_diff_to_file(file_path, diff)
    
    if success:
        return JsonResponse({"ok": True, "message": "Diff applied successfully"})
    else:
        return JsonResponse({"ok": False, "error": "Failed to apply diff"}, status=500)


@require_http_methods(["GET"])
def file_diff(request, file_path):
    """
    Get side-by-side diff for a file.
    
    GET /sandbox/diff/file/<file_path>/?new_content=...
    """
    new_content = request.GET.get("new_content", "")
    
    if not new_content:
        return JsonResponse({"ok": False, "error": "new_content required"}, status=400)
    
    integration = EditorDiffIntegration()
    diff = integration.get_side_by_side(file_path, new_content)
    
    return JsonResponse({"ok": True, "diff": diff})


@require_http_methods(["GET"])
def unified_diff(request, file_path):
    """
    Get unified diff format for a file.
    
    GET /sandbox/diff/unified/<file_path>/?new_content=...
    """
    new_content = request.GET.get("new_content", "")
    
    if not new_content:
        return JsonResponse({"ok": False, "error": "new_content required"}, status=400)
    
    integration = EditorDiffIntegration()
    diff = integration.get_unified_diff(file_path, new_content)
    
    return JsonResponse({"ok": True, "diff": diff})


# =============================================================================
# Collaborative Editing API
# =============================================================================

@csrf_exempt
@require_http_methods(["POST"])
def collab_create_document(request):
    """
    Create a new collaborative document.
    
    POST /sandbox/collab/create/
    Example payload:
        content: "initial content"
        file_path: "path/to/file.py"
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    content = data.get("content", "")
    file_path = data.get("file_path", "")
    
    streamer = LiveDiffStreamer()
    stream_id = streamer.create_stream("collab", file_path, content)
    
    return JsonResponse({
        "ok": True,
        "stream_id": stream_id,
        "status": "created",
    })


@require_http_methods(["GET"])
def collab_document_state(request, stream_id):
    """
    Get the current state of a collaborative document.
    
    GET /sandbox/collab/<stream_id>/state/
    """
    streamer = LiveDiffStreamer()
    session = streamer.get_execution(stream_id)
    
    if not session:
        return JsonResponse({"ok": False, "error": "Stream not found"}, status=404)
    
    return JsonResponse({
        "ok": True,
        "session": {
            "stream_id": session['session_id'],
            "file_path": session['file_path'],
            "content": session['content'],
            "version": session['version'],
            "created_at": session['created_at'],
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def collab_apply_operation(request, stream_id):
    """
    Apply an operation to a collaborative document.
    
    POST /sandbox/collab/<stream_id>/operation/
    Example payload:
        operation: "insert|delete|replace"
        position: 10
        text: "new text"
        length: 5
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    operation = data.get("operation")
    position = data.get("position", 0)
    text = data.get("text", "")
    length = data.get("length", 0)
    
    if not operation:
        return JsonResponse({"ok": False, "error": "operation required"}, status=400)
    
    streamer = LiveDiffStreamer()
    session = streamer.get_execution(stream_id)
    
    if not session:
        return JsonResponse({"ok": False, "error": "Stream not found"}, status=404)
    
    # Apply operation
    content = session['content']
    if operation == "insert":
        content = content[:position] + text + content[position:]
    elif operation == "delete":
        content = content[:position] + content[position + length:]
    elif operation == "replace":
        content = content[:position] + text + content[position + length:]
    
    session['content'] = content
    session['version'] += 1
    
    return JsonResponse({
        "ok": True,
        "version": session['version'],
        "content": content,
    })


@csrf_exempt
@require_http_methods(["POST"])
def collab_rollback_document(request, stream_id):
    """
    Rollback a collaborative document to a previous version.
    
    POST /sandbox/collab/<stream_id>/rollback/
    Example payload:
        version: 5
    """
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    
    version = data.get("version")
    if version is None:
        return JsonResponse({"ok": False, "error": "version required"}, status=400)
    
    # In a full implementation, would restore from history
    return JsonResponse({"ok": True, "message": "Rollback not fully implemented yet"})


@require_http_methods(["GET"])
def collab_document_history(request, stream_id):
    """
    Get history of a collaborative document.
    
    GET /sandbox/collab/<stream_id>/history/
    """
    return JsonResponse({
        "ok": True,
        "history": [],
        "message": "History not fully implemented yet"
    })