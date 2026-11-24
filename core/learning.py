"""
14. Error-Fed Learning Mechanism
Converts failures/runtime errors into experience inputs for continuous self-update.
"""
import asyncio
import hashlib
import time
import traceback
import sys
import json
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections import defaultdict
import ast


class ErrorCategory(Enum):
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    LOGIC = "logic"
    PERFORMANCE = "performance"
    SECURITY = "security"
    INTEGRATION = "integration"
    RESOURCE = "resource"
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"
    NETWORK = "network"


class LearningAction(Enum):
    PATTERN_EXTRACTION = "pattern_extraction"
    FIX_GENERATION = "fix_generation"
    TEST_CREATION = "test_creation"
    DOCUMENTATION = "documentation"
    RULE_UPDATE = "rule_update"
    MODEL_RETRAIN = "model_retrain"


@dataclass
class ErrorExperience:
    id: str
    error_type: str
    error_message: str
    traceback: str
    category: ErrorCategory
    context: Dict
    code_snippet: str
    file_path: str
    line_number: int
    timestamp: float
    resolution: Optional[str] = None
    fix_applied: Optional[str] = None
    tests_passed: bool = False
    recurrence_count: int = 1
    learned_patterns: List[str] = field(default_factory=list)
    prevention_rules: List[str] = field(default_factory=list)


@dataclass
class LearningRule:
    id: str
    name: str
    description: str
    condition: str  # Pattern to match
    action: LearningAction
    parameters: Dict
    confidence: float
    created_at: float
    trigger_count: int = 0
    success_count: int = 0


class ErrorClassifier:
    """Classifies errors into categories."""
    
    ERROR_PATTERNS = {
        ErrorCategory.SYNTAX: [
            "SyntaxError", "IndentationError", "TabError",
            "invalid syntax", "unexpected EOF", "unmatched"
        ],
        ErrorCategory.RUNTIME: [
            "TypeError", "ValueError", "AttributeError", "KeyError",
            "IndexError", "NameError", "ZeroDivisionError",
            "NoneType", "object has no attribute"
        ],
        ErrorCategory.LOGIC: [
            "AssertionError", "logic error", "incorrect result",
            "unexpected behavior", "wrong output"
        ],
        ErrorCategory.PERFORMANCE: [
            "TimeoutError", "MemoryError", "RecursionError",
            "too slow", "performance", "bottleneck"
        ],
        ErrorCategory.SECURITY: [
            "SecurityError", "PermissionError", "vulnerability",
            "injection", "xss", "csrf", "sql injection"
        ],
        ErrorCategory.INTEGRATION: [
            "ConnectionError", "ConnectionRefusedError", "TimeoutError",
            "HTTPError", "APIError", "service unavailable"
        ],
        ErrorCategory.RESOURCE: [
            "MemoryError", "DiskError", "ResourceExhausted",
            "out of memory", "disk full", "file descriptor"
        ],
        ErrorCategory.CONFIGURATION: [
            "ConfigError", "ConfigurationError", "missing config",
            "invalid config", "environment variable"
        ],
        ErrorCategory.DEPENDENCY: [
            "ImportError", "ModuleNotFoundError", "DistributionNotFound",
            "version conflict", "dependency"
        ],
        ErrorCategory.NETWORK: [
            "ConnectionError", "DNSError", "SSL Error",
            "certificate", "timeout", "unreachable"
        ],
    }
    
    @classmethod
    def classify(cls, error: Exception, traceback_str: str) -> ErrorCategory:
        """Classify error based on type and message."""
        error_str = f"{type(error).__name__}: {str(error)} {traceback_str}".lower()
        
        scores = defaultdict(int)
        for category, patterns in cls.ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in error_str:
                    scores[category] += 1
        
        if scores:
            return max(scores.items(), key=lambda x: x[1])[0]
        return ErrorCategory.RUNTIME
    
    @classmethod
    def extract_root_cause(cls, traceback_str: str) -> str:
        """Extract root cause from traceback."""
        lines = traceback_str.strip().split('\n')
        for line in reversed(lines):
            if any(err in line for err in ["Error:", "Exception:", "Error"]):
                return line.strip()
        return traceback_str.strip().split('\n')[-1] if traceback_str else "Unknown"


