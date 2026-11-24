import os
import json
import secrets
import hashlib
import hmac
import time
from urllib.parse import urlencode
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.middleware.csrf import get_token
from django.db import transaction
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
import requests

from .models import User as DBUser
from . import tokens as token_manager
from . import rate_limit as limiter

# Cookie settings
COOKIE_SECURE = not settings.DEBUG
COOKIE_SAMESITE = 'Lax'

def send_verification_email(user):
    """Send email verification link to user"""
    token = secrets.token_urlsafe(32)
    user.email_verification_token = token
    user.email_verification_sent_at = timezone.now()
    user.save(update_fields=['email_verification_token', 'email_verification_sent_at'])
    
    verification_url = f"{settings.FRONTEND_URL}/auth/verify-email/?token={token}"
    
    subject = 'Verify your email address - EMOG'
    message = f'''
Hi {user.username or user.email},

Welcome to EMOG! Please verify your email address by clicking the link below:

{verification_url}

This link will expire in 24 hours.

If you didn't create an account, you can safely ignore this email.

Best regards,
The EMOG Team
'''
    
    try:
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Failed to send verification email: {e}")
        return False

OAUTH_PROVIDERS = {
    'google': {
        'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
        'token_url': 'https://oauth2.googleapis.com/token',
        'user_url': 'https://www.googleapis.com/oauth2/v2/userinfo',
        'scopes': ['openid', 'email', 'profile'],
        'client_id': os.getenv('GOOGLE_CLIENT_ID'),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
    },
    'github': {
        'auth_url': 'https://github.com/login/oauth/authorize',
        'token_url': 'https://github.com/login/oauth/access_token',
        'user_url': 'https://api.github.com/user',
        'scopes': ['read:user', 'user:email'],
        'client_id': os.getenv('GITHUB_CLIENT_ID'),
        'client_secret': os.getenv('GITHUB_CLIENT_SECRET'),
    },
    'apple': {
        'auth_url': 'https://appleid.apple.com/auth/authorize',
        'token_url': 'https://appleid.apple.com/auth/token',
        'user_url': 'https://appleid.apple.com/auth/userinfo',
        'scopes': ['name', 'email'],
        'client_id': os.getenv('APPLE_CLIENT_ID'),
        'client_secret': os.getenv('APPLE_CLIENT_SECRET'),
    },
    'facebook': {
        'auth_url': 'https://www.facebook.com/v18.0/dialog/oauth',
        'token_url': 'https://graph.facebook.com/v18.0/oauth/access_token',
        'user_url': 'https://graph.facebook.com/v18.0/me',
        'scopes': ['email', 'public_profile'],
        'client_id': os.getenv('FACEBOOK_CLIENT_ID'),
        'client_secret': os.getenv('FACEBOOK_CLIENT_SECRET'),
    },
}

def get_oauth_config(provider):
    config = OAUTH_PROVIDERS.get(provider)
    if not config or not config['client_id'] or not config['client_secret']:
        return None
    return config

def hash_password(password):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), settings.SECRET_KEY.encode()[:32], 100000).hex()

def verify_password(password, password_hash):
    return hmac.compare_digest(hash_password(password), password_hash)

def create_session_token(user):
    token = secrets.token_urlsafe(32)
    user.meta = user.meta or {}
    user.meta['session_token'] = token
    user.meta['session_created'] = timezone.now().isoformat()
    user.save(update_fields=['meta'])
    return token

def get_user_from_token(token):
    try:
        return DBUser.objects.get(meta__session_token=token)
    except DBUser.DoesNotExist:
        return None

def delete_session_token(token):
    try:
        user = DBUser.objects.get(meta__session_token=token)
        meta = user.meta or {}
        meta.pop('session_token', None)
        meta.pop('session_created', None)
        user.meta = meta
        user.save(update_fields=['meta'])
    except DBUser.DoesNotExist:
        pass

def get_or_create_user(provider, provider_id, email, username, full_name, avatar_url):
    user = DBUser.objects.filter(provider=provider, provider_id=provider_id).first()
    
    if user:
        user.email = email
        user.username = username
        user.full_name = full_name
        user.avatar_url = avatar_url
        user.updated_at = timezone.now()
        user.save()
        return user
    
    user = DBUser.objects.filter(email=email).first()
    if user:
        user.provider = provider
        user.provider_id = provider_id
        user.username = username
        user.full_name = full_name
        user.avatar_url = avatar_url
        user.updated_at = timezone.now()
        user.save()
        return user
    
    user = DBUser.objects.create(
        email=email,
        username=username,
        provider=provider,
        provider_id=provider_id,
        full_name=full_name,
        avatar_url=avatar_url,
        password_hash=None,
        is_active=True,
        continue_in_background=(str(username).strip().lower() == "whoches"),
    )
    return user

