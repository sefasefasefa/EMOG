"""
Merkezi sır yöneticisi.

KURAL: API anahtarları ve sırlar ASLA koda gömülmez.
      Tümü .env / ortam değişkenlerinden okunur.

Bu modül:
  - Sırları tek noktadan okur (env-only)
  - Eksik anahtarları başlangıçta tespit eder
  - Değerleri loglarda maskeler
  - Koda gömülü SIR OLABİLECEK varsayılan değerlere izin vermez
      (kritik sırlar için fallback YOKTUR - eksikse hata verir)
"""
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# KRİTİK SİRLAR - EKSİKSE UYGULAMA ÇALIŞMAZ
# ============================================================

CRITICAL_SECRETS = {
    "SECRET_KEY": {
        "min_length": 32,
        "required_in_prod": True,
        "desc": "Django imzalama anahtarı (session, CSRF, JWT, parola türetme)",
    },
    "EMOG_JWT_SECRET": {
        "min_length": 32,
        "required_in_prod": True,
        "desc": "Access token JWT imzalama anahtarı",
    },
}

# ============================================================
# DIŞ SERVİS SIRLARI - EKSİKSE O ÖZELLİK ÇALIŞMAZ
# ============================================================

OPTIONAL_SECRETS = {
    "NVIDIA_API_KEY": {"min_length": 20, "desc": "NVIDIA NIM API anahtarı"},
    "OPENAI_API_KEY": {"min_length": 20, "desc": "OpenAI API anahtarı"},
    "ANTHROPIC_API_KEY": {"min_length": 20, "desc": "Anthropic API anahtarı"},
    "GITHUB_TOKEN": {"min_length": 20, "desc": "GitHub personal access token"},
    "SEMANTIC_SCHOLAR_API_KEY": {"min_length": 10, "desc": "Semantic Scholar API anahtarı"},
    "GOOGLE_CLIENT_SECRET": {"min_length": 16, "desc": "Google OAuth client secret"},
    "GITHUB_CLIENT_SECRET": {"min_length": 16, "desc": "GitHub OAuth client secret"},
    "APPLE_CLIENT_SECRET": {"min_length": 16, "desc": "Apple OAuth client secret"},
    "FACEBOOK_CLIENT_SECRET": {"min_length": 16, "desc": "Facebook OAuth client secret"},
    "EMAIL_HOST_PASSWORD": {"min_length": 8, "desc": "SMTP parolası"},
    "NEO4J_PASSWORD": {"min_length": 8, "desc": "Neo4j parolası"},
    "DB_PASSWORD": {"min_length": 8, "desc": "Veritabanı parolası"},
    "REDIS_URL": {"min_length": 10, "desc": "Redis bağlantı adresi (parola içerir)"},
    "ADMIN_API_TOKEN": {"min_length": 24, "desc": "Yönetici API token'ı"},
    "VAULT_MASTER_KEY": {"min_length": 32, "desc": "Zero-Trust Vault ana anahtarı"},
}

# Ayar (config) değerleri - sır değil, ortam değişkeninde tutulur
CONFIG_VARS = {
    "USE_NVIDIA_API", "NVIDIA_DEFAULT_MODEL", "NVIDIA_TIMEOUT",
    "NVIDIA_ULTRA_TIER_MODEL", "NVIDIA_HIGH_TIER_MODEL", "NVIDIA_STANDARD_TIER_MODEL",
    "NVIDIA_FAST_TIER_MODEL", "NVIDIA_LITE_TIER_MODEL",
    "NVIDIA_MULTIMODAL_TIER_MODEL", "NVIDIA_GLM_TIER_MODEL", "NVIDIA_SUPER_VL_TIER_MODEL",
    "NVIDIA_MINIMAX_TIER_MODEL", "NVIDIA_BOLT_TIER_MODEL", "NVIDIA_MISTRAL_TIER_MODEL",
    "NVIDIA_GEMMA4_TIER_MODEL", "NVIDIA_ISING_TIER_MODEL", "NVIDIA_NANO_OMNI_TIER_MODEL",
    "OMNIROUTE_BASE_URL",
    "OMNIROUTE_API_KEY", "OMNIROUTE_DEFAULT_MODEL", "OMNIROUTE_TIMEOUT",
    "SUPERUSER_EMAIL", "ADMIN_URL", "FRONTEND_URL", "DATABASE_URL",
    "EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_BACKEND",
    "DEBUG", "ALLOWED_HOSTS", "CACHE_BACKEND", "LOG_LEVEL",
    "ACCESS_TOKEN_TTL_MINUTES", "REFRESH_TOKEN_TTL_DAYS",
    "ADMIN_REQUIRE_OTP", "LOGIN_MAX_ATTEMPTS", "PORT",
    "REQUEST_BINDING_ENABLED", "CSRF_BINDING_ENABLED",
    "CORS_ALLOWED_ORIGINS", "GOOGLE_CLIENT_ID", "GITHUB_CLIENT_ID",
    "APPLE_CLIENT_ID", "FACEBOOK_CLIENT_ID", "DEFAULT_FROM_EMAIL",
    "EMAIL_USE_TLS", "NEO4J_USER", "NEO4J_URI", "REDIS_HOST",
    "ADMIN_ALLOWED_USERNAMES", "JWT_ALGORITHM",
}

