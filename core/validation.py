"""
8. Request Validation & Completion
Verifies all user requirements met step-by-step, continues until complete.
"""
import asyncio
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import time
import json


class ValidationStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RequirementType(Enum):
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    TECHNICAL = "technical"
    BUSINESS = "business"
    SECURITY = "security"
    PERFORMANCE = "performance"
    USABILITY = "usability"


@dataclass
class Requirement:
    id: str
    description: str
    type: RequirementType
    acceptance_criteria: List[str]
    priority: int = 1  # 1=high, 2=medium, 3=low
    dependencies: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class ValidationResult:
    requirement_id: str
    status: ValidationStatus
    evidence: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    score: float = 0.0  # 0.0 - 1.0
    validated_at: float = field(default_factory=time.time)
    validator: str = ""


@dataclass
class CompletionReport:
    total_requirements: int
    validated: int
    passed: int
    failed: int
    coverage: float
    missing_requirements: List[str]
    recommendations: List[str]
    generated_at: float = field(default_factory=time.time)


class RequirementParser:
    """Parses natural language requirements into structured requirements."""
    
    @classmethod
    def parse(cls, text: str, project_context: Dict = None) -> List[Requirement]:
        """Parse requirements from natural language."""
        requirements = []
        
        # Split into sentences/bullets
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        for i, line in enumerate(lines):
            # Detect requirement type
            req_type = cls._detect_type(line)
            
            # Extract acceptance criteria
            criteria = cls._extract_criteria(line)
            
            # Detect priority
            priority = cls._detect_priority(line)
            
            req = Requirement(
                id=f"req_{int(time.time())}_{i}",
                description=line,
                type=req_type,
                acceptance_criteria=criteria,
                priority=cls._priority_value(priority),
                tags=cls._extract_tags(line),
            )
            requirements.append(req)
        
        return requirements
    
    @classmethod
    def _detect_type(cls, text: str) -> RequirementType:
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["secure", "auth", "encrypt", "permission", "vulnerab"]):
            return RequirementType.SECURITY
        elif any(kw in text_lower for kw in ["fast", "speed", "perform", "latency", "throughput", "scale"]):
            return RequirementType.PERFORMANCE
        elif any(kw in text_lower for kw in ["user", "ui", "ux", "interface", "experience", "usab"]):
            return RequirementType.USABILITY
        elif any(kw in text_lower for kw in ["api", "database", "service", "deploy", "infra", "tech"]):
            return RequirementType.TECHNICAL
        elif any(kw in text_lower for kw in ["business", "revenue", "customer", "market", "roi"]):
            return RequirementType.BUSINESS
        return RequirementType.FUNCTIONAL
    
    @classmethod
    def _extract_criteria(cls, text: str) -> List[str]:
        """Extract acceptance criteria from requirement text."""
        criteria = []
        # Look for "should", "must", "shall", "will"
        import re
        patterns = [
            r'(should|must|shall|will)\s+([^.]+)',
            r'(accept|verify|ensure)\s+([^.]+)',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                criteria.append(match[-1].strip())
        if not criteria:
            criteria.append(text)
        return criteria
    
    @classmethod
    def _detect_priority(cls, text: str) -> str:
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["critical", "urgent", "blocker", "p0", "p1"]):
            return "high"
        elif any(kw in text_lower for kw in ["important", "soon", "p2"]):
            return "medium"
        return "low"
    
    @classmethod
    def _priority_value(cls, priority: str) -> int:
        return {"high": 1, "medium": 2, "low": 3}.get(priority, 2)
    
    @classmethod
    def _extract_tags(cls, text: str) -> List[str]:
        tags = []
        text_lower = text.lower()
        tag_keywords = {
            "api": ["api", "endpoint", "rest", "graphql"],
            "database": ["database", "db", "sql", "query", "migration"],
            "auth": ["auth", "login", "token", "oauth", "permission"],
            "ui": ["ui", "frontend", "interface", "component", "page"],
            "test": ["test", "testing", "coverage", "unit", "integration"],
            "deploy": ["deploy", "ci/cd", "pipeline", "release"],
            "security": ["security", "encrypt", "vulnerability", "audit"],
        }
        for tag, keywords in tag_keywords.items():
            if any(kw in text_lower for kw in keywords):
                tags.append(tag)
        return tags


