"""WhatsApp servisi ve telefon numarası normalizasyonu testleri."""
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings
from django.urls import resolve

from agent_app import whatsapp_service_manager as manager
from agent_app.whatsapp_views import (
    WhatsAppPairView, WhatsAppStatusView, normalize_phone_number,
    _is_placeholder_name,
)


class DisplayNameTests(SimpleTestCase):
    def test_saved_name_is_not_a_placeholder(self):
        self.assertFalse(_is_placeholder_name('Ayşe Yılmaz', '905551234567@s.whatsapp.net'))

    def test_pushname_is_not_a_placeholder(self):
        self.assertFalse(_is_placeholder_name('Ali', '905551234567@s.whatsapp.net'))

    def test_raw_jid_and_number_are_placeholders(self):
        identity = '905551234567@s.whatsapp.net'
        self.assertTrue(_is_placeholder_name('', identity))
        self.assertTrue(_is_placeholder_name('905551234567', identity))
        self.assertTrue(_is_placeholder_name(identity, identity))
        self.assertTrue(_is_placeholder_name('120363346672474295@lid', identity))


class PhoneNormalizationTests(SimpleTestCase):
    def test_local_number_gets_default_country_code(self):
        self.assertEqual(
            normalize_phone_number("+90", "0532 123 45 67"),
            "+905321234567",
        )

    def test_local_number_without_leading_zero(self):
        self.assertEqual(
            normalize_phone_number("+90", "532 123 45 67"),
            "+905321234567",
        )

    def test_international_number_is_not_doubled(self):
        self.assertEqual(
            normalize_phone_number("+90", "+90 532 123 45 67"),
            "+905321234567",
        )

    def test_empty_phone_is_rejected(self):
        self.assertEqual(normalize_phone_number("+90", "  "), "")

    def test_country_code_without_plus_is_accepted(self):
        self.assertEqual(
            normalize_phone_number("90", "5321234567"),
            "+905321234567",
        )


class WhatsAppConnectTemplateTests(SimpleTestCase):
    def test_country_code_input_always_renders_90(self):
        html = render_to_string(
            "agent_app/whatsapp_connect.html",
            {"user_id": "test-user", "whatsapp_status": "not_initialized"},
        )
        self.assertIn('id="wa-country-code"', html)
        self.assertIn('value="+90"', html)
        # Ülke kodu ayrı kutuda, telefon numarası ayrı kutuda olmalı.
        self.assertIn('id="wa-phone"', html)
        country_pos = html.index('id="wa-country-code"')
        phone_pos = html.index('id="wa-phone"')
        self.assertNotEqual(country_pos, phone_pos)


class WhatsAppServiceManagerTests(SimpleTestCase):
    def setUp(self):
        manager._last_start_attempt = 0.0

    @override_settings(WHATSAPP_SERVICE_URL="http://localhost:3002")
    def test_healthy_service_is_not_spawned(self):
        with patch.object(manager, "_service_is_healthy", return_value=True), \
                patch.object(manager, "_spawn_service") as spawn:
            self.assertTrue(manager.ensure_service_running())
            spawn.assert_not_called()

    @override_settings(WHATSAPP_SERVICE_URL="http://localhost:3002")
    def test_unhealthy_local_service_is_spawned(self):
        with patch.object(
            manager, "_service_is_healthy", side_effect=[False, False, True]
        ), patch.object(manager, "_spawn_service") as spawn, \
                patch.object(manager.time, "sleep", return_value=None):
            self.assertTrue(manager.ensure_service_running(wait_seconds=1))
            spawn.assert_called_once()

    @override_settings(WHATSAPP_SERVICE_URL="http://example.com:3002")
    def test_remote_service_is_never_spawned(self):
        with patch.object(manager, "_service_is_healthy", return_value=False), \
                patch.object(manager, "_spawn_service") as spawn:
            self.assertFalse(manager.ensure_service_running(wait_seconds=0))
            spawn.assert_not_called()


class WhatsAppPairUrlTests(SimpleTestCase):
    """whatsapp_connect.html, /api/whatsapp/pair/ adresine POST atıyor.

    Bu route kayıtlı değilse kullanıcı HTTP 404 HTML hata sayfası görüyor.
    """

    def test_api_pair_url_resolves_to_pair_view(self):
        match = resolve('/api/whatsapp/pair/')
        self.assertIs(match.func.view_class, WhatsAppPairView)


class WhatsAppStatusPayloadTests(SimpleTestCase):
    """Kopuk bağlantıda eski hesap adı gösterilmemeli, net uyarı verilmeli."""

    @staticmethod
    def _session(status='auth_failed', connected=False):
        return SimpleNamespace(
            status=status,
            is_connected=connected,
            phone_number='905425549734' if connected else '',
            pushname='Sefa' if connected else '',
            session_data={'auth_error': 'Connection Closed'},
            last_qr_code='',
        )

    def test_lost_connection_hides_old_identity_and_explains(self):
        payload = WhatsAppStatusView._payload(
            self._session(), {'connected': False, 'status': 'auth_failed'}
        )
        self.assertFalse(payload['connected'])
        self.assertEqual(payload['name'], '')
        self.assertEqual(payload['phone'], '')
        self.assertIn('bağlantısı koptu', payload['message'])

    def test_connected_session_returns_identity(self):
        payload = WhatsAppStatusView._payload(
            self._session(status='connected', connected=True),
            {'connected': True, 'status': 'connected', 'info': {'pushname': 'Sefa'}},
        )
        self.assertTrue(payload['connected'])
        self.assertEqual(payload['name'], 'Sefa')
        self.assertEqual(payload['message'], '')


class WhatsAppStaticAssetTests(SimpleTestCase):
    """whatsapp.html, /static/css ve /static/js altındaki dosyaları yüklüyor.

    WSGI (waitress) altında runserver'ın otomatik static servisi çalışmadığı
    için bu istekler HTML 404 dönüyordu; tarayıcı da MIME uyuşmazlığı
    nedeniyle CSS/JS'yi reddediyordu.
    """

    def test_whatsapp_css_is_served_with_css_mime(self):
        response = self.client.get('/static/css/whatsapp.css')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/css', response['Content-Type'])

    def test_whatsapp_js_is_served_with_js_mime(self):
        response = self.client.get('/static/js/whatsapp.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response['Content-Type'])
