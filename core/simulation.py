"""
2. Simulation & Self-Training
Runs hypothetical scenarios during idle time, generates/solves own problems to keep skills sharp.
"""
import asyncio
import random
import time
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import json


class ScenarioType(Enum):
    CODE_GENERATION = "code_generation"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"
    ARCHITECTURE = "architecture"
    PERFORMANCE = "performance"
    SECURITY = "security"
    EDGE_CASE = "edge_case"


@dataclass
class Scenario:
    id: str
    type: ScenarioType
    prompt: str
    expected_outcome: str
    difficulty: float  # 0.0 - 1.0
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class SimulationResult:
    scenario_id: str
    success: bool
    output: str
    metrics: Dict[str, float]
    duration: float
    errors: List[str] = field(default_factory=list)
    learnings: List[str] = field(default_factory=list)


class ScenarioGenerator:
    """Generates diverse training scenarios."""
    
    SCENARIO_TEMPLATES = {
        ScenarioType.CODE_GENERATION: [
            "Create a {language} function that {task} with proper error handling",
            "Implement a {pattern} pattern for {domain} in {language}",
            "Write a {language} class for {concept} with unit tests",
        ],
        ScenarioType.DEBUGGING: [
            "Fix the bug in this {language} code: {bug_description}",
            "Debug why this {language} function returns {wrong_result} instead of {expected}",
            "Find and fix the memory leak in this {language} code",
        ],
        ScenarioType.REFACTORING: [
            "Refactor this {language} code to use {pattern} pattern",
            "Improve performance of this {language} function by {target}%",
            "Extract this monolithic {language} function into smaller modules",
        ],
        ScenarioType.ARCHITECTURE: [
            "Design a {scale} system for {domain} with {requirements}",
            "Choose appropriate architecture for {use_case} with {constraints}",
            "Plan migration from {old_tech} to {new_tech} for {system}",
        ],
        ScenarioType.PERFORMANCE: [
            "Optimize this {language} query to run under {target}ms",
            "Reduce memory usage of this {language} process by {target}%",
            "Implement caching strategy for {endpoint} with {requirements}",
        ],
        ScenarioType.SECURITY: [
            "Identify and fix SQL injection vulnerability in {language} code",
            "Implement proper authentication for {api_endpoint}",
            "Add rate limiting and input validation to {endpoint}",
        ],
        ScenarioType.EDGE_CASE: [
            "Handle {edge_case} in this {language} function",
            "Make this {language} code work with {unusual_input}",
            "Ensure this {language} function is thread-safe",
        ],
    }
    
    LANGUAGES = ["Python", "JavaScript", "TypeScript", "Go", "Rust", "Java"]
    DOMAINS = ["web API", "data processing", "real-time systems", "ML pipelines", "auth systems"]
    PATTERNS = ["factory", "observer", "strategy", "decorator", "adapter", "builder"]
    
    @classmethod
    def generate(cls, scenario_type: Optional[ScenarioType] = None) -> Scenario:
        """Generate a random scenario."""
        if scenario_type is None:
            scenario_type = random.choice(list(ScenarioType))
        
        templates = cls.SCENARIO_TEMPLATES[scenario_type]
        template = random.choice(templates)
        
        prompt = template.format(
            language=random.choice(cls.LANGUAGES),
            domain=random.choice(cls.DOMAINS),
            pattern=random.choice(cls.PATTERNS),
            task=random.choice(["processes data", "validates input", "transforms format", "calculates metrics"]),
            bug_description=random.choice(["off-by-one error", "null pointer", "race condition", "type mismatch"]),
            wrong_result=random.choice(["null", "empty list", "wrong type", "incorrect calculation"]),
            expected=random.choice(["valid object", "correct count", "proper format", "success status"]),
            target=random.choice([50, 80, 90, 95]),
            requirements=random.choice(["high availability", "low latency", "strong consistency", "horizontal scaling"]),
            use_case=random.choice(["user authentication", "data analytics", "file processing", "notification system"]),
            constraints=random.choice(["limited budget", "legacy integration", "regulatory compliance", "team size"]),
            old_tech=random.choice(["monolith", "legacy framework", "on-premise", "manual deployment"]),
            new_tech=random.choice(["microservices", "serverless", "Kubernetes", "CI/CD"]),
            system=random.choice(["payment processing", "user management", "inventory", "reporting"]),
            endpoint=random.choice(["/api/users", "/api/data", "/auth/login", "/webhook"]),
            edge_case=random.choice(["empty input", "null values", "huge dataset", "network timeout", "concurrent access"]),
            unusual_input=random.choice(["malformed JSON", "unicode edge cases", "extremely large numbers", "circular references"]),
        )
        
        return Scenario(
            id=f"scenario_{int(time.time() * 1000)}_{random.randint(1000, 9999)}",
            type=scenario_type,
            prompt=prompt,
            expected_outcome=f"Working {scenario_type.value} solution with tests",
            difficulty=random.uniform(0.3, 0.9),
            tags=[scenario_type.value, random.choice(cls.LANGUAGES).lower()]
        )
    
    @classmethod
    def generate_batch(cls, count: int, scenario_type: Optional[ScenarioType] = None) -> List[Scenario]:
        """Generate multiple scenarios."""
        return [cls.generate(scenario_type) for _ in range(count)]


