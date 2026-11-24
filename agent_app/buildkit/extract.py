"""Web sayfasi cekme ve yapisal analiz.

Iki isi birden yapar:

1. `HtmlDoc` - bagimlilik istemeyen HTML cozumleyici. bs4 kuruluysa o
   kullanilir, degilse stdlib `html.parser` uzerinden ayni arayuz sunulur.
   (bu modul `.venv`de bs4 olmadan da calisir.)
2. `analyze_site` - bir siteyi ziyaret edip ozelligini, teknolojisini,
   SEO/erisilebilirlik/performans sinyalini cikaran profilleyici.

Amaç: "haber sitesi nasıl olur" sorusunu tahmine degil, gercek sayfalardan
toplanan kanita dayandirmak.
"""
import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# Metin ve oge taramasini bastiran etiketler (script gibi).
DROP_TEXT_TAGS = {
    "script", "style", "noscript", "svg", "template", "iframe", "canvas",
}
# Yalnizca *okunabilir metin* cikariminda atilir; menuleri ayrica topluyoruz
# cunku navigasyon yapisi urun analizi icin onemsizdir.
LAYOUT_TEXT_TAGS = {"nav", "footer", "aside", "head"}
UA_ROTATION = (
    DEFAULT_UA,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
)


class FetchError(Exception):
    """Sayfa cekilemedi."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _is_url_safe(url: str):
    from agent_app.project_scanner import _is_url_safe as guard

    return guard(url)


def _ssl_context():
    """Windows'ta urllib bazen CA hatasi verir; certifi varsa onu kullan."""
    try:
        import ssl

        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


_SSL_CONTEXT = None
_SSL_READY = False


def _guarded_opener():
    global _SSL_CONTEXT, _SSL_READY
    if not _SSL_READY:
        _SSL_CONTEXT = _ssl_context()
        _SSL_READY = True
    if _SSL_CONTEXT is None:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CONTEXT))


