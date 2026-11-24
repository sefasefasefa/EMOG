"""
WhatsApp API Views - REST endpoints for WhatsApp Web integration.
Each user connects their own WhatsApp account via QR code.
"""
import json
import logging
import re
import uuid
from datetime import timedelta, datetime, timezone as dt_timezone

import requests
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from django.db import models
from .models import (
    WhatsAppSession, WhatsAppMessage, WhatsAppChat, WhatsAppContact,
    User
)
from .whatsapp_service_manager import ensure_service_running

logger = logging.getLogger(__name__)

# WhatsApp service URL
WHATSAPP_SERVICE_URL = getattr(settings, 'WHATSAPP_SERVICE_URL', 'http://localhost:3002')
WHATSAPP_SERVICE_TOKEN = getattr(settings, 'WHATSAPP_SERVICE_TOKEN', '')


def get_whatsapp_service_headers():
    """Headers for WhatsApp service API calls."""
    headers = {'Content-Type': 'application/json'}
    if WHATSAPP_SERVICE_TOKEN:
        headers['Authorization'] = f'Bearer {WHATSAPP_SERVICE_TOKEN}'
    return headers


def normalize_phone_number(country_code, phone):
    """Ülke kodunu ve yerel numarayı WhatsApp'ın beklediği biçime getir.

    Örnek: ("+90", "0532 123 45 67") -> "+905321234567"
    Numarada zaten ülke kodu varsa (başında + ile) iki kez eklenmez.
    """
    raw = str(phone or '').strip()
    if not raw:
        return ''
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return ''
    if raw.startswith('+'):
        return f'+{digits}'

    code = re.sub(r'\D', '', str(country_code or ''))
    if code and digits.startswith(code) and len(digits) > len(code) + 6:
        # Kullanıcı numarayı ülke koduyla birlikte yazmış (ör. 90 532...).
        return f'+{digits}'

    digits = digits.lstrip('0')
    if not digits:
        return ''
    return f'+{code}{digits}' if code else digits


def _is_placeholder_name(name, identity):
    """Ham JID / telefon numarası gibi görünen isimleri placeholder say."""
    value = str(name or '').strip()
    if not value:
        return True
    if identity and value == str(identity):
        return True
    if identity and value == str(identity).split('@')[0]:
        return True
    # Ham WhatsApp kimlikleri (@lid, @s.whatsapp.net, @c.us, @g.us) gösterilmemeli.
    if '@' in value and any(
        tag in value for tag in ('@lid', '@s.whatsapp.net', '@c.us', '@g.us')
    ):
        return True
    # Yalnızca rakamlardan / + ile başlayan rakamlardan oluşan numaralar.
    return value.lstrip('+').isdigit()


def get_current_user(request):
    """Get current authenticated user (EMOG JWT veya oturum)."""
    try:
        from .auth_views import get_current_user as _auth_current_user
        user = _auth_current_user(request)
        if user:
            return user
    except Exception:
        pass
    if hasattr(request, 'user') and request.user.is_authenticated:
        return request.user
    # Try session-based auth
    session_key = request.session.session_key
    if session_key:
        try:
            from .models import Session
            session = Session.objects.get(session_key=session_key)
            return session.get_user()
        except Exception:
            pass
    return None


