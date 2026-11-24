"""
Admin dashboard: kullanıcı/ziyaretçi analitiği.

Topladığı veriler:
  - toplam istek, API istek sayısı, hata oranı, ortalama yanıt süresi
  - saatlik/günlük istek grafiği
  - ülke, dil, cihaz, tarayıcı dağılımı
  - ekran genişliği dağılımı
  - en çok istek atan kullanıcılar / IP'ler / endpoint'ler
  - şu anda online oturumlar
"""
from datetime import timedelta

from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from audit.models import AuditLog, VisitorSession
from audit.tracking import SCREEN_BUCKETS


def _pct(value, total):
    if not total:
        return 0.0
    return round((value / total) * 100, 1)


def _named_counts(qs, field, limit=8):
    rows = list(qs.values(field).annotate(total=Count("id")).order_by("-total")[:limit])
    return [
        {"name": row[field] or "Bilinmiyor", "total": row["total"]}
        for row in rows
    ]


def screen_distribution(sessions):
    buckets = {}
    for session in sessions:
        width = session.screen_width
        label = "Bilinmiyor"
        for limit, name in SCREEN_BUCKETS:
            if width and width <= limit:
                label = name
                break
        buckets[label] = buckets.get(label, 0) + 1

    ordered = []
    for _, label in SCREEN_BUCKETS:
        ordered.append({"name": label, "total": buckets.get(label, 0)})
    if buckets.get("Bilinmiyor"):
        ordered.append({"name": "Bilinmiyor", "total": buckets["Bilinmiyor"]})
    return ordered