def login_page(request):
    csrf_token = get_token(request)
    return render(request, 'agent_app/login.html', {'csrf_token': csrf_token})

def register_page(request):
    csrf_token = get_token(request)
    return render(request, 'agent_app/register.html', {'csrf_token': csrf_token})

@csrf_exempt
@require_http_methods(["POST"])
def oauth_login(request, provider):
    config = get_oauth_config(provider)
    if not config:
        return JsonResponse({'error': 'Provider not configured'}, status=400)
    
    state = secrets.token_urlsafe(32)
    request.session[f'oauth_state_{provider}'] = state
    
    params = {
        'client_id': config['client_id'],
        'redirect_uri': request.build_absolute_uri(f'/auth/oauth/callback/{provider}/'),
        'scope': ' '.join(config['scopes']),
        'response_type': 'code',
        'state': state,
        'access_type': 'offline',
        'prompt': 'consent',
    }
    
    auth_url = f"{config['auth_url']}?{urlencode(params)}"
    return JsonResponse({'auth_url': auth_url})

@csrf_exempt
def oauth_callback(request, provider):
    config = get_oauth_config(provider)
    if not config:
        return render(request, 'agent_app/oauth_error.html', {'error': 'Provider not configured'})
    
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')
    
    if error:
        return render(request, 'agent_app/oauth_error.html', {'error': error})
    
    stored_state = request.session.pop(f'oauth_state_{provider}', None)
    if not stored_state or stored_state != state:
        return render(request, 'agent_app/oauth_error.html', {'error': 'Invalid state parameter'})
    
    if not code:
        return render(request, 'agent_app/oauth_error.html', {'error': 'No authorization code received'})
    
    token_data = {
        'client_id': config['client_id'],
        'client_secret': config['client_secret'],
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': request.build_absolute_uri(f'/auth/oauth/callback/{provider}/'),
    }
    
    headers = {'Accept': 'application/json'}
    if provider == 'github':
        headers['Accept'] = 'application/json'
    
    try:
        token_resp = requests.post(config['token_url'], data=token_data, headers=headers, timeout=10)
        token_resp.raise_for_status()
        token_json = token_resp.json()
        
        access_token = token_json.get('access_token')
        if not access_token:
            return render(request, 'agent_app/oauth_error.html', {'error': 'Failed to get access token'})
        
        user_headers = {'Authorization': f'Bearer {access_token}'}
        if provider == 'github':
            user_headers['Accept'] = 'application/vnd.github.v3+json'
        
        user_resp = requests.get(config['user_url'], headers=user_headers, timeout=10)
        user_resp.raise_for_status()
        user_data = user_resp.json()
        
        if provider == 'google':
            provider_id = user_data.get('id')
            email = user_data.get('email')
            full_name = user_data.get('name')
            username = email.split('@')[0] if email else f'google_{provider_id}'
            avatar_url = user_data.get('picture')
        elif provider == 'github':
            provider_id = str(user_data.get('id'))
            email = user_data.get('email')
            if not email:
                emails_resp = requests.get('https://api.github.com/user/emails', headers=user_headers, timeout=10)
                emails = emails_resp.json()
                primary_email = next((e['email'] for e in emails if e['primary']), None)
                email = primary_email
            full_name = user_data.get('name') or user_data.get('login')
            username = user_data.get('login')
            avatar_url = user_data.get('avatar_url')
        elif provider == 'apple':
            provider_id = user_data.get('sub')
            email = user_data.get('email')
            full_name = user_data.get('name', {}).get('firstName', '') + ' ' + user_data.get('name', {}).get('lastName', '')
            username = email.split('@')[0] if email else f'apple_{provider_id}'
            avatar_url = None
        elif provider == 'facebook':
            provider_id = user_data.get('id')
            email = user_data.get('email')
            full_name = user_data.get('name')
            username = email.split('@')[0] if email else f'fb_{provider_id}'
            avatar_url = f"https://graph.facebook.com/{provider_id}/picture?type=large"
        else:
            return render(request, 'agent_app/oauth_error.html', {'error': 'Unknown provider'})
        
        user = get_or_create_user(provider, provider_id, email, username, full_name, avatar_url)
        
        # Kısa ömürlü access + yenilenen refresh token
        access, refresh = issue_token_pair(user, request)
        legacy = create_session_token(user)
        
        response = HttpResponseRedirect('/chat/')
        token_manager.set_auth_cookies(response, access, refresh)
        response.delete_cookie('auth_token')
        return response
        
    except requests.RequestException as e:
        return render(request, 'agent_app/oauth_error.html', {'error': f'Network error: {str(e)}'})
    except Exception as e:
        return render(request, 'agent_app/oauth_error.html', {'error': f'Authentication failed: {str(e)}'})

