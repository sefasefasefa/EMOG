"""NVIDIA (integrate.api.nvidia.com) icin dayanikli ag katmani.

Bu makinenin aginda NVIDIA ucuna giden TLS el sikismalari zaman zaman
dusurulur (ClientHello gonderilir, yanit hic gelmez) ve DNS'in dondurdugu
bu IP'lerden biri calisirken digeri engellenebilir. Bu modul:

* aday IP'leri DNS ile bulur ve "son calisan" IP'yi onde tutar,
* TCP + TLS denemesini kisa zaman asimiyla yapar; bir IP basarisizsa
  diger aday IP'ye gecer (SNI/sertifika dogrulamasi hostname ile yapilir),
* provider_reachable icin paralel ve hizli yoklama saglar.

Baglanti sadece bu modul uzerinden yapildiginda:
  - kapali pencerede ~8sn icinde net hata doner (120sn beklemez),
  - acik pencerede ekstra maliyet yok (dogrudan calisan IP'ye baglanir).
"""

from __future__ import annotations

import logging
import select
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpcore
import httpx

logger = logging.getLogger(__name__)

DEFAULT_PORT = 443
TCP_TIMEOUT = 4.0
TLS_TIMEOUT = 4.0
RESOLVE_TTL = 300.0
ORDER_TTL = 120.0
PROBE_TIMEOUT = 4.0

DEFAULT_CLIENT_TIMEOUT = httpx.Timeout(
    connect=8.0,
    read=110.0,
    write=30.0,
    pool=10.0,
)

_ssl_ctx = ssl.create_default_context()

_resolve_lock = threading.Lock()
_resolved: dict[str, tuple[float, list[str]]] = {}
_order_lock = threading.Lock()
_order: dict[str, tuple[float, list[str]]] = {}


def resolve_ips(host: str) -> list[str]:
    """host icin IPv4 adaylarini dondurur (TTL cache'li)."""
    now = time.monotonic()
    cached = _resolved.get(host)
    if cached and cached[1] and (now - cached[0]) < RESOLVE_TTL:
        return list(cached[1])
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        logger.debug("DNS cozulemedi %s: %s", host, exc)
        return list(cached[1]) if cached else []
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    with _resolve_lock:
        _resolved[host] = (now, ips)
    return ips


def _candidate_ips(host: str) -> list[str]:
    """Denenecek IP sirasi: son calisan IP en onde."""
    ips = resolve_ips(host)
    if not ips:
        return []
    now = time.monotonic()
    with _order_lock:
        entry = _order.get(host)
    if entry and (now - entry[0]) < ORDER_TTL:
        known = [ip for ip in entry[1] if ip in ips]
        rest = [ip for ip in ips if ip not in known]
        return known + rest
    return ips


def _mark_good(host: str, ip: str) -> None:
    with _order_lock:
        others = [x for x in resolve_ips(host) if x != ip]
        _order[host] = (time.monotonic(), [ip] + others)


def _cap(timeout: float | None, cap: float) -> float:
    if timeout is None:
        return cap
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return cap
    if value <= 0:
        return cap
    return min(value, cap)


def probe_tls(ip: str, host: str, port: int = DEFAULT_PORT, timeout: float = PROBE_TIMEOUT) -> bool:
    """ip:port uzerinden host icin tam TLS el sikismasi yapar."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            raw.settimeout(timeout)
            with _ssl_ctx.wrap_socket(raw, server_hostname=host) as tls:
                return tls.version() is not None
    except Exception:
        return False


def probe_host(host: str, port: int = DEFAULT_PORT, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Bir hostun tum adaylarini paralel yoklar. (ok, detay) dondurur."""
    ips = _candidate_ips(host) or resolve_ips(host)
    if not ips:
        return False, f"{host}: DNS cozulemedi"
    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(ips))) as pool:
        futures = {pool.submit(probe_tls, ip, host, port, timeout): ip for ip in ips}
        for future in futures:
            results[futures[future]] = future.result()
    working = [ip for ip in ips if results.get(ip)]
    if working:
        _mark_good(host, working[0])
        return True, f"{host}: {len(working)}/{len(ips)} IP TLS OK"
    return False, f"{host}: {len(ips)}/{len(ips)} IP TLS basarisiz (zaman asimi)"