class SimulationEngine:
    """
    Runs hypothetical scenarios during idle time.
    Generates problems, solves them, extracts learnings.
    """
    
    def __init__(self, executor: Callable, idle_threshold: int = 300):
        self.executor = executor  # Function that executes a prompt and returns result
        self.idle_threshold = idle_threshold  # seconds of inactivity before starting
        self.scenario_generator = ScenarioGenerator()
        self.last_activity = time.time()
        self.running = False
        self.results_history: List[SimulationResult] = []
        self.learnings_db: List[str] = []
        self._task: Optional[asyncio.Task] = None
    
    def mark_activity(self) -> None:
        """Mark that user activity occurred."""
        self.last_activity = time.time()
    
    def is_idle(self) -> bool:
        """Check if system has been idle long enough."""
        return (time.time() - self.last_activity) >= self.idle_threshold
    
    async def run_simulation_cycle(self, max_scenarios: int = 3) -> List[SimulationResult]:
        """Run a batch of simulations."""
        scenarios = self.scenario_generator.generate_batch(max_scenarios)
        results = []
        
        for scenario in scenarios:
            start = time.time()
            try:
                # Execute scenario
                output = await self.executor(scenario.prompt)
                duration = time.time() - start
                
                # Evaluate result
                success = self._evaluate_output(scenario, output)
                metrics = self._calculate_metrics(scenario, output, duration)
                learnings = self._extract_learnings(scenario, output, success)
                
                result = SimulationResult(
                    scenario_id=scenario.id,
                    success=success,
                    output=output[:500],
                    metrics=metrics,
                    duration=duration,
                    learnings=learnings
                )
                
                # Store learnings
                self.learnings_db.extend(learnings)
                if len(self.learnings_db) > 1000:
                    self.learnings_db = self.learnings_db[-1000:]
                
            except Exception as e:
                duration = time.time() - start
                result = SimulationResult(
                    scenario_id=scenario.id,
                    success=False,
                    output="",
                    metrics={},
                    duration=duration,
                    errors=[str(e)]
                )
            
            results.append(result)
            self.results_history.append(result)
            if len(self.results_history) > 500:
                self.results_history = self.results_history[-500:]
        
        return results
    
    def _evaluate_output(self, scenario: Scenario, output: str) -> bool:
        """Evaluate if output meets expectations."""
        # Basic checks
        if not output or len(output) < 50:
            return False
        # Check for error indicators
        error_indicators = ["error", "exception", "traceback", "failed", "syntax error"]
        return not any(ind in output.lower() for ind in error_indicators)
    
    def _calculate_metrics(self, scenario: Scenario, output: str, duration: float) -> Dict:
        """Calculate performance metrics."""
        return {
            "duration": duration,
            "output_length": len(output),
            "complexity_score": scenario.difficulty,
            "efficiency": len(output) / max(duration, 0.001)
        }
    
    def _extract_learnings(self, scenario: Scenario, output: str, success: bool) -> List[str]:
        """Extract learnings from simulation."""
        learnings = []
        if success:
            learnings.append(f"Successfully solved {scenario.type.value}: {scenario.prompt[:100]}")
            if "pattern" in scenario.prompt.lower():
                learnings.append(f"Pattern application effective for {scenario.type.value}")
        else:
            learnings.append(f"Failed {scenario.type.value}: need better approach for {scenario.prompt[:100]}")
        return learnings
    
    async def start_background_simulation(self) -> None:
        """Start background simulation loop."""
        self.running = True
        while self.running:
            if self.is_idle():
                await self.run_simulation_cycle()
            await asyncio.sleep(60)  # Check every minute
    
    def stop(self) -> None:
        """Stop background simulation."""
        self.running = False
        if self._task:
            self._task.cancel()
    
    def get_statistics(self) -> Dict:
        """Get simulation statistics."""
        if not self.results_history:
            return {"total": 0, "success_rate": 0, "avg_duration": 0}
        
        total = len(self.results_history)
        successful = sum(1 for r in self.results_history if r.success)
        avg_duration = sum(r.duration for r in self.results_history) / total
        
        return {
            "total_scenarios": total,
            "success_rate": successful / total,
            "avg_duration": avg_duration,
            "unique_learnings": len(set(self.learnings_db)),
            "by_type": self._group_by_type()
        }
    
    def _group_by_type(self) -> Dict:
        """Group results by scenario type."""
        grouped = {}
        for r in self.results_history:
            # Extract type from scenario_id (simplified)
            for t in ScenarioType:
                if t.value in r.scenario_id:
                    if t.value not in grouped:
                        grouped[t.value] = {"total": 0, "success": 0}
                    grouped[t.value]["total"] += 1
                    if r.success:
                        grouped[t.value]["success"] += 1
                    break
        return grouped