"""
İsteklerden ziyaretçi profili çıkarır: ülke, dil, cihaz, tarayıcı, işletim sistemi.
Harici GeoIP veritabanı gerektirmez; Accept-Language ve User-Agent başlıkları kullanılır.
"""
import hashlib

# Tarayıcı tespiti (sırayla önemli)
BROWSERS = [
    ("Edge", ("Edg/", "Edge/")),
    ("Opera", ("OPR/", "Opera")),
    ("Samsung Internet", ("SamsungBrowser/",)),
    ("Chrome", ("Chrome/", "CriOS/")),
    ("Firefox", ("Firefox/", "FxiOS/")),
    ("Safari", ("Safari/",)),
    ("Brave", ("Brave/")),
]

OPERATING_SYSTEMS = [
    ("Windows", ("Windows",)),
    ("Android", ("Android",)),
    ("iOS", ("iPhone", "iPad", "iPod")),
    ("macOS", ("Mac OS X",)),
    ("Linux", ("Linux", "X11")),
]

# Ekran genişliği aralıkları (dashboard gruplaması için)
SCREEN_BUCKETS = [
    (360, "≤ 360 (küçük mobil)"),
    (414, "361 - 414 (mobil)"),
    (768, "415 - 768 (tablet)"),
    (1024, "769 - 1024 (tablet yatay)"),
    (1440, "1025 - 1440 (laptop)"),
    (1920, "1441 - 1920 (masaüstü)"),
    (2560, "1921 - 2560 (geniş ekran)"),
    (99999, "2560+ (4K+)"),
]


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def session_key_hash(request):
    """Oturum anahtarının kısa hash'i (kişisel veri sızdırmaz)."""
    key = ""
    sid = request.COOKIES.get("sessionid")
    if sid:
        key = sid
    else:
        key = request.COOKIES.get("auth_token") or ""
        if not key:
            ua = request.META.get("HTTP_USER_AGENT", "")
            key = f"{client_ip(request)}|{ua[:120]}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def detect_browser(user_agent):
    for name, needles in BROWSERS:
        if any(needle in user_agent for needle in needles):
            return name
    return "Diğer"


def detect_os(user_agent):
    for name, needles in OPERATING_SYSTEMS:
        if any(needle in user_agent for needle in needles):
            return name
    return "Diğer"


def detect_device(user_agent):
    ua = user_agent.lower()
    if "ipad" in ua or "tablet" in ua:
        return "Tablet"
    if "mobi" in ua or "iphone" in ua:
        return "Mobil"
    if any(x in ua for x in ("windows", "macintosh", "linux", "x11")):
        return "Masaüstü"
    return "Diğer"


def preferred_language(request):
    """Accept-Language başlığından kısa dil kodu (örn: tr)."""
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "") or ""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()
        if not code:
            continue
        code = code.replace("_", "-")
        if code in ("*",):
            continue
        return code.split("-")[0]
    return ""


def country_from_headers(request):
    """
    Ülke kodu tahmini. Gerçek GeoIP DB yoksa:
      1) Cloudflare/uzak başlıkları (cf-ipcountry, x-appengine-country)
      2) Accept-Language bölgesi (tr-TR -> TR)
      3) Zaman dilimi ofseti
    """
    for header in ("HTTP_CF_IPCOUNTRY", "HTTP_X_APPENGINE_COUNTRY", "HTTP_X_COUNTRY_CODE"):
        value = (request.META.get(header) or "").strip().upper()
        if len(value) == 2 and value.isalpha():
            return value

    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "") or ""
    for part in header.split(","):
        code = part.split(";")[0].strip().replace("_", "-")
        if "-" in code:
            region = code.split("-")[1].upper()
            if len(region) == 2 and region.isalpha():
                return region

    offset = (request.META.get("HTTP_X_UTC_OFFSET") or "").strip()
    try:
        hours = int(float(offset))
    except (TypeError, ValueError):
        return ""
    if hours == 3:
        return "TR"
    if hours == 0:
        return "GB"
    if hours in (-5, -4):
        return "US"
    if hours == 1:
        return "DE"
    return ""


def is_api_request(path):
    return path.startswith("/api/") or path.startswith("/omni/") or path.startswith("/nvidia/")


def screen_bucket(width):
    if not width:
        return "Bilinmiyor"
    for limit, label in SCREEN_BUCKETS:
        if width <= limit:
            return label
    return "Diğer"


def extract_profile(request):
    """İstekten ziyaretçi profili döndürür."""
    ua = request.META.get("HTTP_USER_AGENT", "") or ""
    return {
        "ip_address": client_ip(request) or None,
        "country": country_from_headers(request),
        "language": preferred_language(request),
        "device": detect_device(ua),
        "os": detect_os(ua),
        "browser": detect_browser(ua),
        "user_agent": ua[:1000],
        "session_key": session_key_hash(request),
    }