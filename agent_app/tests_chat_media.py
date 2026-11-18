# agent_app/tests_chat_media.py
import json
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from agent_app.models import ChatAttachment, Conversation, ImageJob, Node, User


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


class ChatAttachmentApiAndHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="owner@example.com", username="owner-user")
        self.other = User.objects.create(email="other@example.com", username="other-user")
        self.client.defaults["HTTP_HOST"] = "testserver"

    def _auth(self, user=None):
        from agent_app import tokens

        self.client.cookies["emog_at"] = tokens.create_access_token(user or self.user)

    def _create_attachment(self, user, conversation, name="a.txt", content=b"hello", mime="text/plain", kind="file"):
        up = SimpleUploadedFile(name, content, content_type=mime)
        return ChatAttachment.objects.create(
            user=user,
            conversation=conversation,
            node=None,
            kind=kind,
            name=name,
            mime_type=mime,
            size=len(content),
            file=up,
        )

    def test_history_includes_attachments_field(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                self._auth(self.user)
                resp0 = self.client.get("/chat/history/")
                self.assertEqual(resp0.status_code, 200)
                conv_id = resp0.json().get("conversation_id")
                conv = Conversation.objects.get(id=conv_id)
                session = self.client.session
                session["chat_conversation_id"] = str(conv.id)
                session.save()
                node = Node.objects.create(
                    conversation=conv,
                    parent=None,
                    role="user",
                    content="hello",
                    meta_data={"attachments": [{"id": "att1", "name": "pic.png", "type": "image"}]},
                )

                resp = self.client.get("/chat/history/")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertTrue(data.get("ok"))
                self.assertEqual(str(data.get("conversation_id")), str(conv.id), data)
                msgs = [m for m in data.get("messages", []) if m.get("content") == "hello"]
                self.assertTrue(msgs, data)
                self.assertEqual(msgs[0].get("attachments"), node.meta_data["attachments"])

    def test_history_defaults_empty_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                self._auth(self.user)
                resp0 = self.client.get("/chat/history/")
                self.assertEqual(resp0.status_code, 200)
                conv_id = resp0.json().get("conversation_id")
                conv = Conversation.objects.get(id=conv_id)
                session = self.client.session
                session["chat_conversation_id"] = str(conv.id)
                session.save()
                Node.objects.create(conversation=conv, role="user", content="plain")

                resp = self.client.get("/chat/history/")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(str(data.get("conversation_id")), str(conv.id), data)
                msg = [m for m in data.get("messages", []) if m.get("content") == "plain"][0]
                self.assertEqual(msg.get("attachments"), [])

    def test_download_forbidden_for_other_user(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                conv = Conversation.objects.create(app_user=self.user, title="t")
                att = self._create_attachment(self.user, conv)
                self._auth(self.other)

                resp = self.client.get(f"/api/chat/attachments/{att.id}/download/")
                self.assertIn(resp.status_code, (403, 404))

    def test_download_allowed_for_owner(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                conv = Conversation.objects.create(app_user=self.user, title="t")
                att = self._create_attachment(self.user, conv, name="note.txt", content=b"abc123", mime="text/plain")
                self._auth(self.user)

                resp = self.client.get(f"/api/chat/attachments/{att.id}/download/")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(b"".join(resp.streaming_content), b"abc123")
                self.assertIn("text/plain", resp.get("Content-Type", ""))
                self.assertIn("note.txt", resp.get("Content-Disposition", ""))

    def test_chat_media_helpers_save_and_serialize(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from django.test import RequestFactory
                from agent_app import chat_media

                conv = Conversation.objects.create(app_user=self.user, title="t")
                rf = RequestFactory()
                up = SimpleUploadedFile("pic.png", b"\x89PNG\r\n\x1a\nfake", content_type="image/png")
                req = rf.post("/assistant/message/", {"message": "hi"})
                req.FILES.setlist("attachments", [up])
                req.user_app = self.user

                saved = chat_media.save_chat_attachments(req, self.user, conv, node=None)
                self.assertEqual(len(saved), 1)
                self.assertTrue(saved[0]["id"])
                self.assertEqual(saved[0]["name"], "pic.png")
                self.assertEqual(saved[0]["type"], "image")
                self.assertEqual(saved[0]["kind"], "image")
                self.assertTrue(saved[0]["url"].endswith("/download/"))

                node = Node.objects.create(conversation=conv, role="user", content="x", meta_data={"attachments": saved})
                serialized = chat_media.serialize_attachments_for_node(node)
                self.assertEqual(serialized[0]["id"], saved[0]["id"])
                self.assertEqual(chat_media.serialize_attachments_for_node({"attachments": saved}), saved)

    def test_assistant_message_persists_attachments(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from unittest.mock import patch

                self._auth(self.user)
                # Establish conversation in session
                resp0 = self.client.get("/chat/history/")
                self.assertEqual(resp0.status_code, 200)
                conv_id = resp0.json().get("conversation_id")
                conv = Conversation.objects.get(id=conv_id)
                session = self.client.session
                session["chat_conversation_id"] = str(conv.id)
                session.save()

                class _Msg:
                    content = json.dumps({"intent": "chat", "message": "ok", "task": "", "model": "", "priority": "medium"})

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                up = SimpleUploadedFile("doc.txt", b"data", content_type="text/plain")
                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient", return_value=_Resp()):
                    resp = self.client.post(
                        "/assistant/message/",
                        {"message": "merhaba", "attachments": [up]},
                    )

                self.assertEqual(resp.status_code, 200, resp.content)
                data = resp.json()
                self.assertTrue(data.get("ok"), data)
                self.assertTrue(data.get("attachments"), data)
                self.assertEqual(str(data.get("conversation_id") or ""), str(conv.id), data)

                user_node = Node.objects.filter(conversation=conv, role="user").order_by("-timestamp").first()
                self.assertIsNotNone(user_node)
                atts = (user_node.meta_data or {}).get("attachments") or []
                self.assertEqual(len(atts), 1)
                self.assertEqual(atts[0].get("name"), "doc.txt")
                self.assertTrue(atts[0].get("url"))