@csrf_exempt
@require_http_methods(["POST"])
def register(request):
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    email = data.get('email', '').strip().lower()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not email or not username or not password:
        return JsonResponse({'error': 'Email, username, and password are required'}, status=400)
    
    if len(password) < 8:
        return JsonResponse({'error': 'Password must be at least 8 characters'}, status=400)

    # ---- KAYIT DENEME SINIRI (spam / kayit bombasi korumasi) ----
    client = limiter.client_ip(request)
    if limiter.enabled_for("register"):
        allowed, wait_seconds, _ = limiter.check_login_allowed(f"register:{email}", client)
        if not allowed:
            return limiter.lockout_response(wait_seconds, email)

    if DBUser.objects.filter(email=email).exists():
        limiter.register_failure(f"register:{email}", client)
        return JsonResponse({'error': 'Email already registered'}, status=400)
    
    if DBUser.objects.filter(username=username).exists():
        return JsonResponse({'error': 'Username already taken'}, status=400)
    
    user = DBUser.objects.create(
        email=email,
        username=username,
        password_hash=hash_password(password),
        full_name=username,
        provider='local',
        provider_id=None,
        is_active=True,
        email_verified=False,
    )
    
    # Send verification email
    send_verification_email(user)
    
    return JsonResponse({
        'success': True, 
        'message': 'Registration successful! Please check your email to verify your account.',
        'user': {'id': str(user.id), 'email': user.email, 'username': user.username}
    })

