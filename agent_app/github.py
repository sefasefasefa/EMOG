"""GitHub entegrasyonu: device flow / OAuth, repo listesi, repo olusturma.

Token oturumda saklanir (session['github_token']).
Gereken ortam degiskenleri:
    GITHUB_CLIENT_ID
    GITHUB_CLIENT_SECRET   (OAuth web akisi icin; device flow icin gerekmez)
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

GITHUB_API = "https://api.github.com"
GITHUB_OAUTH_API = "https://github.com/login/device/code"
GITHUB_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEFAULT_SCOPE = "repo user read:org workflow"
SESSION_TOKEN_KEY = "github_token"
SESSION_DEVICE_KEY = "github_device"

# Device akisinda surekli deneme yapilmasin diye kisa bir bekleme suresi.
_POLL_MIN_INTERVAL = 3


def _client_id():
    return os.environ.get("GITHUB_CLIENT_ID", "") or getattr(settings, "GITHUB_CLIENT_ID", "")


def _client_secret():
    return os.environ.get("GITHUB_CLIENT_SECRET", "") or getattr(settings, "GITHUB_CLIENT_SECRET", "")


def _token(request):
    return request.session.get(SESSION_TOKEN_KEY) or ""


def _api(method, url, token=None, payload=None, headers=None, timeout=15):
    """GitHub API cagrisi. (ok, veri, hata_mesaji) doner."""
    data = None
    request_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "EMOG-Agent",
    }
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(524288).decode(errors="replace")
            try:
                return True, json.loads(body) if body.strip() else {}, None
            except Exception:
                return True, {"raw": body}, None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(512).decode(errors="replace").strip()
        except Exception:
            pass
        return False, None, f"GitHub HTTP {exc.code}: {detail or exc.reason}"
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


@csrf_exempt
def github_page(request):
    return render(request, "agent_app/github.html")


@csrf_exempt
def github_status(request):
    token = _token(request)
    if not token:
        return JsonResponse({"ok": False, "authenticated": False, "error": "GitHub bagli degil"})
    ok, data, error = _api("GET", f"{GITHUB_API}/user", token=token)
    if not ok:
        return JsonResponse({"ok": False, "authenticated": False, "error": error}, status=200)
    user = data if isinstance(data, dict) else {}
    return JsonResponse(
        {
            "ok": True,
            "authenticated": True,
            "user": {
                "login": user.get("login"),
                "name": user.get("name") or user.get("login"),
                "avatar_url": user.get("avatar_url"),
                "html_url": user.get("html_url"),
            },
        }
    )


@csrf_exempt
def github_repositories(request):
    token = _token(request)
    if not token:
        return JsonResponse({"ok": False, "error": "GitHub bagli degil"}, status=200)
    ok, data, error = _api(
        "GET",
        f"{GITHUB_API}/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member",
        token=token,
    )
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=200)
    repos = [
        {
            "full_name": r.get("full_name"),
            "private": r.get("private"),
            "description": r.get("description"),
            "html_url": r.get("html_url"),
            "default_branch": r.get("default_branch"),
            "language": r.get("language"),
            "stargazers_count": r.get("stargazers_count", 0),
            "forks_count": r.get("forks_count", 0),
            "updated_at": r.get("updated_at"),
        }
        for r in (data if isinstance(data, list) else [])
    ]
    return JsonResponse({"ok": True, "repositories": repos, "count": len(repos)})


@csrf_exempt
def github_device_start(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    client_id = _client_id()
    if not client_id:
        return JsonResponse(
            {"ok": False, "error": "GITHUB_CLIENT_ID tanimli degil (.env icine ekleyin)."}, status=400
        )

    scope = (request.POST.get("scope") or DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
    body = urllib.parse.urlencode({"client_id": client_id, "scope": scope}).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_OAUTH_API,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "EMOG-Agent"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode(errors="replace"))
    except urllib.error.HTTPError as exc:
        return JsonResponse({"ok": False, "error": f"GitHub HTTP {exc.code}: {exc.reason}"}, status=502)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=502)

    if "device_code" not in payload:
        return JsonResponse(
            {"ok": False, "error": payload.get("error_description") or payload.get("error") or "Device akisi baslatilamadi."},
            status=400,
        )

    request.session[SESSION_DEVICE_KEY] = {
        "device_code": payload["device_code"],
        "interval": int(payload.get("interval") or _POLL_MIN_INTERVAL),
        "last_poll": 0.0,
    }
    return JsonResponse(
        {
            "ok": True,
            "user_code": payload.get("user_code"),
            "verification_uri": payload.get("verification_uri"),
            "expires_in": int(payload.get("expires_in") or 900),
            "interval": int(payload.get("interval") or _POLL_MIN_INTERVAL),
        }
    )


@csrf_exempt
def github_device_poll(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    state = request.session.get(SESSION_DEVICE_KEY) or {}
    device_code = state.get("device_code")
    if not device_code:
        return JsonResponse({"ok": False, "authorized": False, "error": "Aktif device akisi yok."}, status=400)

    interval = max(_POLL_MIN_INTERVAL, int(state.get("interval") or _POLL_MIN_INTERVAL))
    now = time.time()
    wait = interval - (now - float(state.get("last_poll") or 0))
    if wait > 0:
        return JsonResponse({"ok": True, "authorized": False, "interval": int(wait) + 1})
    state["last_poll"] = now
    request.session[SESSION_DEVICE_KEY] = state

    client_id = _client_id()
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_DEVICE_TOKEN_URL,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "EMOG-Agent"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode(errors="replace"))
    except Exception as exc:
        return JsonResponse(
            {"ok": False, "authorized": False, "error": f"{type(exc).__name__}: {exc}", "interval": interval},
            status=502,
        )

    if payload.get("access_token"):
        request.session[SESSION_TOKEN_KEY] = payload["access_token"]
        request.session.pop(SESSION_DEVICE_KEY, None)
        request.session.modified = True
        return JsonResponse({"ok": True, "authorized": True, "interval": 0})

    error = payload.get("error") or "authorization_slow_down"
    message = payload.get("error_description") or error
    return JsonResponse({"ok": True, "authorized": False, "error": message, "interval": interval})


@csrf_exempt
def github_oauth_start(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    client_id = _client_id()
    if not client_id:
        return JsonResponse(
            {"ok": False, "error": "GITHUB_CLIENT_ID tanimli degil (.env icine ekleyin)."}, status=400
        )
    scope = (request.POST.get("scope") or DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
    params = {"client_id": client_id, "scope": scope}
    return JsonResponse(
        {"ok": True, "auth_url": f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"}
    )


@csrf_exempt
def github_disconnect(request):
    request.session.pop(SESSION_TOKEN_KEY, None)
    request.session.pop(SESSION_DEVICE_KEY, None)
    request.session.modified = True
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@csrf_exempt
def github_create(request):
    token = _token(request)
    if not token:
        return JsonResponse({"ok": False, "error": "GitHub bagli degil"}, status=200)

    name = (request.POST.get("repo_name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "repo_name gerekli"}, status=400)

    payload = {"name": name, "private": request.POST.get("private") in ("1", "true", "on", "yes")}
    description = (request.POST.get("description") or "").strip()
    if description:
        payload["description"] = description
    if request.POST.get("auto_init") in ("1", "true", "on", "yes"):
        payload["auto_init"] = True

    ok, data, error = _api("POST", f"{GITHUB_API}/user/repos", token=token, payload=payload)
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=400)
    return JsonResponse({"ok": True, "repository": data})
