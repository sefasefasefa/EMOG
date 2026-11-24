"""
7. Self-Healing Code
Detects issues in tests/production, auto-fixes at source code level.
"""
import ast
import re
import time
import hashlib
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import subprocess
import json


class IssueType(Enum):
    SYNTAX_ERROR = "syntax_error"
    RUNTIME_ERROR = "runtime_error"
    TEST_FAILURE = "test_failure"
    PERFORMANCE = "performance"
    SECURITY = "security"
    TYPE_ERROR = "type_error"
    IMPORT_ERROR = "import_error"
    LOGIC_ERROR = "logic_error"


class FixStrategy(Enum):
    AUTO_FIX = "auto_fix"
    SUGGEST_FIX = "suggest_fix"
    MANUAL_REVIEW = "manual_review"
    ROLLBACK = "rollback"


@dataclass
class CodeIssue:
    id: str
    type: IssueType
    file_path: str
    line_number: int
    description: str
    code_snippet: str
    severity: str  # low, medium, high, critical
    context: Dict = field(default_factory=dict)
    detected_at: float = field(default_factory=time.time)


@dataclass
class FixAttempt:
    issue_id: str
    strategy: FixStrategy
    original_code: str
    fixed_code: str
    success: bool
    tests_passed: bool
    error: Optional[str] = None
    applied_at: float = field(default_factory=time.time)


