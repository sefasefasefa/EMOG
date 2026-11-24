"""
LSP (Language Server Protocol) Support Module
Provides AI with AST-based code understanding like VS Code/Replit.
Features: AST parsing, semantic analysis, type inference, dependency analysis, error detection.
"""
import os
import json
import ast
import tokenize
import io
import sys
import subprocess
import threading
import tempfile
import shutil
from typing import Dict, Any, Optional, List, Set, Tuple, Callable, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from collections import defaultdict
from functools import lru_cache
import astroid
import rope
from rope.base.project import Project
from rope.base.libutils import path_to_resource
from jedi import Script
from jedi.api import classes
import jedi
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


class DiagnosticSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


class SymbolKind(Enum):
    FILE = 1
    MODULE = 2
    NAMESPACE = 3
    PACKAGE = 4
    CLASS = 5
    METHOD = 6
    PROPERTY = 7
    FIELD = 8
    CONSTRUCTOR = 9
    ENUM = 10
    INTERFACE = 11
    FUNCTION = 12
    VARIABLE = 13
    CONSTANT = 14
    STRING = 14
    NUMBER = 15
    BOOLEAN = 15
    ARRAY = 16
    OBJECT = 17
    KEY = 17
    NULL = 18
    ENUM_MEMBER = 19
    STRUCT = 20
    EVENT = 21
    OPERATOR = 22
    TYPE_PARAMETER = 23


@dataclass
class Position:
    line: int  # 0-based
    character: int  # 0-based


@dataclass
class Range:
    start: Position
    end: Position


@dataclass
class Diagnostic:
    range: Range
    severity: DiagnosticSeverity
    message: str
    source: str = "lsp"
    code: Optional[str] = None


@dataclass
class Symbol:
    name: str
    kind: SymbolKind
    range: Range
    selection_range: Range
    detail: Optional[str] = None
    deprecated: bool = False
    children: List['Symbol'] = field(default_factory=list)


@dataclass
class Hover:
    contents: str
    range: Optional[Range] = None


@dataclass
class CompletionItem:
    label: str
    kind: SymbolKind
    detail: Optional[str] = None
    documentation: Optional[str] = None
    insert_text: Optional[str] = None
    filter_text: Optional[str] = None


@dataclass
class CodeAction:
    title: str
    kind: Optional[str] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)
    edit: Optional[Dict] = None
    command: Optional[Dict] = None