def http_get(url, *, params=None, headers=None, timeout=15, data=None, method=None, max_bytes=2_500_000):
    """Guvenli GET/POST. (status, body_bytes, headers, final_url, elapsed_ms) doner."""
    safe, reason = _is_url_safe(url)
    if not safe:
        raise FetchError(reason)

    if params:
        sep = "&" if urllib.parse.urlparse(url).query else "?"
        url = url + sep + urllib.parse.urlencode(params)

    payload = None
    req_headers = {
        "User-Agent": UA_ROTATION[int(time.time()) % len(UA_ROTATION)],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    req_headers.update(headers or {})
    if data is not None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers["Content-Length"] = str(len(data))

    request = urllib.request.Request(url, data=payload or data, headers=req_headers, method=method)
    started = time.monotonic()
    with _guarded_opener().open(request, timeout=timeout) as resp:
        raw = resp.read(max_bytes)
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
        final_url = resp.getheader("Location") or url
        status = resp.getcode()
        header_map = {k.lower(): v for k, v in resp.headers.items()}
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except zlib.error:
            pass

    # Yonlendirmeleri de SSRF kontrolunden gecir.
    if final_url != url:
        safe2, reason2 = _is_url_safe(final_url)
        if not safe2:
            raise FetchError(f"Yonlendirme engellendi: {reason2}")

    return status, raw, header_map, final_url, elapsed_ms


def fetch_html(url, *, timeout=15, max_bytes=1_500_000):
    """Sayfayi cekip (status, html, headers, final_url, elapsed_ms) doner."""
    status, raw, headers, final_url, elapsed_ms = http_get(
        url, timeout=timeout, max_bytes=max_bytes
    )
    match = re.search(r"charset=([\w-]+)", headers.get("content-type", "") or "", re.I)
    charset = match.group(1) if match else "utf-8"
    try:
        html = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html = raw.decode("utf-8", errors="replace")
    return status, html, headers, final_url, elapsed_ms


# --------------------------------------------------------------------------
# HTML cozumleyici
# --------------------------------------------------------------------------
class _StdlibCollector(HTMLParser):
    """bs4 yokken kullanilan minimal HTMLParser tabanli toplayici."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.anchors = []
        self.headings = []
        self.forms = []
        self.images = []
        self.scripts = []
        self.stylesheets = []
        self.links = []
        self.metas = []
        self.buttons = []
        self.inputs = []
        self.jsonld = []
        self.tag_counts = Counter()
        self.html_tag_attrs = {}
        self._in_title = False
        self._anchor = None
        self._heading = None
        self._heading_tag = None
        self._form = None
        self._button = None
        self._script_parts = None
        self._script_attrs = None
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        self.tag_counts[tag] += 1
        if tag == "html":
            self.html_tag_attrs = a
        elif tag == "title":
            self._in_title = True
        elif tag in DROP_TEXT_TAGS and tag != "head":
            self._skip_depth += 1
        if tag == "a":
            self._anchor = {"href": a.get("href", ""), "text": [], "rel": a.get("rel", "")}
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = []
            self._heading_tag = tag
        elif tag == "form":
            self._form = {
                "action": a.get("action", ""),
                "method": a.get("method", "get"),
                "inputs": [],
                "labels": [],
            }
        elif tag == "label":
            if self._form is not None:
                self._form["labels"].append([])
                self._button = {"tag": "label", "text": self._form["labels"][-1]}
        elif tag == "input":
            record = {
                "type": (a.get("type") or "text").lower(),
                "name": a.get("name", ""),
                "id": a.get("id", ""),
                "placeholder": a.get("placeholder", ""),
                "aria_label": a.get("aria-label", ""),
            }
            self.inputs.append(record)
            if self._form is not None:
                self._form["inputs"].append(record)
        elif tag == "textarea":
            record = {
                "type": "textarea", "name": a.get("name", ""), "id": a.get("id", ""),
                "placeholder": a.get("placeholder", ""), "aria_label": a.get("aria-label", ""),
            }
            self.inputs.append(record)
            if self._form is not None:
                self._form["inputs"].append(record)
        elif tag == "img":
            self.images.append(
                {
                    "src": a.get("src") or a.get("data-src", ""),
                    "alt": a.get("alt"),
                    "width": a.get("width"),
                    "height": a.get("height"),
                    "loading": a.get("loading", ""),
                }
            )
        elif tag == "script":
            # NOT: 'script' DROP_TEXT_TAGS icinde oldugu icin yukarida zaten
            # _skip_depth artirildi; burada tekrar artirmiyoruz.
            self._script_parts = []
            self._script_attrs = a
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            entry = {"rel": rel, "href": a.get("href", ""), "as": a.get("as", "")}
            self.links.append(entry)
            if "stylesheet" in rel:
                self.stylesheets.append(entry)
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "button":
            self._button = {"tag": "button", "text": []}

    def handle_startendtag(self, tag, attrs):
        # <img ... /> gibi kendini kapatan etiketler
        self.handle_starttag(tag, attrs)
        if tag in DROP_TEXT_TAGS or tag == "script":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in DROP_TEXT_TAGS and tag != "head" and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "a" and self._anchor is not None:
            self._anchor["text"] = " ".join("".join(self._anchor["text"]).split())
            self.anchors.append(self._anchor)
            self._anchor = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            self.headings.append({"tag": self._heading_tag, "text": " ".join("".join(self._heading).split())})
            self._heading = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
            self._button = None
        elif tag == "label" and self._button and self._button.get("tag") == "label":
            if self._form and self._form["labels"]:
                self._form["labels"][-1] = " ".join("".join(self._button["text"]).split())
            self._button = None
        elif tag == "button" and self._button and self._button.get("tag") == "button":
            self.buttons.append(" ".join("".join(self._button["text"]).split()))
            self._button = None
        elif tag == "script":
            if self._script_parts is not None:
                body = "".join(self._script_parts)
                script_type = (self._script_attrs or {}).get("type", "").lower()
                if "ld+json" in script_type:
                    self.jsonld.append(body)
                elif "src" not in (self._script_attrs or {}):
                    self.scripts.append({"inline": body})
                else:
                    self.scripts.append({"src": (self._script_attrs or {}).get("src", "")})
            self._script_parts = None
            self._script_attrs = None

    def handle_data(self, data):
        if self._script_parts is not None:
            self._script_parts.append(data)
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth > 0:
            return
        if self._anchor is not None:
            self._anchor["text"].append(data)
        elif self._heading is not None:
            self._heading.append(data)
        elif self._button is not None:
            self._button["text"].append(data)


class HtmlDoc:
    """Bir HTML sayfasinin yapisal gorunumu.

    `.anchors`, `.headings`, `.forms`, `.meta`, `.jsonld` gibi alanlar hem
    arama sonuclarini hem de rakip site analizini besler.
    """

    def __init__(self, html: str, url: str = ""):
        self.html = html or ""
        self.url = url
        self._soup = None
        self._collect = None
        self._use_soup(html)

    def _use_soup(self, html):
        try:
            from bs4 import BeautifulSoup

            self._soup = BeautifulSoup(html, "html.parser")
            return
        except Exception:
            self._soup = None

    def _fallback(self):
        if self._collect is None:
            collector = _StdlibCollector()
            try:
                collector.feed(self.html)
                collector.close()
            except Exception as exc:
                logger.debug("stdlib HTML parse sorunu: %s", exc)
            self._collect = collector
        return self._collect

    # -- temel alanlar ----------------------------------------------------
    @property
    def title(self) -> str:
        if self._soup is not None:
            node = self._soup.title
            return node.get_text(strip=True) if node else ""
        return " ".join("".join(self._fallback().title_parts).split())

    @property
    def lang(self) -> str:
        if self._soup is not None:
            node = self._soup.find("html")
            return (node.get("lang") or "") if node else ""
        return self._fallback().html_tag_attrs.get("lang", "")

    @property
    def meta(self):
        if self._soup is not None:
            return [
                {k.lower(): (v if v is not None else "") for k, v in node.attrs.items()}
                for node in self._soup.find_all("meta")
            ]
        return self._fallback().metas

    def meta_content(self, *names) -> str:
        wanted = {n.lower() for n in names}
        for entry in self.meta:
            key = (entry.get("name") or entry.get("property") or "").lower()
            if key in wanted:
                return (entry.get("content") or "").strip()
        return ""

    @property
    def anchors(self):
        if self._soup is not None:
            out = []
            for node in self._soup.find_all("a"):
                out.append(
                    {
                        "href": node.get("href", "") or "",
                        "text": node.get_text(" ", strip=True),
                        "rel": " ".join(node.get("rel", []) or []),
                    }
                )
            return out
        return self._fallback().anchors

    @property
    def headings(self):
        if self._soup is not None:
            out = []
            for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                for node in self._soup.find_all(tag):
                    text = node.get_text(" ", strip=True)
                    if text:
                        out.append({"tag": tag, "text": text})
            return out
        return self._fallback().headings

    @property
    def forms(self):
        if self._soup is not None:
            out = []
            for node in self._soup.find_all("form"):
                inputs = []
                for field in node.find_all(["input", "textarea", "select"]):
                    inputs.append(
                        {
                            "type": (field.get("type") or field.name).lower(),
                            "name": field.get("name", "") or "",
                            "id": field.get("id", "") or "",
                            "placeholder": field.get("placeholder", "") or "",
                            "aria_label": field.get("aria-label", "") or "",
                        }
                    )
                labels = [n.get_text(" ", strip=True) for n in node.find_all("label")]
                out.append(
                    {
                        "action": node.get("action", "") or "",
                        "method": (node.get("method") or "get"),
                        "inputs": inputs,
                        "labels": labels,
                    }
                )
            return out
        return self._fallback().forms

    @property
    def images(self):
        if self._soup is not None:
            return [
                {
                    "src": n.get("src") or n.get("data-src") or "",
                    "alt": n.get("alt"),
                    "width": n.get("width"),
                    "height": n.get("height"),
                    "loading": n.get("loading", "") or "",
                }
                for n in self._soup.find_all("img")
            ]
        return self._fallback().images

    @property
    def scripts(self):
        if self._soup is not None:
            out = []
            for node in self._soup.find_all("script"):
                if node.get("src"):
                    out.append({"src": node.get("src")})
                else:
                    out.append({"inline": node.string or node.get_text()})
            return out
        return self._fallback().scripts

    @property
    def stylesheets(self):
        if self._soup is not None:
            return [
                {"href": n.get("href", "") or ""}
                for n in self._soup.find_all("link", rel=lambda v: v and "stylesheet" in " ".join(v))
            ]
        return [{"href": s.get("href", "")} for s in self._fallback().stylesheets]

    @property
    def buttons(self):
        if self._soup is not None:
            return [n.get_text(" ", strip=True) for n in self._soup.find_all("button")]
        return self._fallback().buttons

    @property
    def inputs(self):
        if self._soup is not None:
            out = []
            for field in self._soup.find_all(["input", "textarea", "select"]):
                out.append(
                    {
                        "type": (field.get("type") or field.name).lower(),
                        "name": field.get("name", "") or "",
                        "id": field.get("id", "") or "",
                        "placeholder": field.get("placeholder", "") or "",
                        "aria_label": field.get("aria-label", "") or "",
                    }
                )
            return out
        return self._fallback().inputs

    @property
    def jsonld(self):
        if self._soup is not None:
            out = []
            for node in self._soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
                out.append(node.string or node.get_text())
            return out
        return self._fallback().jsonld

    @property
    def tag_counts(self):
        if self._soup is not None:
            return Counter(tag.name for tag in self._soup.find_all(True))
        return self._fallback().tag_counts

    @property
    def attrs_html(self):
        if self._soup is not None:
            node = self._soup.find("html")
            return {k.lower(): (v if isinstance(v, str) else " ".join(v or [])) for k, v in (node.attrs or {}).items()} if node else {}
        return self._fallback().html_tag_attrs

    # -- metin ------------------------------------------------------------
    def text(self, max_chars: int = 0) -> str:
        """Okunabilir metin: nav/footer/script gibi bloklar atilir."""
        if self._soup is not None:
            node = self._soup.body or self._soup
            for bad in node.find_all(list(DROP_TEXT_TAGS | LAYOUT_TEXT_TAGS)):
                bad.decompose()
            raw = node.get_text("\n", strip=True)
        else:
            # stdlib yolunda HTML'den blok blok temizleyip metin cikaralim.
            raw = self.html
            for pattern in (
                r"(?is)<(script|style|noscript|svg|template)\b.*?</\1>",
                r"(?is)<(nav|footer|aside)\b.*?</\1>",
            ):
                raw = re.sub(pattern, " ", raw)
            raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
            raw = re.sub(r"(?is)<[^>]+>", "\n", raw)

        lines = []
        seen = set()
        for line in _html_unescape(raw).splitlines():
            line = " ".join(line.split())
            if len(line) < 2:
                continue
            key = line.lower()
            if key in seen:  # menulerin tekrarini at
                continue
            seen.add(key)
            lines.append(line)
            if max_chars and sum(len(x) for x in lines) > max_chars:
                break
        out = "\n".join(lines)
        return out[:max_chars] if max_chars else out


# --------------------------------------------------------------------------
# Teknoloji ve ozellik parmak izleri
# --------------------------------------------------------------------------
TECH_SIGNATURES = {
    "Next.js": [r"/_next/", r"__NEXT_DATA__"],
    "Nuxt": [r"/_nuxt/", r"__NUXT__"],
    "React": [r"data-reactroot", r"react(-dom)?(?:\.production)?(?:\.min)?\.js"],
    "Vue": [r"vue\.(runtime|global)", r"data-v-[0-9a-f]{8}", r"/_nuxt/"],
    "Angular": [r"ng-version", r"ng-version=\d"],
    "Svelte/SvelteKit": [r"svelte-[0-9a-z]{6,}", r"__sveltekit"],
    "Remix": [r"__remixContext"],
    "Astro": [r"astro-island", r"astro-[0-9a-z]{6,}\.js"],
    "Gatsby": [r"___gatsby", r"page-data/app-data"],
    "Three.js": [r"three(?:\.min)?\.js", r"THREE\.WebGLRenderer"],
    "Babylon.js": [r"babylon(?:\.js|\.max)?\.js", r"BABYLON\.Engine"],
    "A-Frame": [r"aframe(?:\.min)?\.js"],
    "Phaser": [r"phaser(?:\.min)?\.js", r"new Phaser\.Game"],
    "Unity WebGL": [r"\.wasm[\"']", r"unityInstance"],
    "jQuery": [r"jquery[-\d.]*\.min?\.js"],
    "Bootstrap": [r"bootstrap[-\d.]*\.min?\.css", r"class=\"[^\"]*\bcontainer-fluid\b"],
    "Tailwind CSS": [r"tailwind", r"class=\"[^\"]*\b(flex|grid)\s+[^\"]*\b(items-center|gap-\d)"],
    "Material UI": [r"MuiButton|mui-"],
    "Chakra UI": [r"chakra-"],
    "Bulma": [r"bulma(?:\.min)?\.css"],
    "Django": [r"csrfmiddlewaretoken", r"__admin_media_prefix__"],
    "Flask": [r"werkzeug"],
    "Laravel": [r"laravel_session|XSRF-TOKEN"],
    "Rails": [r"data-params=|rails-ujs"],
    "ASP.NET": [r"__VIEWSTATE", r"aspnet-"],
    "WordPress": [r"wp-content/", r"wp-includes/"],
    "Shopify": [r"cdn\.shopify\.com", r"Shopify\.theme"],
    "Wix": [r"wixstatic\.com", r"X-Wix-"],
    "Webflow": [r"webflow\.js", r"data-wf-page"],
    "Ghost": [r"ghost\.io", r"/ghost/api/"],
    "Squarespace": [r"squarespace\.com", r"Static\.SQUARESPACE_CONTEXT"],
    "Contentful": [r"contentful\.com"],
    "Strapi": [r"strapi"],
    "Directus": [r"directus"],
    "Framer": [r"framer\.com", r"framer-sh"],
    "Bubble": [r"bubble\.io", r"__bubble"],
    "HubSpot": [r"hs-scripts|hubspot"],
    "Firebase": [r"firebase(app|io|js)"],
    "Supabase": [r"supabase"],
    "Auth0": [r"auth0\.com"],
    "Clerk": [r"clerk\.(accounts|com)"],
    "Supabase Realtime": [r"supabase.*realtime", r"channel\('"],
    "Socket.IO": [r"socket\.io"],
    "WebRTC": [r"RTCPeerConnection|webrtc"],
    "Cloudflare": [r"cdnjs\.cloudflare\.com", r"cloudflare"],
    "jsDelivr": [r"cdn\.jsdelivr\.net"],
    "unpkg": [r"unpkg\.com"],
    "Google Fonts": [r"fonts\.googleapis\.com", r"fonts\.gstatic\.com"],
    "Stripe": [r"stripe\.com|js\.stripe\.com|Stripe\("],
    "PayPal": [r"paypal\.com|paypalobjects"],
    "iyzico": [r"iyzipay"],
    "PayTR": [r"paytr\.com"],
    "PWA": [r"rel=[\"']manifest[\"']", r"manifest\.json", r"serviceWorker"],
}

ANALYTICS_SIGNATURES = {
    "Google Analytics": [r"gtag\(", r"google-analytics\.com", r"GUA-[A-Z0-9-]+"],
    "Google Tag Manager": [r"googletagmanager\.com/gtm"],
    "Plausible": [r"plausible\.io"],
    "Matomo": [r"matomo", r"piwik"],
    "Mixpanel": [r"mixpanel"],
    "Segment": [r"segment\.io|analytics\.load"],
    "Hotjar": [r"hotjar"],
    "Microsoft Clarity": [r"clarity\.ms"],
    "Yandex Metrica": [r"mc\.yandex"],
    "Facebook Pixel": [r"connect\.facebook\.net|fbq\("],
    "TikTok Pixel": [r"analytics\.tiktok"],
    "Klaviyo": [r"klaviyo"],
    "Mailchimp": [r"mailchimp|list-manage"],
}

AD_SIGNATURES = {
    "AdSense": [r"googlesyndication|adsbygoogle"],
    "Taboola": [r"taboola"],
    "Outbrain": [r"outbrain"],
    "Criteo": [r"criteo"],
    "MGID": [r"mgid"],
    "Facebook Audience Network": [r"facebook\.net/.*audience"],
    "PropellerAds": [r"propellerads"],
}

FEATURE_SIGNATURES = {
    "search": [
        r"type=[\"']search[\"']", r"role=[\"']search[\"']",
        r"(name|id|aria-label|placeholder)=[\"'][^\"']*(search|ara[qı]t|q)",
    ],
    "auth_login": [
        r"type=[\"']password[\"']",
        r"[\"'](/[^\"']*(?:/|^)(?:login|signin|sign-in|giris-yap|oturum-ac))",
        r"\b(sign in|log in|oturum aç|giriş yap)\b",
    ],
    "register": [
        r"[\"'](/[^\"']*(?:register|signup|sign-up|signin/kayit|kayit-ol|kaydol))",
        r"\b(sign up|register|üye ol|kayıt ol|üyelik)\b",
    ],
    "newsletter": [r"newsletter", r"bülten", r"abonelik", r"e-?bülten"],
    "cookie_banner": [r"cookie[-_ ]?(banner|consent|policy|uyum)", r"çerez", r"gdpr"],
    "social_share": [
        r"(twitter|x)\.com/intent/tweet", r"facebook\.com/sharer", r"linkedin\.com/sharing",
        r"whatsapp://send", r"telegram\.me/share", r"\bpaylaş",
    ],
    "comments": [r"\bcomment(s|ing)?\b", r"yorum", r"discussion"],
    "pagination": [r"pagination", r"rel=[\"']next[\"']", r"[?&]page=", r"\bsayfa\b"],
    "filtering": [r"\bfilter\b", r"filtre", r"\bsort\b", r"sırala", r"[?&]sort="],
    "pricing": [r"\bpricing\b", r"\bfiyat(land|lar)?\b", r"/plans?\b", r"subscription"],
    "cart_checkout": [r"\bcart\b", r"sepet", r"add[-_ ]to[-_ ]cart", r"basket", r"checkout"],
    "dark_mode": [r"prefers-color-scheme", r"\bdark[-_ ]?mode\b", r"theme[-_ ]?(toggle|switch)", r"tema"],
    "i18n": [r"hreflang", r"\blang=[\"']", r"/(tr|en|de|fr|es)\b", r"language[-_ ]?switch", r"\bdil\b"],
    "push_notifications": [r"pushManager|Notification\.requestPermission", r"\bbildirim", r"webpush"],
    "video": [r"<video", r"(youtube|youtube-nocookie|vimeo|dailymotion|brightcove)\.?(com|net)"],
    "live_chat": [r"(intercom|tawk|crisp|zendesk|drift|hubspot)\.?(com|io)", r"livechat", r"canlı destek"],
    "chatbot": [r"chatbot|dialogflow|rasa\.ai|openai\.com/api"],
    "ratings_reviews": [r"\b(rating|review|stars?)\b", r"yıldız", r"değerlendirme", r"puan"],
    "wishlist": [r"wishlist", r"favori", r"beğen"],
    "dashboard_admin": [r"[\"'](/[^\"']*/(?:admin|dashboard|yonetim|panel)\b)", r"\badmin panel\b"],
    "api_docs": [r"swagger|openapi|redoc|/docs\b|api[- ]reference"],
    "maps": [r"(google|maps)\.(com|googleapis)|mapbox|leaflet|openstreetmap"],
    "community": [r"\bforum(s)?\b", r"topluluk", r"\bsohbet\b", r"discord|telegram|reddit"],
    "realtime": [r"WebSocket|socket\.io|SockJS|EventSource|pusher|ably|supabase\.realtime"],
    "3d_graphics": [r"THREE\.|babylonjs|aframe|WebGLRenderingContext|<canvas[^>]*webgl"],
    "mobile_app": [r"itunes\.apple\.com|play\.google\.com|\.apk\b|market://"],
    "gamification": [r"\bleaderboard\b|puan tablosu|achievement|rozet|streak|\bxp\b"],
    "offline": [r"serviceWorker|navigator\.onLine|workbox"],
    "analytics_dashboard": [r"google-analytics|gtag\(|plausible|matomo|chart\.js|recharts"],
    "search_engine": [r"algolia|elasticsearch|meilisearch|typesense|opensearch|lunr|fuse\.js"],
    "admin_crud": [r"[\"'](/[^\"']*/(?:admin|yonetici|moderator))"],
    "seo_meta": [r"<meta[^>]+name=[\"']description[\"']", r"property=[\"']og:", r"application/ld\+json"],
}


def _match_signatures(haystack: str, signature_map: dict):
    found = []
    lowered = haystack.lower()
    for label, patterns in signature_map.items():
        for pattern in patterns:
            try:
                if re.search(pattern, lowered, re.I):
                    found.append(label)
                    break
            except re.error:
                continue
    return sorted(found)


def _hostname(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _html_unescape(text: str) -> str:
    """&nbsp; &amp; &#39; gibi entity'leri coz (stdlib yolunda gerekli)."""
    import html as _html

    try:
        return _html.unescape(text or "")
    except Exception:
        return text or ""


def _registrable_domain(host: str) -> str:
    """site.co.uk -> site.co.uk gibi basit kurallar (public suffix list yok)."""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    two_level = {"com", "co", "net", "org", "gov", "edu", "ac"}
    if parts[-2] in two_level and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def extract_features(doc: HtmlDoc, text: str) -> dict:
    haystack = f"{doc.html}\n{text}"
    return {name: bool(_match_signatures(haystack, {name: patterns}))
            for name, patterns in FEATURE_SIGNATURES.items()}


def _seo_profile(doc: HtmlDoc, headers: dict) -> dict:
    html = doc.html
    jsonld_types = []
    for blob in doc.jsonld:
        try:
            parsed = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(node, dict):
                node_type = node.get("@type")
                if isinstance(node_type, list):
                    jsonld_types.extend(str(t) for t in node_type)
                elif node_type:
                    jsonld_types.append(str(node_type))
                graph = node.get("@graph")
                if isinstance(graph, list):
                    for child in graph:
                        if isinstance(child, dict) and child.get("@type"):
                            jsonld_types.append(str(child["@type"]))

    hreflang = re.findall(r"hreflang=[\"']([^\"']+)", html, re.I)
    robots_meta = ""
    for entry in doc.meta:
        if (entry.get("name") or "").lower() == "robots":
            robots_meta = entry.get("content", "")

    title = doc.title
    description = doc.meta_content("description", "og:description")
    return {
        "title": title,
        "title_length": len(title),
        "has_title": bool(title),
        "description": description[:400],
        "description_length": len(description),
        "has_description": bool(description),
        "canonical": doc.meta_content("canonical") or _first_link(doc, "canonical"),
        "og_title": bool(doc.meta_content("og:title")),
        "og_description": bool(doc.meta_content("og:description")),
        "og_image": bool(doc.meta_content("og:image", "twitter:image")),
        "twitter_card": doc.meta_content("twitter:card"),
        "jsonld_types": sorted(set(jsonld_types)),
        "hreflang": sorted(set(hreflang)),
        "robots_meta": robots_meta,
        "robots_txt_link": bool(re.search(r'href=[\'"][^\'"]*robots\.txt', html, re.I)),
        "sitemap_link": bool(re.search(r"sitemap", html, re.I)),
        "lang": doc.lang,
        "server": headers.get("server", ""),
        "x_powered_by": headers.get("x-powered-by", ""),
        "h1_count": sum(1 for h in doc.headings if h["tag"] == "h1"),
        "heading_count": len(doc.headings),
    }


def _first_link(doc: HtmlDoc, rel_name: str) -> str:
    """<link rel="canonical|alternate|manifest" href="..."> ilk eşleşme."""
    pattern = re.compile(
        r"<link[^>]+rel=[\"'][^\"']*\b%s\b[^\"']*[\"'][^>]*href=[\"']([^\"']+)" % re.escape(rel_name),
        re.I,
    )
    match = pattern.search(doc.html or "")
    return match.group(1) if match else ""


def _a11y_profile(doc: HtmlDoc) -> dict:
    images = doc.images
    with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    decorative = sum(1 for img in images if img.get("alt") == "")
    labeled_fields = 0
    for form in doc.forms:
        for field in form["inputs"]:
            if field.get("aria_label") or field.get("placeholder") or field.get("id"):
                labeled_fields += 1
    order = [int(h["tag"][1]) for h in doc.headings if h["tag"] and h["tag"][1:].isdigit()]
    violations = sum(1 for prev, cur in zip(order, order[1:]) if cur - prev > 1)
    html = doc.html
    return {
        "image_count": len(images),
        "images_with_alt": with_alt,
        "images_alt_ratio": round(with_alt / len(images), 2) if images else None,
        "images_missing_alt": decorative,
        "images_lazy": sum(1 for img in images if (img.get("loading") or "") == "lazy"),
        "form_fields": len(doc.inputs),
        "form_fields_labeled": labeled_fields,
        "labels": sum(len(f["labels"]) for f in doc.forms),
        "heading_order_violations": violations,
        "skip_link": bool(re.search(r"skip[-_ ]?(to|link|content)|ana içerik|içeriğe geç", html, re.I)),
        "aria_attributes": len(re.findall(r"\baria-[\w-]+=", html, re.I)),
        "role_attributes": len(re.findall(r"\brole=[\"']", html, re.I)),
        "buttons": len(doc.buttons),
        "buttons_with_name": sum(1 for b in doc.buttons if b.strip()),
        "has_viewport": bool(re.search(r"name=[\"']viewport[\"']", html, re.I)),
        "tabindex": len(re.findall(r"tabindex=[\"']-?\d+", html, re.I)),
        "positive_tabindex": len(re.findall(r"tabindex=[\"'][1-9]", html, re.I)),
    }


def _perf_profile(doc: HtmlDoc, headers: dict, elapsed_ms: int, bytes_len: int) -> dict:
    scripts = doc.scripts
    external = [s for s in scripts if s.get("src")]
    inline = [s for s in scripts if s.get("inline")]
    inline_bytes = sum(len(s.get("inline", "")) for s in inline)
    images = doc.images
    cache_control = headers.get("cache-control", "")
    return {
        "html_kb": round(bytes_len / 1024, 1),
        "first_byte_ms": elapsed_ms,
        "script_count": len(scripts),
        "external_script_count": len(external),
        "inline_script_count": len(inline),
        "inline_script_kb": round(inline_bytes / 1024, 1),
        "stylesheet_count": len(doc.stylesheets),
        "image_count": len(images),
        "images_without_dimensions": sum(
            1 for img in images if not (img.get("width") and img.get("height"))
        ),
        "content_encoding": headers.get("content-encoding", ""),
        "compressed": headers.get("content-encoding", "") in ("gzip", "br", "deflate"),
        "cache_control": cache_control,
        "has_cache_policy": bool(cache_control),
        "etag": bool(headers.get("etag")),
        "blocking_render_css": len(doc.stylesheets),
        "render_blocking_note": "staysil CSS sayisi yuksekse kritik CSS ayrilmali",
    }


def analyze_site(url: str, *, timeout: int = 15, max_text: int = 6000) -> dict:
    """Bir siteyi ziyaret edip tam profil cikar."""
    profile = {
        "url": url,
        "ok": False,
        "error": "",
        "fetched_at": time.time(),
    }
    try:
        status, html, headers, final_url, elapsed_ms = fetch_html(url, timeout=timeout)
    except FetchError as exc:
        profile["error"] = f"FetchError: {exc}"
        return profile
    except urllib.error.HTTPError as exc:
        profile["error"] = f"HTTP {exc.code}"
        return profile
    except Exception as exc:
        profile["error"] = f"{type(exc).__name__}: {exc}"
        return profile

    doc = HtmlDoc(html, final_url)
    text = doc.text(max_chars=max_text)
    host = _hostname(final_url) or _hostname(url)
    anchors = doc.anchors

    nav_links = []
    seen = set()
    for anchor in anchors:
        label = anchor["text"][:80]
        href = anchor["href"]
        if not label or not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urllib.parse.urljoin(final_url, href)
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        nav_links.append({"label": label, "url": absolute})
        if len(nav_links) >= 60:
            break

    tech = _match_signatures(html, TECH_SIGNATURES)
    analytics = _match_signatures(html, ANALYTICS_SIGNATURES)
    ads = _match_signatures(html, AD_SIGNATURES)

    profile.update(
        {
            "ok": status < 400,
            "status": status,
            "final_url": final_url,
            "domain": host,
            "registrable_domain": _registrable_domain(host),
            "bytes": len(html),
            "title": doc.title,
            "features": extract_features(doc, text),
            "nav_links": nav_links[:40],
            "headings": doc.headings[:40],
            "forms": doc.forms[:10],
            "buttons": [b for b in doc.buttons if b][:25],
            "tech": tech,
            "analytics": analytics,
            "ads": ads,
            "seo": _seo_profile(doc, headers),
            "a11y": _a11y_profile(doc),
            "perf": _perf_profile(doc, headers, elapsed_ms, len(html)),
            "content_sample": text,
        }
    )
    return profile


def summarize_site(profile: dict, max_chars: int = 700) -> str:
    """Profili LLM'e verilecek kisa metne indirger."""
    if not profile.get("ok") and not profile.get("seo"):
        return f"- {profile.get('url')}: ERISILEMEDI ({profile.get('error', 'bilinmiyor')})"

    features = [name for name, on in (profile.get("features") or {}).items() if on]
    nav = [item["label"] for item in (profile.get("nav_links") or [])][:18]
    seo = profile.get("seo") or {}
    a11y = profile.get("a11y") or {}
    perf = profile.get("perf") or {}

    lines = [
        f"- {profile.get('final_url') or profile.get('url')} ({profile.get('title','')[:90]})",
        f"  Teknoloji: {', '.join(profile.get('tech') or []) or 'bilinmiyor'}",
        f"  Analitik/reklam: {', '.join((profile.get('analytics') or []) + (profile.get('ads') or [])) or 'yok'}",
        f"  Menü: {' | '.join(nav) or 'yok'}",
        f"  Tespit edilen özellikler ({len(features)}): {', '.join(features) or 'yok'}",
        f"  Formlar: {len(profile.get('forms') or [])} | Butonlar: {len(profile.get('buttons') or [])}",
        f"  SEO: başlık={'var' if seo.get('has_title') else 'yok'} "
        f"({seo.get('title_length', 0)}kr) açıklama={'var' if seo.get('has_description') else 'yok'} "
        f"canonical={'var' if seo.get('canonical') else 'yok'} OG={'var' if seo.get('og_title') else 'yok'} "
        f"JSON-LD={seo.get('jsonld_types') or '-'} hreflang={seo.get('hreflang') or '-'} "
        f"robots.txt={'var' if seo.get('robots_txt_link') else 'yok'} sitemap={'var' if seo.get('sitemap_link') else 'yok'}",
        f"  Erişilebilirlik: viewport={'var' if a11y.get('has_viewport') else 'yok'} "
        f"alt oranı={a11y.get('images_alt_ratio')} atlama bağlantısı={'var' if a11y.get('skip_link') else 'yok'} "
        f"aria={a11y.get('aria_attributes')} başlık sıra hatası={a11y.get('heading_order_violations')}",
        f"  Performans: {perf.get('html_kb')}KB ilk yanıt {perf.get('first_byte_ms')}ms "
        f"script={perf.get('script_count')} (inline {perf.get('inline_script_kb')}KB) "
        f"css={perf.get('stylesheet_count')} görsel={perf.get('image_count')} "
        f"sıkıştırma={'var' if perf.get('compressed') else 'yok'} "
        f"cache={'var' if perf.get('has_cache_policy') else 'yok'}",
    ]
    sample = (profile.get("content_sample") or "").strip()
    if sample:
        lines.append(f"  İçerik örneği: {sample[:max_chars]}")
    return "\n".join(lines)