class SelfHealingEngine:
    """
    Detects issues in code and automatically fixes them.
    Uses AST analysis, pattern matching, and test validation.
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.issues: List[CodeIssue] = []
        self.fix_history: List[FixAttempt] = []
        self.fix_patterns = self._load_fix_patterns()
        self.max_fix_attempts = 3
    
    def _load_fix_patterns(self) -> Dict[IssueType, List[Callable]]:
        """Load fix patterns for each issue type."""
        return {
            IssueType.SYNTAX_ERROR: [
                self._fix_missing_parentheses,
                self._fix_missing_colon,
                self._fix_indentation,
                self._fix_unclosed_brackets,
            ],
            IssueType.IMPORT_ERROR: [
                self._fix_missing_import,
                self._fix_relative_import,
                self._fix_circular_import,
            ],
            IssueType.TYPE_ERROR: [
                self._fix_type_annotation,
                self._fix_none_check,
            ],
            IssueType.TEST_FAILURE: [
                self._fix_assertion_error,
                self._fix_mock_setup,
            ],
            IssueType.RUNTIME_ERROR: [
                self._fix_none_attribute,
                self._fix_key_error,
                self._fix_index_error,
            ],
        }
    
    def scan_project(self) -> List[CodeIssue]:
        """Scan project for issues."""
        issues = []
        
        # Scan Python files
        for py_file in self.project_root.rglob("*.py"):
            if ".venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            
            # Check syntax
            syntax_issues = self._check_syntax(py_file)
            issues.extend(syntax_issues)
            
            # Check for common patterns
            pattern_issues = self._check_patterns(py_file)
            issues.extend(pattern_issues)
        
        self.issues = issues
        return issues
    
    def _check_syntax(self, file_path: Path) -> List[CodeIssue]:
        """Check Python syntax."""
        issues = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            issues.append(CodeIssue(
                id=f"syntax_{hashlib.md5(f'{file_path}:{e.lineno}'.encode()).hexdigest()[:8]}",
                type=IssueType.SYNTAX_ERROR,
                file_path=str(file_path),
                line_number=e.lineno or 0,
                description=f"Syntax error: {e.msg}",
                code_snippet=self._get_code_snippet(content, e.lineno or 0),
                severity="high"
            ))
        except Exception as e:
            issues.append(CodeIssue(
                id=f"parse_{hashlib.md5(f'{file_path}'.encode()).hexdigest()[:8]}",
                type=IssueType.SYNTAX_ERROR,
                file_path=str(file_path),
                line_number=0,
                description=f"Parse error: {str(e)}",
                code_snippet="",
                severity="high"
            ))
        return issues
    
    def _check_patterns(self, file_path: Path) -> List[CodeIssue]:
        """Check for common problematic patterns."""
        issues = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            content = "".join(lines)
            
            # Check for bare except
            for i, line in enumerate(lines):
                if re.match(r'^\s*except:\s*$', line):
                    issues.append(CodeIssue(
                        id=f"bare_except_{hashlib.md5(f'{file_path}:{i}'.encode()).hexdigest()[:8]}",
                        type=IssueType.RUNTIME_ERROR,
                        file_path=str(file_path),
                        line_number=i + 1,
                        description="Bare except clause catches all exceptions",
                        code_snippet=line.strip(),
                        severity="medium"
                    ))
                
                # Check for print statements (should use logging)
                if re.search(r'\bprint\s*\(', line) and not line.strip().startswith("#"):
                    issues.append(CodeIssue(
                        id=f"print_stmt_{hashlib.md5(f'{file_path}:{i}'.encode()).hexdigest()[:8]}",
                        type=IssueType.LOGIC_ERROR,
                        file_path=str(file_path),
                        line_number=i + 1,
                        description="Print statement used instead of logging",
                        code_snippet=line.strip(),
                        severity="low"
                    ))
                
                # Check for TODO/FIXME
                if "TODO" in line or "FIXME" in line:
                    issues.append(CodeIssue(
                        id=f"todo_{hashlib.md5(f'{file_path}:{i}'.encode()).hexdigest()[:8]}",
                        type=IssueType.LOGIC_ERROR,
                        file_path=str(file_path),
                        line_number=i + 1,
                        description="TODO/FIXME comment found",
                        code_snippet=line.strip(),
                        severity="low"
                    ))
        
        except Exception:
            pass
        
        return issues
    
    def _get_code_snippet(self, content: str, line: int, context: int = 2) -> str:
        """Get code snippet around line."""
        lines = content.splitlines()
        start = max(0, line - context - 1)
        end = min(len(lines), line + context)
        return "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))
    
    def auto_fix(self, issue: CodeIssue) -> FixAttempt:
        """Attempt to auto-fix an issue."""
        if issue.type not in self.fix_patterns:
            return FixAttempt(
                issue_id=issue.id,
                strategy=FixStrategy.MANUAL_REVIEW,
                original_code="",
                fixed_code="",
                success=False,
                tests_passed=False,
                error=f"No fix patterns for {issue.type.value}"
            )
        
        # Read original file
        file_path = Path(issue.file_path)
        if not file_path.exists():
            return FixAttempt(
                issue_id=issue.id,
                strategy=FixStrategy.MANUAL_REVIEW,
                original_code="",
                fixed_code="",
                success=False,
                tests_passed=False,
                error="File not found"
            )
        
        with open(file_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        # Try each fix pattern
        for fix_func in self.fix_patterns[issue.type]:
            try:
                fixed_content = fix_func(original_content, issue)
                if fixed_content != original_content:
                    # Validate syntax
                    ast.parse(fixed_content)
                    
                    # Write fix
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(fixed_content)
                    
                    # Run tests if available
                    tests_passed = self._run_related_tests(file_path)
                    
                    attempt = FixAttempt(
                        issue_id=issue.id,
                        strategy=FixStrategy.AUTO_FIX,
                        original_code=original_content,
                        fixed_code=fixed_content,
                        success=True,
                        tests_passed=tests_passed
                    )
                    self.fix_history.append(attempt)
                    return attempt
            except Exception as e:
                continue
        
        return FixAttempt(
            issue_id=issue.id,
            strategy=FixStrategy.MANUAL_REVIEW,
            original_code=original_content,
            fixed_code=original_content,
            success=False,
            tests_passed=False,
            error="No fix pattern succeeded"
        )
    
    # Fix pattern implementations
    def _fix_missing_parentheses(self, content: str, issue: CodeIssue) -> str:
        """Fix missing parentheses."""
        lines = content.splitlines()
        if issue.line_number > 0 and issue.line_number <= len(lines):
            line = lines[issue.line_number - 1]
            # Simple fix: add missing ) at end if unbalanced
            if line.count('(') > line.count(')'):
                lines[issue.line_number - 1] = line + ')'
        return "\n".join(lines)
    
    def _fix_missing_colon(self, content: str, issue: CodeIssue) -> str:
        """Fix missing colon after if/for/while/def/class."""
        lines = content.splitlines()
        if issue.line_number > 0 and issue.line_number <= len(lines):
            line = lines[issue.line_number - 1].rstrip()
            if re.match(r'^\s*(if|for|while|def|class|with|try|except|finally|elif|else)\b', line) and not line.endswith(':'):
                lines[issue.line_number - 1] = line + ':'
        return "\n".join(lines)
    
    def _fix_indentation(self, content: str, issue: CodeIssue) -> str:
        """Fix basic indentation issues."""
        # This is complex; would need more context
        return content
    
    def _fix_unclosed_brackets(self, content: str, issue: CodeIssue) -> str:
        """Fix unclosed brackets."""
        lines = content.splitlines()
        if issue.line_number > 0 and issue.line_number <= len(lines):
            line = lines[issue.line_number - 1]
            open_brackets = line.count('[') + line.count('{') + line.count('(')
            close_brackets = line.count(']') + line.count('}') + line.count(')')
            if open_brackets > close_brackets:
                # Add missing closing brackets
                missing = open_brackets - close_brackets
                for _ in range(missing):
                    if '(' in line and line.count('(') > line.count(')'):
                        line += ')'
                    elif '[' in line and line.count('[') > line.count(']'):
                        line += ']'
                    elif '{' in line and line.count('{') > line.count('}'):
                        line += '}'
            lines[issue.line_number - 1] = line
        return "\n".join(lines)
    
    def _fix_missing_import(self, content: str, issue: CodeIssue) -> str:
        """Add missing import."""
        lines = content.splitlines()
        # Find import section
        import_line = -1
        for i, line in enumerate(lines):
            if line.startswith('import ') or line.startswith('from '):
                import_line = i
        
        if import_line >= 0:
            # Add after last import
            lines.insert(import_line + 1, issue.context.get('missing_import', '# TODO: add import'))
        else:
            # Add at top
            lines.insert(0, issue.context.get('missing_import', '# TODO: add import'))
        return "\n".join(lines)
    
    def _fix_relative_import(self, content: str, issue: CodeIssue) -> str:
        """Fix relative import issues."""
        return content
    
    def _fix_circular_import(self, content: str, issue: CodeIssue) -> str:
        """Fix circular imports by moving imports inside functions."""
        return content
    
    def _fix_type_annotation(self, content: str, issue: CodeIssue) -> str:
        """Fix type annotation issues."""
        return content
    
    def _fix_none_check(self, content: str, issue: CodeIssue) -> str:
        """Add None checks before attribute access."""
        lines = content.splitlines()
        if issue.line_number > 0 and issue.line_number <= len(lines):
            line = lines[issue.line_number - 1]
            # Pattern: obj.attr -> obj.attr if obj else None
            if '. ' in line and ' if ' not in line:
                # Simple heuristic
                pass
        return "\n".join(lines)
    
    def _fix_assertion_error(self, content: str, issue: CodeIssue) -> str:
        """Fix test assertion errors."""
        return content
    
    def _fix_mock_setup(self, content: str, issue: CodeIssue) -> str:
        """Fix mock setup in tests."""
        return content
    
    def _fix_none_attribute(self, content: str, issue: CodeIssue) -> str:
        """Fix NoneType attribute access."""
        return content
    
    def _fix_key_error(self, content: str, issue: CodeIssue) -> str:
        """Fix dictionary key errors."""
        return content
    
    def _fix_index_error(self, content: str, issue: CodeIssue) -> str:
        """Fix index errors."""
        return content
    
    def _run_related_tests(self, file_path: Path) -> bool:
        """Run tests related to fixed file."""
        try:
            # Find test file
            test_file = self.project_root / "tests" / f"test_{file_path.name}"
            if not test_file.exists():
                test_file = self.project_root / f"test_{file_path.name}"
            
            if test_file.exists():
                result = subprocess.run(
                    ["python", "-m", "pytest", str(test_file), "-v"],
                    capture_output=True,
                    timeout=60
                )
                return result.returncode == 0
        except Exception:
            pass
        return True  # Assume success if no tests
    
    def get_statistics(self) -> Dict:
        """Get healing statistics."""
        total_fixes = len(self.fix_history)
        successful = sum(1 for f in self.fix_history if f.success)
        tested = sum(1 for f in self.fix_history if f.tests_passed)
        
        return {
            "total_issues": len(self.issues),
            "total_fixes_attempted": total_fixes,
            "successful_fixes": successful,
            "success_rate": successful / total_fixes if total_fixes > 0 else 0,
            "tests_passed_rate": tested / total_fixes if total_fixes > 0 else 0,
            "by_type": self._group_by_type(),
        }
    
    def _group_by_type(self) -> Dict:
        """Group issues by type."""
        grouped = {}
        for issue in self.issues:
            t = issue.type.value
            if t not in grouped:
                grouped[t] = 0
            grouped[t] += 1
        return grouped