def build_dashboard(hours=24):
    now = timezone.now()
    since = now - timedelta(hours=hours)

    logs = AuditLog.objects.filter(created_at__gte=since)
    sessions_qs = VisitorSession.objects.filter(last_seen__gte=since)

    total_requests = logs.count()
    total_api = logs.filter(is_api=True).count()
    total_errors = logs.filter(response_status__gte=400).count()

    avg_ms = logs.aggregate(v=Avg("response_time_ms"))["v"] or 0
    unique_ips = logs.values("ip_address").distinct().count()
    unique_users = logs.exclude(user__isnull=True).values("user").distinct().count()
    unique_sessions = sessions_qs.count()

    # Saatlik dağılım
    hourly = (
        logs.annotate(bucket=TruncHour("created_at"))
        .values("bucket")
        .annotate(
            total=Count("id"),
            api=Count("id", filter=Q(is_api=True)),
            errors=Count("id", filter=Q(response_status__gte=400)),
        )
        .order_by("bucket")
    )
    timeline = [
        {
            "label": timezone.localtime(row["bucket"]).strftime("%H:%M"),
            "total": row["total"],
            "api": row["api"],
            "errors": row["errors"],
        }
        for row in hourly
    ]

    countries = _named_counts(
        logs.exclude(country="").values("country"), "country", limit=10
    )
    languages = _named_counts(
        logs.exclude(language="").values("language"), "language", limit=10
    )
    devices = _named_counts(
        logs.exclude(device="").values("device"), "device", limit=8
    )
    browsers = _named_counts(
        sessions_qs.exclude(browser="").values("browser"), "browser", limit=8
    )
    operating_systems = _named_counts(
        sessions_qs.exclude(os="").values("os"), "os", limit=8
    )
    methods = _named_counts(logs.values("request_method"), "request_method", limit=6)
    statuses = (
        logs.exclude(response_status__isnull=True)
        .values("response_status")
        .annotate(total=Count("id"))
        .order_by("-total")[:8]
    )
    status_rows = [
        {
            "name": _status_label(row["response_status"]),
            "total": row["total"],
            "code": row["response_status"],
        }
        for row in statuses
    ]

    top_users = (
        logs.exclude(user_email="")
        .values("user_email")
        .annotate(
            total=Count("id"),
            api=Count("id", filter=Q(is_api=True)),
            errors=Count("id", filter=Q(response_status__gte=400)),
            last=Max("created_at"),
        )
        .order_by("-total")[:10]
    )
    top_users = [
        {
            "name": row["user_email"],
            "total": row["total"],
            "api": row["api"],
            "errors": row["errors"],
            "last": timezone.localtime(row["last"]).strftime("%d.%m.%Y %H:%M"),
        }
        for row in top_users
    ]

    top_endpoints = (
        logs.values("request_path")
        .annotate(
            total=Count("id"),
            api=Count("id", filter=Q(is_api=True)),
            errors=Count("id", filter=Q(response_status__gte=400)),
            avg_ms=Avg("response_time_ms"),
        )
        .order_by("-total")[:10]
    )
    top_endpoints = [
        {
            "name": row["request_path"],
            "total": row["total"],
            "api": row["api"],
            "errors": row["errors"],
            "avg_ms": int(row["avg_ms"] or 0),
        }
        for row in top_endpoints
    ]

    top_ips = (
        logs.exclude(ip_address__isnull=True)
        .values("ip_address")
        .annotate(total=Count("id"), country=Max("country"), last=Max("created_at"))
        .order_by("-total")[:10]
    )
    top_ips = [
        {
            "name": row["ip_address"],
            "total": row["total"],
            "country": row["country"] or "-",
            "last": timezone.localtime(row["last"]).strftime("%d.%m %H:%M"),
        }
        for row in top_ips
    ]

    slowest = (
        logs.exclude(response_time_ms__isnull=True)
        .values("request_path")
        .annotate(avg_ms=Avg("response_time_ms"), total=Count("id"))
        .order_by("-avg_ms")[:5]
    )
    slowest = [
        {
            "name": row["request_path"],
            "avg_ms": int(row["avg_ms"] or 0),
            "total": row["total"],
        }
        for row in slowest
    ]

    session_list = list(
        sessions_qs.order_by("-request_count")[:50]
    )
    online_threshold = now - timedelta(minutes=5)
    online_sessions = VisitorSession.objects.filter(last_seen__gte=online_threshold)

    return {
        "generated_at": timezone.localtime(now).strftime("%d.%m.%Y %H:%M:%S"),
        "window_hours": hours,
        "since": timezone.localtime(since).strftime("%d.%m.%Y %H:%M"),
        "kpi": {
            "total_requests": total_requests,
            "total_api": total_api,
            "total_errors": total_errors,
            "error_rate": _pct(total_errors, total_requests),
            "api_rate": _pct(total_api, total_requests),
            "avg_ms": int(avg_ms),
            "unique_ips": unique_ips,
            "unique_users": unique_users,
            "unique_sessions": unique_sessions,
            "online_now": online_sessions.count(),
        },
        "timeline": timeline,
        "countries": countries,
        "languages": languages,
        "devices": devices,
        "browsers": browsers,
        "operating_systems": operating_systems,
        "methods": methods,
        "statuses": status_rows,
        "screens": screen_distribution(session_list),
        "top_users": top_users,
        "top_endpoints": top_endpoints,
        "top_ips": top_ips,
        "slowest": slowest,
        "sessions": [
            {
                "user": (
                    session.user.get_username()
                    if session.user
                    else (session.app_user_id or "Anonim")
                ),
                "email": session.user.email if session.user else "",
                "ip": session.ip_address or "-",
                "country": session.country or "-",
                "language": session.language or "-",
                "device": session.device or "-",
                "os": session.os or "-",
                "browser": session.browser or "-",
                "screen_width": session.screen_width,
                "viewport_width": session.viewport_width,
                "touch": session.touch,
                "requests": session.request_count,
                "api": session.api_count,
                "errors": session.error_count,
                "first_seen": timezone.localtime(session.first_seen).strftime("%d.%m %H:%M"),
                "last_seen": timezone.localtime(session.last_seen).strftime("%d.%m %H:%M"),
                "online": session.last_seen >= online_threshold,
            }
            for session in session_list
        ],
    }


def _status_label(code):
    code = int(code)
    if code < 300:
        return f"{code} Başarılı"
    if code < 400:
        return f"{code} Yönlendirme"
    if code < 500:
        return f"{code} İstemci Hatası"
    return f"{code} Sunucu Hatası"


def max_value(rows):
    if not rows:
        return 1
    return max(row["total"] for row in rows) or 1