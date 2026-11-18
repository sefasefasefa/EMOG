from django.test import TestCase

from agent_app.models import User


class VideoRedirectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="vr@example.com", username="vr-user")
        self.client.defaults["HTTP_HOST"] = "testserver"

    def _auth(self, user=None):
        from agent_app import tokens

        self.client.cookies["emog_at"] = tokens.create_access_token(user or self.user)

    def test_video_page_redirects_to_studio_media(self):
        self._auth()
        resp = self.client.get("/video/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/studio/?tab=media")