def probe_endpoints(
    endpoints: list[str],
    timeout: float = PROBE_TIMEOUT,
    attempts: int = 2,
    gap: float = 1.0,
) -> tuple[bool, str]:
    """Birden fazla base URL'i paralel yoklar. (ok, detay) dondurur.

    TLS el sikismasi kisa surede biterse endpoint erisilebilir sayilir;
    kisa sureli kesintilerde `attempts` kez tekrar deneyerek toparlanir.
    """
    hosts: list[tuple[str, int]] = []
    for raw in endpoints:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = parsed.hostname or ""
        port = parsed.port or (DEFAULT_PORT if (parsed.scheme or "https") == "https" else 80)
        if host and (host, port) not in hosts:
            hosts.append((host, port))
    if not hosts:
        return False, "BASE_URL bos."

    last_detail = ""
    for attempt in range(max(1, attempts)):
        with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
            futures = {pool.submit(probe_host, h, p, timeout): (h, p) for h, p in hosts}
            results = [(futures[f], f.result()) for f in futures]
        for (host, port), (ok, detail) in results:
            if ok:
                return True, detail
            last_detail = detail
        if attempt < attempts - 1:
            time.sleep(gap)
    return False, last_detail


def _close_quietly(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        pass


def _is_readable(sock: socket.socket) -> bool:
    try:
        return bool(select.select([sock], [], [], 0)[0])
    except Exception:
        return False


class _Stream:
    """httpcore.NetworkStream uyumlu; TLS basarisizsa aday IP'ler arasinda gecis yapar."""

    def __init__(
        self,
        sock: socket.socket,
        host: str,
        port: int,
        peer: str,
        pending: list[str],
        handshaked: bool = False,
    ) -> None:
        self._sock = sock
        self._host = host
        self._port = port
        self._peer = peer
        self._pending = list(pending)
        self._handshaked = handshaked

    # -- NetworkStream arayuzu ------------------------------------------------
    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            self._sock.settimeout(timeout)
            return self._sock.recv(max_bytes)
        except socket.timeout as exc:
            raise httpcore.ReadTimeout(str(exc) or "read timed out") from exc
        except OSError as exc:
            raise httpcore.ReadError(str(exc) or "read error") from exc

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        try:
            while buffer:
                self._sock.settimeout(timeout)
                sent = self._sock.send(buffer)
                buffer = buffer[sent:]
        except socket.timeout as exc:
            raise httpcore.WriteTimeout(str(exc) or "write timed out") from exc
        except OSError as exc:
            raise httpcore.WriteError(str(exc) or "write error") from exc

    def close(self) -> None:
        _close_quietly(self._sock)

    def get_extra_info(self, info: str):
        if info == "ssl_object" and isinstance(self._sock, ssl.SSLSocket):
            return self._sock._sslobj
        if info == "socket":
            return self._sock
        if info == "client_addr":
            try:
                return self._sock.getsockname()
            except OSError:
                return None
        if info == "server_addr":
            try:
                return self._sock.getpeername()
            except OSError:
                return None
        if info == "is_readable":
            return _is_readable(self._sock)
        return None

    # -- TLS ------------------------------------------------------------------
    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> "_Stream":
        if self._handshaked:
            return self

        tls_timeout = _cap(timeout, TLS_TIMEOUT)
        errors: list[str] = []

        # Once zaten acik olan TCP baglantisi ile dene.
        try:
            self._sock.settimeout(tls_timeout)
            tls = ssl_context.wrap_socket(self._sock, server_hostname=server_hostname)
            tls.settimeout(None)
            _mark_good(self._host, self._peer)
            return _Stream(tls, self._host, self._port, self._peer, [], handshaked=True)
        except Exception as exc:
            errors.append(f"{self._peer}: {type(exc).__name__}")
            _close_quietly(self._sock)
            self._sock = None  # type: ignore[assignment]

        # Kalan aday IP'leri tek tek TCP+TLS ile dene.
        for ip in self._pending:
            sock = None
            try:
                sock = socket.create_connection((ip, self._port), timeout=tcp_timeout(tls_timeout))
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(tls_timeout)
                tls = ssl_context.wrap_socket(sock, server_hostname=server_hostname)
                tls.settimeout(None)
                _mark_good(self._host, ip)
                logger.info("TLS failover: %s -> %s", self._host, ip)
                return _Stream(tls, self._host, self._port, ip, [], handshaked=True)
            except Exception as exc:
                errors.append(f"{ip}: {type(exc).__name__}")
                _close_quietly(sock)

        detail = "; ".join(errors[:4])
        raise httpcore.ConnectTimeout(
            f"TLS handshake tum adaylarda basarisiz ({self._host}): {detail}"
        )


def tcp_timeout(timeout: float | None) -> float:
    return _cap(timeout, TCP_TIMEOUT)


class ResilientBackend(httpcore.SyncBackend):
    """Aday IP'ler arasinda gecis yapan network backend.

    TCP baglantisini aday IP'ler sirayla dener; TLS el sikismasi sonraki
    asamada (_Stream.start_tls) sirayla devam eder.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> _Stream:
        source_address = None if local_address is None else (local_address, 0)
        connect_timeout = tcp_timeout(timeout)
        candidates = _candidate_ips(host) or ([host] if host else [])
        if not candidates:
            raise httpcore.ConnectError(f"DNS cozulemedi: {host}")

        last_exc: Exception | None = None
        for idx, target in enumerate(candidates):
            sock = None
            try:
                sock = socket.create_connection(
                    (target, port), timeout=connect_timeout, source_address=source_address
                )
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for option in socket_options or ():
                    try:
                        sock.setsockopt(*option)
                    except OSError:
                        pass
                pending = [ip for ip in candidates[idx + 1:] if ip != target]
                return _Stream(sock, host, port, target, pending)
            except OSError as exc:
                last_exc = exc
                _close_quietly(sock)

        # TCP duzulmediyse DNS'e gitmeyi de bir kez dene (IP cache'i taze olabilir).
        if candidates and all(c != host for c in candidates) and last_exc is not None:
            try:
                sock = socket.create_connection((host, port), timeout=connect_timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return _Stream(sock, host, port, host, [])
            except OSError:
                pass

        raise httpcore.ConnectError(
            f"TCP baglanamadi {host}:{port} ({len(candidates)} IP): "
            f"{type(last_exc).__name__ if last_exc else 'bilinmiyor'}"
        )

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix socket desteklenmiyor.")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def make_transport() -> httpx.HTTPTransport:
    """IP failover backend'i takili httpx transport olusturur."""
    transport = httpx.HTTPTransport(retries=1)
    pool = getattr(transport, "_pool", None)
    if pool is not None and hasattr(pool, "_network_backend"):
        pool._network_backend = ResilientBackend()
    else:  # pragma: no cover - beklenmeyen httpcore surumu
        logger.warning("ResilientBackend takilamadi; varsayilan HTTP transport kullanilacak.")
    return transport


def make_http_client(timeout: httpx.Timeout | float | None = None) -> httpx.Client:
    return httpx.Client(
        transport=make_transport(),
        timeout=DEFAULT_CLIENT_TIMEOUT if timeout is None else timeout,
    )


def invalidate(host: str | None = None) -> None:
    """DNS/sira cache'ini temizle (test ve yeniden deneme icin)."""
    with _resolve_lock:
        if host is None:
            _resolved.clear()
        else:
            _resolved.pop(host, None)
    with _order_lock:
        if host is None:
            _order.clear()
        else:
            _order.pop(host, None)
