"""Canlı önizleme (preview) fonksiyonel testi.

Üretilen siteyi gerçek bir Chromium tarayıcısında açar ve şunları denetler:

  * sayfa yükleniyor mu, HTTP hatası var mı?
  * konsol hataları / yakalanmamış JS istisnaları
  * başarısız ağ istekleri (JS/CSS/asset 404 vb.)
  * aynı origin'deki linklerin HTTP durumu
  * görünür butonların tıklanabilirliği ve tıklama sırasında oluşan JS hataları

Bulgular `to_critic_items()` ile build döngüsüne (critic kalemleri) geri
beslenir; böylece ajan çalışmayan butonları/JS hatalarını düzeltmek zorunda
kalır. Aynı modül `/preview/live-qa/` ucu üzerinden manuel olarak da
çalıştırılabilir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

# Aynı anda birden fazla Chromium/QA çalışmasın.
_QA_LOCK = threading.Lock()


_ENTRY_CANDIDATES = (
    "index.html", "public/index.html", "src/index.html",
    "dist/index.html", "build/index.html", "out/index.html",
    "app/index.html", "html/index.html", "site/index.html",
)

# Tıklanması yan etki üretebilecek / veri değiştirebilecek kontroller.
_DESTRUCTIVE_RE = re.compile(
    r"(sil|delete|remove|kaldır|kaldir|temizle|çıkış|çikis|cikis|logout|sign\s*out"
    r"|ödeme|odeme|öde|ode|satın|satin|buy|checkout|unsubscribe|abonelik"
    r"|iptal|cancel|reset|sıfırla|sifirla|yükle|yukle|upload|gönder|gonder"
    r"|pay|transfer|delete\s+account|hesabı\s+sil)",
    re.IGNORECASE,
)

# DOM değişikliği üretmese de geçerli işlevi olan kontroller (pano, yazdırma vb.)
_NO_EFFECT_EXEMPT_RE = re.compile(
    r"(kopyala|copy|paylaş|paylas|share|yazdır|yazdir|print|indir|download"
    r"|tema|theme|dark|light|geç|geri|ileri|prev|next|scroll|yukarı|yukari|aşağı|asagi)",
    re.IGNORECASE,
)

# Konsolda gürültü sayılan, gerçek hata olmayan mesajlar.
_CONSOLE_NOISE = (
    "download the react devtools",
    "react devtools",
    "source map",
    "favicon",
    "[hmr]",
    "[vite]",
    "__vite",
    "net::err_aborted",
)

_BUTTON_SELECTOR = (
    'button, [role="button"], input[type="button"], '
    'input[type="submit"], input[type="reset"]'
)

_COLLECT_CONTROLS_JS = r"""
() => {
  const cssEscape = (value) => (window.CSS && CSS.escape)
    ? CSS.escape(value)
    : String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');

  const cssPath = (el) => {
    if (el.id) return '#' + cssEscape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let sel = node.tagName.toLowerCase();
      const cls = Array.from(node.classList || []).slice(0, 2);
      if (cls.length) sel += '.' + cls.map(cssEscape).join('.');
      const parent = node.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sameTag.length > 1) sel += ':nth-of-type(' + (sameTag.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      node = parent;
    }
    return parts.join(' > ');
  };

  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 1 && rect.height > 1
      && style.display !== 'none'
      && style.visibility !== 'hidden'
      && style.opacity !== '0'
      && !el.hasAttribute('hidden')
      && el.getAttribute('aria-hidden') !== 'true';
  };

  const out = [];
  document.querySelectorAll(`__SELECTOR__`).forEach((el) => {
    if (!visible(el)) return;
    const text = String(
      el.innerText || el.value || el.getAttribute('aria-label')
      || el.getAttribute('title') || el.name || ''
    ).replace(/\s+/g, ' ').trim().slice(0, 100);
    out.push({
      selector: cssPath(el),
      text: text,
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      inForm: !!el.closest('form'),
      href: el.getAttribute('href') || '',
      download: el.hasAttribute('download'),
    });
  });
  return out;
}
""".replace("__SELECTOR__", _BUTTON_SELECTOR)

_COLLECT_LINKS_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href]').forEach((a) => {
    let url;
    try { url = new URL(a.getAttribute('href'), document.baseURI); }
    catch (e) { return; }
    if (!/^https?:$/.test(url.protocol)) return;
    if (url.origin !== window.location.origin) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    if (seen.has(url.href)) return;
    seen.add(url.href);
    out.push({
      href: url.href,
      text: String(a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 80),
      download: a.hasAttribute('download'),
      target: a.getAttribute('target') || '',
    });
  });
  return out.slice(0, 40);
}
"""


