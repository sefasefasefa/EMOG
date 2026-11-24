"""Görsel Tasarım Denetimi: oluşturulan uygulamayı tarayıcıda açar,
ekran görüntüleri alır, tasarım kalitesini analiz eder ve
bulguları eleştiri (critic) döngüsüne besler.
"""
import asyncio
import base64
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from django.conf import settings

from .events import RunLogger

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


@dataclass
class DesignFinding:
    severity: str  # "critical" | "major" | "minor" | "info"
    category: str  # "layout" | "color" | "typography" | "spacing" | "responsive" | "accessibility" | "console" | "performance"
    title: str
    description: str
    element_selector: str = ""
    screenshot_ref: str = ""
    suggestion: str = ""
    wcag_rule: str = ""


@dataclass
class PageReview:
    url: str
    title: str
    viewport: str
    screenshot_path: str
    findings: List[DesignFinding] = field(default_factory=list)
    console_errors: List[str] = field(default_factory=list)
    network_failures: List[str] = field(default_factory=list)
    performance_metrics: Dict[str, float] = field(default_factory=dict)


@dataclass
class DesignReviewResult:
    pages: List[PageReview] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    overall_score: float = 0.0  # 0-100


class DesignReviewer:
    """Playwright ile uygulamayı görsel denetler."""

    def __init__(
        self,
        run_logger: RunLogger,
        project_root: str,
        base_url: str = "http://localhost:8000",
        dev_server_cmd: Optional[str] = None,
        model: str = None,
    ):
        self.log = run_logger
        self.project_root = Path(project_root)
        self.base_url = base_url
        self.dev_server_cmd = dev_server_cmd or self._detect_dev_server()
        self.model = model
        self.server_process: Optional[subprocess.Popen] = None
        self.server_port: Optional[int] = None
        self.browser = None
        self.context = None

        # Viewport'ler: mobil, tablet, masaüstü
        self.viewports = [
            {"name": "mobile", "width": 375, "height": 667, "device_scale": 2},
            {"name": "tablet", "width": 768, "height": 1024, "device_scale": 2},
            {"name": "desktop", "width": 1440, "height": 900, "device_scale": 1},
        ]

    def _detect_dev_server(self) -> Optional[str]:
        """Proje türüne göre geliştirme sunucusu komutunu tahmin et."""
        pkg = self.project_root / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8"))
                scripts = data.get("scripts", {})
                if "dev" in scripts:
                    return "npm run dev"
                if "start" in scripts:
                    return "npm start"
            except Exception:
                pass
        manage = self.project_root / "manage.py"
        if manage.exists():
            return "python manage.py runserver 0.0.0.0:8000"
        return None

    async def __aenter__(self):
        await self._start_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_browser()
        self._stop_dev_server()

    async def _start_browser(self):
        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(headless=True)
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
            )
        except ImportError:
            self.log.warn("Playwright yüklü değil: 'pip install playwright && playwright install chromium'")
            raise
        except Exception as exc:
            self.log.log("exception", f"Tarayıcı başlatılamadı: {exc}")
            raise

    async def _close_browser(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if hasattr(self, "_playwright"):
            await self._playwright.stop()

    def _start_dev_server(self) -> bool:
        if not self.dev_server_cmd:
            self.log.warn("Geliştirme sunucusu komutu bulunamadı")
            return False
        try:
            self.log.status(f"Geliştirme sunucusu başlatılıyor: {self.dev_server_cmd}")
            self.server_process = subprocess.Popen(
                self.dev_server_cmd,
                shell=True,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Sunucunun ayaklanmasını bekle
            for _ in range(30):
                time.sleep(1)
                try:
                    import urllib.request
                    urllib.request.urlopen(self.base_url, timeout=2)
                    self.log.status("Geliştirme sunucusu hazır")
                    return True
                except Exception:
                    continue
            self.log.warn("Geliştirme sunucusu zamanında yanıt vermedi")
            return False
        except Exception as exc:
            self.log.log("exception", f"Sunucu başlatılamadı: {exc}")
            return False

    def _stop_dev_server(self):
        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
            self.server_process = None

    async def review(self, routes: List[str] = None) -> DesignReviewResult:
        """Belirtilen rotaları (veya otomatik keşfedilenleri) denetle."""
        self.log.stage("design_review", "Görsel tasarım denetimi başlıyor")

        if not self._start_dev_server():
            return DesignReviewResult(
                summary={"error": "dev_server_failed"},
                overall_score=0.0,
            )

        if routes is None:
            routes = await self._discover_routes()

        all_pages: List[PageReview] = []

        for route in routes:
            for vp in self.viewports:
                page_review = await self._review_page(route, vp)
                all_pages.append(page_review)

        result = self._aggregate(all_pages)
        self.log.save("design_review", self._result_to_dict(result))
        self.log.stage("design_review", f"Görsel denetim bitti: skor {result.overall_score:.0f}/100")
        return result

    async def _discover_routes(self) -> List[str]:
        """Ana sayfadan linkleri tarayarak rotaları keşfet."""
        page = await self.context.new_page()
        try:
            await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=10000)

            # Tüm <a href> linklerini topla
            links = await page.eval_on_selector_all(
                "a[href]",
                "elements => elements.map(el => el.href).filter(h => h.startsWith(window.location.origin))"
            )
            routes = list(set(links))
            # Sadece ilk 12 rotayı al (token bütçesi)
            return routes[:12]
        except Exception as exc:
            self.log.log("exception", f"Rota keşfi başarısız: {exc}")
            return ["/"]
        finally:
            await page.close()

    async def _review_page(self, route: str, viewport: Dict) -> PageReview:
        page = await self.context.new_page()
        try:
            # Viewport ayarla
            await page.set_viewport_size({"width": viewport["width"], "height": viewport["height"]})
            await page.set_device_scale_factor(viewport.get("device_scale", 1))

            url = urljoin(self.base_url, route)
            console_errors: List[str] = []
            network_failures: List[str] = []

            # Konsol hatalarını yakala
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            # Ağ hatalarını yakala
            page.on("response", lambda resp: network_failures.append(f"{resp.url} -> {resp.status}") if resp.status >= 400 else None)

            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Performans metrikleri
            perf = await page.evaluate("""() => {
                const nav = performance.getEntriesByType('navigation')[0];
                const paint = performance.getEntriesByType('paint');
                return {
                    dom_content_loaded: nav?.domContentLoadedEventEnd - nav?.domContentLoadedEventStart || 0,
                    load_complete: nav?.loadEventEnd - nav?.loadEventStart || 0,
                    first_paint: paint.find(p => p.name === 'first-paint')?.startTime || 0,
                    first_contentful_paint: paint.find(p => p.name === 'first-contentful-paint')?.startTime || 0,
                };
            }""")

            # Ekran görüntüsü al
            screenshot_name = f"design_{viewport['name']}_{route.replace('/', '_') or 'home'}.png"
            screenshot_dir = Path(self.log.log_path).parent / "design_screenshots"
            screenshot_dir.mkdir(exist_ok=True)
            screenshot_path = str(screenshot_dir / screenshot_name)
            self.log.status(
                f"Ekran görüntüsü alınıyor: {route} ({viewport['name']})"
            )
            await page.screenshot(path=screenshot_path, full_page=True)
            self.log.status(
                f"Ekran görüntüsü alındı: {route} ({viewport['name']})"
            )

            # Sayfa başlığı
            title = await page.title()

            # Görsel analiz
            self.log.status(
                f"Görsel analiz yapılıyor: {route} ({viewport['name']})"
            )
            findings = await self._analyze_design(page, route, viewport, screenshot_path)

            return PageReview(
                url=url,
                title=title,
                viewport=viewport["name"],
                screenshot_path=screenshot_path,
                findings=findings,
                console_errors=console_errors[:20],
                network_failures=network_failures[:20],
                performance_metrics=perf,
            )
        except Exception as exc:
            self.log.log("exception", f"Sayfa denetimi başarısız {route}: {exc}")
            return PageReview(url=route, title="ERROR", viewport=viewport["name"], screenshot_path="", findings=[
                DesignFinding("critical", "console", "Sayfa yüklenemedi", str(exc))
            ])
        finally:
            await page.close()

    async def _analyze_design(self, page, route: str, viewport: Dict, screenshot_path: str) -> List[DesignFinding]:
        """Sayfada görsel/erişilebilirlik/performans analizleri çalıştır."""
        findings: List[DesignFinding] = []

        # 1. Erişilebilirlik (axe-core)
        a11y_findings = await self._run_axe(page, route, viewport)
        findings.extend(a11y_findings)

        # 2. Layout / boşluk / taşma kontrolleri
        layout_findings = await self._check_layout(page, route, viewport)
        findings.extend(layout_findings)

        # 3. Renk kontrastı (basit örnekleme)
        contrast_findings = await self._check_contrast(page, route, viewport)
        findings.extend(contrast_findings)

        # 4. Responsive: mobilde yatay kaydırma var mı?
        responsive_findings = await self._check_responsive(page, route, viewport)
        findings.extend(responsive_findings)

        # 5. Görsel boşluk / whitespace dengelemesi
        spacing_findings = await self._check_spacing(page, route, viewport)
        findings.extend(spacing_findings)

        return findings

    async def _run_axe(self, page, route: str, viewport: Dict) -> List[DesignFinding]:
        """axe-core ile erişilebilirlik denetimi."""
        findings: List[DesignFinding] = []
        try:
            # axe-core scripti enjekte et
            await page.add_script_tag(url="https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.8.4/axe.min.js")
            results = await page.evaluate("""() => {
                return new Promise((resolve) => {
                    axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] } }, (err, results) => {
                        if (err) resolve({ violations: [] });
                        else resolve(results);
                    });
                });
            }""")
            for violation in results.get("violations", []):
                for node in violation.get("nodes", [])[:3]:  # İlk 3 örnek
                    findings.append(DesignFinding(
                        severity=self._axe_severity(violation.get("impact", "minor")),
                        category="accessibility",
                        title=f"A11Y: {violation.get('help', 'Erişilebilirlik ihlali')}",
                        description=violation.get("description", "")[:300],
                        element_selector=node.get("target", [""])[0] if node.get("target") else "",
                        wcag_rule=violation.get("id", ""),
                        suggestion=violation.get("helpUrl", ""),
                    ))
        except Exception as exc:
            logger.debug("axe-core çalıştırılamadı: %s", exc)
        return findings

    def _axe_severity(self, impact: str) -> str:
        mapping = {"critical": "critical", "serious": "major", "moderate": "major", "minor": "minor"}
        return mapping.get(impact, "minor")

    async def _check_layout(self, page, route: str, viewport: Dict) -> List[DesignFinding]:
        """Layout bozuklukları: taşma, gizli içerik, sıfır boyutlu elementler."""
        findings: List[DesignFinding] = []
        try:
            issues = await page.evaluate("""() => {
                const issues = [];
                const all = document.querySelectorAll('*');
                let overflowCount = 0;
                all.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    // Yatay taşma
                    if (rect.width > window.innerWidth + 2) {
                        overflowCount++;
                        if (overflowCount <= 5) {
                            issues.push({
                                type: 'horizontal_overflow',
                                selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + el.className.split(' ')[0] : ''),
                                message: `Element viewport genişliğinden taşıyor: ${rect.width}px > ${window.innerWidth}px`
                            });
                        }
                    }
                    // Sıfır boyutlu ama içerikli elementler
                    if ((rect.width === 0 || rect.height === 0) && el.textContent?.trim()) {
                        issues.push({
                            type: 'zero_size_with_content',
                            selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''),
                            message: 'İçeriği olan element sıfır boyutlu'
                        });
                    }
                    // position:fixed/sticky ile viewport dışı
                    if (style.position === 'fixed' || style.position === 'sticky') {
                        if (rect.top > window.innerHeight || rect.left > window.innerWidth ||
                            rect.bottom < 0 || rect.right < 0) {
                            issues.push({
                                type: 'fixed_outside_viewport',
                                selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''),
                                message: 'Fixed/sticky element viewport dışında'
                            });
                        }
                    }
                });
                return issues;
            }""")
            for issue in issues:
                findings.append(DesignFinding(
                    severity="major" if issue["type"] == "horizontal_overflow" else "minor",
                    category="layout",
                    title=f"Layout: {issue['type']}",
                    description=issue["message"],
                    element_selector=issue["selector"],
                    suggestion="CSS overflow, max-width veya position değerlerini kontrol edin",
                ))
        except Exception as exc:
            logger.debug("Layout kontrolü başarısız: %s", exc)
        return findings

    async def _check_contrast(self, page, route: str, viewport: Dict) -> List[DesignFinding]:
        """Basit renk kontrastı örnelemesi (metin/arka plan)."""
        findings: List[DesignFinding] = []
        try:
            # Tüm metin elementlerinin hesaplanan renklerini al
            samples = await page.evaluate("""() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const samples = [];
                while (walker.nextNode() && samples.length < 50) {
                    const node = walker.currentNode;
                    const parent = node.parentElement;
                    if (!parent) continue;
                    const style = getComputedStyle(parent);
                    const color = style.color;
                    const bg = style.backgroundColor;
                    const fontSize = parseFloat(style.fontSize);
                    if (color && bg && color !== 'rgba(0, 0, 0, 0)' && bg !== 'rgba(0, 0, 0, 0)') {
                        samples.push({ color, backgroundColor: bg, fontSize, selector: parent.tagName.toLowerCase() });
                    }
                }
                return samples;
            }""")
            # Basit kontrast hesaplama (WCAG 2.1 AA: 4.5:1 normal, 3:1 large)
            for s in samples[:20]:
                contrast = self._calculate_contrast(s["color"], s["backgroundColor"])
                min_ratio = 3.0 if s["fontSize"] >= 18 else 4.5
                if contrast < min_ratio:
                    findings.append(DesignFinding(
                        severity="major" if contrast < 3 else "minor",
                        category="color",
                        title=f"Düşük kontrast: {contrast:.1f}:1 (min {min_ratio}:1)",
                        description=f"Renk: {s['color']}, Arka plan: {s['backgroundColor']}, Font: {s['fontSize']}px",
                        element_selector=s["selector"],
                        suggestion="Metin/arka plan rengini WCAG AA standartlarına uygun hale getirin",
                        wcag_rule="1.4.3 Contrast (Minimum)",
                    ))
        except Exception as exc:
            logger.debug("Kontrast kontrolü başarısız: %s", exc)
        return findings

    def _calculate_contrast(self, color1: str, color2: str) -> float:
        """İki CSS rengi arasındaki kontrast oranını hesapla (basit)."""
        def parse_rgb(css_color: str):
            import re
            m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", css_color)
            if m:
                return [int(m.group(1)), int(m.group(2)), int(m.group(3))]
            # Hex
            if css_color.startswith("#"):
                h = css_color.lstrip("#")
                if len(h) == 3:
                    h = "".join(c * 2 for c in h)
                return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
            return [0, 0, 0]

        def luminance(rgb):
            def channel(c):
                c = c / 255.0
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            r, g, b = [channel(c) for c in rgb]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        rgb1 = parse_rgb(color1)
        rgb2 = parse_rgb(color2)
        l1 = luminance(rgb1)
        l2 = luminance(rgb2)
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

    async def _check_responsive(self, page, route: str, viewport: Dict) -> List[DesignFinding]:
        """Mobilde yatay kaydırma, dokunma hedefleri boyutu."""
        findings: List[DesignFinding] = []
        if viewport["name"] != "mobile":
            return findings
        try:
            issues = await page.evaluate("""() => {
                const issues = [];
                // Yatay kaydırma
                if (document.documentElement.scrollWidth > window.innerWidth) {
                    issues.push({ type: 'horizontal_scroll', message: 'Mobilde yatay kaydırma var' });
                }
                // Dokunma hedefleri (min 44x44px)
                const interactive = document.querySelectorAll('a, button, input, select, textarea, [role="button"]');
                interactive.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 44 || rect.height < 44) {
                        issues.push({
                            type: 'small_touch_target',
                            selector: el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''),
                            message: `Dokunma hedefi çok küçük: ${rect.width}x${rect.height}px (min 44x44)`
                        });
                    }
                });
                // Viewport meta etiketi
                const viewportMeta = document.querySelector('meta[name="viewport"]');
                if (!viewportMeta) {
                    issues.push({ type: 'missing_viewport_meta', message: 'Viewport meta etiketi eksik' });
                }
                return issues;
            }""")
            for issue in issues[:10]:
                findings.append(DesignFinding(
                    severity="major" if issue["type"] in ("horizontal_scroll", "missing_viewport_meta") else "minor",
                    category="responsive",
                    title=f"Responsive: {issue['type']}",
                    description=issue.get("message", ""),
                    element_selector=issue.get("selector", ""),
                    suggestion="CSS media query, viewport meta veya padding/margin ayarlarını düzeltin",
                ))
        except Exception as exc:
            logger.debug("Responsive kontrolü başarısız: %s", exc)
        return findings

    async def _check_spacing(self, page, route: str, viewport: Dict) -> List[DesignFinding]:
        """Whitespace / boşluk dengelemesi: çok sıkışık veya çok boş alanlar."""
        findings: List[DesignFinding] = []
        try:
            issues = await page.evaluate("""() => {
                const issues = [];
                const containers = document.querySelectorAll('section, main, article, .container, [class*="wrapper"], [class*="content"]');
                containers.forEach(c => {
                    const style = getComputedStyle(c);
                    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) +
                                   parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
                    const children = c.children.length;
                    if (children > 0 && padding < 16) {
                        issues.push({
                            type: 'tight_container',
                            selector: c.tagName.toLowerCase() + (c.id ? '#' + c.id : '') + (c.className ? '.' + c.className.split(' ')[0] : ''),
                            message: f'Kapsayıcı çok az padding: {padding}px'
                        });
                    }
                });
                return issues;
            }""")
            for issue in issues[:5]:
                findings.append(DesignFinding(
                    severity="minor",
                    category="spacing",
                    title=f"Boşluk: {issue['type']}",
                    description=issue["message"],
                    element_selector=issue["selector"],
                    suggestion="Tutarlı spacing scale (8px/16px/24px) uygulayın",
                ))
        except Exception as exc:
            logger.debug("Spacing kontrolü başarısız: %s", exc)
        return findings

    def _aggregate(self, pages: List[PageReview]) -> DesignReviewResult:
        """Tüm sayfa denetimlerini özetle."""
        severity_counts = {"critical": 0, "major": 0, "minor": 0, "info": 0}
        category_counts = {}
        total_findings = 0

        for p in pages:
            for f in p.findings:
                severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
                category_counts[f.category] = category_counts.get(f.category, 0) + 1
                total_findings += 1

        # Skor: critical=-25, major=-10, minor=-3, info=0 (başlangıç 100)
        score = 100
        score -= severity_counts.get("critical", 0) * 25
        score -= severity_counts.get("major", 0) * 10
        score -= severity_counts.get("minor", 0) * 3
        score = max(0, min(100, score))

        return DesignReviewResult(
            pages=pages,
            summary={
                "total_pages": len(pages),
                "total_findings": total_findings,
                "by_severity": severity_counts,
                "by_category": category_counts,
                "console_errors": sum(len(p.console_errors) for p in pages),
                "network_failures": sum(len(p.network_failures) for p in pages),
            },
            overall_score=score,
        )

    def _result_to_dict(self, result: DesignReviewResult) -> Dict:
        return {
            "overall_score": result.overall_score,
            "summary": result.summary,
            "pages": [
                {
                    "url": p.url,
                    "title": p.title,
                    "viewport": p.viewport,
                    "screenshot": p.screenshot_path,
                    "findings": [
                        {
                            "severity": f.severity,
                            "category": f.category,
                            "title": f.title,
                            "description": f.description,
                            "element": f.element_selector,
                            "suggestion": f.suggestion,
                            "wcag": f.wcag_rule,
                        }
                        for f in p.findings
                    ],
                    "console_errors": p.console_errors,
                    "network_failures": p.network_failures,
                    "performance": p.performance_metrics,
                }
                for p in result.pages
            ],
        }


async def run_design_review(
    run_logger: RunLogger,
    project_root: str,
    base_url: str = None,
    routes: List[str] = None,
    model: str = None,
) -> DesignReviewResult:
    """Kısayol fonksiyon: tasarım denetimini çalıştır."""
    base = base_url or getattr(settings, "DESIGN_REVIEW_BASE_URL", "http://localhost:8000")
    async with DesignReviewer(run_logger, project_root, base_url=base, model=model) as reviewer:
        return await reviewer.review(routes)


def run_design_review_sync(
    run_logger: RunLogger,
    project_root: str,
    base_url: str = None,
    routes: List[str] = None,
    model: str = None,
) -> DesignReviewResult:
    """Senkron wrapper (thread'den çağrılabilir)."""
    return asyncio.run(run_design_review(run_logger, project_root, base_url, routes, model))


def to_critic_items(result: DesignReviewResult) -> List[Dict]:
    """DesignReviewResult'u critic'in anlayacağı missing_items formatına çevir."""
    items: List[Dict] = []
    for page in result.pages:
        for finding in page.findings:
            if finding.severity in ("critical", "major"):
                items.append({
                    "title": f"Tasarım: {finding.title}",
                    "phase": "hardening" if finding.category in ("accessibility", "color", "responsive") else "polish",
                    "objective": finding.description[:200],
                    "detail": f"{finding.description}\n\nSayfa: {page.url} ({page.viewport})\nÖneri: {finding.suggestion}",
                    "files": [page.screenshot_path] if page.screenshot_path else [],
                    "acceptance": [finding.suggestion] if finding.suggestion else ["Tasarım sorunu giderildi"],
                    "complexity": 2 if finding.severity == "major" else 1,
                })
    return items[:10]  # Max 10 yeni kalem