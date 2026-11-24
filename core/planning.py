"""
Planning & TDD Module
Generates comprehensive architectural plans and test scenarios before implementation.
"""
import json
import ast
import asyncio
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import uuid
from datetime import datetime


class TestType(Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    PERFORMANCE = "performance"
    SECURITY = "security"
    CONTRACT = "contract"
    PROPERTY = "property"


class PlanStatus(Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class TestScenario:
    id: str
    name: str
    test_type: TestType
    description: str
    given: str  # Preconditions
    when: str   # Action
    then: str   # Expected outcome
    priority: int = 5  # 1-10
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    code_template: Optional[str] = None


@dataclass
class ArchitecturalComponent:
    name: str
    type: str  # service, library, api, database, queue, cache, etc.
    description: str
    responsibilities: List[str]
    interfaces: List[str]  # Provided interfaces
    dependencies: List[str]  # Required interfaces
    technology: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    tests: List[str] = field(default_factory=list)  # Test scenario IDs


@dataclass
class DataFlow:
    source: str
    target: str
    data_type: str
    protocol: str
    frequency: str  # real-time, batch, scheduled
    transformation: Optional[str] = None


@dataclass
class ArchitecturalPlan:
    id: str
    name: str
    description: str
    status: PlanStatus = PlanStatus.DRAFT
    version: str = "1.0.0"
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    updated_at: float = field(default_factory=lambda: datetime.now().timestamp())
    components: List[ArchitecturalComponent] = field(default_factory=list)
    data_flows: List[DataFlow] = field(default_factory=list)
    test_scenarios: List[TestScenario] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class RequirementParser:
    """Parses natural language requirements into structured format."""
    
    @staticmethod
    def parse(text: str) -> Dict[str, Any]:
        """Parse requirements from natural language."""
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        requirements = {
            "functional": [],
            "non_functional": [],
            "constraints": [],
            "assumptions": [],
            "actors": [],
            "use_cases": []
        }
        
        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in ["must", "shall", "should", "will", "can"]):
                if any(kw in lower for kw in ["performance", "latency", "throughput", "scale", "speed"]):
                    requirements["non_functional"].append(line)
                elif any(kw in lower for kw in ["security", "encrypt", "auth", "permission", "audit"]):
                    requirements["non_functional"].append(line)
                elif any(kw in lower for kw in ["constraint", "limit", "budget", "deadline", "must not"]):
                    requirements["constraints"].append(line)
                else:
                    requirements["functional"].append(line)
            elif "assume" in lower or "assuming" in lower:
                requirements["assumptions"].append(line)
            elif "user" in lower or "admin" in lower or "actor" in lower:
                requirements["actors"].append(line)
            elif "use case" in lower or "scenario" in lower:
                requirements["use_cases"].append(line)
        
        return requirements


class TDDPlanner:
    """Generates comprehensive test scenarios using TDD approach."""
    
    def __init__(self):
        self.test_templates = {
            TestType.UNIT: [
                "test_{function}_happy_path",
                "test_{function}_edge_case_{case}",
                "test_{function}_invalid_input",
                "test_{function}_boundary_values",
                "test_{function}_exception_handling",
            ],
            TestType.INTEGRATION: [
                "test_{component}_integration_with_{dependency}",
                "test_{workflow}_end_to_end",
                "test_{api}_contract_compliance",
                "test_{component}_failure_recovery",
            ],
            TestType.E2E: [
                "test_user_{journey}_complete",
                "test_{feature}_with_real_data",
                "test_cross_browser_compatibility",
            ],
            TestType.PERFORMANCE: [
                "test_{operation}_under_{threshold}ms",
                "test_{system}_under_{load}_concurrent_users",
                "test_{operation}_memory_usage_under_{limit}mb",
            ],
            TestType.SECURITY: [
                "test_{endpoint}_rejects_unauthorized",
                "test_{input}_sanitization",
                "test_{data}_encryption_at_rest",
                "test_{endpoint}_rate_limiting",
            ],
            TestType.CONTRACT: [
                "test_{api}_schema_validation",
                "test_{api}_backward_compatibility",
                "test_{message_queue}_schema_evolution",
            ],
            TestType.PROPERTY: [
                "test_{function}_invariant_{property}",
                "test_{function}_commutative_property",
                "test_{function}_idempotent_property",
            ],
        }
    
    def generate_test_scenarios(self, plan: ArchitecturalPlan) -> List[TestScenario]:
        """Generate comprehensive test scenarios from architectural plan."""
        scenarios = []
        
        # Generate tests for each component
        for component in plan.components:
            scenarios.extend(self._generate_component_tests(component, plan))
        
        # Generate integration tests for data flows
        for flow in plan.data_flows:
            scenarios.extend(self._generate_integration_tests(flow, plan))
        
        # Generate tests from requirements
        for req in plan.requirements:
            scenarios.extend(self._generate_requirement_tests(req, plan))
        
        # Deduplicate
        seen = set()
        unique = []
        for s in scenarios:
            key = (s.name, s.test_type)
            if key not in seen:
                seen.add(key)
                unique.append(s)
        
        return unique
    
    def _generate_component_tests(self, component: ArchitecturalComponent, plan: ArchitecturalPlan) -> List[TestScenario]:
        tests = []
        
        # Unit tests
        for template in self.test_templates[TestType.UNIT]:
            for resp in component.responsibilities[:3]:  # Limit
                tests.append(TestScenario(
                    id=f"unit_{component.name}_{resp[:20]}_{uuid.uuid4().hex[:8]}",
                    name=template.format(function=resp),
                    test_type=TestType.UNIT,
                    description=f"Unit test for {resp} in {component.name}",
                    given=f"{component.name} is initialized with valid config",
                    when=f"Calling {resp} with valid/invalid inputs",
                    then=f"{resp} returns expected result or raises appropriate error",
                    priority=8,
                    tags=["unit", component.name, component.type],
                ))
        
        # Integration tests
        for dep in component.dependencies:
            tests.append(TestScenario(
                id=f"int_{component.name}_{dep}_{uuid.uuid4().hex[:8]}",
                name=f"test_{component.name}_integration_with_{dep}",
                test_type=TestType.INTEGRATION,
                description=f"Verify {component.name} integrates correctly with {dep}",
                given=f"{component.name} and {dep} are deployed and configured",
                when=f"{component.name} calls {dep} interface",
                then="Data flows correctly between components with proper error handling",
                priority=7,
                tags=["integration", component.name, dep],
            ))
        
        # Contract tests
        for interface in component.interfaces:
            tests.append(TestScenario(
                id=f"contract_{component.name}_{interface}_{uuid.uuid4().hex[:8]}",
                name=f"test_{component.name}_{interface}_contract",
                test_type=TestType.CONTRACT,
                description=f"Verify {interface} contract compliance",
                given=f"{component.name} implements {interface}",
                when="Validating request/response schemas",
                then="All requests/responses conform to contract schema",
                priority=6,
                tags=["contract", component.name, interface],
            ))
        
        return tests
    
    def _generate_integration_tests(self, flow: DataFlow, plan: ArchitecturalPlan) -> List[TestScenario]:
        tests = []
        
        tests.append(TestScenario(
            id=f"flow_{flow.source}_{flow.target}_{uuid.uuid4().hex[:8]}",
            name=f"test_data_flow_{flow.source}_to_{flow.target}",
            test_type=TestType.INTEGRATION,
            description=f"Verify data flow from {flow.source} to {flow.target}",
            given=f"{flow.source} produces {flow.data_type} data",
            when=f"Data is sent via {flow.protocol} ({flow.frequency})",
            then=f"{flow.target} receives and processes data correctly",
            priority=7,
            tags=["data_flow", flow.source, flow.target],
        ))
        
        return tests
    
    def _generate_requirement_tests(self, requirement: str, plan: ArchitecturalPlan) -> List[TestScenario]:
        tests = []
        req_lower = requirement.lower()
        
        if "performance" in req_lower or "latency" in req_lower:
            tests.append(TestScenario(
                id=f"perf_{uuid.uuid4().hex[:8]}",
                name="test_performance_requirement",
                test_type=TestType.PERFORMANCE,
                description=f"Validate performance: {requirement}",
                given="System under normal load",
                when="Measuring response times",
                then="All operations complete within specified latency",
                priority=9,
                tags=["performance", "requirement"],
            ))
        
        if "security" in req_lower or "auth" in req_lower:
            tests.append(TestScenario(
                id=f"sec_{uuid.uuid4().hex[:8]}",
                name="test_security_requirement",
                test_type=TestType.SECURITY,
                description=f"Validate security: {requirement}",
                given="System with authentication enabled",
                when="Attempting unauthorized access",
                then="Access is denied with appropriate error",
                priority=10,
                tags=["security", "requirement"],
            ))
        
        return tests


class ArchitecturalPlanner:
    """Creates comprehensive architectural plans from requirements."""
    
    def __init__(self):
        self.tdd_planner = TDDPlanner()
    
    def create_plan(self, requirements_text: str, project_name: str = "Project") -> ArchitecturalPlan:
        """Create comprehensive architectural plan from requirements."""
        requirements = RequirementParser.parse(requirements_text)
        
        plan = ArchitecturalPlan(
            id=f"plan_{uuid.uuid4().hex[:8]}",
            name=project_name,
            description=f"Architectural plan for {project_name}",
            requirements=requirements.get("functional", []) + requirements.get("non_functional", []),
            constraints=requirements.get("constraints", []),
            assumptions=requirements.get("assumptions", []),
        )
        
        # Identify components from requirements
        plan.components = self._identify_components(requirements, plan)
        
        # Define data flows
        plan.data_flows = self._design_data_flows(plan.components)
        
        # Select technology stack
        self._assign_technologies(plan.components, requirements.get("constraints", []))
        
        # Generate test scenarios
        plan.test_scenarios = self.tdd_planner.generate_test_scenarios(plan)
        
        return plan
    
    def _identify_components(self, requirements: Dict, plan: ArchitecturalPlan) -> List[ArchitecturalComponent]:
        """Identify architectural components from requirements."""
        components = []
        req_text = " ".join(requirements.get("functional", []) + requirements.get("non_functional", [])).lower()
        
        # Common component patterns
        patterns = {
            "api_gateway": ["api", "gateway", "rest", "graphql", "endpoint"],
            "auth_service": ["auth", "authentication", "login", "token", "oauth", "jwt"],
            "user_service": ["user", "profile", "account", "registration"],
            "database": ["database", "db", "storage", "persist", "sql", "nosql"],
            "cache": ["cache", "redis", "memcached", "caching"],
            "message_queue": ["queue", "message", "event", "kafka", "rabbitmq", "async"],
            "file_storage": ["file", "storage", "upload", "download", "s3", "blob"],
            "notification_service": ["notification", "email", "sms", "push", "alert"],
            "search_service": ["search", "elasticsearch", "index", "query"],
            "analytics_service": ["analytics", "metrics", "dashboard", "report", "tracking"],
            "payment_service": ["payment", "billing", "subscription", "stripe", "paypal"],
            "frontend": ["ui", "frontend", "web", "react", "vue", "angular", "spa"],
            "admin_panel": ["admin", "dashboard", "management", "cms"],
        }
        
        for comp_id, keywords in patterns.items():
            if any(kw in req_text for kw in keywords):
                component = self._create_component(comp_id, keywords, requirements)
                components.append(component)
        
        # Always include core infrastructure
        if "database" not in [c.name for c in components]:
            components.append(self._create_core_database())
        
        if "api_gateway" not in [c.name for c in components]:
            components.append(self._create_api_gateway())
        
        return components
    
    def _create_component(self, name: str, keywords: List[str], requirements: Dict) -> ArchitecturalComponent:
        """Create component definition."""
        descriptions = {
            "api_gateway": "API Gateway for request routing, rate limiting, and authentication",
            "auth_service": "Authentication and authorization service",
            "user_service": "User management and profile service",
            "database": "Primary data persistence layer",
            "cache": "Distributed caching layer",
            "message_queue": "Asynchronous message processing",
            "file_storage": "File and blob storage service",
            "notification_service": "Multi-channel notification delivery",
            "search_service": "Full-text search and indexing",
            "analytics_service": "Analytics and metrics collection",
            "payment_service": "Payment processing and billing",
            "frontend": "Web frontend application",
            "admin_panel": "Administrative dashboard",
            "notification_service": "Notification delivery service",
        }
        
        return ArchitecturalComponent(
            name=name,
            type=self._infer_type(name),
            description=descriptions.get(name, f"{name} service"),
            responsibilities=self._infer_responsibilities(name),
            interfaces=self._infer_interfaces(name),
            dependencies=self._infer_dependencies(name),
        )
    
    def _create_core_database(self) -> ArchitecturalComponent:
        return ArchitecturalComponent(
            name="database",
            type="database",
            description="Primary data persistence layer with ACID guarantees",
            responsibilities=["Data persistence", "Transaction management", "Query optimization", "Backup/restore"],
            interfaces=["SQL/ORM", "Connection pool", "Migration runner"],
            dependencies=[],
            technology="PostgreSQL",
        )
    
    def _create_api_gateway(self) -> ArchitecturalComponent:
        return ArchitecturalComponent(
            name="api_gateway",
            type="gateway",
            description="API Gateway for request routing, authentication, and rate limiting",
            responsibilities=["Request routing", "Authentication", "Rate limiting", "Request/response transformation", "SSL termination"],
            interfaces=["REST API", "WebSocket", "GraphQL"],
            dependencies=["auth_service"],
            technology="Kong/NGINX/AWS API Gateway",
        )
    
    def _infer_type(self, name: str) -> str:
        types = {
            "api_gateway": "gateway",
            "auth_service": "service",
            "user_service": "service",
            "database": "database",
            "cache": "cache",
            "message_queue": "queue",
            "file_storage": "storage",
            "notification_service": "service",
            "search_service": "service",
            "analytics_service": "service",
            "payment_service": "service",
            "frontend": "application",
            "admin_panel": "application",
        }
        return types.get(name, "service")
    
    def _infer_responsibilities(self, name: str) -> List[str]:
        responsibilities = {
            "api_gateway": ["Request routing", "Authentication", "Rate limiting", "SSL termination"],
            "auth_service": ["User authentication", "Token management", "OAuth/OIDC", "Permission checks"],
            "user_service": ["User CRUD", "Profile management", "Account lifecycle"],
            "database": ["Data persistence", "Transactions", "Migrations", "Backups"],
            "cache": ["Session storage", "Query caching", "Rate limit counters"],
            "message_queue": ["Task scheduling", "Event publishing", "Retry logic", "Dead letter handling"],
            "file_storage": ["Upload/download", "CDN integration", "Access control", "Versioning"],
            "notification_service": ["Email", "SMS", "Push", "In-app", "Templates"],
            "search_service": ["Indexing", "Full-text search", "Filters", "Facets"],
            "analytics_service": ["Event collection", "Aggregation", "Dashboards", "Alerts"],
            "payment_service": ["Payment processing", "Subscriptions", "Invoicing", "Refunds"],
            "frontend": ["UI rendering", "State management", "API integration", "Routing"],
            "admin_panel": ["User management", "Analytics", "Configuration", "Audit logs"],
        }
        return responsibilities.get(name, ["Core business logic"])
    
    def _infer_interfaces(self, name: str) -> List[str]:
        interfaces = {
            "api_gateway": ["REST API", "WebSocket", "GraphQL"],
            "auth_service": ["OAuth2", "OIDC", "JWT", "API Keys"],
            "user_service": ["gRPC", "REST"],
            "database": ["SQL", "ORM"],
            "cache": ["Redis Protocol", "Memcached"],
            "message_queue": ["AMQP", "Kafka Protocol", "HTTP Webhooks"],
            "file_storage": ["S3 API", "HTTP"],
            "notification_service": ["SMTP", "Webhooks", "FCM/APNS"],
            "search_service": ["Elasticsearch API", "REST"],
            "analytics_service": ["REST", "Webhooks"],
            "payment_service": ["REST", "Webhooks"],
            "frontend": ["REST", "GraphQL", "Server-Sent Events"],
            "admin_panel": ["REST", "GraphQL"],
        }
        return interfaces.get(name, ["REST"])
    
    def _infer_dependencies(self, name: str) -> List[str]:
        deps = {
            "api_gateway": ["auth_service"],
            "auth_service": ["database", "cache"],
            "user_service": ["database", "auth_service", "cache"],
            "database": [],
            "cache": [],
            "message_queue": ["database"],
            "file_storage": ["database"],
            "notification_service": ["message_queue", "user_service"],
            "search_service": ["database", "message_queue"],
            "analytics_service": ["database", "message_queue"],
            "payment_service": ["database", "auth_service", "message_queue"],
            "frontend": ["api_gateway", "auth_service"],
            "admin_panel": ["api_gateway", "auth_service"],
        }
        return deps.get(name, [])
    
    def _design_data_flows(self, components: List[ArchitecturalComponent]) -> List[DataFlow]:
        """Design data flows between components."""
        flows = []
        comp_names = {c.name for c in components}
        
        # Standard flows
        standard_flows = [
            ("frontend", "api_gateway", "HTTP Requests", "HTTPS", "real-time"),
            ("api_gateway", "auth_service", "Auth Validation", "gRPC/REST", "real-time"),
            ("api_gateway", "user_service", "User Data", "gRPC/REST", "real-time"),
            ("user_service", "database", "User CRUD", "SQL", "real-time"),
            ("user_service", "cache", "Session Cache", "Redis Protocol", "real-time"),
            ("api_gateway", "message_queue", "Async Tasks", "AMQP/HTTP", "async"),
            ("message_queue", "notification_service", "Notifications", "AMQP", "async"),
            ("notification_service", "database", "Notification Logs", "SQL", "async"),
            ("frontend", "analytics_service", "Events", "HTTP/Beacon", "real-time"),
            ("analytics_service", "database", "Analytics Storage", "SQL", "batch"),
        ]
        
        for source, target, data_type, protocol, freq in standard_flows:
            if source in comp_names and target in comp_names:
                flows.append(DataFlow(
                    source=source,
                    target=target,
                    data_type=data_type,
                    protocol=protocol,
                    frequency=freq,
                ))
        
        return flows
    
    def _assign_technologies(self, components: List[ArchitecturalComponent], constraints: List[str]):
        """Assign technology stack based on constraints."""
        constraint_text = " ".join(constraints).lower()
        
        # Default stack
        defaults = {
            "api_gateway": "Kong / NGINX / AWS API Gateway",
            "auth_service": "Python/FastAPI + PostgreSQL + Redis",
            "user_service": "Python/FastAPI + PostgreSQL",
            "database": "PostgreSQL 15+",
            "cache": "Redis 7+",
            "message_queue": "RabbitMQ / Kafka",
            "file_storage": "MinIO / S3",
            "notification_service": "Python + Celery + Redis",
            "search_service": "Elasticsearch / OpenSearch",
            "analytics_service": "ClickHouse / TimescaleDB",
            "payment_service": "Python + Stripe SDK",
            "frontend": "React 18 + TypeScript + Vite",
            "admin_panel": "React + TypeScript + Ant Design",
        }
        
        # Adjust for constraints
        if "python" in constraint_text:
            pass  # Already Python-based
        if "go" in constraint_text or "golang" in constraint_text:
            for k in ["auth_service", "user_service", "api_gateway"]:
                if k in defaults:
                    defaults[k] = defaults[k].replace("Python/FastAPI", "Go/Gin")
        
        if "serverless" in constraint_text or "lambda" in constraint_text:
            defaults["deployment"] = "AWS Lambda / Cloudflare Workers / Vercel"
        
        for comp in components:
            if comp.technology is None and comp.name in defaults:
                comp.technology = defaults[comp.name]


def export_plan(plan: ArchitecturalPlan, format: str = "json") -> str:
    """Export plan in specified format."""
    if format == "json":
        return json.dumps({
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "status": plan.status.value,
            "version": plan.version,
            "components": [
                {
                    "name": c.name,
                    "type": c.type,
                    "description": c.description,
                    "responsibilities": c.responsibilities,
                    "interfaces": c.interfaces,
                    "dependencies": c.dependencies,
                    "technology": c.technology,
                    "config": c.config,
                }
                for c in plan.components
            ],
            "data_flows": [
                {
                    "source": f.source,
                    "target": f.target,
                    "data_type": f.data_type,
                    "protocol": f.protocol,
                    "frequency": f.frequency,
                    "transformation": f.transformation,
                }
                for f in plan.data_flows
            ],
            "test_scenarios": [
                {
                    "id": t.id,
                    "name": t.name,
                    "type": t.test_type.value,
                    "description": t.description,
                    "given": t.given,
                    "when": t.when,
                    "then": t.then,
                    "priority": t.priority,
                    "tags": t.tags,
                }
                for t in plan.test_scenarios
            ],
            "requirements": plan.requirements,
            "constraints": plan.constraints,
            "assumptions": plan.assumptions,
        }, indent=2)
    
    elif format == "markdown":
        return _plan_to_markdown(plan)
    
    elif format == "mermaid":
        return _plan_to_mermaid(plan)
    
    return ""


def _plan_to_markdown(plan: ArchitecturalPlan) -> str:
    md = f"# {plan.name} - Architectural Plan\n\n"
    md += f"**Version:** {plan.version}  \n"
    md += f"**Status:** {plan.status.value}  \n"
    md += f"**Description:** {plan.description}\n\n"
    
    md += "## Components\n\n"
    for c in plan.components:
        md += f"### {c.name} ({c.type})\n"
        md += f"**Technology:** {c.technology or 'TBD'}  \n"
        md += f"**Description:** {c.description}\n\n"
        md += "**Responsibilities:**\n"
        for r in c.responsibilities:
            md += f"- {r}\n"
        md += "\n**Interfaces:**\n"
        for i in c.interfaces:
            md += f"- {i}\n"
        md += "\n**Dependencies:**\n"
        for d in c.dependencies:
            md += f"- {d}\n"
        md += "\n---\n\n"
    
    md += "## Data Flows\n\n"
    for f in plan.data_flows:
        md += f"- **{f.source}** → **{f.target}** ({f.data_type}) via {f.protocol} [{f.frequency}]\n"
    
    md += "\n## Test Scenarios\n\n"
    for t in plan.test_scenarios:
        md += f"### {t.name} ({t.test_type.value})\n"
        md += f"**Priority:** {t.priority}/10  \n"
        md += f"**Given:** {t.given}  \n"
        md += f"**When:** {t.when}  \n"
        md += f"**Then:** {t.then}  \n"
        md += f"**Tags:** {', '.join(t.tags)}\n\n"
    
    md += "## Requirements\n\n"
    for req in plan.requirements:
        md += f"- {req}\n"
    
    return md


def _plan_to_mermaid(plan: ArchitecturalPlan) -> str:
    """Generate Mermaid diagram for architecture."""
    lines = ["```mermaid", "graph TD"]
    
    # Nodes
    for c in plan.components:
        shape = "cylinder" if c.type == "database" else "box"
        lines.append(f'    {c.name.replace("-", "_")}[{c.name}]')
    
    # Edges
    for f in plan.data_flows:
        lines.append(f'    {f.source.replace("-", "_")} -->|{f.data_type}| {f.target.replace("-", "_")}')
    
    for c in plan.components:
        for dep in c.dependencies:
            if dep in [c.name for c in plan.components]:
                lines.append(f'    {c.name.replace("-", "_")} -.-> {dep.replace("-", "_")}')
    
    lines.append("```")
    return "\n".join(lines)


# CLI Integration
async def create_plan_from_requirements(requirements_text: str, output_format: str = "json") -> str:
    """Create plan from requirements text."""
    planner = ArchitecturalPlanner()
    plan = planner.create_plan(requirements_text)
    return export_plan(plan, output_format)


# Example usage
if __name__ == "__main__":
    sample_requirements = """
    The system must provide a REST API for user management.
    Users should be able to register, login, and manage their profiles.
    The system must support JWT-based authentication with refresh tokens.
    Performance: API response time must be under 200ms for 95th percentile.
    The system must handle 10,000 concurrent users.
    Data must be encrypted at rest and in transit.
    Audit logging must be enabled for all administrative actions.
    The system should support horizontal scaling.
    Budget constraint: prefer open-source technologies.
    Deployment target: Kubernetes on AWS.
    """
    
    plan = asyncio.run(create_plan_from_requirements(sample_requirements, "markdown"))
    print(plan)


class PlanningEngine:
    """High-level planning engine for backward compatibility."""
    
    def __init__(self):
        self.planner = ArchitecturalPlanner()
    
    async def create_plan(self, requirements_text: str, project_name: str = "Project") -> ArchitecturalPlan:
        """Create an architectural plan from requirements."""
        return self.planner.create_plan(requirements_text, project_name)
    
    async def generate_tests(self, plan: ArchitecturalPlan) -> List[TestScenario]:
        """Generate test scenarios from a plan."""
        return self.planner.tdd_planner.generate_test_scenarios(plan)
    
    def export_plan(self, plan: ArchitecturalPlan, format: str = "json") -> str:
        """Export plan in specified format."""
        return export_plan(plan, format)