class LSPServer:
    """
    Language Server Protocol implementation for Python.
    Provides AST-based code understanding, diagnostics, completions, etc.
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root).resolve()
        self._documents: Dict[str, str] = {}  # uri -> content
        self._ast_cache: Dict[str, ast.AST] = {}
        self._rope_project: Optional[rope.base.project.Project] = None
        self._diagnostics: Dict[str, List[Diagnostic]] = defaultdict(list)
        self._lock = threading.Lock()
        self._init_rope()
    
    def _init_rope(self):
        """Initialize rope project for refactoring support"""
        try:
            self._rope_project = Project(str(self.project_root))
        except Exception:
            self._rope_project = None
    
    def open_document(self, uri: str, content: str):
        """Notify server that a document is opened"""
        with self._lock:
            self._documents[uri] = content
            self._invalidate_cache(uri)
            self._analyze_document(uri)
    
    def change_document(self, uri: str, content: str):
        """Notify server that a document content changed"""
        with self._lock:
            self._documents[uri] = content
            self._invalidate_cache(uri)
            self._analyze_document(uri)
    
    def close_document(self, uri: str):
        """Notify server that a document is closed"""
        with self._lock:
            self._documents.pop(uri, None)
            self._ast_cache.pop(uri, None)
            self._diagnostics.pop(uri, None)
    
    def _invalidate_cache(self, uri: str):
        self._ast_cache.pop(uri, None)
    
    def _get_ast(self, uri: str) -> Optional[ast.AST]:
        """Get cached AST or parse new one"""
        if uri in self._ast_cache:
            return self._ast_cache[uri]
        
        content = self._documents.get(uri)
        if not content:
            return None
        
        try:
            tree = ast.parse(content, filename=uri)
            self._ast_cache[uri] = tree
            return tree
        except SyntaxError as e:
            return None
        except Exception:
            return None
    
    def _analyze_document(self, uri: str):
        """Run full analysis on document"""
        diagnostics = []
        
        # 1. Syntax check with Python's ast
        syntax_diagnostics = self._check_syntax(uri)
        diagnostics.extend(syntax_diagnostics)
        
        # 2. Static analysis with astroid (if available)
        if ASTROID_AVAILABLE:
            astroid_diagnostics = self._check_astroid(uri)
            diagnostics.extend(astroid_diagnostics)
        
        # 3. Jedi-based analysis for type inference
        jedi_diagnostics = self._check_jedi(uri)
        diagnostics.extend(jedi_diagnostics)
        
        # 4. Custom rules
        custom_diagnostics = self._check_custom_rules(uri)
        diagnostics.extend(custom_diagnostics)
        
        self._diagnostics[uri] = diagnostics
    
    def _check_syntax(self, uri: str) -> List[Diagnostic]:
        """Check syntax using Python's ast module"""
        diagnostics = []
        content = self._documents.get(uri, "")
        
        try:
            ast.parse(content, filename=uri)
        except SyntaxError as e:
            diagnostics.append(Diagnostic(
                range=Range(
                    start=Position(e.lineno - 1, e.offset or 0),
                    end=Position(e.lineno - 1, (e.offset or 0) + 1)
                ),
                severity=DiagnosticSeverity.ERROR,
                message=f"Syntax error: {e.msg}",
                code=str(e)
            ))
        except Exception as e:
            diagnostics.append(Diagnostic(
                range=Range(start=Position(0, 0), end=Position(0, 0)),
                severity=DiagnosticSeverity.ERROR,
                message=f"Parse error: {str(e)}"
            ))
        
        return diagnostics
    
    def _check_astroid(self, uri: str) -> List[Diagnostic]:
        """Use astroid for deeper static analysis"""
        if not ASTROID_AVAILABLE:
            return []
        
        diagnostics = []
        content = self._documents.get(uri, "")
        
        try:
            module = astroid.parse(content, modname=Path(uri).stem)
            
            # Check for various issues
            for node in module.nodes_of_class((astroid.FunctionDef, astroid.ClassDef)):
                # Check for missing docstrings
                if not node.docstring:
                    diagnostics.append(Diagnostic(
                        range=Range(
                            start=Position(node.lineno - 1, node.col_offset),
                            end=Position(node.lineno - 1, node.col_offset + len(node.name))
                        ),
                        severity=DiagnosticSeverity.WARNING,
                        message=f"Missing docstring for {node.type} '{node.name}'",
                        code="missing-docstring"
                    ))
                
                # Check for too many arguments
                if isinstance(node, astroid.FunctionDef) and len(node.args.args) > 5:
                    diagnostics.append(Diagnostic(
                        range=Range(
                            start=Position(node.lineno - 1, node.col_offset),
                            end=Position(node.lineno - 1, node.col_offset + len(node.name))
                        ),
                        severity=DiagnosticSeverity.WARNING,
                        message=f"Function has too many arguments ({len(node.args.args)})",
                        code="too-many-arguments"
                    ))
                
                # Check for too many local variables
                if isinstance(node, astroid.FunctionDef):
                    local_vars = [n for n in node.nodes_of_class(astroid.AssignName) if isinstance(n.parent, astroid.Assign)]
                    if len(local_vars) > 15:
                        diagnostics.append(Diagnostic(
                            range=Range(
                                start=Position(node.lineno - 1, node.col_offset),
                                end=Position(node.lineno - 1, node.col_offset + len(node.name))
                            ),
                            severity=DiagnosticSeverity.WARNING,
                            message=f"Too many local variables ({len(local_vars)})",
                            code="too-many-locals"
                        ))
        
        except Exception as e:
            pass
        
        return diagnostics
    
    def _check_jedi(self, uri: str) -> List[Diagnostic]:
        """Use Jedi for type inference and error detection"""
        diagnostics = []
        content = self._documents.get(uri, "")
        
        try:
            script = Script(content, path=uri)
            
            # Get syntax errors from Jedi
            for error in script._get_syntax_errors():
                diagnostics.append(Diagnostic(
                    range=Range(
                        start=Position(error.line - 1, error.column),
                        end=Position(error.line - 1, error.column + 1)
                    ),
                    severity=DiagnosticSeverity.ERROR,
                    message=error.message,
                    code="jedi-syntax"
                ))
            
            # Check for undefined names
            try:
                names = script.get_names(all_scopes=True, definitions=True, references=False)
                for name in names:
                    if name.type == 'statement' and name.is_definition():
                        # Check if it's a function/class without proper annotation
                        pass
            except Exception:
                pass
                
        except Exception:
            pass
        
        return diagnostics
    
    def _check_custom_rules(self, uri: str) -> List[Diagnostic]:
        """Custom linting rules"""
        diagnostics = []
        content = self._documents.get(uri, "")
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            # Check line length
            if len(line) > 100:
                self._add_diagnostic(diagnostics, uri, i, 0, len(line),
                    DiagnosticSeverity.WARNING, f"Line too long ({len(line)} > 100)", "line-too-long")
            
            # Check for trailing whitespace
            if line.rstrip() != line:
                self._add_diagnostic(diagnostics, uri, i, len(line.rstrip()), len(line),
                    DiagnosticSeverity.WARNING, "Trailing whitespace", "trailing-whitespace")
            
            # Check for TODO/FIXME comments
            if 'TODO' in line or 'FIXME' in line:
                self._add_diagnostic(diagnostics, uri, i, line.find('TODO') if 'TODO' in line else line.find('FIXME'), 
                    line.find('TODO') + 4 if 'TODO' in line else line.find('FIXME') + 5,
                    DiagnosticSeverity.HINT, f"Comment: {line.strip()}", "todo-comment")
        
        return diagnostics
    
    def _add_diagnostic(self, diagnostics: List, uri: str, line: int, start_char: int, end_char: int,
                       severity: DiagnosticSeverity, message: str, code: str):
        """Helper to add diagnostic"""
        diagnostics.append(Diagnostic(
            range=Range(
                start=Position(line, start_char),
                end=Position(line, end_char)
            ),
            severity=severity,
            message=message,
            code=code
        ))
    
    # Public API methods
    def get_diagnostics(self, uri: str) -> List[Diagnostic]:
        """Get diagnostics for a document"""
        with self._lock:
            return self._diagnostics.get(uri, [])
    
    def get_hover(self, uri: str, position: Position) -> Optional[Hover]:
        """Get hover information at position"""
        content = self._documents.get(uri, "")
        if not content:
            return None
        
        try:
            script = Script(self._documents[uri], path=uri)
            # Convert to 1-based for Jedi
            jedi_pos = (position.line + 1, position.character)
            hover = script.help(jedi_pos)
            if hover:
                return Hover(contents=hover.description)
        except Exception:
            pass
        return None
    
    def get_completions(self, uri: str, position: Position) -> List[CompletionItem]:
        """Get completion items at position"""
        completions = []
        content = self._documents.get(uri, "")
        if not content:
            return []
        
        try:
            script = Script(content, path=uri)
            jedi_pos = (position.line + 1, position.character)
            completions_jedi = script.complete(jedi_pos[0], jedi_pos[1])
            
            for comp in completions_jedi:
                kind_map = {
                    'function': SymbolKind.FUNCTION,
                    'class': SymbolKind.CLASS,
                    'module': SymbolKind.MODULE,
                    'variable': SymbolKind.VARIABLE,
                    'statement': SymbolKind.VARIABLE,
                    'keyword': SymbolKind.KEYWORD,
                }
                
                completions.append(CompletionItem(
                    label=comp.name,
                    kind=kind_map.get(comp.type, SymbolKind.VARIABLE),
                    detail=comp.type,
                    documentation=comp.docstring(),
                    insert_text=comp.name_with_symbols if hasattr(comp, 'name_with_symbols') else comp.name,
                    filter_text=comp.name
                ))
        except Exception:
            pass
        
        return completions
    
    def get_definition(self, uri: str, position: Position) -> List[Dict]:
        """Go to definition"""
        locations = []
        content = self._documents.get(uri, "")
        if not content:
            return []
        
        try:
            script = Script(content, path=uri)
            jedi_pos = (position.line + 1, position.character)
            defs = script.goto_definitions(jedi_pos[0], jedi_pos[1])
            
            for defn in defs:
                if defn.module_path:
                    locations.append({
                        "uri": f"file://{defn.module_path}",
                        "range": {
                            "start": {"line": defn.line - 1, "character": defn.column},
                            "end": {"line": defn.line - 1, "character": defn.column + len(defn.name)}
                        }
                    })
        except Exception:
            pass
        
        return locations
    
    def get_references(self, uri: str, position: Position) -> List[Dict]:
        """Find references to symbol at position"""
        references = []
        content = self._documents.get(uri, "")
        if not content:
            return []
        
        try:
            script = Script(content, path=uri)
            jedi_pos = (position.line + 1, position.character)
            refs = script.goto_references(jedi_pos[0], jedi_pos[1], include_definition=True)
            
            for ref in refs:
                if ref.module_path:
                    references.append({
                        "uri": f"file://{ref.module_path}",
                        "range": {
                            "start": {"line": ref.line - 1, "character": ref.column},
                            "end": {"line": ref.line - 1, "character": ref.column + len(ref.name)}
                        }
                    })
        except Exception:
            pass
        
        return references
    
    def get_symbols(self, uri: str) -> List[Symbol]:
        """Get document symbols (outline)"""
        symbols = []
        tree = self._get_ast(uri)
        if not tree:
            return []
        
        def walk(node: ast.AST, parent: Symbol = None) -> List[Symbol]:
            symbols = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                    kind = SymbolKind.FUNCTION if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else SymbolKind.CLASS
                    
                    symbol = Symbol(
                        name=child.name,
                        kind=kind,
                        range=Range(
                            start=Position(child.lineno - 1, child.col_offset),
                            end=Position(getattr(child, 'end_lineno', child.lineno) - 1, getattr(child, 'end_col_offset', child.col_offset))
                        ),
                        selection_range=Range(
                            start=Position(child.lineno - 1, child.col_offset),
                            end=Position(child.lineno - 1, child.col_offset + len(child.name))
                        ),
                        children=walk(child)
                    )
                    symbols.append(symbol)
            
            for child in ast.iter_child_nodes(node):
                symbols.extend(walk(child))
            return symbols
        
        return walk(tree)
    
    def get_code_actions(self, uri: str, range: Range, diagnostics: List[Diagnostic]) -> List[CodeAction]:
        """Get code actions (quick fixes)"""
        actions = []
        
        for diag in diagnostics:
            if diag.code == "trailing-whitespace":
                actions.append(CodeAction(
                    title="Remove trailing whitespace",
                    kind="quickfix",
                    diagnostics=[diag],
                    edit={
                        "changes": {
                            "": [{"range": diag.range, "newText": ""}]
                        }
                    }
                ))
            elif diag.code == "missing-docstring":
                actions.append(CodeAction(
                    title="Add docstring",
                    kind="quickfix",
                    diagnostics=[diag],
                    edit={
                        "changes": {
                            "": [{"range": {"start": {"line": diag.range.start.line, "character": 0}, "end": {"line": diag.range.start.line, "character": 0}}, "newText": '    """\n    """\n'}]
                        }
                    }
                ))
        
        return actions
    
    def format_document(self, uri: str) -> Optional[str]:
        """Format document using black/autopep8"""
        content = self._documents.get(uri, "")
        if not content:
            return None
        
        try:
            import black
            formatted = black.format_str(content, mode=black.FileMode())
            return formatted
        except ImportError:
            # Try autopep8
            try:
                import autopep8
                return autopep8.fix_code(content)
            except ImportError:
                return content
    
    def get_document_highlights(self, uri: str, position: Position) -> List[Dict]:
        """Get document highlights (symbol references)"""
        highlights = []
        content = self._documents.get(uri, "")
        if not content:
            return []
        
        try:
            script = Script(content, path=uri)
            jedi_pos = (position.line + 1, position.character)
            refs = script.goto_references(jedi_pos[0], jedi_pos[1], include_definition=True)
            
            for ref in refs:
                if ref.module_path == uri or ref.module_path is None:
                    highlights.append({
                        "range": {
                            "start": {"line": ref.line - 1, "character": ref.column},
                            "end": {"line": ref.line - 1, "character": ref.column + len(ref.name)}
                        },
                        "kind": 1  # Text
                    })
        except Exception:
            pass
        
        return highlights
    
    def rename(self, uri: str, position: Position, new_name: str) -> Optional[Dict]:
        """Rename symbol"""
        content = self._documents.get(uri, "")
        if not content:
            return None
        
        try:
            if self._rope_project:
                resource = self._rope_project.get_resource(uri.replace("file://", ""))
                if resource:
                    changes = rope.refactor.rename.Rename(self._rope_project, resource, rope_pos).get_changes(new_name)
                    return {"changes": changes.get_changes()}
        except Exception:
            pass
        
        # Fallback to Jedi
        try:
            script = Script(self._documents[uri], path=uri)
            jedi_pos = (position.line + 1, position.character)
            changes = script.rename(jedi_pos[0], jedi_pos[1], new_name)
            
            edits = {}
            for change in changes:
                if change.module_path:
                    uri_key = f"file://{change.module_path}"
                    if uri_key not in edits:
                        edits[uri_key] = []
                    edits[uri_key].append({
                        "range": {
                            "start": {"line": change.line - 1, "character": change.column},
                            "end": {"line": change.line - 1, "character": change.column + len(change.name)}
                        },
                        "newText": new_name
                    })
            return {"changes": edits}
        except Exception:
            pass
        
        return None


