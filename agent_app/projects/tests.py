"""Proje WhatsApp test endpoint'i testleri."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .views import api_whatsapp_test


class WhatsAppTestEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _call(self):
        request = self.factory.post(
            '/projects/api/p1/integrations/whatsapp/test/',
            data='{}',
            content_type='application/json',
        )
        return api_whatsapp_test(request, project_id='p1')

    @patch('agent_app.project_integrations.send_project_whatsapp_message',
           return_value=(False, 'WhatsApp bağlı değil'))
    @patch('agent_app.project_store.owner_matches', return_value=True)
    @patch('agent_app.project_store.read_project',
           return_value={'id': 'p1', 'name': 'Test', 'owner_guid': 'u1'})
    @patch('agent_app.permissions.current_user', return_value=SimpleNamespace(id='u1'))
    @patch('agent_app.projects.views.require_auth', return_value=None)
    def test_send_failure_is_200_with_ok_false(self, *mocks):
        response = self._call()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data['ok'])
        self.assertIn('bağlı', data['detail'])

    @patch('agent_app.project_integrations.send_project_whatsapp_message',
           return_value=(True, 'WhatsApp mesajı gönderildi'))
    @patch('agent_app.project_store.owner_matches', return_value=True)
    @patch('agent_app.project_store.read_project',
           return_value={'id': 'p1', 'name': 'Test', 'owner_guid': 'u1'})
    @patch('agent_app.permissions.current_user', return_value=SimpleNamespace(id='u1'))
    @patch('agent_app.projects.views.require_auth', return_value=None)
    def test_send_success_is_200_with_ok_true(self, *mocks):
        response = self._call()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
