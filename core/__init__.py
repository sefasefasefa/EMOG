"""
EMOG Core - Architectural Features Package
"""
from .self_reflection import SelfReflectionEngine, ReasoningStep, ReasoningStepType, ReflectionResult
from .simulation import SimulationEngine, ScenarioGenerator, Scenario, ScenarioType, SimulationResult
from .planning import (
    PlanningEngine, TDDPlanner, ArchitecturalPlan, ArchitecturalComponent, DataFlow,
    TestScenario, TestType, PlanStatus, RequirementParser, ArchitecturalPlanner, export_plan
)
from .modular_arch import (
    ServiceBase, ServiceConfig, ServiceState, ServiceRegistry, ServiceEndpoint,
    ServiceHealth, ServiceScope, ServiceFactory, ServiceInterface, MicroserviceManager
)
from .live_env import LiveEnvironmentManager, EnvironmentConfig, DeploymentTarget, EnvironmentStatus, DeploymentResult
from .auto_testing import AutonomousTester, TestScenarioBuilder, TestCase, TestStep, TestType, TestStatus, TestResult
from .self_healing import SelfHealingEngine, IssueType, FixStrategy, CodeIssue, FixAttempt
from .validation import RequestValidator, Requirement, RequirementType, ValidationResult, CompletionReport, RequirementValidator, RequirementParser
from .db_manager import DatabaseManager, Migration, MigrationDirection, QueryResult, User, Project
from .vulnerability import VulnerabilityScanner, VulnerabilityPatcher, Vulnerability, VulnerabilityType, Severity, PatchResult
from .activity_tracker import ActivityTracker, MimicryEngine, ActivityMimicry, UserAction, ActionType, ActionPattern, MimicryTask
from .memory import ImmutableMemory, MemoryManager, MemoryEntry, MemoryType, MemoryQuery
from .plugin_marketplace import PluginRegistry, Plugin, PluginMetadata, PluginCategory, PluginStatus, BasePlugin, get_plugin_registry, initialize_plugins
from .learning import ErrorFedLearning, ErrorMonitor, ErrorExperience, ErrorCategory, LearningAction, LearningRule, ErrorClassifier
from .browser_exec import BrowserExecutor, CodeSandbox, ExecutionEngine, ExecutionStatus, ExecutionRequest, ExecutionResult, CodeSandbox
from .background_loop import (
    BackgroundLoopManager,
    BackgroundLoopDB,
    GoalExecutor,
    SelfHealingLoop,
    SubtaskGenerator,
    Goal,
    Subtask,
    GoalStatus,
    TaskPriority,
    LoopState,
    LoopMetrics,
    create_background_loop,
)
from .voice import VoiceInterface, VoiceChat, VoiceCommand, VoiceResponse, VoiceCommandType, VoiceState, STTEngine, TTSEngine, WhisperSTT, EdgeTTS, VoiceCommandProcessor

__all__ = [
    # Self-Reflection
    "SelfReflectionEngine", "ReasoningStep", "ReasoningStepType", "ReflectionResult",
    # Simulation
    "SimulationEngine", "ScenarioGenerator", "Scenario", "ScenarioType", "SimulationResult",
    # Planning
    "PlanningEngine", "TDDPlanner", "ArchitecturalPlan", "ArchitecturalComponent", "DataFlow",
    "TestScenario", "TestType", "PlanStatus", "RequirementParser", "ArchitecturalPlanner", "export_plan",
    # Modular Architecture
    "ServiceBase", "ServiceConfig", "ServiceState", "ServiceRegistry", "ServiceEndpoint",
    "ServiceHealth", "ServiceScope", "ServiceFactory", "ServiceInterface", "MicroserviceManager",
    # Live Environment
    "LiveEnvironmentManager", "EnvironmentConfig", "DeploymentTarget", "EnvironmentStatus", "DeploymentResult",
    # Autonomous Testing
    "AutonomousTester", "TestScenarioBuilder", "TestCase", "TestStep", "TestType", "TestStatus", "TestResult",
    # Self-Healing
    "SelfHealingEngine", "IssueType", "FixStrategy", "CodeIssue", "FixAttempt",
    # Validation
    "RequestValidator", "Requirement", "RequirementType", "ValidationResult", "CompletionReport", "RequirementValidator", "RequirementParser",
    # Database
    "DatabaseManager", "Migration", "MigrationDirection", "QueryResult", "User", "Project",
    # Vulnerability
    "VulnerabilityScanner", "VulnerabilityPatcher", "Vulnerability", "VulnerabilityType", "Severity", "PatchResult",
    # Activity Tracker
    "ActivityTracker", "MimicryEngine", "ActivityMimicry", "UserAction", "ActionType", "ActionPattern", "MimicryTask",
    # Memory
    "ImmutableMemory", "MemoryManager", "MemoryEntry", "MemoryType", "MemoryQuery",
    # Plugin Marketplace
    "PluginRegistry", "Plugin", "PluginMetadata", "PluginCategory", "PluginStatus", "BasePlugin", "get_plugin_registry", "initialize_plugins",
    # Learning
    "ErrorFedLearning", "ErrorMonitor", "ErrorExperience", "ErrorCategory", "LearningAction", "LearningRule", "ErrorClassifier",
    # Browser Execution
    "BrowserExecutor", "CodeSandbox", "ExecutionEngine", "ExecutionStatus", "ExecutionRequest", "ExecutionResult",
    # Background Loop
    "BackgroundLoopManager", "BackgroundLoopDB", "GoalExecutor", "SelfHealingLoop",
    "SubtaskGenerator", "Goal", "Subtask", "GoalStatus", "TaskPriority",
    "LoopState", "LoopMetrics", "create_background_loop",
    # Voice
    "VoiceInterface", "VoiceChat", "VoiceCommand", "VoiceResponse", "VoiceCommandType", "VoiceState",
    "STTEngine", "TTSEngine", "WhisperSTT", "EdgeTTS", "VoiceCommandProcessor",
]

__version__ = "1.0.0"