class WhatsAppBaseView(View):
    """Base view with authentication."""
    
    def dispatch(self, request, *args, **kwargs):
        self.user = get_current_user(request)
        if not self.user:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        return super().dispatch(request, *args, **kwargs)


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppStatusView(WhatsAppBaseView):
    """Get WhatsApp connection status for current user."""
    
    def get(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({
                'connected': False,
                'status': 'not_initialized',
                'message': 'WhatsApp not initialized. Call /api/whatsapp/init/ to start.'
            })
        
        # Also check with service for real-time status
        try:
            response = requests.get(
                f'{WHATSAPP_SERVICE_URL}/api/status/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=5
            )
            if response.status_code == 200:
                service_data = response.json()
                # Update local session if status changed
                if service_data.get('status') != session.status:
                    self._update_session_from_service(session, service_data)
                return JsonResponse({
                    'connected': service_data.get('connected', False),
                    'status': service_data.get('status'),
                    'info': service_data.get('info'),
                    'hasQr': service_data.get('hasQr', False),
                    'pairingCode': service_data.get('pairingCode') or (session.session_data or {}).get('pairing_code'),
                    'phone': session.phone_number,
                    'name': session.pushname
                })
        except requests.RequestException as e:
            logger.warning(f"WhatsApp service unreachable: {e}")
        
        # Fallback to local data
        return JsonResponse({
            'connected': session.is_connected,
            'status': session.status,
            'info': {
                'phone': session.phone_number,
                'pushname': session.pushname,
                'platform': session.platform,
                'wid': session.wid
            } if session.is_connected else None,
            'hasQr': bool(session.last_qr_code),
            'pairingCode': (session.session_data or {}).get('pairing_code'),
            'phone': session.phone_number,
            'name': session.pushname
        })
    
    def _update_session_from_service(self, session, service_data):
        """Update local session from service data."""
        status = service_data.get('status')
        info = service_data.get('info', {})
        
        session.status = status
        if status == 'connected' and info:
            session.phone_number = info.get('phone', '')
            session.pushname = info.get('pushname', '')
            session.platform = info.get('platform', '')
            session.wid = info.get('wid', '')
            session.connected_at = timezone.now()
            session.last_activity_at = timezone.now()
        elif status == 'disconnected':
            session.disconnected_at = timezone.now()
        elif status == 'qr_ready':
            session.last_qr_at = timezone.now()
        
        session.save(update_fields=[
            'status', 'phone_number', 'pushname', 'platform', 'wid',
            'connected_at', 'disconnected_at', 'last_activity_at', 'updated_at'
        ])


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppQRView(WhatsAppBaseView):
    """Get QR code for WhatsApp authentication."""
    
    def get(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'error': 'Session not found. Initialize first.'}, status=404)
        
        # If already connected, no QR needed
        if session.is_connected:
            return JsonResponse({'qr': None, 'message': 'Already connected'})
        
        # Try to get fresh QR from service
        try:
            response = requests.get(
                f'{WHATSAPP_SERVICE_URL}/api/qr/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                qr = data.get('qr')
                if qr:
                    session.set_qr_code(qr)
                    return JsonResponse({'qr': qr})
        except requests.RequestException as e:
            logger.warning(f"WhatsApp service QR request failed: {e}")
        
        # Return cached QR
        if session.last_qr_code:
            return JsonResponse({'qr': session.last_qr_code})
        
        return JsonResponse({'qr': None, 'message': 'QR not ready. Try initializing.'})


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppInitView(WhatsAppBaseView):
    """Initialize WhatsApp connection for current user."""
    
    def post(self, request):
        session, created = WhatsAppSession.objects.get_or_create(user=self.user)
        
        # If already connected, return success
        if session.is_connected:
            return JsonResponse({
                'success': True,
                'message': 'Already connected',
                'info': {
                    'phone': session.phone_number,
                    'name': session.pushname
                }
            })
        
        # Call service to initialize (gerekirse servisi otomatik başlat)
        if not ensure_service_running():
            return JsonResponse({
                'success': False,
                'error': (
                    'WhatsApp servisi başlatılamadı. Node.js kurulu mu ve '
                    'whatsapp_service bağımlılıkları yüklü mü kontrol et.'
                )
            }, status=503)
        try:
            response = requests.post(
                f'{WHATSAPP_SERVICE_URL}/api/init/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                session.status = 'initializing'
                session.save(update_fields=['status', 'updated_at'])
                return JsonResponse({
                    'success': True,
                    'message': 'Initializing... QR code will be available shortly.'
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': response.json().get('error', 'Failed to initialize')
                }, status=500)
        except requests.RequestException as e:
            logger.error(f"WhatsApp init failed: {e}")
            return JsonResponse({
                'success': False,
                'error': f'WhatsApp service unavailable: {str(e)}'
            }, status=503)


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppPairView(WhatsAppBaseView):
    """Telefon numarasıyla WhatsApp pairing code al."""

    def post(self, request):
        try:
            data = json.loads(request.body or b'{}')
        except (TypeError, json.JSONDecodeError):
            data = {}
        country_code = str(data.get('country_code') or '+90').strip()
        phone = normalize_phone_number(country_code, data.get('phone'))
        if not phone:
            return JsonResponse({'success': False, 'error': 'Telefon numarası gerekli'}, status=400)

        if not ensure_service_running():
            return JsonResponse({
                'success': False,
                'error': (
                    'WhatsApp servisi başlatılamadı. Node.js kurulu mu ve '
                    'whatsapp_service bağımlılıkları yüklü mü kontrol et.'
                )
            }, status=503)
        try:
            response = requests.post(
                f'{WHATSAPP_SERVICE_URL}/api/pair/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                json={'phone': phone},
                timeout=40,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {'success': False, 'error': response.text[:500]}
            if response.status_code >= 400:
                return JsonResponse(payload, status=response.status_code)
            session, _ = WhatsAppSession.objects.get_or_create(user=self.user)
            if payload.get('code'):
                session.status = 'pairing_code'
                session.save(update_fields=['status', 'updated_at'])
            return JsonResponse(payload)
        except requests.RequestException as exc:
            logger.warning("WhatsApp pairing code isteği başarısız: %s", exc)
            return JsonResponse(
                {'success': False, 'error': f'WhatsApp servisi çalışmıyor: {exc}'},
                status=503,
            )


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppDisconnectView(WhatsAppBaseView):
    """Disconnect WhatsApp for current user."""
    
    def post(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'success': True, 'message': 'Not connected'})
        
        # Call service to logout
        try:
            requests.post(
                f'{WHATSAPP_SERVICE_URL}/api/disconnect/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=10
            )
        except requests.RequestException:
            pass  # Continue with local cleanup
        
        # Clean up local data
        session.set_disconnected('User requested disconnect')
        
        # Optionally delete messages/chats/contacts (keep for history)
        # WhatsAppMessage.objects.filter(session=session).delete()
        # WhatsAppChat.objects.filter(session=session).delete()
        # WhatsAppContact.objects.filter(session=session).delete()
        
        return JsonResponse({'success': True, 'message': 'Disconnected'})


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppSendView(WhatsAppBaseView):
    """Send WhatsApp message."""
    
    def post(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'error': 'WhatsApp not connected'}, status=400)
        
        if not session.is_connected:
            return JsonResponse({'error': 'WhatsApp not connected'}, status=400)
        
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
        to = data.get('to')
        message = data.get('message', '')
        media = data.get('media')
        
        if not to:
            return JsonResponse({'error': 'Recipient (to) is required'}, status=400)
        
        if not message and not media:
            return JsonResponse({'error': 'Message or media is required'}, status=400)
        
        # Send via service
        try:
            response = requests.post(
                f'{WHATSAPP_SERVICE_URL}/api/send/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                json={'to': to, 'message': message, 'media': media},
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Save to local database
                WhatsAppMessage.objects.create(
                    session=session,
                    whatsapp_id=result.get('messageId'),
                    chat_id=to if '@' in to else f'{to}@c.us',
                    direction='outbound',
                    status='sent',
                    message_type=media.get('mimetype', '').split('/')[0] if media else 'text',
                    body=message,
                    media_mimetype=media.get('mimetype', '') if media else '',
                    media_data=media.get('data', '') if media else '',
                    media_filename=media.get('filename', '') if media else '',
                    from_number=session.phone_number,
                    from_name=session.pushname,
                    to_number=to.replace('@c.us', ''),
                    timestamp=timezone.now(),
                    ack_timestamp=timezone.now()
                )
                
                return JsonResponse({'success': True, 'messageId': result.get('messageId')})
            else:
                error = response.json().get('error', 'Failed to send')
                return JsonResponse({'success': False, 'error': error}, status=500)
        except requests.RequestException as e:
            logger.error(f"WhatsApp send failed: {e}")
            return JsonResponse({'success': False, 'error': str(e)}, status=503)


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppChatsView(WhatsAppBaseView):
    """Get WhatsApp chats for current user."""
    
    def get(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'chats': []})
        
        if not session.is_connected:
            # Return cached chats
            chats = WhatsAppChat.objects.filter(session=session).order_by('-is_pinned', '-last_message_at')
            return JsonResponse({
                'chats': [
                    {
                        'id': c.chat_id,
                        'name': c.name,
                        'isGroup': c.is_group,
                        'unreadCount': c.unread_count,
                        'lastMessage': {
                            'body': c.last_message_body,
                            'fromMe': c.last_message_from_me,
                            'timestamp': c.last_message_at.isoformat() if c.last_message_at else None
                        } if c.last_message_at else None,
                        'isArchived': c.is_archived,
                        'isMuted': c.is_muted,
                        'isPinned': c.is_pinned
                    } for c in chats
                ]
            })
        
        # Fetch fresh from service
        try:
            response = requests.get(
                f'{WHATSAPP_SERVICE_URL}/api/chats/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                chats = data.get('chats', [])
                
                # Update local cache
                self._sync_chats(session, chats)
                
                return JsonResponse({'chats': chats})
        except requests.RequestException as e:
            logger.warning(f"WhatsApp chats fetch failed: {e}")
        
        # Fallback to cache
        chats = WhatsAppChat.objects.filter(session=session).order_by('-is_pinned', '-last_message_at')
        return JsonResponse({
            'chats': [
                {
                    'id': c.chat_id,
                    'name': c.name,
                    'isGroup': c.is_group,
                    'unreadCount': c.unread_count,
                    'lastMessage': {
                        'body': c.last_message_body,
                        'fromMe': c.last_message_from_me,
                        'timestamp': c.last_message_at.isoformat() if c.last_message_at else None
                    } if c.last_message_at else None,
                    'isArchived': c.is_archived,
                    'isMuted': c.is_muted,
                    'isPinned': c.is_pinned
                } for c in chats
            ]
        })
    
    def _sync_chats(self, session, chats):
        """Sync chats from service to local database."""
        for chat_data in chats:
            chat_id = chat_data.get('id')
            if not chat_id:
                continue
            
            chat, created = WhatsAppChat.objects.update_or_create(
                session=session,
                chat_id=chat_id,
                defaults={
                    'name': chat_data.get('name', ''),
                    'is_group': chat_data.get('isGroup', False),
                    'unread_count': chat_data.get('unreadCount', 0),
                    'last_message_body': chat_data.get('lastMessage', {}).get('body', '') if chat_data.get('lastMessage') else '',
                    'last_message_from_me': chat_data.get('lastMessage', {}).get('fromMe', False) if chat_data.get('lastMessage') else False,
                    'last_message_at': timezone.datetime.fromtimestamp(chat_data['lastMessage']['timestamp'] / 1000, tz=timezone.utc) if chat_data.get('lastMessage', {}).get('timestamp') else None,
                    'raw_data': chat_data
                }
            )


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppContactsView(WhatsAppBaseView):
    """Get WhatsApp contacts for current user."""
    
    def get(self, request):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'contacts': []})
        
        if not session.is_connected:
            contacts = WhatsAppContact.objects.filter(session=session)
            return JsonResponse({
                'contacts': [
                    {
                        'id': c.contact_id,
                        'name': c.name or c.pushname,
                        'number': c.number,
                        'isBusiness': c.is_business,
                        'isEnterprise': c.is_enterprise
                    } for c in contacts
                ]
            })
        
        try:
            response = requests.get(
                f'{WHATSAPP_SERVICE_URL}/api/contacts/{self.user.id}',
                headers=get_whatsapp_service_headers(),
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                contacts = data.get('contacts', [])
                
                # Update local cache
                for contact_data in contacts:
                    contact_id = contact_data.get('id')
                    if contact_id:
                        WhatsAppContact.objects.update_or_create(
                            session=session,
                            contact_id=contact_id,
                            defaults={
                                'number': contact_data.get('number', ''),
                                'name': contact_data.get('name', ''),
                                'pushname': contact_data.get('pushname', ''),
                                'is_business': contact_data.get('isBusiness', False),
                                'is_enterprise': contact_data.get('isEnterprise', False),
                                'raw_data': contact_data
                            }
                        )
                
                return JsonResponse({'contacts': contacts})
        except requests.RequestException as e:
            logger.warning(f"WhatsApp contacts fetch failed: {e}")
        
        contacts = WhatsAppContact.objects.filter(session=session)
        return JsonResponse({
            'contacts': [
                {
                    'id': c.contact_id,
                    'name': c.name or c.pushname,
                    'number': c.number,
                    'isBusiness': c.is_business,
                    'isEnterprise': c.is_enterprise
                } for c in contacts
            ]
        })


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppMessagesView(WhatsAppBaseView):
    """Get messages for a specific chat."""
    
    def get(self, request, chat_id):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'messages': []})
        
        limit = int(request.GET.get('limit', 50))
        
        # Try service first if connected
        if session.is_connected:
            try:
                response = requests.get(
                    f'{WHATSAPP_SERVICE_URL}/api/messages/{self.user.id}/{chat_id}',
                    headers=get_whatsapp_service_headers(),
                    params={'limit': limit},
                    timeout=10
                )
                if response.status_code == 200:
                    data = response.json()
                    messages = data.get('messages', [])
                    
                    # Save to local
                    self._save_messages(session, messages)
                    
                    return JsonResponse({'messages': messages})
            except requests.RequestException:
                pass
        
        # Fallback to local
        messages = WhatsAppMessage.objects.filter(
            session=session, chat_id=chat_id
        ).order_by('-timestamp')[:limit]
        
        return JsonResponse({
            'messages': [
                {
                    'id': m.whatsapp_id,
                    'from': m.from_number,
                    'to': m.to_number,
                    'body': m.body,
                    'type': m.message_type,
                    'timestamp': m.timestamp.isoformat(),
                    'fromMe': m.direction == 'outbound',
                    'hasMedia': bool(m.media_data),
                    'media': {
                        'mimetype': m.media_mimetype,
                        'data': m.media_data,
                        'filename': m.media_filename
                    } if m.media_data else None,
                    'status': m.status
                } for m in reversed(messages)
            ]
        })
    
    def _save_messages(self, session, messages):
        """Save messages from service to local database."""
        for msg_data in messages:
            whatsapp_id = msg_data.get('id')
            if not whatsapp_id:
                continue
            
            WhatsAppMessage.objects.update_or_create(
                session=session,
                whatsapp_id=whatsapp_id,
                defaults={
                    'chat_id': msg_data.get('chat', {}).get('id', msg_data.get('to', '')),
                    'direction': 'outbound' if msg_data.get('fromMe') else 'inbound',
                    'status': msg_data.get('ack', 0) == 1 and 'sent' or msg_data.get('ack', 0) == 2 and 'delivered' or msg_data.get('ack', 0) == 3 and 'read' or 'pending',
                    'message_type': msg_data.get('type', 'text'),
                    'body': msg_data.get('body', ''),
                    'media_mimetype': msg_data.get('media', {}).get('mimetype', '') if msg_data.get('media') else '',
                    'media_data': msg_data.get('media', {}).get('data', '') if msg_data.get('media') else '',
                    'media_filename': msg_data.get('media', {}).get('filename', '') if msg_data.get('media') else '',
                    'from_number': msg_data.get('from', ''),
                    'from_name': msg_data.get('contact', {}).get('name', ''),
                    'to_number': msg_data.get('to', '').replace('@c.us', ''),
                    'chat_name': msg_data.get('chat', {}).get('name', ''),
                    'chat_is_group': msg_data.get('chat', {}).get('isGroup', False),
                    'timestamp': timezone.datetime.fromtimestamp(msg_data['timestamp'] / 1000, tz=timezone.utc),
                    'raw_data': msg_data
                }
            )


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppWebhookView(View):
    """Webhook endpoint for WhatsApp service to notify Django of events."""
    
    def post(self, request):
        # Verify service token
        auth_header = request.headers.get('Authorization', '')
        if WHATSAPP_SERVICE_TOKEN and auth_header != f'Bearer {WHATSAPP_SERVICE_TOKEN}':
            return JsonResponse({'error': 'Unauthorized'}, status=401)
        
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
        user_id = data.get('user_id')
        event = data.get('event')
        event_data = data.get('data', {})
        
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
        
        session, _ = WhatsAppSession.objects.get_or_create(user=user)
        
        # Handle events
        if event == 'qr_code':
            qr = event_data.get('qr')
            if qr:
                session.set_qr_code(qr)
        
        elif event == 'authenticated':
            session.status = 'authenticated'
            session.save(update_fields=['status', 'updated_at'])

        elif event == 'pairing_code':
            session.status = 'pairing_code'
            session.session_data = {
                **(session.session_data if isinstance(session.session_data, dict) else {}),
                'pairing_code': event_data.get('code', ''),
                'pairing_phone': event_data.get('phone', ''),
            }
            session.save(update_fields=['status', 'session_data', 'updated_at'])
        
        elif event == 'connected':
            info = event_data.get('info', {})
            session.update_from_client(info)
        
        elif event == 'disconnected':
            reason = event_data.get('reason', '')
            session.set_disconnected(reason)
        
        elif event == 'auth_failed':
            session.status = 'auth_failed'
            session.session_data = {'auth_error': event_data.get('message', '')}
            session.save(update_fields=['status', 'session_data', 'updated_at'])
        
        elif event == 'message_received':
            self._handle_incoming_message(session, event_data)
        
        elif event == 'message_ack':
            self._handle_message_ack(session, event_data)
        
        elif event == 'logged_out':
            session.set_disconnected('Logged out')
            WhatsAppMessage.objects.filter(session=session).delete()
            WhatsAppChat.objects.filter(session=session).delete()
            WhatsAppContact.objects.filter(session=session).delete()
        
        return JsonResponse({'ok': True})
    
    def _handle_incoming_message(self, session, msg_data):
        """Process incoming message from webhook."""
        message_id = str(msg_data.get('id') or '')
        # message + message_create aynı mesajı iki kez gönderebilir.
        if message_id and WhatsAppMessage.objects.filter(
            session=session, whatsapp_id=message_id
        ).exists():
            return

        from_me = bool(msg_data.get('fromMe'))
        chat_data = msg_data.get('chat') or {}
        chat_id = str(chat_data.get('id') or msg_data.get('from') or '')
        body = str(msg_data.get('body') or '')
        raw_timestamp = msg_data.get('timestamp')
        try:
            timestamp = timezone.datetime.fromtimestamp(float(raw_timestamp) / 1000, tz=dt_timezone.utc)
        except (TypeError, ValueError, OSError):
            timestamp = timezone.now()

        message = WhatsAppMessage.objects.create(
            session=session,
            whatsapp_id=message_id or uuid.uuid4().hex,
            chat_id=chat_id or '',  # For inbound, chat_id is sender; self-chat for fromMe
            direction='outbound' if from_me else 'inbound',
            status='sent' if from_me else 'delivered',
            message_type=msg_data.get('type', 'text'),
            body=body,
            media_mimetype=msg_data.get('media', {}).get('mimetype', '') if msg_data.get('media') else '',
            media_data=msg_data.get('media', {}).get('data', '') if msg_data.get('media') else '',
            media_filename=msg_data.get('media', {}).get('filename', '') if msg_data.get('media') else '',
            from_number=str(msg_data.get('from', '')).replace('@c.us', '').replace('@s.whatsapp.net', ''),
            from_name=msg_data.get('contact', {}).get('name', ''),
            to_number=chat_id if from_me else session.phone_number,
            chat_name=chat_data.get('name', ''),
            chat_is_group=chat_data.get('isGroup', False),
            timestamp=timestamp,
            raw_data=msg_data
        )

        # Update or create chat. AI/bot replies do not create unread counts.
        chat, created = WhatsAppChat.objects.get_or_create(
            session=session,
            chat_id=chat_id,
            defaults={
                'name': chat_data.get('name', msg_data.get('contact', {}).get('name', '')),
                'is_group': chat_data.get('isGroup', False),
                'last_message_body': body,
                'last_message_from_me': from_me,
                'last_message_at': message.timestamp,
                'unread_count': 0 if from_me else 1,
                'raw_data': chat_data
            }
        )
        if not created:
            chat.name = chat_data.get('name', msg_data.get('contact', {}).get('name', '')) or chat.name
            chat.is_group = chat_data.get('isGroup', False)
            chat.last_message_body = body
            chat.last_message_from_me = from_me
            chat.last_message_at = message.timestamp
            if not from_me:
                chat.unread_count = (chat.unread_count or 0) + 1
            chat.raw_data = chat_data
            chat.save(update_fields=[
                'name', 'is_group', 'last_message_body', 'last_message_from_me',
                'last_message_at', 'unread_count', 'raw_data', 'updated_at',
            ])

        # Refresh to get updated unread_count
        chat.refresh_from_db()

        # Update contact
        from_id = msg_data.get('from')
        if from_id:
            WhatsAppContact.objects.update_or_create(
                session=session,
                contact_id=from_id,
                defaults={
                    'number': str(msg_data.get('from', '')).replace('@c.us', '').replace('@s.whatsapp.net', ''),
                    'name': msg_data.get('contact', {}).get('name', ''),
                    'pushname': msg_data.get('contact', {}).get('pushname', ''),
                    'raw_data': msg_data.get('contact', {})
                }
            )

        # Uzaktan komut: yalnızca yetkili numara/self-chat ise, webhook'u bloklamadan.
        try:
            from .whatsapp_remote import queue_incoming_message
            queue_incoming_message(str(session.user_id), msg_data)
        except Exception as exc:
            logger.debug("WhatsApp remote kuyruğa alınamadı: %s", exc)
    
    def _handle_message_ack(self, session, ack_data):
        """Update message status on acknowledgment."""
        message_id = ack_data.get('messageId')
        ack = ack_data.get('ack')
        
        status_map = {1: 'sent', 2: 'delivered', 3: 'read'}
        new_status = status_map.get(ack, 'pending')
        
        WhatsAppMessage.objects.filter(
            session=session, whatsapp_id=message_id
        ).update(
            status=new_status,
            ack_timestamp=timezone.now(),
            updated_at=timezone.now()
        )


