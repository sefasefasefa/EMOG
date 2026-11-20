# agent_app/tests_chat_media.py
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from agent_app.models import ChatAttachment, Conversation, ImageJob, Node, User, Scene, SceneAsset, SceneNode


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


class JobAttachmentStudioUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="jobatt@example.com", username="jobatt-user")

    def test_video_and_image_attachments_have_studio_url(self):
        from agent_app.chat_media import _job_attachment
        from agent_app.models import ImageJob, VideoJob
        v = VideoJob.objects.create(user=self.user, prompt="p", status="pending")
        att_v = _job_attachment("video", v, poll_url="/api/video/%s/" % v.id)
        self.assertEqual(att_v.get("studio_url"), "/studio/?tab=media")
        i = ImageJob.objects.create(user=self.user, prompt="p", status="pending")
        att_i = _job_attachment("image", i, poll_url="/api/image/%s/" % i.id)
        self.assertEqual(att_i.get("studio_url"), "/studio/?tab=media")


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

    def _establish_conversation(self, user=None):
        user = user or self.user
        self._auth(user)
        resp0 = self.client.get("/chat/history/")
        self.assertEqual(resp0.status_code, 200, resp0.content)
        conv_id = resp0.json().get("conversation_id")
        conv = Conversation.objects.get(id=conv_id)
        session = self.client.session
        session["chat_conversation_id"] = str(conv.id)
        session.save()
        return conv

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

    def test_3d_upload_without_scene_creates_pending_question_not_placement(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from unittest.mock import patch
                from agent_app.models import Scene, SceneAsset, SceneNode

                conv = self._establish_conversation(self.user)
                Scene.objects.create(user=self.user, name="Mevcut Sahne")
                glb = SimpleUploadedFile("model.glb", b"glTF-binary-data", content_type="model/gltf-binary")

                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient") as mock_llm:
                    resp = self.client.post(
                        "/assistant/message/",
                        {"message": "bu 3d modeli ekle", "attachments": [glb]},
                    )

                self.assertEqual(resp.status_code, 200, resp.content)
                data = resp.json()
                self.assertTrue(data.get("ok"), data)
                reply = (data.get("reply") or "").lower()
                self.assertTrue(
                    any(x in reply for x in ("sahne", "mevcut", "yeni", "sadece", "seç", "sec")),
                    data,
                )
                self.assertEqual(SceneAsset.objects.count(), 0)
                self.assertEqual(SceneNode.objects.count(), 0)
                conv.refresh_from_db()
                pending = (conv.meta_data or {}).get("pending_action")
                self.assertIsInstance(pending, dict, conv.meta_data)
                self.assertIn(pending.get("type"), ("place_3d_upload", "3d_upload_placement", "upload_3d_placement"))
                # Intent routing via LLM should not be required for mandatory clarification
                self.assertEqual(mock_llm.call_count, 0, "LLM should not decide/guess placement")

    def test_pending_action_resolved_by_user_choice_places_asset(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from unittest.mock import patch
                from agent_app.models import Scene, SceneAsset, SceneNode

                conv = self._establish_conversation(self.user)
                scene = Scene.objects.create(user=self.user, name="Hedef Sahne")
                # seed a 3D chat attachment as if previously uploaded
                att = self._create_attachment(
                    self.user, conv, name="part.glb", content=b"glTF-data", mime="model/gltf-binary", kind="3d_model"
                )
                conv.meta_data = {
                    "pending_action": {
                        "id": "pa1",
                        "type": "place_3d_upload",
                        "source_attachment_ids": [str(att.id)],
                        "options": [
                            {"id": "scene:" + str(scene.id), "type": "scene", "scene_id": str(scene.id), "label": scene.name},
                            {"id": "new_scene", "type": "new_scene", "label": "Yeni sahne oluştur ve yerleştir"},
                            {"id": "store_only", "type": "store_only", "label": "Sadece dosya olarak ekle (sahneye koyma)"},
                        ],
                    }
                }
                conv.save()

                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient") as mock_llm:
                    resp = self.client.post("/assistant/message/", {"message": "1"})

                self.assertEqual(resp.status_code, 200, resp.content)
                data = resp.json()
                self.assertTrue(data.get("ok"), data)
                self.assertEqual(SceneAsset.objects.filter(scene=scene, user=self.user).count(), 1)
                self.assertEqual(SceneNode.objects.filter(scene=scene).count(), 1)
                conv.refresh_from_db()
                self.assertIsNone((conv.meta_data or {}).get("pending_action"))
                self.assertEqual(mock_llm.call_count, 0)
    def test_instantiate_entity_class_without_scene_creates_pending_question(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from unittest.mock import patch
                from agent_app.models import EntityClass, EntityInstance, SceneNode

                conv = self._establish_conversation(self.user)
                scene = Scene.objects.create(user=self.user, name="Hedef Sahne")
                ec = EntityClass.objects.create(
                    user=self.user,
                    name="TestBot",
                    category="other",
                    description="test",
                    components_template={},
                    skeleton_template={},
                    render_template={},
                    parameters={},
                )

                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient") as mock_llm:
                    resp = self.client.post("/assistant/message/", {"message": "TestBot sınıfını sahneye ekle"})

                self.assertEqual(resp.status_code, 200, resp.content)
                data = resp.json()
                self.assertTrue(data.get("ok"), data)
                self.assertTrue(data.get("needs_user_choice"), data)
                reply = (data.get("reply") or "").lower()
                self.assertTrue(any(x in reply for x in ("sahne", "yerleştir", "yerlestir", "sahnesiz", "seç", "sec")), data)
                self.assertEqual(EntityInstance.objects.filter(entity_class=ec, user=self.user).count(), 0)
                self.assertEqual(SceneNode.objects.filter(scene=scene).count(), 0)
                conv.refresh_from_db()
                pending = (conv.meta_data or {}).get("pending_action")
                self.assertIsInstance(pending, dict, conv.meta_data)
                self.assertEqual(pending.get("type"), "instantiate_entity_class")
                params = pending.get("params") or {}
                self.assertEqual(str(params.get("class_id")), str(ec.id))
                self.assertEqual(mock_llm.call_count, 0)

                # Resolve by explicit user choice -> instantiate in chosen scene
                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient") as mock_llm2:
                    resp2 = self.client.post("/assistant/message/", {"message": "1"})
                self.assertEqual(resp2.status_code, 200, resp2.content)
                data2 = resp2.json()
                self.assertTrue(data2.get("ok"), data2)
                self.assertEqual(EntityInstance.objects.filter(entity_class=ec, user=self.user, scene=scene).count(), 1)
                self.assertGreaterEqual(SceneNode.objects.filter(scene=scene).count(), 1)
                conv.refresh_from_db()
                self.assertIsNone((conv.meta_data or {}).get("pending_action"))
                self.assertEqual(mock_llm2.call_count, 0)

    def test_video_edit_disambiguation_prefers_conversation_media(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                from unittest.mock import patch
                from agent_app.models import VideoJob

                conv = self._establish_conversation(self.user)
                v1 = VideoJob.objects.create(user=self.user, prompt="konusma video 1", status="completed")
                v2 = VideoJob.objects.create(user=self.user, prompt="konusma video 2", status="completed")
                v_other = VideoJob.objects.create(user=self.user, prompt="kullanici kutuphane video", status="completed")
                # Two conversation video results in this chat flow
                Node.objects.create(
                    conversation=conv, role="assistant", content="v1 hazir",
                    meta_data={"attachments": [{"type": "video", "job_kind": "video", "job_id": str(v1.id), "name": "conv-v1", "status": "completed", "url": f"/api/video/{v1.id}/download/"}]},
                )
                Node.objects.create(
                    conversation=conv, role="assistant", content="v2 hazir",
                    meta_data={"attachments": [{"type": "video", "job_kind": "video", "job_id": str(v2.id), "name": "conv-v2", "status": "completed", "url": f"/api/video/{v2.id}/download/"}]},
                )

                with patch("agent_app.views.provider_reachable", return_value=(True, "")), \
                     patch("agent_app.views.llm_create_resilient") as mock_llm:
                    resp = self.client.post("/assistant/message/", {"message": "bu videoyu gif yap"})

                self.assertEqual(resp.status_code, 200, resp.content)
                data = resp.json()
                self.assertTrue(data.get("ok"), data)
                self.assertTrue(data.get("needs_user_choice"), data)
                conv.refresh_from_db()
                pending = (conv.meta_data or {}).get("pending_action")
                self.assertIsInstance(pending, dict, conv.meta_data)
                self.assertEqual(pending.get("type"), "edit_video")
                options = pending.get("options") or []
                option_job_ids = {str(o.get("job_id")) for o in options}
                # Must prefer conversation media/results in this chat flow
                self.assertIn(str(v1.id), option_job_ids)
                self.assertIn(str(v2.id), option_job_ids)
                self.assertNotIn(str(v_other.id), option_job_ids)
                self.assertEqual(mock_llm.call_count, 0)


class VisionLlmIntegrationTests(SimpleTestCase):
    image_attachment = {
        "id": "vision-attachment-1",
        "kind": "image",
        "mime": "image/png",
    }
    configured_vision = {
        "VISION_LLM_MODEL": "vision/test-model",
        "VISION_LLM_BASE_URL": "https://vision.example/v1",
        "VISION_LLM_API_KEY": "test-api-key",
    }

    @staticmethod
    def _response(content):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    def test_disabled_external_vision_uses_pil_without_calling_llm(self):
        from agent_app.chat_media import maybe_handle_chat_vision_analysis

        with override_settings(VISION_LLM_ENABLED=False, **self.configured_vision), \
             patch("agent_app.views.llm_create_vision") as mock_llm, \
             patch("agent_app.chat_media._pil_summary_for_attachment", return_value="yerel özet"):
            result = maybe_handle_chat_vision_analysis(
                None,
                None,
                "bu fotoğrafı analiz et",
                saved_attachments=[self.image_attachment],
            )

        self.assertEqual(
            result["reply"],
            "Görsel analizi (yerel PIL, dış API kapalı): yerel özet",
        )
        self.assertEqual(result["analyzed_by"], "pil-local")
        mock_llm.assert_not_called()

    def test_enabled_vision_without_configuration_uses_pil_without_calling_llm(self):
        from agent_app.chat_media import maybe_handle_chat_vision_analysis

        with override_settings(
            VISION_LLM_ENABLED=True,
            VISION_LLM_MODEL="",
            VISION_LLM_BASE_URL="",
            VISION_LLM_API_KEY="",
        ), patch("agent_app.views.llm_create_vision") as mock_llm, \
             patch("agent_app.chat_media._attachment_image_bytes", return_value=b"image-bytes"), \
             patch("agent_app.chat_media._pil_summary_for_attachment", return_value="yerel özet"):
            result = maybe_handle_chat_vision_analysis(
                None,
                None,
                "merhaba",
                saved_attachments=[self.image_attachment],
            )

        self.assertIsNone(result)
        mock_llm.assert_not_called()

    def test_enabled_configured_vision_calls_llm_with_multimodal_data_url(self):
        from agent_app.chat_media import maybe_handle_chat_vision_analysis

        with override_settings(VISION_LLM_ENABLED=True, **self.configured_vision), \
             patch("agent_app.views.llm_create_vision", return_value=self._response("Dış model yanıtı")) as mock_llm, \
             patch("agent_app.chat_media._attachment_image_bytes", return_value=b"image-bytes"):
            result = maybe_handle_chat_vision_analysis(
                None,
                None,
                "bu fotoğrafı analiz et",
                saved_attachments=[self.image_attachment],
            )

        self.assertEqual(result["reply"], "Dış model yanıtı")
        self.assertEqual(result["analyzed_by"], "vision/test-model")
        mock_llm.assert_called_once()
        call = mock_llm.call_args.kwargs
        self.assertEqual(call["model"], "vision/test-model")
        self.assertEqual(call["temperature"], 0)
        image_part = call["messages"][1]["content"][1]
        self.assertEqual(
            image_part,
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,aW1hZ2UtYnl0ZXM="
                },
            },
        )

    def test_enabled_vision_call_exception_falls_back_to_pil(self):
        from agent_app.chat_media import maybe_handle_chat_vision_analysis

        with override_settings(VISION_LLM_ENABLED=True, **self.configured_vision), \
             patch("agent_app.views.llm_create_vision", side_effect=RuntimeError("404 model not found")), \
             patch("agent_app.chat_media._attachment_image_bytes", return_value=b"image-bytes"), \
             patch("agent_app.chat_media._pil_summary_for_attachment", return_value="yerel özet"):
            result = maybe_handle_chat_vision_analysis(
                None,
                None,
                "bu fotoğrafı analiz et",
                saved_attachments=[self.image_attachment],
            )

        self.assertEqual(
            result["reply"],
            "(Dış vision hizmetine bağlanılamadı, yerel analizle devam ediliyor: yerel özet)",
        )
        self.assertEqual(result["analyzed_by"], "pil-local")

    def test_enabled_vision_empty_response_falls_back_to_pil(self):
        from agent_app.chat_media import maybe_handle_chat_vision_analysis

        with override_settings(VISION_LLM_ENABLED=True, **self.configured_vision), \
             patch("agent_app.views.llm_create_vision", return_value=self._response("   ")), \
             patch("agent_app.chat_media._attachment_image_bytes", return_value=b"image-bytes"), \
             patch("agent_app.chat_media._pil_summary_for_attachment", return_value="yerel özet"):
            result = maybe_handle_chat_vision_analysis(
                None,
                None,
                "bu fotoğrafı analiz et",
                saved_attachments=[self.image_attachment],
            )

        self.assertEqual(
            result["reply"],
            "(Dış vision hizmetine bağlanılamadı, yerel analizle devam ediliyor: yerel özet)",
        )
        self.assertEqual(result["analyzed_by"], "pil-local")
