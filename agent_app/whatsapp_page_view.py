from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import redirect, render

from .models import WhatsAppSession
from .permissions import current_user


def whatsapp_page(request):
    """WhatsApp Web interface page (EMOG JWT authentication)."""
    user = current_user(request)
    if user is None:
        return redirect("/auth/login/?" + urlencode({"next": request.get_full_path()}))

    remote_enabled = bool(getattr(settings, "WHATSAPP_REMOTE_ENABLED", True))
    try:
        session = WhatsAppSession.objects.get(user=user)
        context = {
            'whatsapp_connected': session.is_connected,
            'whatsapp_status': session.status,
            'whatsapp_phone': session.phone_number,
            'whatsapp_name': session.pushname,
            'user_id': str(user.id),
            'remote_enabled': remote_enabled,
        }
    except WhatsAppSession.DoesNotExist:
        session = None
        context = {
            'whatsapp_connected': False,
            'whatsapp_status': 'not_initialized',
            'whatsapp_phone': '',
            'whatsapp_name': '',
            'user_id': str(user.id),
            'remote_enabled': remote_enabled,
        }

    template_name = 'agent_app/whatsapp.html' if session and session.is_connected else 'agent_app/whatsapp_connect.html'
    return render(request, template_name, context)