# ----------------------------------------------------------------------
# Veri modelleri
# ----------------------------------------------------------------------

@dataclass
class QAIssue:
    severity: str  # critical | major | minor | info
    category: str  # console | page_error | network | interaction | navigation | empty
    title: str
    description: str = ""
    selector: str = ""
    url: str = ""
    screenshot: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QAReport:
    url: str
    ok: bool = True
    title: str = ""
    issues: List[QAIssue] = field(default_factory=list)
    console_errors: List[str] = field(default_factory=list)
    page_errors: List[str] = field(default_factory=list)
    network_failures: List[str] = field(default_factory=list)
    checked: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""

    def blocking_issues(self) -> List[QAIssue]:
        return [i for i in self.issues if i.severity in ("critical", "major")]

    def summary_text(self) -> str:
        if self.error and not self.issues:
            return f"Test çalıştırılamadı: {self.error}"
        blocking = len(self.blocking_issues())
        if blocking:
            return f"{blocking} kritik/önemli sorun bulundu"
        if self.issues:
            return f"{len(self.issues)} küçük uyarı; engelleyici sorun yok"
        return "Tüm canlı kontroller başarılı"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "ok": self.ok,
            "title": self.title,
            "summary": self.summary_text(),
            "issues": [i.to_dict() for i in self.issues],
            "console_errors": self.console_errors,
            "page_errors": self.page_errors,
            "network_failures": self.network_failures,
            "checked": self.checked,
            "notes": self.notes,
            "screenshots": self.screenshots,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class _NoopLogger:
    log_path = ""

    def stage(self, *args, **kwargs):
        return None

    def status(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warn(self, *args, **kwargs):
        return None

    def log(self, *args, **kwargs):
        return None

    def save(self, *args, **kwargs):
        return None



# ----------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------

def _find_entry(root: Path) -> str:
    for rel in _ENTRY_CANDIDATES:
        try:
            if (root / rel).is_file():
                return rel.replace("\\", "/")
        except OSError:
            continue
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name not in {"node_modules", ".git", ".next", "__pycache__", "venv", ".venv"}:
                if (child / "index.html").is_file():
                    return f"{child.name}/index.html"
    except OSError:
        pass
    return ""


def _read_dev_port(root: Path) -> Optional[int]:
    meta_file = root / "project.json"
    if not meta_file.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        port = meta.get("dev_port")
        return int(port) if port else None
    except Exception:
        return None


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def detect_preview_url(project_root: Optional[str] = None, project_id: Optional[str] = None) -> str:
    """Proje için canlı test edilebilecek bir URL döndür (yoksa boş string)."""
    if not project_id:
        try:
            from agent_app.project_scanner import get_project_guid
            project_id = get_project_guid()
        except Exception:
            project_id = None

    root: Optional[Path]
    if project_root:
        root = Path(project_root)
    else:
        try:
            from agent_app.project_scanner import get_project_root
            root = get_project_root()
        except Exception:
            root = None

    if not project_id and root and root.is_dir():
        try:
            meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
            project_id = meta.get("id")
        except Exception:
            project_id = root.name or None

    if not project_id:
        return ""

    base = str(getattr(settings, "FRONTEND_URL", "") or "http://localhost:8002").rstrip("/")

    if root and root.is_dir():
        entry = _find_entry(root)
        if entry:
            folder = entry.rsplit("/", 1)[0] if "/" in entry else ""
            url = f"{base}/preview/site/{project_id}/"
            if folder:
                url += folder.strip("/") + "/"
            return url

        port = _read_dev_port(root)
        if port and _port_open(port):
            return f"http://localhost:{port}"

    return ""


def _is_console_noise(text: str) -> bool:
    low = (text or "").lower()
    return any(token in low for token in _CONSOLE_NOISE)


def _is_network_noise(url: str) -> bool:
    low = (url or "").lower()
    return any(token in low for token in (
        "favicon.ico", ".map", "devtools", "__vite", "hot-update",
        "chrome-extension://",
    ))


def _network_severity(status: Optional[int], resource_type: str) -> str:
    if resource_type in ("script", "stylesheet", "document", "xhr", "fetch"):
        return "major"
    if status is not None and status >= 500:
        return "major"
    return "minor"


def _unique(items: List[str], limit: int = 20) -> List[str]:
    seen = set()
    out = []
    for item in items:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def _dedupe_issues(issues: List[QAIssue]) -> List[QAIssue]:
    seen = set()
    out: List[QAIssue] = []
    for issue in issues:
        key = (issue.severity, issue.category, issue.title, issue.description[:200], issue.selector)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    order = {"critical": 0, "major": 1, "minor": 2, "info": 3}
    out.sort(key=lambda i: order.get(i.severity, 9))
    return out[:40]



# ----------------------------------------------------------------------
# Playwright akışı
# ----------------------------------------------------------------------

async def _settle(page, timeout: int = 6000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass
    try:
        await page.wait_for_timeout(250)
    except Exception:
        pass


async def _install_observer(page) -> None:
    """Tıklama sonrası DOM değişimini ölçmek için MutationObserver kur."""
    try:
        await page.evaluate("""() => {
          if (window.__emogQaObserver) return;
          window.__emogQaMutations = 0;
          window.__emogQaObserver = new MutationObserver((mutations) => {
            window.__emogQaMutations += mutations.length;
          });
          window.__emogQaObserver.observe(document.documentElement, {
            subtree: true, childList: true, attributes: true, characterData: true
          });
        }""")
    except Exception:
        pass


async def _reset_page(page, url: str) -> None:
    try:
        if page.url != url:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        else:
            await page.reload(wait_until="domcontentloaded", timeout=20000)
        await _settle(page)
    except Exception:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
    await _install_observer(page)


async def _close_extra_pages(context, current) -> None:
    for candidate in list(context.pages):
        if candidate is current:
            continue
        try:
            await candidate.close()
        except Exception:
            pass


def _screenshot_dir(run_logger) -> Path:
    log_path = getattr(run_logger, "log_path", "") if run_logger else ""
    base = Path(log_path).parent if log_path else Path("task_runs")
    target = base / "preview_qa"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        target = Path("task_runs") / "preview_qa"
        target.mkdir(parents=True, exist_ok=True)
    return target


async def _run_qa_async(
    url: str,
    run_logger=None,
    max_buttons: int = 10,
    max_links: int = 12,
) -> QAReport:
    started = time.monotonic()
    log = run_logger or _NoopLogger()
    report = QAReport(url=url)

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        report.ok = False
        report.error = "Playwright kurulu değil (pip install playwright && playwright install chromium)"
        return report

    log.stage("tests", "Canlı önizleme testi başlıyor")

    console_errors: List[str] = []
    page_errors: List[str] = []
    network_failures: List[Dict[str, Any]] = []

    browser = None
    context = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                ignore_https_errors=True,
            )
            qa_token = str(getattr(settings, "PREVIEW_QA_TOKEN", "") or "")
            preview_origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
            preview_origins = {preview_origin}
            try:
                parsed = urlparse(url)
                if parsed.hostname in ("localhost", "127.0.0.1"):
                    for host in ("localhost", "127.0.0.1"):
                        preview_origins.add(
                            f"{parsed.scheme}://{host}:{parsed.port}" if parsed.port
                            else f"{parsed.scheme}://{host}"
                        )
            except Exception:
                pass
            if qa_token:
                # Token'ı YALNIZCA kendi preview origin'imize gönder; harici
                # CDN/üçüncü taraf isteklere sızdırma.
                async def _qa_route(route):
                    try:
                        new_headers = dict(route.request.headers)
                        if any(route.request.url.startswith(o) for o in preview_origins):
                            new_headers["x-emog-qa-token"] = qa_token
                        await route.continue_(headers=new_headers)
                    except Exception:
                        try:
                            await route.continue_()
                        except Exception:
                            pass

                await context.route("**/*", _qa_route)

            page = await context.new_page()
            page.set_default_timeout(8000)
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            def _on_request_failed(req):
                network_failures.append({
                    "url": req.url,
                    "status": None,
                    "error": str(req.failure or ""),
                    "type": getattr(req, "resource_type", ""),
                })

            def _on_response(resp):
                if resp.status >= 400:
                    network_failures.append({
                        "url": resp.url,
                        "status": resp.status,
                        "error": "",
                        "type": getattr(resp.request, "resource_type", ""),
                    })

            page.on("requestfailed", _on_request_failed)
            page.on("response", _on_response)

            response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            if response is not None and response.status >= 400:
                report.issues.append(QAIssue(
                    "critical", "navigation", "Sayfa yüklenemedi",
                    f"HTTP {response.status} — {url}", url=url,
                ))
            await _settle(page)
            await _install_observer(page)
            try:
                report.title = await page.title()
            except Exception:
                report.title = ""

            shot_dir = _screenshot_dir(run_logger)
            try:
                shot_path = shot_dir / f"qa_{uuid.uuid4().hex[:8]}.png"
                await page.screenshot(path=str(shot_path), full_page=False)
                report.screenshots.append(str(shot_path))
            except Exception:
                pass

            try:
                body_text = (await page.inner_text("body")).strip()
            except Exception:
                body_text = ""
            report.checked["body_chars"] = len(body_text)
            if len(body_text) < 5:
                report.issues.append(QAIssue(
                    "major", "empty", "Sayfa neredeyse boş",
                    "Body içeriği 5 karakterden kısa; site yüklenmemiş veya render edilmemiş olabilir.",
                    url=url,
                ))



            # --- Link kontrolü ---------------------------------------------
            links: List[Dict[str, Any]] = []
            try:
                links = await page.evaluate(_COLLECT_LINKS_JS)
            except Exception as exc:
                logger.debug("link toplama hatasi: %s", exc)
            report.checked["links_found"] = len(links)
            link_failures = 0
            for link in links[:max_links]:
                if link.get("download") or link.get("target") == "_blank":
                    continue
                try:
                    request_headers = {}
                    if qa_token and any(link["href"].startswith(o) for o in preview_origins):
                        request_headers["X-EMOG-QA-Token"] = qa_token
                    link_resp = await context.request.get(
                        link["href"], timeout=8000, headers=request_headers,
                        fail_on_status_code=False,
                    )
                    if link_resp.status >= 400:
                        link_failures += 1
                        report.issues.append(QAIssue(
                            "major", "navigation", "Kırık link",
                            f"HTTP {link_resp.status} — {link.get('text') or link['href']}",
                            url=link["href"],
                        ))
                except Exception as exc:
                    link_failures += 1
                    report.issues.append(QAIssue(
                        "minor", "navigation", "Link doğrulanamadı",
                        f"{link['href']} — {exc}", url=link["href"],
                    ))
            report.checked["links_checked"] = min(len(links), max_links)
            report.checked["links_failed"] = link_failures

            # --- Buton / etkileşim kontrolü --------------------------------
            controls: List[Dict[str, Any]] = []
            try:
                controls = await page.evaluate(_COLLECT_CONTROLS_JS)
            except Exception as exc:
                logger.debug("kontrol toplama hatasi: %s", exc)
            report.checked["controls_found"] = len(controls)

            tested = 0
            skipped = 0
            for control in controls:
                if tested >= max_buttons:
                    break
                text = (control.get("text") or "").strip()
                label = text or control.get("selector") or control.get("tag") or "kontrol"
                if control.get("disabled"):
                    skipped += 1
                    report.notes.append(f"Devre dışı: {label}")
                    continue
                if control.get("type") == "submit" or control.get("inForm"):
                    skipped += 1
                    report.notes.append(f"Form gönderimi atlandı: {label}")
                    continue
                if _DESTRUCTIVE_RE.search(text):
                    skipped += 1
                    report.notes.append(f"Yan etkili olabileceği için atlandı: {label}")
                    continue


                baseline = (len(page_errors), len(console_errors), len(network_failures))
                url_before = page.url
                screenshot_path = ""
                try:
                    try:
                        await page.evaluate("() => { window.__emogQaMutations = 0; }")
                    except Exception:
                        pass
                    locator = page.locator(control["selector"]).first
                    await locator.scroll_into_view_if_needed(timeout=2000)
                    await locator.click(timeout=5000)
                    tested += 1
                    await page.wait_for_timeout(600)

                    new_page_errors = page_errors[baseline[0]:]
                    new_console_errors = [e for e in console_errors[baseline[1]:] if not _is_console_noise(e)]
                    new_network = network_failures[baseline[2]:]
                    try:
                        mutations = await page.evaluate("() => window.__emogQaMutations || 0")
                    except Exception:
                        mutations = 0

                    if new_page_errors or new_console_errors:
                        try:
                            shot_path = shot_dir / f"qa_error_{uuid.uuid4().hex[:8]}.png"
                            await page.screenshot(path=str(shot_path), full_page=False)
                            report.screenshots.append(str(shot_path))
                            screenshot_path = str(shot_path)
                        except Exception:
                            pass
                        detail = "\n".join((new_page_errors + new_console_errors)[:3])
                        report.issues.append(QAIssue(
                            "critical", "interaction",
                            f"Buton çalışırken JS hatası: {label}",
                            detail[:600],
                            selector=control["selector"], url=url,
                            screenshot=screenshot_path,
                        ))
                    for failure in new_network:
                        if _is_network_noise(failure.get("url", "")):
                            continue
                        report.issues.append(QAIssue(
                            _network_severity(failure.get("status"), failure.get("type", "")),
                            "network",
                            f"Buton sonrası başarısız istek: {label}",
                            f"{failure.get('status') or failure.get('error')} — {failure.get('url')}",
                            selector=control["selector"], url=url,
                        ))
                    if (mutations == 0 and page.url == url_before
                            and not new_page_errors and not new_console_errors and not new_network
                            and not _NO_EFFECT_EXEMPT_RE.search(label)):
                        report.notes.append(f"Görünür etki yok: {label}")
                        report.issues.append(QAIssue(
                            "major", "interaction",
                            f"Buton görünür bir etki üretmedi: {label}",
                            "Tıklama sonrası DOM değişmedi, yönlendirme veya ağ isteği oluşmadı. "
                            "Butonun işlevi bağlı değil olabilir.",
                            selector=control["selector"], url=url,
                        ))
                except Exception as exc:
                    report.issues.append(QAIssue(
                        "major", "interaction",
                        f"Buton tıklanamadı: {label}",
                        str(exc)[:500],
                        selector=control.get("selector", ""), url=url,
                    ))
                finally:
                    await _close_extra_pages(context, page)
                    await _reset_page(page, url)

            report.checked["controls_tested"] = tested
            report.checked["controls_skipped"] = skipped


            # --- Toplanan hataları rapora ekle -----------------------------
            for msg in _unique([e for e in page_errors if not _is_console_noise(e)]):
                report.issues.append(QAIssue("critical", "page_error", "Yakalanmamış JS hatası", msg, url=url))
            for msg in _unique([e for e in console_errors if not _is_console_noise(e)]):
                report.issues.append(QAIssue("major", "console", "Konsol hatası", msg, url=url))
            for failure in network_failures:
                if _is_network_noise(failure.get("url", "")):
                    continue
                if failure.get("status") is None and "net::" not in (failure.get("error") or ""):
                    continue
                report.issues.append(QAIssue(
                    _network_severity(failure.get("status"), failure.get("type", "")),
                    "network",
                    "Başarısız ağ isteği",
                    f"{failure.get('status') or failure.get('error')} — {failure.get('url')}",
                    url=failure.get("url", ""),
                ))

            await page.close()
    except Exception as exc:
        logger.exception("Preview QA calisamadi: %s", exc)
        report.ok = False
        report.error = str(exc)
        report.issues.append(QAIssue("critical", "runtime", "Test çalıştırılamadı", str(exc)[:500], url=url))
    finally:
        try:
            if context is not None:
                await context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass

    report.console_errors = _unique(console_errors)
    report.page_errors = _unique(page_errors)
    report.network_failures = _unique([
        f"{f.get('status') or f.get('error')} {f.get('url')}" for f in network_failures
    ])
    report.issues = _dedupe_issues(report.issues)
    report.ok = not report.blocking_issues() and not report.error
    report.duration_ms = int((time.monotonic() - started) * 1000)

    if report.ok:
        log.stage("tests", f"Canlı test başarılı: {report.summary_text()}")
    else:
        log.warn(f"Canlı test: {report.summary_text()} ({url})")
    return report


def run_preview_qa_sync(
    url: str,
    run_logger=None,
    timeout: float = 100.0,
    max_buttons: int = 10,
    max_links: int = 12,
) -> QAReport:
    """Senkron sarmalayıcı; async pipeline içinden de güvenle çağrılabilir."""
    if not _QA_LOCK.acquire(timeout=5):
        return QAReport(
            url=url,
            ok=False,
            error="Başka bir canlı test hâlâ çalışıyor.",
            issues=[QAIssue(
                "critical", "runtime", "Test meşgul",
                "Aynı anda yalnızca bir canlı preview testi çalışabilir.", url=url,
            )],
        )
    try:
        box: Dict[str, Any] = {}

        def _target():
            try:
                box["report"] = asyncio.run(
                    _run_qa_async(url, run_logger, max_buttons=max_buttons, max_links=max_links)
                )
            except Exception as exc:  # pragma: no cover
                logger.exception("Preview QA thread hatasi: %s", exc)
                box["report"] = QAReport(
                    url=url, ok=False, error=str(exc),
                    issues=[QAIssue("critical", "runtime", "Test çalıştırılamadı", str(exc)[:500], url=url)],
                )

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=max(5.0, float(timeout)))

        if "report" not in box:
            return QAReport(
                url=url,
                ok=False,
                error=f"Test zaman aşımına uğradı ({int(timeout)}s)",
                issues=[QAIssue("critical", "runtime", "Preview testi zaman aşımı",
                                f"{int(timeout)} saniye içinde tamamlanmadı.", url=url)],
            )
        return box["report"]
    finally:
        _QA_LOCK.release()


def to_critic_items(report: QAReport, limit: int = 8) -> List[Dict[str, Any]]:
    """QA bulgularını plan/critic kalemlerine çevir."""
    items: List[Dict[str, Any]] = []
    for issue in report.blocking_issues()[:limit]:
        if issue.category == "runtime":
            # Test altyapısı hataları ajanın düzeltebileceği kod sorunu değildir.
            continue
        issue_title = f"Canlı test: {issue.title}"
        if issue.description:
            issue_title += f" — {issue.description[:70]}"
        items.append({
            "title": issue_title[:140],
            "phase": "hardening",
            "objective": issue.description[:300] or issue.title,
            "detail": (
                f"Sayfa: {report.url}\n"
                f"Seçici: {issue.selector or '-'}\n"
                f"Kategori: {issue.category}\n"
                f"Hata: {issue.description}"
            )[:1500],
            "files": [issue.screenshot] if issue.screenshot else [],
            "acceptance": [
                "Sayfa hatasız yükleniyor (konsol/JS hatası yok)",
                "Buton tıklanınca hata oluşmuyor ve görünür bir etki üretiyor",
            ],
            "complexity": 2 if issue.severity == "critical" else 1,
            "user_requested": False,
        })
    return items