ALL_TRACKED = set(CRITICAL_SECRETS) | set(OPTIONAL_SECRETS) | CONFIG_VARS

# ============================================================
# ÖRNEK / SAhte DEĞER DESENLERİ (kabul edilmez)
# ============================================================

WEAK_PATTERNS = (
    "django-insecure",          # Django otomatik uretilmis yer tutucu
    "change-in-production",
    "change-me",
    "changeme",
    "replace-me",
    "your-secret-key",
    "your-super-secret",
    "your-super",
    "your_secret",
    "your-secret",
    "your-",
    "your_",
    "supersecret",
    "super-secret",
    "dev-secret",
    "example",
    "placeholder",
    "dummy",
    "test-key",
    "todo",
    "xxx",
    "foobar",
    "insecure",
)

# Bilinen sahte API anahtarı kalıpları
FAKE_KEY_PREFIXES = (
    "sk-...",
    "ghp_...",
    "AKIA...",
    "AIza...",
    "nvapi-...",
)

# ============================================================
# OKUMA
# ============================================================

def get(name, default=None, required=False):
    """
    Sır veya ayar değerini ortam değişkeninden okur.

    required=True ise ve eksikse hata fırlatır.
    """
    value = os.environ.get(name)

    if value is None or value == "":
        if required:
            raise MissingSecretError(name)
        return default

    return value


def require(name):
    """Zorunlu sır. Eksikse hata fırlatır."""
    return get(name, required=True)


def has(name):
    value = os.environ.get(name)
    return bool(value and value.strip())


def is_weak(name):
    """Değer güvenli mi? (eksik veya sahte desen)"""
    value = os.environ.get(name)
    if not value:
        return True

    lowered = value.lower().strip()
    if any(pattern in lowered for pattern in WEAK_PATTERNS):
        return True

    if value.strip() in FAKE_KEY_PREFIXES:
        return True

    spec = CRITICAL_SECRETS.get(name) or OPTIONAL_SECRETS.get(name)
    if spec and len(value.strip()) < spec.get("min_length", 8):
        return True

    return False


# ============================================================
# DOĞRULAMA
# ============================================================

def validate(check_optional=True):
    """
    Tüm sırları doğrular.

    Returns: {
        "ok": bool,
        "errors": [...],       # kritik eksik/güçsüz
        "warnings": [...],     # opsiyonel eksik/güçsüz
        "configured": [...],   # ayarlı sırlar
        "config_vars": [...],
    }
    """
    errors = []
    warnings = []
    configured = []

    for name, spec in CRITICAL_SECRETS.items():
        if not has(name):
            errors.append(f"[KRITIK] {name} tanimli degil - {spec['desc']}")
        elif is_weak(name):
            errors.append(
                f"[KRITIK] {name} zayif veya ornek deger - gercek bir sirlar"
            )
        else:
            configured.append(name)

    if check_optional:
        for name, spec in OPTIONAL_SECRETS.items():
            if has(name):
                if is_weak(name):
                    warnings.append(f"[UYARI] {name} zayif - {spec['desc']}")
                else:
                    configured.append(name)

    config_vars = [name for name in sorted(CONFIG_VARS) if has(name)]

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "configured": sorted(configured),
        "config_vars": config_vars,
    }


