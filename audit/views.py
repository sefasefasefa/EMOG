"""
Tarayıcı istemci metadatası (ekran genişliği, dil, cihaz) toplama endpoint'i.
"""
import json
import logging

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _int(value, maximum=20000):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > maximum:
        return None
    return number


def _bool(value):
    return str(value).lower() in ("true", "1", "yes", "on")


@csrf_exempt
@require_POST
def track_client(request):
    """Tarayıcıdan gelen ekran/dil bilgisini mevcut oturuma yazar."""
    from audit.models import VisitorSession
    from audit.tracking import extract_profile

    try:
        raw = request.body.decode("utf-8")[:4000]
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    profile = extract_profile(request)
    now = timezone.now()

    session = VisitorSession.objects.filter(session_key=profile["session_key"]).first()
    if session is None:
        session = VisitorSession.objects.create(
            session_key=profile["session_key"],
            ip_address=profile["ip_address"],
            country=profile["country"],
            language=profile["language"],
            device=profile["device"],
            os=profile["os"],
            browser=profile["browser"],
            user_agent=profile["user_agent"],
            referer=request.META.get("HTTP_REFERER", "")[:500],
            first_seen=now,
            last_seen=now,
        )

    fields = ["last_seen"]

    screen_width = _int(data.get("screen_width"))
    screen_height = _int(data.get("screen_height"))
    viewport_width = _int(data.get("viewport_width"))

    if screen_width:
        session.screen_width = screen_width
        fields.append("screen_width")
    if screen_height:
        session.screen_height = screen_height
        fields.append("screen_height")
    if viewport_width:
        session.viewport_width = viewport_width
        fields.append("viewport_width")
    if data.get("pixel_ratio") is not None:
        try:
            session.pixel_ratio = round(float(data.get("pixel_ratio")), 3)
            fields.append("pixel_ratio")
        except (TypeError, ValueError):
            pass
    if _bool(data.get("touch")):
        session.touch = True
        fields.append("touch")

    client_lang = str(data.get("language") or "").strip()[:16]
    if client_lang:
        session.language = client_lang.split("-")[0].lower()
        fields.append("language")

    session.save(update_fields=list(dict.fromkeys(fields)))

    return JsonResponse({"ok": True, "screen_width": session.screen_width})