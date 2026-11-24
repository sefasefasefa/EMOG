"""
6. Autonomous UI & Web Testing
Visits deployed systems like a user, tests functionality/performance/flow.
"""
import asyncio
import time
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json


class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TestType(Enum):
    NAVIGATION = "navigation"
    INTERACTION = "interaction"
    VISUAL = "visual"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"
    FUNCTIONAL = "functional"
    REGRESSION = "regression"


@dataclass
class TestStep:
    action: str  # click, type, wait, navigate, scroll, screenshot
    selector: Optional[str] = None
    value: Optional[str] = None
    wait_for: Optional[str] = None
    timeout: int = 5000
    description: str = ""


@dataclass
class TestCase:
    id: str
    name: str
    test_type: TestType
    description: str
    steps: List[TestStep]
    expected_outcome: str
    url: Optional[str] = None
    viewport: Dict = field(default_factory=lambda: {"width": 1280, "height": 720})
    tags: List[str] = field(default_factory=list)
    status: TestStatus = TestStatus.PENDING


@dataclass
class TestResult:
    test_id: str
    status: TestStatus
    duration: float
    steps_completed: int
    total_steps: int
    screenshots: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: Dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class TestScenarioBuilder:
    """Builds test scenarios from user stories or requirements."""
    
    COMMON_FLOWS = {
        "login": [
            TestStep(action="navigate", value="/login", description="Go to login page"),
            TestStep(action="type", selector="input[name='email']", value="test@example.com", description="Enter email"),
            TestStep(action="type", selector="input[name='password']", value="password123", description="Enter password"),
            TestStep(action="click", selector="button[type='submit']", description="Click login"),
            TestStep(action="wait_for", selector=".dashboard, .home", description="Wait for dashboard"),
        ],
        "signup": [
            TestStep(action="navigate", value="/signup", description="Go to signup page"),
            TestStep(action="type", selector="input[name='email']", value="newuser@example.com"),
            TestStep(action="type", selector="input[name='password']", value="securepass123"),
            TestStep(action="type", selector="input[name='confirm']", value="securepass123"),
            TestStep(action="click", selector="button[type='submit']"),
            TestStep(action="wait_for", selector=".welcome, .dashboard"),
        ],
        "search": [
            TestStep(action="click", selector="input[type='search'], .search-input"),
            TestStep(action="type", selector="input[type='search']", value="test query"),
            TestStep(action="wait_for", selector=".results, .search-results"),
            TestStep(action="click", selector=".result-item:first-child"),
        ],
        "form_submit": [
            TestStep(action="navigate", value="/form"),
            TestStep(action="type", selector="input[name='name']", value="Test User"),
            TestStep(action="type", selector="input[name='email']", value="test@example.com"),
            TestStep(action="type", selector="textarea[name='message']", value="Test message"),
            TestStep(action="click", selector="button[type='submit']"),
            TestStep(action="wait_for", selector=".success, .confirmation"),
        ],
        "navigation": [
            TestStep(action="navigate", value="/"),
            TestStep(action="click", selector="nav a[href='/about']"),
            TestStep(action="wait_for", selector="h1:contains('About')"),
            TestStep(action="click", selector="nav a[href='/contact']"),
            TestStep(action="wait_for", selector="form"),
        ],
    }
    
    @classmethod
    def create_from_user_story(cls, story: str, base_url: str) -> List[TestCase]:
        """Create test cases from user story."""
        tests = []
        story_lower = story.lower()
        
        # Detect flow types from story
        for flow_name, steps in cls.COMMON_FLOWS.items():
            if flow_name in story_lower or any(kw in story_lower for kw in cls._get_keywords(flow_name)):
                test = TestCase(
                    id=f"test_{flow_name}_{int(time.time())}",
                    name=f"Test {flow_name} flow",
                    test_type=TestType.FUNCTIONAL,
                    description=f"Verify {flow_name} works as described: {story}",
                    steps=steps,
                    expected_outcome=f"{flow_name.capitalize()} completes successfully",
                    url=base_url,
                    tags=[flow_name, "user-story"],
                )
                tests.append(test)
        
        # If no specific flow detected, create generic navigation test
        if not tests:
            tests.append(TestCase(
                id=f"test_generic_{int(time.time())}",
                name="Generic navigation test",
                test_type=TestType.NAVIGATION,
                description=f"Basic navigation test for: {story}",
                steps=cls.COMMON_FLOWS["navigation"],
                expected_outcome="Page loads and navigation works",
                url=base_url,
                tags=["generic"],
            ))
        
        return tests
    
    @classmethod
    def _get_keywords(cls, flow: str) -> List[str]:
        keywords = {
            "login": ["login", "sign in", "authenticate"],
            "signup": ["signup", "sign up", "register", "create account"],
            "search": ["search", "find", "look for"],
            "form_submit": ["form", "submit", "contact", "feedback"],
            "navigation": ["navigate", "menu", "page"],
        }
        return keywords.get(flow, [])
    
    @classmethod
    def create_regression_suite(cls, base_url: str, features: List[str]) -> List[TestCase]:
        """Create regression test suite for features."""
        tests = []
        for feature in features:
            tests.extend(cls.create_from_user_story(f"Test {feature} functionality", base_url))
        return tests


