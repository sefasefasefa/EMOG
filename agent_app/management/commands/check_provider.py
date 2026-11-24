"""Provider (NVIDIA / OmniRoute) baglanti ve model listesi teşhisi.

Kullanim:
    python manage.py check_provider
"""
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand

from agent_app.views import _fetch_provider_model_ids, _normalize_model_ids

# Karsilastirma icin bilinen calisan bir NVIDIA hostu (public).
CONTROL_HOST = "build.nvidia.com"


def _staged_check(host, port=443, timeout=6.0):
    """DNS -> TCP -> TLS adimlarini tek tek olcer. (okurlar, hata) doner."""
    steps = []
    try:
        started = time.time()
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        steps.append((True, f"DNS OK ({time.time() - started:.2f}sn) -> {', '.join(ips)}"))
    except Exception as exc:
        steps.append((False, f"DNS BASARISIZ: {type(exc).__name__}: {exc}"))
        return steps, False

    try:
        started = time.time()
        sock = socket.create_connection((host, port), timeout=timeout)
        steps.append((True, f"TCP baglantisi OK ({time.time() - started:.2f}sn)"))
    except Exception as exc:
        steps.append((False, f"TCP BASARISIZ: {type(exc).__name__}: {exc}"))
        return steps, False

    try:
        started = time.time()
        sock.settimeout(timeout)
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            subject = dict(x[0] for x in tls.getpeercert().get("subject", []))
            steps.append((True, f"TLS OK ({time.time() - started:.2f}sn) sertifika={subject}"))
    except Exception as exc:
        steps.append((False, f"TLS BASARISIZ: {type(exc).__name__}: {exc}"))
        return steps, False
    finally:
        try:
            sock.close()
        except Exception:
            pass

    return steps, True