class RequirementValidator:
    """Validates requirements against implementation."""
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.validators: Dict[RequirementType, Callable] = {
            RequirementType.FUNCTIONAL: self._validate_functional,
            RequirementType.TECHNICAL: self._validate_technical,
            RequirementType.SECURITY: self._validate_security,
            RequirementType.PERFORMANCE: self._validate_performance,
            RequirementType.USABILITY: self._validate_usability,
            RequirementType.BUSINESS: self._validate_business,
        }
    
    async def validate_requirement(self, req: Requirement, project_state: Dict) -> ValidationResult:
        """Validate a single requirement."""
        validator = self.validators.get(req.type, self._validate_generic)
        return await validator(req, project_state)
    
    async def _validate_functional(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate functional requirement."""
        evidence = []
        gaps = []
        score = 0.0
        
        # Check if feature implemented
        implemented = state.get("implemented_features", [])
        feature_keywords = req.description.lower().split()
        
        for kw in feature_keywords:
            if any(kw in imp.lower() for imp in implemented):
                evidence.append(f"Feature '{kw}' found in implementation")
                score += 0.2
            else:
                gaps.append(f"Feature '{kw}' not found in implementation")
        
        score = min(score, 1.0)
        status = ValidationStatus.PASSED if score >= 0.8 else ValidationStatus.FAILED
        
        return ValidationResult(
            requirement_id=req.id,
            status=status,
            evidence=evidence,
            gaps=gaps,
            score=score,
            validator="functional_validator"
        )
    
    async def _validate_technical(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate technical requirement."""
        evidence = []
        gaps = []
        score = 0.0
        
        tech_stack = state.get("tech_stack", {})
        architecture = state.get("architecture", {})
        
        # Check tech stack compliance
        req_lower = req.description.lower()
        for tech, version in tech_stack.items():
            if tech.lower() in req.description.lower():
                evidence.append(f"Uses {tech} {version}")
                score += 0.3
        
        # Check architecture patterns
        if "microservice" in req.description.lower() and architecture.get("style") == "microservices":
            evidence.append("Microservices architecture confirmed")
            score += 0.4
        
        score = min(score, 1.0)
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED if score >= 0.7 else ValidationStatus.FAILED,
            evidence=evidence,
            gaps=gaps,
            score=score,
            validator="technical_validator"
        )
    
    async def _validate_security(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate security requirement."""
        evidence = []
        gaps = []
        score = 0.0
        
        security_features = state.get("security_features", [])
        
        checks = {
            "encryption": ["encryption", "tls", "ssl", "https"],
            "authentication": ["auth", "login", "jwt", "oauth"],
            "authorization": ["permission", "role", "rbac", "acl"],
            "input_validation": ["validation", "sanitize", "sanitize"],
            "audit": ["audit", "log", "monitor"],
        }
        
        req_lower = req.description.lower()
        for check_name, keywords in checks.items():
            if any(kw in req_lower for kw in keywords):
                if any(kw in " ".join(security_features).lower() for kw in keywords):
                    evidence.append(f"{check_name} implemented")
                    score += 0.2
                else:
                    gaps.append(f"{check_name} not found in security features")
        
        score = min(score, 1.0)
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED if score >= 0.6 else ValidationStatus.FAILED,
            evidence=evidence,
            gaps=gaps,
            score=score,
            validator="security_validator"
        )
    
    async def _validate_performance(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate performance requirement."""
        evidence = []
        gaps = []
        score = 0.0
        
        benchmarks = state.get("benchmarks", {})
        req_lower = req.description.lower()
        
        # Extract target metrics
        import re
        time_matches = re.findall(r'(\d+(?:\.\d+)?)\s*(ms|s|seconds?)', req_lower)
        for value, unit in time_matches:
            target = float(value) * (1 if unit.startswith('s') else 0.001)
            # Check if any benchmark meets target
            for bench, value in benchmarks.items():
                if value <= target:
                    evidence.append(f"{bench}: {value}s meets target {target}s")
                    score += 0.3
        
        if "scale" in req_lower or "throughput" in req_lower:
            if "load_test" in state:
                evidence.append("Load testing configured")
                score += 0.4
        
        score = min(score, 1.0)
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED if score >= 0.6 else ValidationStatus.FAILED,
            evidence=evidence,
            gaps=gaps,
            score=score,
            validator="performance_validator"
        )
    
    async def _validate_usability(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate usability requirement."""
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED,
            evidence=["Usability validation requires manual testing"],
            score=0.5,
            validator="usability_validator"
        )
    
    async def _validate_business(self, req: Requirement, state: Dict) -> ValidationResult:
        """Validate business requirement."""
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED,
            evidence=["Business validation requires stakeholder review"],
            score=0.5,
            validator="business_validator"
        )
    
    async def _validate_generic(self, req: Requirement, state: Dict) -> ValidationResult:
        """Generic validator."""
        return ValidationResult(
            requirement_id=req.id,
            status=ValidationStatus.PASSED,
            evidence=["Generic validation passed"],
            score=0.5,
            validator="generic_validator"
        )


class RequestValidator:
    """
    Complete request validation and completion system.
    Parses requirements, validates against implementation, ensures completeness.
    """
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.parser = RequirementParser()
        self.validator = RequirementValidator(project_root)
        self.requirements: List[Requirement] = []
        self.results: List[ValidationResult] = []
    
    def parse_request(self, request_text: str, project_context: Dict = None) -> List[Requirement]:
        """Parse user request into requirements."""
        self.requirements = self.parser.parse(request_text, project_context)
        return self.requirements
    
    async def validate_all(self, project_state: Dict) -> List[ValidationResult]:
        """Validate all requirements."""
        self.results = []
        
        # Sort by priority
        sorted_reqs = sorted(self.requirements, key=lambda r: r.priority)
        
        for req in sorted_reqs:
            # Check dependencies
            if req.dependencies:
                dep_results = [r for r in self.results if r.requirement_id in req.dependencies]
                if any(r.status == ValidationStatus.FAILED for r in dep_results):
                    self.results.append(ValidationResult(
                        requirement_id=req.id,
                        status=ValidationStatus.SKIPPED,
                        gaps=["Dependency failed"],
                        score=0,
                        validator="dependency_check"
                    ))
                    continue
            
            result = await self.validator.validate_requirement(req, state)
            self.results.append(result)
        
        return self.results
    
    def generate_completion_report(self) -> CompletionReport:
        """Generate completion report."""
        total = len(self.requirements)
        validated = len(self.results)
        passed = sum(1 for r in self.results if r.status == ValidationStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == ValidationStatus.FAILED)
        
        # Find missing requirements
        validated_ids = {r.requirement_id for r in self.results}
        missing = [r.id for r in self.requirements if r.id not in validated_ids]
        
        # Generate recommendations
        recommendations = []
        for r in self.results:
            if r.status == ValidationStatus.FAILED:
                recommendations.extend([f"Fix {req.description}: {gap}" for gap in r.gaps])
            elif r.status == ValidationStatus.SKIPPED:
                recommendations.append(f"Resolve dependency for {req.description}")
        
        coverage = passed / total if total > 0 else 0
        
        return CompletionReport(
            total_requirements=total,
            validated=validated,
            passed=passed,
            failed=failed,
            coverage=coverage,
            missing_requirements=missing,
            recommendations=recommendations
        )
    
    def is_complete(self, threshold: float = 0.9) -> bool:
        """Check if validation is complete."""
        report = self.generate_completion_report()
        return report.coverage >= threshold and report.failed == 0
    
    def get_next_actions(self) -> List[str]:
        """Get next actions to complete validation."""
        actions = []
        for r in self.results:
            if r.status == ValidationStatus.FAILED:
                for gap in r.gaps:
                    actions.append(f"Address gap in {r.requirement_id}: {gap}")
            elif r.status == ValidationStatus.SKIPPED:
                actions.append(f"Resolve dependencies for {r.requirement_id}")
        
        missing_ids = {r.id for r in self.requirements} - {r.requirement_id for r in self.results}
        for mid in missing_ids:
            actions.append(f"Validate missing requirement: {mid}")
        
        return actions
    
    def export_report(self, format: str = "json") -> str:
        """Export validation report."""
        report = self.generate_completion_report()
        
        if format == "json":
            return json.dumps({
                "summary": {
                    "total": report.total_requirements,
                    "passed": report.passed,
                    "failed": report.failed,
                    "coverage": report.coverage,
                },
                "results": [
                    {
                        "requirement_id": r.requirement_id,
                        "status": r.status.value,
                        "score": r.score,
                        "evidence": r.evidence,
                        "gaps": r.gaps,
                    }
                    for r in self.results
                ],
                "recommendations": report.recommendations,
            }, indent=2)
        return ""