class AutonomousTester:
    """
    Autonomous web tester that visits deployed systems
    and tests functionality, performance, and user flows.
    """
    
    def __init__(self, base_url: str, headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.results: List[TestResult] = []
        self.screenshots_dir = Path("./test_screenshots")
        self.screenshots_dir.mkdir(exist_ok=True)
    
    async def initialize(self) -> bool:
        """Initialize Playwright browser."""
        try:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 720},
                record_video_dir=str(self.screenshots_dir / "videos") if not self.headless else None
            )
            self.page = await self.context.new_page()
            return True
        except ImportError:
            return False
        except Exception:
            return False
    
    async def close(self) -> None:
        """Close browser."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
    
    async def run_test(self, test: TestCase) -> TestResult:
        """Run a single test case."""
        if not self.page:
            await self.initialize()
        
        start_time = time.time()
        steps_completed = 0
        screenshots = []
        errors = []
        metrics = {}
        
        try:
            # Navigate to test URL
            url = test.url or self.base_url
            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Set viewport if specified
            if test.viewport:
                await self.page.set_viewport_size(test.viewport)
            
            # Execute each step
            for i, step in enumerate(test.steps):
                await self._execute_step(step)
                steps_completed += 1
                
                # Take screenshot on visual tests or every few steps
                if test.test_type == TestType.VISUAL or i % 3 == 0:
                    screenshot_path = await self._take_screenshot(test.id, i)
                    screenshots.append(screenshot_path)
            
            # Verify expected outcome
            success = await self._verify_outcome(test)
            
            status = TestStatus.PASSED if success else TestStatus.FAILED
            if not success:
                errors.append(f"Expected outcome not met: {test.expected_outcome}")
            
        except Exception as e:
            status = TestStatus.FAILED
            errors.append(f"Test execution error: {str(e)}")
            # Take error screenshot
            screenshot_path = await self._take_screenshot(test.id, steps_completed, error=True)
            screenshots.append(screenshot_path)
        
        duration = time.time() - start_time
        
        result = TestResult(
            test_id=test.id,
            status=status,
            duration=duration,
            steps_completed=steps_completed,
            total_steps=len(test.steps),
            screenshots=screenshots,
            errors=errors,
            metrics=metrics
        )
        
        self.results.append(result)
        return result
    
    async def _execute_step(self, step: TestStep) -> None:
        """Execute a single test step."""
        if step.action == "navigate":
            await self.page.goto(step.value, wait_until="networkidle", timeout=step.timeout)
        
        elif step.action == "click":
            await self.page.click(step.selector, timeout=step.timeout)
        
        elif step.action == "type":
            await self.page.fill(step.selector, step.value, timeout=step.timeout)
        
        elif step.action == "wait_for":
            await self.page.wait_for_selector(step.wait_for, timeout=step.timeout)
        
        elif step.action == "scroll":
            await self.page.evaluate(f"window.scrollTo(0, {step.value or 'document.body.scrollHeight'})")
        
        elif step.action == "screenshot":
            await self._take_screenshot("manual", 0)
        
        elif step.action == "wait":
            await asyncio.sleep(step.timeout / 1000)
        
        elif step.action == "hover":
            await self.page.hover(step.selector, timeout=step.timeout)
        
        elif step.action == "select":
            await self.page.select_option(step.selector, step.value, timeout=step.timeout)
        
        elif step.action == "press":
            await self.page.press(step.selector, step.value, timeout=step.timeout)
    
    async def _verify_outcome(self, test: TestCase) -> bool:
        """Verify test outcome."""
        # Basic verification - check for error indicators
        error_selectors = [
            ".error", ".alert-danger", "[role='alert']",
            ".toast-error", ".notification-error"
        ]
        
        for selector in error_selectors:
            if await self.page.query_selector(selector):
                return False
        
        # Check for success indicators if specified
        if test.expected_outcome:
            # Could check for specific text, URL, or element
            pass
        
        return True
    
    async def _take_screenshot(self, test_id: str, step: int, error: bool = False) -> str:
        """Take screenshot and save to file."""
        prefix = "error" if error else "step"
        filename = f"{prefix}_{test_id}_{step}_{int(time.time())}.png"
        path = self.screenshots_dir / filename
        await self.page.screenshot(path=str(path), full_page=True)
        return str(path)
    
    async def run_test_suite(self, tests: List[TestCase], parallel: bool = False) -> List[TestResult]:
        """Run multiple tests."""
        if parallel:
            # Run tests in parallel (limited concurrency)
            semaphore = asyncio.Semaphore(3)
            
            async def run_with_semaphore(test):
                async with semaphore:
                    return await self.run_test(test)
            
            tasks = [run_with_semaphore(t) for t in tests]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            processed = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    processed.append(TestResult(
                        test_id=tests[i].id,
                        status=TestStatus.FAILED,
                        duration=0,
                        steps_completed=0,
                        total_steps=len(tests[i].steps),
                        errors=[str(r)]
                    ))
                else:
                    processed.append(r)
            return processed
        else:
            results = []
            for test in tests:
                result = await self.run_test(test)
                results.append(result)
            return results
    
    async def run_performance_test(self, url: str, iterations: int = 10) -> TestResult:
        """Run performance test on a URL."""
        test = TestCase(
            id=f"perf_{int(time.time())}",
            name=f"Performance test: {url}",
            test_type=TestType.PERFORMANCE,
            description=f"Measure load time for {url}",
            steps=[
                TestStep(action="navigate", value=url, description="Load page"),
            ],
            expected_outcome="Page loads within acceptable time",
            url=url,
        )
        
        metrics_list = []
        for _ in range(iterations):
            start = time.time()
            await self.page.goto(url, wait_until="networkidle", timeout=30000)
            load_time = time.time() - start
            metrics_list.append(load_time)
        
        avg_time = sum(metrics_list) / len(metrics_list)
        
        return TestResult(
            test_id=test.id,
            status=TestStatus.PASSED if avg_time < 3.0 else TestStatus.FAILED,
            duration=sum(metrics_list),
            steps_completed=iterations,
            total_steps=1,
            metrics={
                "avg_load_time": avg_time,
                "min_load_time": min(metrics_list),
                "max_load_time": max(metrics_list),
                "iterations": iterations,
                "threshold": 3.0
            }
        )
    
    async def run_accessibility_test(self, url: str) -> TestResult:
        """Run basic accessibility test."""
        test = TestCase(
            id=f"a11y_{int(time.time())}",
            name=f"Accessibility test: {url}",
            test_type=TestType.ACCESSIBILITY,
            description=f"Check basic accessibility for {url}",
            steps=[
                TestStep(action="navigate", value=url),
            ],
            expected_outcome="Page passes basic accessibility checks",
            url=url,
        )
        
        await self.page.goto(url, wait_until="networkidle")
        
        # Run axe-core or basic checks
        issues = []
        
        # Check for images without alt
        images = await self.page.query_selector_all("img")
        for img in images:
            alt = await img.get_attribute("alt")
            if not alt:
                issues.append("Image missing alt attribute")
        
        # Check for form labels
        inputs = await self.page.query_selector_all("input, select, textarea")
        for inp in inputs:
            id_attr = await inp.get_attribute("id")
            if id_attr:
                label = await self.page.query_selector(f"label[for='{id_attr}']")
                if not label:
                    issues.append(f"Input {id_attr} missing label")
        
        # Check heading hierarchy
        headings = await self.page.query_selector_all("h1, h2, h3, h4, h5, h6")
        if not headings:
            issues.append("No heading structure found")
        
        # Check color contrast (basic)
        # Would need more sophisticated analysis
        
        return TestResult(
            test_id=test.id,
            status=TestStatus.PASSED if not issues else TestStatus.FAILED,
            duration=0,
            steps_completed=1,
            total_steps=1,
            errors=issues,
            metrics={"issues_found": len(issues)}
        )
    
    def get_summary(self) -> Dict:
        """Get test summary."""
        if not self.results:
            return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0}
        
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": sum(1 for r in self.results if r.status == TestStatus.SKIPPED),
            "pass_rate": passed / total if total > 0 else 0,
            "avg_duration": sum(r.duration for r in self.results) / total,
            "total_screenshots": sum(len(r.screenshots) for r in self.results),
        }
    
    def export_results(self, format: str = "json") -> str:
        """Export test results."""
        if format == "json":
            return json.dumps({
                "summary": self.get_summary(),
                "results": [
                    {
                        "test_id": r.test_id,
                        "status": r.status.value,
                        "duration": r.duration,
                        "steps_completed": r.steps_completed,
                        "total_steps": r.total_steps,
                        "errors": r.errors,
                        "metrics": r.metrics,
                        "screenshots": r.screenshots,
                        "timestamp": r.timestamp,
                    }
                    for r in self.results
                ]
            }, indent=2)
        return ""