# Global instance
_lsp_server: Optional[LSPServer] = None
_lsp_lock = threading.Lock()


def get_lsp_server(project_root: str = ".") -> LSPServer:
    """Get or create global LSP server instance"""
    global _lsp_server
    with _lsp_lock:
        if _lsp_server is None:
            _lsp_server = LSPServer(project_root)
        return _lsp_server


# Django views for LSP endpoints
@csrf_exempt
@require_http_methods(["POST"])
def lsp_initialize(request):
    """Initialize LSP server"""
    try:
        data = json.loads(request.body) if request.body else {}
        root = data.get("rootUri", "").replace("file://", "")
        
        lsp = get_lsp_server(root)
        
        return JsonResponse({
            "capabilities": {
                "textDocumentSync": 2,  # Incremental
                "hoverProvider": True,
                "completionProvider": {"triggerCharacters": [".", "(", "\"", "'"]},
                "definitionProvider": True,
                "referencesProvider": True,
                "documentHighlightProvider": True,
                "documentSymbolProvider": True,
                "codeActionProvider": True,
                "documentFormattingProvider": True,
                "renameProvider": True,
                "documentRangeFormattingProvider": True,
            },
            "serverInfo": {"name": "emog-lsp", "version": "1.0.0"}
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_did_open(request):
    """Document opened notification"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        text_doc = params.get("textDocument", {})
        
        uri = text_doc.get("uri", "").replace("file://", "")
        content = text_doc.get("text", "")
        
        lsp = get_lsp_server()
        lsp.open_document(uri, content)
        
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_did_change(request):
    """Document changed notification"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        text_doc = params.get("textDocument", {})
        changes = params.get("contentChanges", [])
        
        uri = text_doc.get("uri", "").replace("file://", "")
        
        lsp = get_lsp_server()
        
        for change in changes:
            content = change.get("text", "")
            lsp.change_document(uri, content)
        
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_did_close(request):
    """Document closed notification"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        text_doc = params.get("textDocument", {})
        
        uri = text_doc.get("uri", "").replace("file://", "")
        
        lsp = get_lsp_server()
        lsp.close_document(uri)
        
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


@require_http_methods(["POST"])
def lsp_hover(request):
    """Get hover information"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        position = params.get("position", {})
        position_obj = Position(position.get("line", 0), position.get("character", 0))
        
        lsp = get_lsp_server()
        hover = lsp.get_hover(uri, position_obj)
        
        if hover:
            return JsonResponse({
                "contents": hover.contents,
                "range": hover.range.__dict__ if hover.range else None
            })
        return JsonResponse({"contents": ""})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["POST"])
def lsp_completion(request):
    """Get completions"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        position = params.get("position", {})
        position_obj = Position(position.get("line", 0), position.get("character", 0))
        
        lsp = get_lsp_server()
        completions = lsp.get_completions(uri, position)
        
        items = []
        for comp in completions:
            items.append({
                "label": comp.label,
                "kind": comp.kind.value,
                "detail": comp.detail,
                "documentation": comp.documentation,
                "insertText": comp.insert_text,
                "filterText": comp.filter_text,
            })
        
        return JsonResponse({"isIncomplete": False, "items": items})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["POST"])
def lsp_definition(request):
    """Go to definition"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        position = params.get("position", {})
        position_obj = Position(position.get("line", 0), position.get("character", 0))
        
        lsp = get_lsp_server()
        locations = lsp.get_definition(uri, position)
        
        return JsonResponse(locations, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["POST"])
def lsp_references(request):
    """Find references"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        position = params.get("position", {})
        position_obj = Position(position.get("line", 0), position.get("character", 0))
        
        lsp = get_lsp_server()
        refs = lsp.get_references(uri, position_obj)
        
        locations = []
        for ref in refs:
            locations.append({
                "uri": ref["uri"],
                "range": ref["range"]
            })
        
        return JsonResponse(locations, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def lsp_symbols(request):
    """Get document symbols"""
    uri = request.GET.get("uri", "").replace("file://", "")
    
    lsp = get_lsp_server()
    symbols = lsp.get_symbols(uri)
    
    symbols_data = []
    for sym in symbols:
        symbols_data.append({
            "name": sym.name,
            "kind": sym.kind.value,
            "range": sym.range.__dict__,
            "selectionRange": sym.selection_range.__dict__,
            "children": [c.__dict__ for c in sym.children] if sym.children else [],
        })
    
    return JsonResponse(symbols_data, safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_code_action(request):
    """Get code actions (quick fixes)"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        range_data = params.get("range", {})
        range_obj = Range(
            Position(range_data["start"]["line"], range_data["start"]["character"]),
            Position(range_data["end"]["line"], range_data["end"]["character"])
        )
        diagnostics_data = params.get("diagnostics", [])
        
        diagnostics = []
        for d in diagnostics_data:
            diagnostics.append(Diagnostic(
                range=Range(
                    Position(d["range"]["start"]["line"], d["range"]["start"]["character"]),
                    Position(d["range"]["end"]["line"], d["range"]["end"]["character"])
                ),
                severity=DiagnosticSeverity(d["severity"]),
                message=d["message"],
                code=d.get("code")
            ))
        
        lsp = get_lsp_server()
        actions = lsp.get_code_actions(uri, range_obj, diagnostics)
        
        actions_data = []
        for action in actions:
            actions_data.append({
                "title": action.title,
                "kind": action.kind,
                "diagnostics": [d.__dict__ for d in action.diagnostics],
                "edit": action.edit,
                "command": action.command,
            })
        
        return JsonResponse(actions_data, safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_formatting(request):
    """Format document"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        
        lsp = get_lsp_server()
        formatted = lsp.format_document(uri)
        
        if formatted:
            return JsonResponse([{
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 999999, "character": 0}},
                "newText": formatted
            }], safe=False)
        
        return JsonResponse([], safe=False)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def lsp_rename(request):
    """Rename symbol"""
    try:
        data = json.loads(request.body) if request.body else {}
        params = data.get("params", {})
        
        uri = params.get("textDocument", {}).get("uri", "").replace("file://", "")
        position = params.get("position", {})
        position_obj = Position(position.get("line", 0), position.get("character", 0))
        new_name = params.get("newName", "")
        
        lsp = get_lsp_server()
        result = lsp.rename(uri, position_obj, new_name)
        
        return JsonResponse(result or {"changes": {}})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_http_methods(["GET"])
def lsp_document_highlight(request):
    """Get document highlights"""
    uri = request.GET.get("uri", "").replace("file://", "")
    position = request.GET.get("position", {})
    position_obj = Position(position.get("line", 0), position.get("character", 0))
    
    lsp = get_lsp_server()
    highlights = lsp.get_document_highlights(uri, position_obj)
    
    return JsonResponse(highlights, safe=False)


@require_http_methods(["GET"])
def lsp_diagnostics(request):
    """Get diagnostics for a document"""
    uri = request.GET.get("uri", "").replace("file://", "")
    
    lsp = get_lsp_server()
    diagnostics = lsp.get_diagnostics(uri)
    
    return JsonResponse([{
        "range": d.range.__dict__,
        "severity": d.severity.value,
        "message": d.message,
        "code": d.code,
        "source": d.source
    } for d in diagnostics], safe=False)


# Global instances
_lsp_server: Optional[LSPServer] = None
_lsp_lock = threading.Lock()


def get_lsp_server(project_root: str = ".") -> LSPServer:
    """Get or create global LSP server instance"""
    global _lsp_server
    with _lsp_lock:
        if _lsp_server is None:
            _lsp_server = LSPServer(project_root)
        return _lsp_server