class Command(BaseCommand):
    help = "Model saglayicisina erisimi adim adim test eder ve model listesini gosterir."

    def add_arguments(self, parser):
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Ayarlardaki her modele kucuk bir istek gonderip gercekten calisip calismadigini olcer.",
        )

    def handle(self, *args, **options):
        cfg = getattr(settings, "OMNIROUTE", {}) or {}
        base = (cfg.get("BASE_URL") or "").rstrip("/")
        api_key = cfg.get("API_KEY") or ""
        model = getattr(settings, "OMNIROUTE_DEFAULT_MODEL", "")

        self.stdout.write("=== EMOG saglayici kontrolu ===")
        self.stdout.write(f"BASE_URL        : {base or '(bos)'}")
        self.stdout.write(f"API anahtari    : {'var (' + str(len(api_key)) + ' karakter)' if api_key else 'YOK'}")
        self.stdout.write(f"Varsayilan model: {model}")
        self.stdout.write(f"En hizli model  : {getattr(settings, 'FASTEST_MODEL', '')}")
        self.stdout.write("")

        if not base:
            self.stdout.write(self.style.ERROR("BASE_URL bos."))
            return
        if not api_key:
            self.stdout.write(self.style.WARNING("API anahtari yok (.env icinde NVIDIA_API_KEY veya OMNIROUTE_API_KEY)."))

        host = urllib.parse.urlparse(base).hostname or ""
        self.stdout.write(f"--- Ag adimlari: {host} ---")
        steps, tls_ok = _staged_check(host)
        for ok, text in steps:
            line = self.style.SUCCESS(f"  [OK] {text}") if ok else self.style.ERROR(f"  [HATA] {text}")
            self.stdout.write(line)

        self.stdout.write(f"--- Kontrol: {CONTROL_HOST} (bilinen calisan host) ---")
        ctrl_steps, ctrl_ok = _staged_check(CONTROL_HOST, timeout=6.0)
        for ok, text in ctrl_steps:
            line = self.style.SUCCESS(f"  [OK] {text}") if ok else self.style.ERROR(f"  [HATA] {text}")
            self.stdout.write(line)

        self.stdout.write("")
        if tls_ok:
            self.stdout.write(self.style.SUCCESS("TLS calisiyor; asagidaki model listesi testine gecildi."))
        elif ctrl_ok:
            self.stdout.write(self.style.ERROR(
                "Internet ve TLS calisiyor, ANCAK bu host engelli. "
                "Bu bir SNI/firewall ya da datacenter-IP engelidir (NVIDIA API bazi IP'leri reddeder)."
            ))
            self.stdout.write(self.style.WARNING(
                "Cozumler: 1) Farkli ag/VPN deneyin  2) NVIDIA_API_KEY anahtarini kontrol edin."
            ))
        else:
            self.stdout.write(self.style.ERROR("Genel ag/TLS sorunu var; kontrol hostu da basilmiyor."))

        self.stdout.write("")
        started = time.time()
        try:
            req = urllib.request.Request(
                base + "/models",
                headers={"Accept": "application/json", "Authorization": f"Bearer {api_key or 'none'}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read(262144).decode(errors="replace")
                self.stdout.write(self.style.SUCCESS(f"HTTP {resp.getcode()} ({time.time() - started:.1f}sn)"))
                ids = _normalize_model_ids(body)
                self.stdout.write(f"Model sayisi: {len(ids)}")
                for mid in ids[:25]:
                    self.stdout.write(f"  - {mid}")
                if len(ids) > 25:
                    self.stdout.write(f"  ... ve {len(ids) - 25} model daha")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(300).decode(errors="replace").strip()
            except Exception:
                pass
            self.stdout.write(self.style.ERROR(f"HTTP {exc.code} {exc.reason} ({time.time() - started:.1f}sn) {detail}"))
            if exc.code in (401, 403):
                self.stdout.write(self.style.ERROR("API anahtari gecersiz/yetkisiz."))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f"Istek basarisiz: {type(exc).__name__}: {exc} ({time.time() - started:.1f}sn)"
            ))

        self.stdout.write("")
        ids, error = _fetch_provider_model_ids()
        if ids:
            self.stdout.write(self.style.SUCCESS(f"Saglayici model listesi: {len(ids)} model"))
            for mid in ids[:15]:
                self.stdout.write(f"  - {mid}")
            if len(ids) > 15:
                self.stdout.write(f"  ... ve {len(ids) - 15} model daha")

            self.stdout.write("")
            self.stdout.write("--- Tier dogrulama (ayarlardaki modeller katalogda var mi?) ---")
            tiers = getattr(settings, "NVIDIA_MODEL_TIERS", {}) or {}
            bad = 0
            for tier_name, info in tiers.items():
                mid = (info or {}).get("model_id")
                if not mid:
                    continue
                if mid in ids:
                    self.stdout.write(self.style.SUCCESS(f"  [OK]   {tier_name:<9} {mid}"))
                else:
                    bad += 1
                    self.stdout.write(self.style.ERROR(
                        f"  [YOK]  {tier_name:<9} {mid}  <- katalogda degil, 404 doner!"
                    ))
            fastest = getattr(settings, "FASTEST_MODEL", "")
            if fastest and fastest not in ids:
                self.stdout.write(self.style.ERROR(
                    f"Varsayilan/en hizli model katalogda degil: {fastest}"
                ))
            if bad == 0:
                self.stdout.write(self.style.SUCCESS("Tum tier modelleri katalogda var."))
            self.stdout.write(self.style.WARNING(
                "Not: katalogda olmak yeterli degil - model hesabiniza deploy edilmisse "
                "cagrilabilir olur. Canli dogrulama icin: python manage.py check_provider --probe"
            ))
        else:
            self.stdout.write(self.style.WARNING(f"Saglayici model listesi alinamadi: {error}"))
            self.stdout.write("Bu durumda model listesi bos gosterilir (sahte liste gosterilmez).")

        if options.get("probe"):
            self.stdout.write("")
            self.stdout.write("--- Canli model dogrulamasi (kucuk istek gonderiliyor) ---")
            self._probe_models(base, api_key)

    def _probe_models(self, base, api_key):
        """Her ayarli modele kucuk bir chat istegi gonderip olcer."""
        from openai import OpenAI

        tiers = getattr(settings, "NVIDIA_MODEL_TIERS", {}) or {}
        unique = []
        for tier_name, info in tiers.items():
            mid = (info or {}).get("model_id")
            if mid and mid not in [u[1] for u in unique]:
                unique.append((tier_name, mid))

        if not unique:
            self.stdout.write(self.style.WARNING("Ayarlandirilmis model yok."))
            return

        client = OpenAI(base_url=base, api_key=api_key or "none", timeout=45, max_retries=0)
        working = []
        for tier_name, mid in unique:
            started = time.time()
            try:
                client.chat.completions.create(
                    model=mid,
                    messages=[{"role": "user", "content": "Say OK"}],
                    max_tokens=8,
                    temperature=0,
                )
                elapsed = time.time() - started
                working.append((elapsed, mid))
                self.stdout.write(self.style.SUCCESS(
                    f"  [CALISIYOR] {tier_name:<9} {mid}  ({elapsed:.2f}s)"
                ))
            except Exception as exc:
                elapsed = time.time() - started
                msg = str(exc).replace("\n", " ")[:90]
                self.stdout.write(self.style.ERROR(
                    f"  [HATA]     {tier_name:<9} {mid}  ({elapsed:.2f}s) {msg}"
                ))

        self.stdout.write("")
        if working:
            working.sort()
            self.stdout.write(self.style.SUCCESS(
                "Hiza gore (en hizli once): " + ", ".join(f"{m} ({t:.2f}s)" for t, m in working)
            ))
        else:
            self.stdout.write(self.style.ERROR("Ayarlanan hicbir model cagrilabilir degil."))


