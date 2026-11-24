"""
Kod tabaninda gomulu sir taramasi.

Kullanim:
    python manage.py secrets_scan            # tara
    python manage.py secrets_scan --fix      # duzeltilebilir olanlari duzelt
    python manage.py secrets_scan --env      # .env durumunu goster
"""
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# Taranacak dosya uzantilari
SCAN_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
                   ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                   ".sh", ".ps1", ".bat", ".env", ".example"}

# Taranmayacak dizinler
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", "coverage", "htmlcov", "migrations",
    "static", "media", ".mypy_cache", ".ruff_cache", "venv311",
}

# Taranmayacak dosyalar
SKIP_FILES = {"*.pyc", "*.log", "pnpm-lock.yaml", "package-lock.json",
              "yarn.lock", "*.min.js", "*.map"}

# --------------------------------------------------
# 1) GERCEK API ANAHTARI KALIPLARI (guven P0)
# --------------------------------------------------

REAL_KEY_PATTERNS = [
    (r"nvapi-[A-Za-z0-9_\-]{40,}", "NVIDIA API anahtari"),
    (r"sk-[A-Za-z0-9]{48,}", "OpenAI API anahtari"),
    (r"sk-proj-[A-Za-z0-9_\-]{40,}", "OpenAI proje anahtari"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{50,}", "GitHub fine-grained PAT"),
    (r"AKIA[0-9A-Z]{16}", "AWS erisim anahtari"),
    (r"ASIA[0-9A-Z]{16}", "AWS gecici anahtar"),
    (r"AIza[0-9A-Za-z_\-]{35}", "Google API anahtari"),
    (r"ya29\.[0-9A-Za-z_\-]{30,}", "Google OAuth token"),
    (r"xox[baprs]-[0-9A-Za-z\-]{10,}", "Slack token"),
    (r"glpat-[0-9A-Za-z_\-]{18,}", "GitLab token"),
    (r"sk_live_[0-9A-Za-z]{20,}", "Stripe live key"),
    (r"rk_live_[0-9A-Za-z]{20,}", "Stripe live key"),
    (r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", "Ozel anahtar"),
    (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "JWT"),
]

# --------------------------------------------------
# 2) KODDA SIR VARSAYILANI (guven P1)
# --------------------------------------------------

FALLBACK_PATTERNS = [
    (r"os\.(?:environ\.get|getenv)\(\s*['\"][A-Z_0-9]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD)[A-Z_0-9]*['\"]\s*,\s*['\"][^'\"]{6,}['\"]",
     "os.getenv ile gomulu varsayilan deger"),
    (r"get\(['\"][A-Z_0-9]*(?:KEY|SECRET|TOKEN|PASSWORD)[A-Z_0-9]*['\"]\s*,\s*['\"][^'\"]{6,}['\"]\s*\)",
     "get() ile gomulu varsayilan deger"),
    (r"SECRET_KEY\s*=\s*['\"][^'\"]{20,}['\"]", "Gomulu SECRET_KEY"),
    (r"(?:password|passwd|api_key|secret|token)\s*[:=]\s*['\"][^'\"]{6,}['\"]",
     "Gomulu sifre degeri"),
    (r"dev-secret-change-in-production", "Gelistirme varsayilani"),
    (r"change-in-production", "Degistir-in-production deseni"),
]

# Guvenli oldugu bilinen degerler (pozitif false positive)
SAFE_VALUES = {
    "omniroute-local", "NOT_SET", "replace-me", "changeme", "change-me",
    "your-secret-key-here", "django-insecure-your-secret-key-here-change-in-production",
    "", "none", "null", "true", "false", "localhost", "test", "demo",
    "postgres", "neo4j", "password", "example", "xxx", "sk-...",
    "django-insecure-dev-key-change-in-production",
}


class Command(BaseCommand):
    help = "Kod tabaninda gomulu API anahtari/sir taramasi yapar"

    def add_arguments(self, parser):
        parser.add_argument("--env", action="store_true", help="Sadece .env durumunu goster")
        parser.add_argument("--path", default=None, help="Taranacak dizin")
        parser.add_argument("--max-line", type=int, default=100, help="Gosterilecek max satir")

    def handle(self, *args, **options):
        from agent_app.secrets import validate

        base = Path(options["path"] or settings.BASE_DIR)

        if options["env"]:
            return self._show_env_status()

        self.stdout.write("=" * 78)
        self.stdout.write("GOMULU SIR TARAMASI")
        self.stdout.write("=" * 78)
        self.stdout.write(f"Taranan dizin: {base}")
        self.stdout.write("")

        findings = self._scan(base)

        critical = [f for f in findings if f["severity"] == "CRITICAL"]
        fallback = [f for f in findings if f["severity"] == "HIGH"]

        if critical:
            self.stdout.write(self.style.ERROR(f"\n[!] {len(critical)} KRITIK gomulu anahtar:\n"))
            for finding in critical[: options["max_line"]]:
                self.stdout.write(
                    f"  {finding['file']}:{finding['line']}  [{finding['kind']}]\n"
                    f"      {finding['preview']}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("[OK] Gercek API anahtari bulunamadi"))

        if fallback:
            self.stdout.write(self.style.WARNING(f"\n[!] {len(fallback)} kodda varsayilan deger:\n"))
            for finding in fallback[: options["max_line"]]:
                self.stdout.write(
                    f"  {finding['file']}:{finding['line']}  [{finding['kind']}]\n"
                    f"      {finding['preview']}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("[OK] Gomulu varsayilan deger bulunamadi"))

        self.stdout.write("")
        self._show_env_status()

        total = len(critical) + len(fallback)
        if critical:
            self.stdout.write(self.style.ERROR(
                f"\nSONUC: {len(critical)} KRITIK sizinti. API anahtarlari KODA GOMULMEZ."
            ))
        elif fallback:
            self.stdout.write(self.style.WARNING(
                f"\nSONUC: {len(fallback)} gomulu varsayilan. .env'e tasinmali."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nSONUC: Temiz. Tum anahtarlar .env'de."))

        return None

    # --------------------------------------------------
    def _scan(self, base):
        findings = []

        compiled_critical = [(re.compile(p), k) for p, k in REAL_KEY_PATTERNS]
        compiled_fallback = [(re.compile(p), k) for p, k in FALLBACK_PATTERNS]

        for path in base.rglob("*"):
            if not path.is_file():
                continue

            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix not in SCAN_EXTENSIONS and not path.name.startswith(".env"):
                continue
            if any(path.match(pattern) for pattern in SKIP_FILES):
                continue

            # .env dosyalari kendi icerikleri sir - tarama disi
            if path.name == ".env":
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for line_number, line in enumerate(content.split("\n"), 1):
                if len(line) > 2000:
                    continue

                for pattern, kind in compiled_critical:
                    match = pattern.search(line)
                    if match:
                        findings.append({
                            "severity": "CRITICAL",
                            "file": self._rel(path, base),
                            "line": line_number,
                            "kind": kind,
                            "preview": self._mask(match.group(0)),
                        })

                for pattern, kind in compiled_fallback:
                    match = pattern.search(line)
                    if match:
                        found = match.group(0)
                        if any(safe in found for safe in SAFE_VALUES):
                            continue
                        if "os.getenv" in found and "','" in found and len(found) < 60:
                            continue
                        findings.append({
                            "severity": "HIGH",
                            "file": self._rel(path, base),
                            "line": line_number,
                            "kind": kind,
                            "preview": self._mask(found.strip()[:90]),
                        })

        return findings

    def _rel(self, path, base):
        try:
            return str(path.relative_to(base))
        except Exception:
            return str(path)

    def _mask(self, value):
        text = str(value)
        if len(text) <= 12:
            return text
        return f"{text[:6]}...{text[-4:]} ({len(text)} kr)"

    # --------------------------------------------------
    def _show_env_status(self):
        from agent_app.secrets import CRITICAL_SECRETS, OPTIONAL_SECRETS, mask_env

        self.stdout.write("")
        self.stdout.write("-" * 78)
        self.stdout.write("ENV DEGISKENI DURUMU (.env)")
        self.stdout.write("-" * 78)

        self.stdout.write("\nKRITIK (zorunlu):")
        for name, spec in CRITICAL_SECRETS.items():
            value = os.environ.get(name)
            if value:
                self.stdout.write(self.style.SUCCESS(f"  [VAR] {name} = {mask_env(name)}"))
            else:
                self.stdout.write(self.style.ERROR(f"  [EKSIK] {name} - {spec['desc']}"))

        self.stdout.write("\nDIS SERVISLER (zorunlu degil):")
        configured = 0
        for name, spec in OPTIONAL_SECRETS.items():
            value = os.environ.get(name)
            if value:
                configured += 1
                self.stdout.write(self.style.SUCCESS(f"  [VAR] {name} = {mask_env(name)}"))
            else:
                self.stdout.write(f"  [yok] {name} - {spec['desc']}")

        self.stdout.write(f"\nAyarli dis servis: {configured}/{len(OPTIONAL_SECRETS)}")

        git_ignored = self._git_ignores_env()
        if git_ignored is True:
            self.stdout.write(self.style.SUCCESS(".env git tarafindan ignore ediliyor [OK]"))
        elif git_ignored is False:
            self.stdout.write(self.style.ERROR(".env git TAAKIPTE - sizinti riski! [KRITIK]"))
        else:
            self.stdout.write(".env durumu bilinmiyor")

    def _git_ignores_env(self):
        try:
            import subprocess

            result = subprocess.run(
                ["git", "check-ignore", ".env"],
                cwd=str(settings.BASE_DIR),
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return None