def fail_loudly():
    """Eksik kritik sır varsa uygulamanın çalışmasını engeller."""
    result = validate(check_optional=False)

    if result["ok"]:
        return result

    message = (
        "\n" + "=" * 72 + "\n"
        "KRITIK GUVENLIK HATASI: API anahtarlari koda gomulu veya eksik!\n"
        "=" * 72 + "\n"
    )
    for error in result["errors"]:
        message += f"  X {error}\n"
    message += (
        "\nCozum: .env dosyaniza su degerleri ekleyin:\n"
        "  SECRET_KEY=<50+ karakter rastgele>\n"
        "  EMOG_JWT_SECRET=<50+ karakter rastgele>\n\n"
        "Uretim anahtari uretmek icin:\n"
        "  python -c \"import secrets; print(secrets.token_urlsafe(64))\"\n"
        "=" * 72 + "\n"
    )

    raise MissingSecretError(message)


# ============================================================
# MASKELEME
# ============================================================

def mask(value, visible=4):
    """Sır değerini loglanabilir hale getirir."""
    if not value:
        return "(bos)"
    text = str(value)
    if len(text) <= visible * 2:
        return "*" * len(text)
    return f"{text[:visible]}...{text[-visible:]} ({len(text)} kr)"


def mask_env(name, visible=4):
    """Ortam değişkenini maskeler."""
    return mask(os.environ.get(name), visible)


def redact_text(text, extra_names=()):
    """
    Serbest metindeki sırları maskeler (log sızıntısı koruması).
    """
    if not text:
        return text

    result = str(text)

    names = set(ALL_TRACKED) | set(extra_names)
    for name in names:
        value = os.environ.get(name)
        if value and len(value) >= 8 and value in result:
            result = result.replace(value, mask(value))

    # Bilinen API anahtarı kalıpları
    patterns = [
        (r"(nvapi-)[A-Za-z0-9_\-]{20,}", r"\1***REDACTED***"),
        (r"(sk-[a-zA-Z0-9\-_]{20,})", r"sk-***REDACTED***"),
        (r"(sk-proj-[a-zA-Z0-9\-_]{20,})", r"sk-proj-***REDACTED***"),
        (r"(gh[pousr]_[A-Za-z0-9]{20,})", r"gh*_***REDACTED***"),
        (r"(github_pat_[A-Za-z0-9_]{20,})", r"github_pat_***REDACTED***"),
        (r"(AKIA|ASIA)[A-Z0-9]{16}", r"AKIA***REDACTED***"),
        (r"(AIza)[A-Za-z0-9_\-]{30,}", r"AIza***REDACTED***"),
        (r"(xox[bpas]-[A-Za-z0-9\-]{10,})", r"xox*-***REDACTED***"),
        (r"(glpat-[A-Za-z0-9\-]{15,})", r"glpat-***REDACTED***"),
        (r"(Bearer\s+)[A-Za-z0-9\-_\.]{20,}", r"\1***REDACTED***"),
        (r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)", r"\1***REDACTED***"),
    ]

    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)

    return result


def redact_mapping(data, extra_names=()):
    """Dict/list yapısındaki sırları maskeler."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if str(key).upper() in {n.upper() for n in (ALL_TRACKED | set(extra_names))}:
                out[key] = mask(value) if value else "(bos)"
            elif isinstance(value, (dict, list)):
                out[key] = redact_mapping(value, extra_names)
            elif isinstance(value, str):
                out[key] = redact_text(value, extra_names)
            else:
                out[key] = value
        return out
    if isinstance(data, list):
        return [redact_mapping(item, extra_names) for item in data]
    if isinstance(data, str):
        return redact_text(data, extra_names)
    return data


# ============================================================
# ÜRETİM KİMLİĞİ
# ============================================================

def generate_secret(length=64):
    import secrets as _secrets

    return _secrets.token_urlsafe(length)


def is_production():
    """Üretim ortamı mı?"""
    debug = os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")
    return not debug


class MissingSecretError(RuntimeError):
    """Zorunlu sır eksik veya güvensiz."""


class WeakSecretError(RuntimeError):
    """Sır var ama zayıf/örnek değer."""