@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppChatActionsView(WhatsAppBaseView):
    """Chat actions: read, archive, mute."""
    
    def post(self, request, chat_id, action):
        try:
            session = WhatsAppSession.objects.get(user=self.user)
        except WhatsAppSession.DoesNotExist:
            return JsonResponse({'error': 'Not connected'}, status=400)
        
        if not session.is_connected:
            return JsonResponse({'error': 'WhatsApp not connected'}, status=400)
        
        endpoint_map = {
            'read': 'read',
            'archive': 'archive',
            'unarchive': 'archive',  # with archive=false
            'mute': 'mute',
            'unmute': 'mute'  # with mute=false
        }
        
        endpoint = endpoint_map.get(action)
        if not endpoint:
            return JsonResponse({'error': 'Invalid action'}, status=400)
        
        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            data = {}
        
        if action == 'unarchive':
            data['archive'] = False
        elif action == 'unmute':
            data['mute'] = False
        
        try:
            response = requests.post(
                f'{WHATSAPP_SERVICE_URL}/api/{endpoint}/{self.user.id}/{chat_id}',
                headers=get_whatsapp_service_headers(),
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                # Update local chat
                chat = WhatsAppChat.objects.filter(session=session, chat_id=chat_id).first()
                if chat:
                    if action in ['archive', 'unarchive']:
                        chat.is_archived = action == 'archive'
                    elif action in ['mute', 'unmute']:
                        chat.is_muted = action == 'mute'
                        if action == 'mute':
                            duration = data.get('duration', -1)
                            if duration > 0:
                                chat.mute_until = timezone.now() + timedelta(seconds=duration)
                            else:
                                chat.mute_until = None
                        else:
                            chat.mute_until = None
                    elif action == 'read':
                        chat.unread_count = 0
                    chat.save(update_fields=['is_archived', 'is_muted', 'mute_until', 'unread_count', 'updated_at'])
                
                return JsonResponse({'success': True})
            else:
                return JsonResponse({'success': False, 'error': response.json().get('error')}, status=500)
        except requests.RequestException as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=503)