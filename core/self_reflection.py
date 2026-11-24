"""
1. Internal Logic Control / Self-Reflection
Analyzes own reasoning chain before output, detects errors/high-cost approaches, autonomously pivots.
"""
import json
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ReasoningStepType(Enum):
    ANALYSIS = "analysis"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    PIVOT = "pivot"


@dataclass
class ReasoningStep:
    step_type: ReasoningStepType
    content: str
    confidence: float
    cost_estimate: float
    timestamp: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)


@dataclass
class ReflectionResult:
    should_continue: bool
    pivot_reason: Optional[str] = None
    alternative_approach: Optional[str] = None
    confidence_adjustment: float = 0.0
    warnings: List[str] = field(default_factory=list)


class SelfReflectionEngine:
    """
    Analyzes reasoning chains for errors, high-cost approaches, and logical inconsistencies.
    Provides autonomous pivot capability when issues detected.
    """
    
    def __init__(self, max_cost_threshold: float = 100.0, min_confidence: float = 0.7):
        self.max_cost_threshold = max_cost_threshold
        self.min_confidence = min_confidence
        self.reasoning_history: List[ReasoningStep] = []
        self.pivot_history: List[Dict] = []
    
    def add_step(self, step: ReasoningStep) -> None:
        """Add a reasoning step to history."""
        self.reasoning_history.append(step)
    
    def reflect(self, current_step: ReasoningStep) -> ReflectionResult:
        """
        Perform self-reflection on the current reasoning chain.
        Returns whether to continue, pivot, or abort.
        """
        warnings = []
        confidence_adj = 0.0
        pivot_reason = None
        alt_approach = None
        
        # Check cumulative cost
        total_cost = sum(s.cost_estimate for s in self.reasoning_history)
        if total_cost > self.max_cost_threshold:
            warnings.append(f"Cumulative cost ({total_cost:.1f}) exceeds threshold ({self.max_cost_threshold})")
            confidence_adj -= 0.2
            pivot_reason = "Cost threshold exceeded"
            alt_approach = "Consider more efficient approach or break into smaller tasks"
        
        # Check confidence
        if current_step.confidence < self.min_confidence:
            warnings.append(f"Low confidence ({current_step.confidence:.2f}) below threshold ({self.min_confidence})")
            confidence_adj -= 0.15
            if not pivot_reason:
                pivot_reason = "Low confidence in current approach"
                alt_approach = "Gather more information or try alternative method"
        
        # Check for circular reasoning
        recent_contents = [s.content.lower() for s in self.reasoning_history[-5:]]
        if current_step.content.lower() in recent_contents:
            warnings.append("Potential circular reasoning detected")
            confidence_adj -= 0.1
            pivot_reason = "Circular reasoning detected"
            alt_approach = "Change perspective or seek external input"
        
        # Check for logical gaps
        if current_step.step_type == ReasoningStepType.EXECUTION:
            prev_analysis = [s for s in self.reasoning_history if s.step_type == ReasoningStepType.ANALYSIS]
            if not prev_analysis:
                warnings.append("Execution without prior analysis")
                confidence_adj -= 0.2
                pivot_reason = "Missing analysis phase"
                alt_approach = "Perform analysis before execution"
        
        # Cost trend analysis
        if len(self.reasoning_history) >= 3:
            recent_costs = [s.cost_estimate for s in self.reasoning_history[-3:]]
            if all(recent_costs[i] > recent_costs[i-1] for i in range(1, len(recent_costs))):
                warnings.append("Increasing cost trend - diminishing returns")
                confidence_adj -= 0.1
        
        should_continue = len([w for w in warnings if "exceeds" in w or "circular" in w]) == 0
        
        result = ReflectionResult(
            should_continue=should_continue,
            pivot_reason=pivot_reason,
            alternative_approach=alt_approach,
            confidence_adjustment=confidence_adj,
            warnings=warnings
        )
        
        if pivot_reason:
            self.pivot_history.append({
                "timestamp": time.time(),
                "reason": pivot_reason,
                "alternative": alt_approach,
                "step": current_step.content[:200]
            })
        
        return result
    
    def get_reflection_summary(self) -> Dict:
        """Get summary of reflection state."""
        return {
            "total_steps": len(self.reasoning_history),
            "total_cost": sum(s.cost_estimate for s in self.reasoning_history),
            "avg_confidence": sum(s.confidence for s in self.reasoning_history) / len(self.reasoning_history) if self.reasoning_history else 0,
            "pivots": len(self.pivot_history),
            "recent_warnings": self.reasoning_history[-1].metadata.get("warnings", []) if self.reasoning_history else []
        }
    
    def reset(self) -> None:
        """Reset reflection state for new task."""
        self.reasoning_history.clear()
        self.pivot_history.clear()