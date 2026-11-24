"""Coklu arama saglayicilari.

Siralama:
  1. API anahtari olan saglayicilar (en kaliteli sonuc)
  2. Anahtar gerektirmeyen saglayicilar (sifir yapilandirma)

Tum saglayicilar ayni sozlesmeyi paylasir:
    fn(query, count, ctx) -> list[{title, url, snippet, provider, rank}]

Dayaniklilik:
  * Bir saglayici cok kez basarisiz olursa devre kesici onu gecici olarak devre
    disi birakir (HOT_INTERVAL), boylece pipeline butun aramalar icin yavaslamaz.
  * HTTP hatasi bos sonuc degildir: `SearchError` firlatilir, cagiran taraf
    saglayici bazinda hatayi gunlukler.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from django.conf import settings

from .extract import FetchError, HtmlDoc, http_get

logger = logging.getLogger(__name__)


class SearchError(Exception):
    """Saglayici bu aramayi gerceklestiremedi."""


# --------------------------------------------------------------------------
# Devre kesici
# --------------------------------------------------------------------------
_FAILURES: Dict[str, int] = {}
_DISABLED_UNTIL: Dict[str, float] = {}
_LOCK = threading.Lock()
HOT_THRESHOLD = 3
HOT_INTERVAL = 300.0  # saniye


def _is_hot(provider: str) -> bool:
    with _LOCK:
        return _DISABLED_UNTIL.get(provider, 0.0) > time.time()


def _record(provider: str, ok: bool) -> None:
    with _LOCK:
        if ok:
            _FAILURES[provider] = 0
            _DISABLED_UNTIL.pop(provider, None)
            return
        _FAILURES[provider] = _FAILURES.get(provider, 0) + 1
        if _FAILURES[provider] >= HOT_THRESHOLD:
            _DISABLED_UNTIL[provider] = time.time() + HOT_INTERVAL


def reset_circuit_breakers() -> None:
    with _LOCK:
        _FAILURES.clear()
        _DISABLED_UNTIL.clear()


# --------------------------------------------------------------------------
# Saglayici sozlesmesi
# --------------------------------------------------------------------------
@dataclass
class Provider:
    name: str
    kind: str                      # "api" (anahtar gerekli) | "free"
    fn: Callable
    key_env: Optional[str] = None
    label: str = ""
    timeout: int = 15
    note: str = ""

    @property
    def needs_key(self) -> bool:
        return self.kind == "api"

    def configured(self) -> bool:
        if not self.needs_key:
            return True
        return bool(os.environ.get(self.key_env or "", "").strip())


@dataclass
class SearchResponse:
    results: List[dict] = field(default_factory=list)
    providers: Dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.results)


def _setting(key: str, default=None):
    return (getattr(settings, "SEARCH_SETTINGS", {}) or {}).get(key, default)


def _timeout(provider: Provider) -> int:
    return int(_setting("timeout", provider.timeout) or provider.timeout)


def _results(*items):
    """(baslik, url, ozet) uculusunden standart sonuc kayitlari uret."""
    out = []
    for title, url, snippet in items:
        if not url:
            continue
        out.append(
            {
                "title": (title or "").strip()[:300],
                "url": url.strip(),
                "snippet": (snippet or "").strip()[:600],
            }
        )
    return out


# --------------------------------------------------------------------------
# Anahtarli API saglayicilari
# --------------------------------------------------------------------------
def _brave(query, count, ctx):
    api_key = os.environ["BRAVE_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(count, 20)},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in (data.get("web") or {}).get("results", [])[:count]:
        description = entry.get("description") or ""
        # Brave bazi sonuclarda alt baslik olarak ek meta doner
        for extra in entry.get("extra_snippets") or []:
            description += " " + extra
        items.append((entry.get("title"), entry.get("url"), description))
    return _results(*items)


def _tavily(query, count, ctx):
    api_key = os.environ["TAVILY_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(
            {
                "api_key": api_key,
                "query": query,
                "max_results": min(count, 20),
                "search_depth": "advanced",
                "include_answer": True,
            }
        ),
        method="POST",
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    answer = data.get("answer") or ""
    items = [
        (entry.get("title"), entry.get("url"), (entry.get("content") or "") + (" | " + answer if answer else ""))
        for entry in (data.get("results") or [])[:count]
    ]
    return _results(*items)


def _serper(query, count, ctx):
    api_key = os.environ["SERPER_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        data=json.dumps({"q": query, "num": min(count, 20), "gl": ctx.get("gl", "tr"), "hl": ctx.get("lang", "tr")}),
        method="POST",
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in (data.get("organic") or [])[:count]:
        items.append((entry.get("title"), entry.get("link"), entry.get("snippet")))
    # Serper bazen "answer box" doner; onu ilk sonuca ozet olarak ekle.
    answer_box = data.get("answerBox") or {}
    if answer_box.get("snippet") and items:
        head = items[0]
        items[0] = (head[0], head[1], f"{head[2]} | {answer_box['snippet']}")
    return _results(*items)


def _bing(query, count, ctx):
    api_key = os.environ["BING_SEARCH_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://api.bing.microsoft.com/v7.0/search",
        params={"q": query, "count": min(count, 20), "mkt": "tr-TR"},
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (entry.get("name"), entry.get("url"), entry.get("snippet"))
        for entry in (data.get("webPages", {}).get("value") or [])[:count]
    ]
    return _results(*items)


def _exa(query, count, ctx):
    api_key = os.environ["EXA_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"query": query, "numResults": min(count, 20), "type": "auto"}),
        method="POST",
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (
            entry.get("title"),
            entry.get("url"),
            (entry.get("text") or "")[:500] or entry.get("summary"),
        )
        for entry in (data.get("results") or [])[:count]
    ]
    return _results(*items)


def _serpapi(query, count, ctx):
    api_key = os.environ["SERPAPI_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://serpapi.com/search.json",
        params={"q": query, "num": min(count, 20), "api_key": api_key, "hl": "tr", "gl": "tr"},
        timeout=ctx["timeout"] + 5,
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (entry.get("title"), entry.get("link"), entry.get("snippet"))
        for entry in (data.get("organic_results") or [])[:count]
    ]
    return _results(*items)


def _kagi(query, count, ctx):
    api_key = os.environ["KAGI_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://kagi.com/api/v0/search",
        params={"q": query},
        headers={"Authorization": f"Bot {api_key}"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (entry.get("title"), entry.get("url"), entry.get("snippet"))
        for entry in (data.get("data") or [])[:count]
    ]
    return _results(*items)


def _google_cse(query, count, ctx):
    api_key = os.environ["GOOGLE_CSE_KEY"]
    cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
    if not cx:
        raise SearchError("GOOGLE_CSE_CX tanimli degil")
    status, raw, _h, _u, _e = http_get(
        "https://www.googleapis.com/customsearch/v1",
        params={"q": query, "num": min(count, 10), "key": api_key, "cx": cx, "hl": "tr"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (entry.get("title"), entry.get("link"), entry.get("snippet"))
        for entry in (data.get("items") or [])[:count]
    ]
    return _results(*items)


def _you(query, count, ctx):
    api_key = os.environ["YOU_API_KEY"]
    status, raw, _h, _u, _e = http_get(
        "https://api.you.com/api/search",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"q": query}),
        method="POST",
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    hits = ((data.get("hits") or {}).get("hits") or [])[:count]
    items = []
    for hit in hits:
        items.append(
            (
                hit.get("title"),
                hit.get("url") or hit.get("link"),
                (hit.get("description") or hit.get("snippet") or "")[:500],
            )
        )
    return _results(*items)


def _searxng(query, count, ctx):
    base = (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/")
    if not base:
        raise SearchError("SEARXNG_URL tanimli degil")
    status, raw, _h, _u, _e = http_get(
        f"{base}/search",
        params={"q": query, "format": "json", "language": ctx.get("lang", "tr")},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (entry.get("title"), entry.get("url"), entry.get("content"))
        for entry in (data.get("results") or [])[:count]
    ]
    return _results(*items)


# --------------------------------------------------------------------------
# Anahtarsiz (ucretsiz) saglayicilari
# --------------------------------------------------------------------------
def _ddg_html(query, count, ctx):
    """DuckDuckGo HTML ucu.

    POST ucu 202 + "anomaly/challenge" sayfasi donuyor (bot korumasi), GET ise
    calisiyor; bu yuzden sorgu parametreyle gonderilir. Sonuclar
    /l/?uddg=<urlencoded> yonlendirmesi icindir.
    """
    status, raw, _headers, _u, _e = http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": query, "kl": _ddg_region(ctx.get("lang"))},
        headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    html = raw.decode("utf-8", errors="replace")
    if _is_blocked(html):
        raise SearchError("bot korumasi (anomaly sayfasi)")
    items = _parse_ddg(html)
    if not items:
        raise SearchError("sonuc ayristirilamadi")
    return _results(*items[:count])


def _ddg_lite(query, count, ctx):
    """html. ucu engellenirse lite ucu."""
    status, raw, _headers, _u, _e = http_get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query, "kl": _ddg_region(ctx.get("lang"))},
        headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    html = raw.decode("utf-8", errors="replace")
    if _is_blocked(html):
        raise SearchError("bot korumasi")
    items = _parse_ddg(html)
    if not items:
        raise SearchError("sonuc ayristirilamadi")
    return _results(*items[:count])


def _parse_ddg(html: str):
    doc = HtmlDoc(html)
    items = []
    seen = set()
    for anchor in doc.anchors:
        href = anchor.get("href") or ""
        if "duckduckgo.com/l/" not in href:
            continue
        target = _extract_uddg(href)
        if not target or target in seen:
            continue
        seen.add(target)
        items.append((anchor.get("text") or "", target, ""))
    return items


def _is_blocked(html: str) -> bool:
    lowered = (html or "").lower()
    return any(marker in lowered for marker in ("anomaly", "unfortunately, bots", "are you a robot"))


def _ddg_region(lang: str) -> str:
    return {"tr": "tr-tr", "en": "us-en", "de": "de-de", "fr": "fr-fr"}.get(lang, "wt-wt")


def _extract_uddg(href: str) -> str:
    match = re.search(r"uddg=([^&]+)", href)
    if match:
        return urllib.parse.unquote(match.group(1))
    match = re.search(r"uddg=([^&\"]+)", html_unescape(href))
    return urllib.parse.unquote(match.group(1)) if match else ""


def html_unescape(text: str) -> str:
    import html as _html

    return _html.unescape(text or "")


def _mojeek(query, count, ctx):
    """Mojeek anahtarsiz arama. SIK 403 doner; devre kesici devreye girer.

    Sonuclar `ul.results-standard` / `#results` icinde; disindaki baglantilar
    (abonelik, footer) sonuc sanilmamali.
    """
    status, raw, _h, _u, _e = http_get(
        "https://www.mojeek.com/search",
        params={"q": query},
        headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
            "Referer": "https://www.mojeek.com/",
            "Upgrade-Insecure-Requests": "1",
        },
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    html = raw.decode("utf-8", errors="replace")
    scope = _mojeek_scope(html)
    if not scope:
        raise SearchError("sonuç konteyneri bulunamadi (markup degismis olabilir)")

    doc = HtmlDoc(scope)
    items = []
    seen = set()
    for anchor in doc.anchors:
        href = anchor.get("href") or ""
        label = anchor.get("text") or ""
        if not href.startswith("http") or len(label) < 6:
            continue
        if "mojeek.com" in (urllib.parse.urlparse(href).hostname or ""):
            continue
        if href in seen:
            continue
        seen.add(href)
        items.append((label, href, ""))
    if not items:
        raise SearchError("sonuc ayristirilamadi")
    return _results(*items[:count])


def _mojeek_scope(html: str) -> str:
    """Mojeek sonuclari govdesini kirp (footer/abonelik baglantilarini ele)."""
    for pattern in (
        r'(?is)<ul[^>]*class="[^"]*results-standard[^"]*".*?</ul>',
        r'(?is)<div[^>]*id="results".*?</div>\s*</div>',
        r"(?is)<main.*?</main>",
    ):
        match = re.search(pattern, html)
        if match and len(match.group(0)) > 500:
            return match.group(0)
    return ""


def _wikipedia(query, count, ctx):
    lang = ctx.get("lang", "tr")
    if lang not in ("tr", "en", "de", "fr", "es", "ar"):
        lang = "en"
    status, raw, _h, _u, _e = http_get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query", "list": "search", "srsearch": query,
            "srlimit": min(count, 20), "format": "json", "utf8": 1,
        },
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in ((data.get("query") or {}).get("search") or [])[:count]:
        title = entry.get("title")
        items.append(
            (
                title,
                f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote((title or '').replace(' ', '_'))}",
                re.sub(r"<[^>]+>", "", entry.get("snippet") or ""),
            )
        )
    return _results(*items)


def _stackexchange(query, count, ctx):
    site = _setting("stackexchange_site", "stackoverflow")
    status, raw, _h, _u, _e = http_get(
        f"https://api.stackexchange.com/2.3/search/advanced",
        params={
            "order": "desc", "sort": "relevance", "q": query,
            "site": site, "pagesize": min(count, 25), "filter": "default",
        },
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in (data.get("items") or [])[:count]:
        items.append(
            (
                entry.get("title"),
                entry.get("link"),
                (entry.get("body_markdown") or "")[:400],
            )
        )
    return _results(*items)


def _hackernews(query, count, ctx):
    status, raw, _h, _u, _e = http_get(
        "https://hn.algolia.com/api/v1/search",
        params={"query": query, "tags": "story", "hitsPerPage": min(count, 30)},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for hit in (data.get("hits") or [])[:count]:
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        items.append((hit.get("title") or hit.get("story_title"), url, _strip_html(hit.get("story_text") or "")))
    return _results(*items)


def _github(query, count, ctx):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, raw, _h, _u, _e = http_get(
        "https://api.github.com/search/repositories",
        params={"q": query, "per_page": min(count, 20), "sort": "stars", "order": "desc"},
        headers=headers,
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in (data.get("items") or [])[:count]:
        summary = entry.get("description") or ""
        summary += f" | yildiz={entry.get('stargazers_count')} dil={entry.get('language')}"
        items.append((entry.get("full_name"), entry.get("html_url"), summary))
    return _results(*items)


def _arxiv(query, count, ctx):
    status, raw, _h, _u, _e = http_get(
        "http://export.arxiv.org/api/query",
        params={"search_query": f"all:{query}", "start": 0, "max_results": min(count, 15)},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    xml = raw.decode("utf-8", errors="replace")
    entries = re.findall(r"(?is)<entry>(.*?)</entry>", xml)
    if not entries:
        raise SearchError("sonuc ayristirilamadi")
    items = []
    for entry in entries[:count]:
        title = _xml_tag(entry, "title")
        link = _xml_tag(entry, "id")
        summary = _xml_tag(entry, "summary")
        items.append((title, link, re.sub(r"\s+", " ", summary)[:400]))
    return _results(*items)


def _crossref(query, count, ctx):
    status, raw, _h, _u, _e = http_get(
        "https://api.crossref.org/works",
        params={"query": query, "rows": min(count, 20), "select": "title,URL,abstract,container-title"},
        headers={"User-Agent": "EMOG-Buildkit/1.0 (mailto:research@emog.local)"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for entry in ((data.get("message") or {}).get("items") or [])[:count]:
        titles = entry.get("title") or []
        items.append(
            (
                titles[0] if titles else "",
                entry.get("URL"),
                _strip_html(entry.get("abstract") or "")[:400],
            )
        )
    return _results(*items)


def _pubmed(query, count, ctx):
    status, raw, _h, _u, _e = http_get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={"db": "pubmed", "term": query, "retmax": min(count, 15), "retmode": "json"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    ids = ((data.get("esearchresult") or {}).get("idlist") or [])
    if not ids:
        return []
    status, raw, _h, _u, _e = http_get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    summary = json.loads(raw.decode("utf-8", errors="replace")).get("result", {})
    items = []
    for pmid in ids:
        entry = summary.get(pmid) or {}
        items.append((entry.get("title"), f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", ""))
    return _results(*items)


def _semantic_scholar(query, count, ctx):
    headers = {}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if key:
        headers["x-api-key"] = key
    status, raw, _h, _u, _e = http_get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": query, "limit": min(count, 20), "fields": "title,abstract,url,year"},
        headers=headers,
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = [
        (
            entry.get("title"),
            entry.get("url"),
            (entry.get("abstract") or "")[:400],
        )
        for entry in (data.get("data") or [])[:count]
    ]
    return _results(*items)


def _reddit(query, count, ctx):
    status, raw, _h, _u, _e = http_get(
        "https://www.reddit.com/search.json",
        params={"q": query, "limit": min(count, 25), "sort": "relevance", "t": "year"},
        headers={"Accept": "application/json"},
        timeout=ctx["timeout"],
    )
    if status != 200:
        raise SearchError(f"HTTP {status}")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = []
    for child in ((data.get("data") or {}).get("children") or [])[:count]:
        entry = child.get("data") or {}
        items.append(
            (
                entry.get("title"),
                "https://www.reddit.com" + (entry.get("permalink") or ""),
                (entry.get("selftext") or "")[:400],
            )
        )
    return _results(*items)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _xml_tag(block: str, tag: str) -> str:
    match = re.search(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", block)
    return _strip_html(match.group(1)) if match else ""


# --------------------------------------------------------------------------
# Kayit
# --------------------------------------------------------------------------
PROVIDERS: tuple = (
    Provider("brave", "api", _brave, "BRAVE_API_KEY", "Brave Search", 15),
    Provider("tavily", "api", _tavily, "TAVILY_API_KEY", "Tavily", 20, "icerik odakli arama"),
    Provider("serper", "api", _serper, "SERPER_API_KEY", "Serper (Google)", 15),
    Provider("bing", "api", _bing, "BING_SEARCH_KEY", "Bing Web Search", 15),
    Provider("exa", "api", _exa, "EXA_API_KEY", "Exa", 15, "anlamsal arama"),
    Provider("serpapi", "api", _serpapi, "SERPAPI_KEY", "SerpApi", 20),
    Provider("kagi", "api", _kagi, "KAGI_API_KEY", "Kagi", 15),
    Provider("google_cse", "api", _google_cse, "GOOGLE_CSE_KEY", "Google Programmable Search", 15,
             "GOOGLE_CSE_CX de gerekli"),
    Provider("you", "api", _you, "YOU_API_KEY", "You.com", 15),
    Provider("searxng", "api", _searxng, "SEARXNG_URL", "SearXNG (kendi sunucun)", 20,
             "kendi SearXNG adresin; anahtarsiz da calisir"),
    Provider("duckduckgo", "free", _ddg_html, None, "DuckDuckGo HTML", 15, "anahtarsiz"),
    Provider("duckduckgo_lite", "free", _ddg_lite, None, "DuckDuckGo Lite", 15, "anahtarsiz, yedek"),
    Provider("mojeek", "free", _mojeek, None, "Mojeek", 15, "anahtarsiz"),
    Provider("wikipedia", "free", _wikipedia, None, "Wikipedia", 12, "anahtarsiz"),
    Provider("github", "free", _github, None, "GitHub", 15, "GITHUB_TOKEN varsa daha iyi"),
    Provider("stackexchange", "free", _stackexchange, None, "Stack Exchange", 15, "anahtarsiz"),
    Provider("hackernews", "free", _hackernews, None, "Hacker News", 12, "anahtarsiz"),
    Provider("reddit", "free", _reddit, None, "Reddit", 15, "bazen engellenir"),
    Provider("arxiv", "free", _arxiv, None, "arXiv", 15, "anahtarsiz"),
    Provider("crossref", "free", _crossref, None, "Crossref", 15, "anahtarsiz"),
    Provider("pubmed", "free", _pubmed, None, "PubMed", 15, "anahtarsiz"),
    Provider("semantic_scholar", "free", _semantic_scholar, None, "Semantic Scholar", 15, "anahtarsiz"),
)

BY_NAME: Dict[str, Provider] = {p.name: p for p in PROVIDERS}


def configured_providers(names=None, include_free=True) -> List[Provider]:
    """Kullanilabilir saglayici listesi: once anahtarlilar, sonra ucretsizler."""
    allowed = set(names) if names else None
    selected = []
    for provider in PROVIDERS:
        if allowed and provider.name not in allowed:
            continue
        if not provider.configured():
            continue
        if provider.needs_key and not include_free:
            continue
        selected.append(provider)
    selected.sort(key=lambda p: 0 if p.needs_key else 1)
    return selected


def provider_status() -> List[dict]:
    """Panel/endpoint icin saglayici durumu (anahtarli olanlar hazir mi)."""
    rows = []
    for provider in PROVIDERS:
        rows.append(
            {
                "name": provider.name,
                "label": provider.label or provider.name,
                "kind": provider.kind,
                "key_env": provider.key_env,
                "configured": provider.configured(),
                "hot": _is_hot(provider.name),
                "failures": _FAILURES.get(provider.name, 0),
                "note": provider.note,
            }
        )
    return rows


def _run_one(provider: Provider, query: str, count: int, ctx: dict):
    local_ctx = dict(ctx)
    local_ctx["timeout"] = _timeout(provider)
    try:
        results = provider.fn(query, count, local_ctx) or []
    except FetchError as exc:
        raise SearchError(f"guvenlik: {exc}") from exc
    except urllib.error.HTTPError as exc:
        raise SearchError(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise SearchError(f"{type(exc).__name__}: {exc}") from exc
    cleaned = []
    for rank, item in enumerate(results[:count]):
        if not item.get("url", "").startswith("http"):
            continue
        item["provider"] = provider.name
        item["rank"] = rank
        cleaned.append(item)
    return cleaned


def search(
    query: str,
    *,
    count: int = 8,
    providers=None,
    max_workers: int = 6,
    timeout: int = None,
) -> SearchResponse:
    """Tek bir sorguyu paralel olarak birden fazla saglayicida calistir."""
    query = (query or "").strip()
    response = SearchResponse()
    if not query:
        return response

    chosen = configured_providers(providers)
    if not chosen:
        response.providers["__none__"] = {"ok": False, "error": "kullanilabilir arama saglayicisi yok"}
        return response

    active = [p for p in chosen if not _is_hot(p.name)]
    skipped = [p.name for p in chosen if _is_hot(p.name)]
    ctx = {"lang": _setting("lang", "tr"), "gl": _setting("gl", "tr"), "timeout": timeout or 20}

    collected: List[dict] = []
    workers = max(1, min(max_workers, len(active)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, provider, query, count, ctx): provider for provider in active
        }
        for future in as_completed(futures, timeout=(timeout or 20) + 12):
            provider = futures[future]
            try:
                results = future.result()
                _record(provider.name, True)
                collected.extend(results)
                response.providers[provider.name] = {"ok": True, "count": len(results)}
            except SearchError as exc:
                _record(provider.name, False)
                response.providers[provider.name] = {"ok": False, "error": str(exc)[:200]}
            except Exception as exc:
                _record(provider.name, False)
                response.providers[provider.name] = {"ok": False, "error": str(exc)[:200]}

    for name in skipped:
        response.providers[name] = {"ok": False, "error": "devre kesici aktif (gecici olarak kapatildi)"}

    response.results = dedupe(collected)
    return response


def dedupe(items: List[dict]) -> List[dict]:
    """Ayni URL'yi birlestir; farkli saglayicilardan gelen ozetleri birlestir."""
    merged: Dict[str, dict] = {}
    order: List[str] = []
    for item in items:
        url = (item.get("url") or "").split("#")[0].rstrip("/")
        if not url:
            continue
        if url in merged:
            existing = merged[url]
            if len(item.get("snippet") or "") > len(existing.get("snippet") or ""):
                existing["snippet"] = item["snippet"]
            if len(item.get("title") or "") > len(existing.get("title") or ""):
                existing["title"] = item["title"]
            existing["providers"] = sorted(set(existing.get("providers", []) + [item.get("provider")]))
            continue
        record = dict(item)
        record["providers"] = [item.get("provider")]
        record["provider"] = item.get("provider")
        merged[url] = record
        order.append(url)

    # Coklu saglayicidan gelenler one cikarilir (dogrulanmis bulgu).
    order.sort(key=lambda u: (-len(merged[u].get("providers") or []), merged[u].get("rank", 99)))
    return [merged[u] for u in order]


