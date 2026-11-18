# agent_app/tests_chat_media.py
import tempfile
from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from agent_app.models import User, Conversation, Node, ChatAttachment, ImageJob


class ChatAttachmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ca@example.com", username="ca-user")
        self.conv = Conversation.objects.create(app_user=self.user, title="t")

    def test_create_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                up = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
                att = ChatAttachment.objects.create(
                    user=self.user, conversation=self.conv, node=None,
                    kind="file", name="a.txt", mime_type="text/plain", size=5, file=up,
                )
                self.assertEqual(att.user_id, self.user.id)
                self.assertEqual(att.conversation_id, self.conv.id)
                self.assertTrue(att.file.name)


class ImageJobModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ij@example.com", username="ij-user")

    def test_defaults(self):
        job = ImageJob.objects.create(user=self.user, prompt="cat")
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.progress, 0)
        self.assertEqual(job.format, "png")
        self.assertFalse(job.is_finished)