class ErrorFedLearning:
    """
    Learns from errors and failures.
    Converts failures into experience, generates fixes, updates rules.
    """
    
    def __init__(self, storage_path: str = "./error_learning.db"):
        self.storage_path = Path(storage_path)
        self.experiences: List[ErrorExperience] = []
        self.rules: List[LearningRule] = []
        self._init_storage()
        
        # Error handlers
        self._error_handlers: Dict[ErrorCategory, List[Callable]] = defaultdict(list)
    
    def _init_storage(self) -> None:
        """Initialize storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    self.experiences = [ErrorExperience(**e) for e in data.get("experiences", [])]
                    self.rules = [LearningRule(**r) for r in data.get("rules", [])]
            except Exception:
                pass
    
    def _save(self) -> None:
        """Save to storage."""
        data = {
            "experiences": [self._exp_to_dict(e) for e in self.experiences],
            "rules": [self._rule_to_dict(r) for r in self.rules],
        }
        with open(self.storage_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _exp_to_dict(self, exp: ErrorExperience) -> Dict:
        return {
            "id": exp.id, "error_type": exp.error_type, "error_message": exp.error_message,
            "traceback": exp.traceback, "category": exp.category.value,
            "context": exp.context, "code_snippet": exp.code_snippet,
            "file_path": exp.file_path, "line_number": exp.line_number,
            "timestamp": exp.timestamp, "resolution": exp.resolution,
            "fix_applied": exp.fix_applied, "tests_passed": exp.tests_passed,
            "recurrence_count": exp.recurrence_count,
            "learned_patterns": exp.learned_patterns,
            "prevention_rules": exp.prevention_rules,
        }
    
    def _rule_to_dict(self, rule: LearningRule) -> Dict:
        return {
            "id": rule.id, "name": rule.name, "description": rule.description,
            "condition": rule.condition, "action": rule.action.value,
            "parameters": rule.parameters, "confidence": rule.confidence,
            "created_at": rule.created_at, "trigger_count": rule.trigger_count,
            "success_count": rule.success_count,
        }
    
    def record_error(self, error: Exception, context: Dict = None, code_snippet: str = "", file_path: str = "", line_number: int = 0) -> ErrorExperience:
        """Record an error and learn from it."""
        traceback_str = traceback.format_exc()
        category = ErrorClassifier.classify(error, traceback_str)
        root_cause = ErrorClassifier.extract_root_cause(traceback_str)
        
        # Check if similar error exists
        existing = self._find_similar(error, traceback_str, category)
        if existing:
            existing.recurrence_count += 1
            existing.timestamp = time.time()
            self._save()
            return existing
        
        exp = ErrorExperience(
            id=f"exp_{hashlib.md5(f'{type(error).__name__}:{root_cause}:{time.time()}'.encode()).hexdigest()[:8]}",
            error_type=type(error).__name__,
            error_message=str(error),
            traceback=traceback_str,
            category=category,
            context=context or {},
            code_snippet=code_snippet,
            file_path=file_path,
            line_number=line_number,
            timestamp=time.time(),
            resolution=root_cause,
        )
        
        # Extract patterns
        exp.learned_patterns = self._extract_patterns(error, traceback_str, category)
        
        # Generate prevention rules
        exp.prevention_rules = self._generate_prevention_rules(exp)
        
        # Generate fix if possible
        fix = self._generate_fix(exp)
        if fix:
            exp.fix_applied = fix
        
        self.experiences.append(exp)
        self._save()
        
        # Trigger handlers
        self._trigger_handlers(category, exp)
        
        # Update/create rules
        self._update_rules(exp)
        
        return exp
    
    def _find_similar(self, error: Exception, traceback_str: str, category: ErrorCategory) -> Optional[ErrorExperience]:
        """Find similar existing error."""
        error_key = f"{type(error).__name__}:{ErrorClassifier.extract_root_cause(traceback_str)}"
        
        for exp in self.experiences:
            if exp.category == category and exp.error_type == type(error).__name__:
                existing_key = f"{exp.error_type}:{ErrorClassifier.extract_root_cause(exp.traceback)}"
                if error_key == existing_key:
                    return exp
        return None
    
    def _extract_patterns(self, error: Exception, traceback_str: str, category: ErrorCategory) -> List[str]:
        """Extract learnable patterns from error."""
        patterns = []
        
        # Error type pattern
        patterns.append(f"error_type:{type(error).__name__}")
        
        # Module pattern
        if "module" in str(error).lower():
            patterns.append("module_error")
        
        # Recurring pattern
        if "recursion" in str(error).lower():
            patterns.append("recursion_error")
        
        # Encoding pattern
        if "encoding" in str(error).lower() or "decode" in str(error).lower():
            patterns.append("encoding_error")
        
        # Category-specific
        if category == ErrorCategory.RUNTIME:
            if "NoneType" in str(error):
                patterns.append("null_pointer")
            if "subscriptable" in str(error):
                patterns.append("type_mismatch")
        
        return patterns
    
    def _generate_prevention_rules(self, exp: ErrorExperience) -> List[str]:
        """Generate prevention rules from experience."""
        rules = []
        
        if exp.category == ErrorCategory.SYNTAX:
            rules.append("Run syntax checker before execution")
            rules.append("Use type hints to catch errors early")
        
        elif exp.category == ErrorCategory.RUNTIME:
            if "NoneType" in exp.error_message:
                rules.append("Add None checks before attribute access")
                rules.append("Use Optional type hints")
            if "KeyError" in exp.error_type:
                rules.append("Use dict.get() instead of direct key access")
            if "IndexError" in exp.error_type:
                rules.append("Check list bounds before access")
        
        elif exp.category == ErrorCategory.PERFORMANCE:
            rules.add("Profile code before optimization")
            rules.add("Set timeouts for external calls")
        
        elif exp.category == ErrorCategory.SECURITY:
            rules.add("Sanitize all user inputs")
            rules.add("Use parameterized queries")
            rules.add("Validate file paths")
        
        elif exp.category == ErrorCategory.DEPENDENCY:
            rules.add("Pin dependency versions")
            rules.add("Run dependency audit regularly")
        
        return list(rules)
    
    def _generate_fix(self, exp: ErrorExperience) -> Optional[str]:
        """Generate fix suggestion for error."""
        fixes = {
            ErrorCategory.SYNTAX: "Check syntax with linter before running",
            ErrorCategory.RUNTIME: {
                "NoneType": "Add None check: `if obj is not None:`",
                "KeyError": "Use `.get(key, default)` instead of `dict[key]`",
                "IndexError": "Check length: `if len(list) > index:`",
                "AttributeError": "Use `hasattr(obj, 'attr')` or `getattr(obj, 'attr', default)`",
                "TypeError": "Add type checks or use type hints",
                "ZeroDivisionError": "Check divisor: `if divisor != 0:`",
            },
            ErrorCategory.PERFORMANCE: "Add caching, use async, profile bottlenecks",
            ErrorCategory.SECURITY: "Use parameterized queries, validate inputs, use secrets management",
            ErrorCategory.INTEGRATION: "Add retries, timeouts, circuit breakers",
            ErrorCategory.DEPENDENCY: "Pin versions, use virtual environments, run `pip audit`",
        }
        
        fix_info = fixes.get(exp.category)
        if isinstance(fix_info, dict):
            for key, fix in fix_info.items():
                if key in exp.error_message:
                    return fix
            return list(fix_info.values())[0]
        return fix_info
    
    def _update_rules(self, exp: ErrorExperience) -> None:
        """Update or create learning rules from experience."""
        for pattern in exp.learned_patterns:
            # Check if rule exists
            existing = next((r for r in self.rules if r.condition == pattern), None)
            
            if existing:
                existing.trigger_count += 1
                if exp.fix_applied and exp.tests_passed:
                    existing.success_count += 1
                existing.confidence = existing.success_count / existing.trigger_count
            else:
                # Create new rule
                rule = LearningRule(
                    id=f"rule_{hashlib.md5(pattern.encode()).hexdigest()[:8]}",
                    name=f"Rule for {pattern}",
                    description=f"Auto-generated from error pattern: {pattern}",
                    condition=pattern,
                    action=self._determine_action(exp.category),
                    parameters={"category": exp.category.value},
                    confidence=0.5,
                    created_at=time.time()
                )
                self.rules.append(rule)
        
        self._save()
    
    def _determine_action(self, category: ErrorCategory) -> LearningAction:
        """Determine learning action from error category."""
        mapping = {
            ErrorCategory.SYNTAX: LearningAction.PATTERN_EXTRACTION,
            ErrorCategory.RUNTIME: LearningAction.FIX_GENERATION,
            ErrorCategory.LOGIC: LearningAction.TEST_CREATION,
            ErrorCategory.PERFORMANCE: LearningAction.PATTERN_EXTRACTION,
            ErrorCategory.SECURITY: LearningAction.RULE_UPDATE,
            ErrorCategory.INTEGRATION: LearningAction.RULE_UPDATE,
            ErrorCategory.RESOURCE: LearningAction.PATTERN_EXTRACTION,
            ErrorCategory.CONFIGURATION: LearningAction.RULE_UPDATE,
            ErrorCategory.DEPENDENCY: LearningAction.RULE_UPDATE,
            ErrorCategory.NETWORK: LearningAction.RULE_UPDATE,
        }
        return mapping.get(category, LearningAction.PATTERN_EXTRACTION)
    
    def _trigger_handlers(self, category: ErrorCategory, exp: ErrorExperience) -> None:
        """Trigger registered handlers."""
        for handler in self._error_handlers[category]:
            try:
                handler(exp)
            except Exception:
                pass
    
    def register_handler(self, category: ErrorCategory, handler: Callable) -> None:
        """Register error handler."""
        self._error_handlers[category].append(handler)
    
    def apply_fix(self, exp_id: str, fix_code: str) -> bool:
        """Apply a fix to an experience."""
        exp = next((e for e in self.experiences if e.id == exp_id), None)
        if not exp:
            return False
        
        exp.fix_applied = fix_code
        exp.resolution = f"Fixed: {fix_code}"
        self._save()
        return True
    
    def mark_tests_passed(self, exp_id: str) -> bool:
        """Mark tests as passed for a fix."""
        exp = next((e for e in self.experiences if e.id == exp_id), None)
        if not exp:
            return False
        
        exp.tests_passed = True
        self._save()
        return True
    
    def get_statistics(self) -> Dict:
        """Get learning statistics."""
        total = len(self.experiences)
        by_category = defaultdict(int)
        fixed = 0
        tests_passed = 0
        
        for exp in self.experiences:
            by_category[exp.category.value] += 1
            if exp.fix_applied:
                fixed += 1
            if exp.tests_passed:
                tests_passed += 1
        
        return {
            "total_experiences": total,
            "by_category": dict(by_category),
            "fixes_generated": fixed,
            "fixes_tested": tests_passed,
            "rules_learned": len(self.rules),
            "top_patterns": self._get_top_patterns(),
        }
    
    def _get_top_patterns(self, limit: int = 10) -> List[Dict]:
        """Get most common patterns."""
        pattern_counts = defaultdict(int)
        for exp in self.experiences:
            for p in exp.learned_patterns:
                pattern_counts[p] += exp.recurrence_count
        
        sorted_patterns = sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True)
        return [{"pattern": p, "count": c} for p, c in sorted_patterns[:limit]]
    
    def get_suggestions(self, error: Exception) -> List[str]:
        """Get fix suggestions for an error."""
        traceback_str = traceback.format_exc()
        category = ErrorClassifier.classify(error, traceback.format_exc())
        
        suggestions = []
        
        # Check existing experiences
        for exp in self.experiences:
            if exp.category == category and exp.fix_applied:
                suggestions.append(exp.fix_applied)
        
        # Check rules
        for rule in self.rules:
            if rule.trigger_count > 5 and rule.confidence > 0.7:
                suggestions.append(f"Rule: {rule.name} - {rule.parameters}")
        
        return list(set(suggestions))[:5]


class ErrorMonitor:
    """Monitors and captures errors automatically."""
    
    def __init__(self, learner: ErrorFedLearning):
        self.learner = learner
        self._original_excepthook = sys.excepthook
        sys.excepthook = self._handle_exception
    
    def _handle_exception(self, exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions."""
        self.learner.record_error(exc_value)
        self._original_excepthook(exc_type, exc_value, exc_traceback)
    
    def restore(self):
        """Restore original exception handler."""
        sys.excepthook = self._original_excepthook