def search_many(queries: List[str], *, per_query: int = 8, providers=None, log=None) -> Dict[str, object]:
    """Birden fazla sorguyu toplu calistir; sonuclari sorgulara gore gruplar."""
    queries = [q for q in (queries or []) if q and q.strip()]
    if not queries:
        return {"groups": [], "all": [], "providers": {}, "stats": {}}

    providers = providers or configured_providers()
    provider_names = [p.name for p in providers]
    stats = {"queries": len(queries), "providers": provider_names, "results": 0, "failures": 0}
    provider_report: Dict[str, dict] = {}

    groups = []
    # Her sorguyu sirayla calistir: saglayicilara yumusaklasma (rate limit) verir
    # ve devre kesici tek bir sorgunun cok saglayiciyi ayni anda yakmasini onler.
    for index, query in enumerate(queries, start=1):
        if log is not None:
            log(f"Araştırma sorgusu {index}/{len(queries)}: {query}")
        response = search(query, count=per_query, providers=provider_names)
        groups.append({"query": query, "results": response.results})
        if log is not None:
            ok_providers = sum(1 for info in response.providers.values() if info.get("ok"))
            log(
                f"Araştırma sorgusu {index}/{len(queries)} tamamlandı: {query} — "
                f"{len(response.results)} sonuç · {ok_providers}/{len(response.providers)} sağlayıcı yanıtladı"
            )
        for name, info in response.providers.items():
            bucket = provider_report.setdefault(name, {"ok": 0, "failed": 0, "last_error": ""})
            if info.get("ok"):
                bucket["ok"] += 1
            elif not name.startswith("__"):
                bucket["failed"] += 1
                bucket["last_error"] = info.get("error", "")[:200]

    all_results = []
    for group in groups:
        for item in group["results"]:
            item.setdefault("query", group["query"])
        all_results.extend(group["results"])
    all_results = dedupe(all_results)
    stats["results"] = len(all_results)
    stats["failures"] = sum(1 for r in all_results if not r.get("title"))

    return {"groups": groups, "all": all_results, "providers": provider_report, "stats": stats}