@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return JsonResponse({'error': 'Email and password are required'}, status=400)

    # ---- DENEME SINIRI (brute force koruması) ----
    client = limiter.client_ip(request)
    allowed, wait_seconds, remaining = limiter.check_login_allowed(email, client)

    if not allowed:
        limiter.record_security_event(email, client, limiter.get_fail_count(email, client), wait_seconds)
        return limiter.lockout_response(wait_seconds, email)

    invalid_response = {
        'error': 'Invalid credentials',
        'attempts_remaining': max(remaining - 1, 0),
        'attempts_remaining_count': max(remaining, 0),
    }

    try:
        user = DBUser.objects.get(email=email)
    except DBUser.DoesNotExist:
        count, lockout = limiter.register_failure(email, client)
        limiter.apply_delay(lockout)
        invalid_response['attempts_remaining_count'] = max(5 - count, 0)
        if lockout >= 5:
            invalid_response['retry_after'] = lockout
        return JsonResponse(invalid_response, status=401)
    
    if not user.password_hash or not verify_password(password, user.password_hash):
        count, lockout = limiter.register_failure(email, client)
        limiter.apply_delay(lockout)
        invalid_response['attempts_remaining_count'] = max(5 - count, 0)
        if lockout >= 5:
            invalid_response['retry_after'] = lockout
        if lockout >= 300:
            limiter.record_security_event(email, client, count, lockout)
        return JsonResponse(invalid_response, status=401)

    # ---- BAŞARILI GİRİŞ ----
    limiter.register_success(email, client)

    if not user.is_active:
        return JsonResponse({'error': 'Account is deactivated'}, status=403)

    if not user.email_verified:
        limiter.register_success(f"register:{email}", client)
        return JsonResponse({
            'error': 'Please verify your email address before logging in.',
            'email_verified': False,
            'resend_verification': True
        }, status=403)
    
    limiter.register_success(f"register:{email}", client)
    access, refresh = issue_token_pair(user, request)
    
    response = JsonResponse({
        'success': True,
        'user': {
            'id': str(user.id), 'email': user.email, 'username': user.username,
            'continue_in_background': bool(user.continue_in_background),
        },
        'token_info': {
            'access_expires_in': token_manager.access_token_remaining_seconds(),
            'ttl_minutes': int(token_manager.access_ttl().total_seconds() // 60),
            'refresh_rotation': True,
        },
    })
    token_manager.set_auth_cookies(response, access, refresh)
    # Eski oturum token'ını da temizle (geriye dönük uyum)
    legacy = request.COOKIES.get('auth_token')
    if legacy:
        delete_session_token(legacy)
    response.delete_cookie('auth_token')
    return response


def issue_token_pair(user, request=None):
    """Yeni access + refresh token çifti üretir."""
    access = token_manager.create_access_token(user, request=request)
    refresh, _ = token_manager.issue_refresh_token(user, request=request)
    return access, refresh


@csrf_exempt
@require_http_methods(["POST"])
def refresh_token(request):
    """
    Refresh token ile yeni access token alır ve refresh token'ı yeniler (rotation).

    Access token kısa ömürlüdür (varsayılan 15 dk). Süresi dolduğunda
    frontend bu endpoint'i çağırır; eski refresh token iptal edilir,
    yenisi verilir.
    """
    _, refresh = token_manager.read_tokens(request)

    if not refresh:
        return JsonResponse({'error': 'Refresh token bulunamadı'}, status=401)

    # ---- DENEME SINIRI (token ile yapılan deneme bombasi koruması) ----
    client = limiter.client_ip(request)
    token_hint = refresh[-12:] if refresh else ""
    allowed, wait_seconds, _ = limiter.check_login_allowed(f"refresh:{token_hint}", client)
    if not allowed:
        return limiter.lockout_response(wait_seconds, "refresh")

    access, new_refresh, error = token_manager.rotate_refresh_token(refresh, request=request)

    if error:
        count, lockout = limiter.register_failure(f"refresh:{token_hint}", client)
        limiter.apply_delay(lockout)
        messages = {
            'invalid': 'Refresh token geçersiz',
            'expired': 'Oturum süresi doldu, lütfen tekrar giriş yapın',
            'revoked': 'Oturum iptal edildi, lütfen tekrar giriş yapın',
            'reuse_detected': 'Güvenlik nedeniyle oturum sonlandırıldı (token yeniden kullanımı)',
        }
        response = JsonResponse(
            {
                'error': messages.get(error, 'Token yenilenemedi'),
                'code': error,
                'reauth_required': True,
            },
            status=401,
        )
        return token_manager.clear_auth_cookies(response)

    limiter.register_success(f"refresh:{token_hint}", client)
    response = JsonResponse({
        'success': True,
        'access_expires_in': token_manager.access_token_remaining_seconds(),
        'ttl_minutes': int(token_manager.access_ttl().total_seconds() // 60),
    })
    return token_manager.set_auth_cookies(response, access, new_refresh)


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    _, refresh = token_manager.read_tokens(request)

    if refresh:
        token_manager.revoke_family(refresh, reason="logout")

    legacy = request.COOKIES.get('auth_token')
    if legacy:
        delete_session_token(legacy)

    response = JsonResponse({'success': True})
    response.delete_cookie('auth_token')
    return token_manager.clear_auth_cookies(response)
    return response

def profile_view(request):
    from agent_app.security import is_authorized_identity

    token = request.COOKIES.get('auth_token')
    user = get_user_from_token(token) if token else None
    
    if not user:
        return JsonResponse({'error': 'Not authenticated'}, status=401)
    
    return JsonResponse({
        'id': str(user.id),
        'email': user.email,
        'username': user.username,
        'full_name': user.full_name,
        'avatar_url': user.avatar_url,
        'provider': user.provider,
        'is_admin': is_authorized_identity(user.email, user.username),
        'continue_in_background': bool(user.continue_in_background),
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def user_settings(request):
    """Kullanıcıya özel çalışma tercihleri."""
    user = get_current_user(request)
    if not user:
        return JsonResponse({'error': 'Not authenticated'}, status=401)

    if request.method == "GET":
        response = JsonResponse({
            'ok': True,
            'continue_in_background': bool(user.continue_in_background),
        })
        return maybe_set_rotated_cookies(request, response)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if 'continue_in_background' in data:
        user.continue_in_background = bool(data.get('continue_in_background'))
        user.save(update_fields=['continue_in_background', 'updated_at'])

    response = JsonResponse({
        'ok': True,
        'continue_in_background': bool(user.continue_in_background),
    })
    return maybe_set_rotated_cookies(request, response)

def auth_status(request):
    from agent_app.security import is_authorized_identity

    user = get_current_user(request)
    info = token_manager.token_info(request)

    if user:
        response = JsonResponse({
            'authenticated': True,
            'user': {
                'id': str(user.id),
                'email': user.email,
                'username': user.username,
                'full_name': user.full_name,
                'avatar_url': user.avatar_url,
                'provider': user.provider,
                'is_admin': is_authorized_identity(user.email, user.username),
                'continue_in_background': bool(user.continue_in_background),
            },
            'token_info': {
                'access_expires_in': info.get('access_expires_in', 0),
                'access_expired': info.get('access_expired', False),
                'ttl_minutes': info.get('ttl_minutes', 15),
                'rotated': info.get('rotated', False),
                'refresh_rotation': True,
            },
        })
        return maybe_set_rotated_cookies(request, response)

    return JsonResponse({'authenticated': False, 'token_info': info})

def get_current_user(request):
    """
    Kısa ömürlü access token (JWT) doğrulanır.

    Access token süresi dolmuşsa refresh token yenilenmeye çalışılır
    (şeffaf yenileme - kullanıcı farkında olmadan oturum devam eder).
    """
    access, _ = token_manager.read_tokens(request)

    if access and token_manager.decode_access_token(access):
        claims = token_manager.decode_access_token(access)
        try:
            return DBUser.objects.get(pk=claims['sub'], is_active=True)
        except (DBUser.DoesNotExist, ValueError, KeyError):
            return None

    # Access token yoksa veya süresi dolmuşsa: refresh ile dene
    _, refresh = token_manager.read_tokens(request)
    if refresh:
        new_access, new_refresh, error = token_manager.rotate_refresh_token(refresh, request=request)
        if not error and new_access:
            claims = token_manager.decode_access_token(new_access)
            try:
                user = DBUser.objects.get(pk=claims['sub'], is_active=True)
                # Yeni token'ları request'e yaz (view sonrası response'a set edilecek)
                request._new_access_token = new_access
                request._new_refresh_token = new_refresh
                return user
            except (DBUser.DoesNotExist, ValueError, KeyError):
                return None

    # Son çare: eski oturum token'ı (geriye dönük uyum)
    legacy = request.COOKIES.get('auth_token')
    return get_user_from_token(legacy) if legacy else None


def maybe_set_rotated_cookies(request, response):
    """Şeffaf yenileme sırasında üretilen token'ları response'a yazar."""
    access = getattr(request, '_new_access_token', None)
    refresh = getattr(request, '_new_refresh_token', None)
    if access and refresh:
        token_manager.set_auth_cookies(response, access, refresh)
    return response


def auth_required(view_func):
    def wrapper(request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        request.user = user
        response = view_func(request, *args, **kwargs)
        return maybe_set_rotated_cookies(request, response)
    return wrapper

def verify_email(request):
    """Verify user's email with token from email link"""
    token = request.GET.get('token')
    if not token:
        return render(request, 'agent_app/email_verify.html', {
            'success': False,
            'error': 'Invalid verification link.'
        })
    
    try:
        user = DBUser.objects.get(email_verification_token=token)
    except DBUser.DoesNotExist:
        return render(request, 'agent_app/email_verify.html', {
            'success': False,
            'error': 'Invalid or expired verification link.'
        })
    
    # Check if token is expired (24 hours)
    if user.email_verification_sent_at:
        from datetime import timedelta
        if timezone.now() - user.email_verification_sent_at > timedelta(hours=24):
            return render(request, 'agent_app/email_verify.html', {
                'success': False,
                'error': 'Verification link has expired. Please request a new one.'
            })
    
    # Verify email
    user.email_verified = True
    user.email_verification_token = None
    user.email_verification_sent_at = None
    user.save(update_fields=['email_verified', 'email_verification_token', 'email_verification_sent_at'])
    
    return render(request, 'agent_app/email_verify.html', {
        'success': True,
        'message': 'Email verified successfully! You can now log in.'
    })

@csrf_exempt
@require_http_methods(["POST"])
def resend_verification_email(request):
    """Resend verification email"""
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    email = data.get('email', '').strip().lower()
    if not email:
        return JsonResponse({'error': 'Email is required'}, status=400)
    
    try:
        user = DBUser.objects.get(email=email)
    except DBUser.DoesNotExist:
        # Don't reveal if email exists
        return JsonResponse({'success': True, 'message': 'If the email exists, a verification link has been sent.'})
    
    if user.email_verified:
        return JsonResponse({'error': 'Email is already verified'}, status=400)
    
    # Rate limiting: don't send more than once per minute
    if user.email_verification_sent_at:
        from datetime import timedelta
        if timezone.now() - user.email_verification_sent_at < timedelta(minutes=1):
            return JsonResponse({'error': 'Please wait before requesting another verification email'}, status=429)
    
    send_verification_email(user)
    
    return JsonResponse({'success': True, 'message': 'Verification email sent. Please check your inbox.'})