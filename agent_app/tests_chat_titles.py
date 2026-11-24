"""Otomatik sohbet başlığı testleri."""
from unittest.mock import patch

from django.test import TestCase

from agent_app.chat_titles import heuristic_title, maybe_title_conversation
from agent_app.models import Conversation


class HeuristicTitleTests(TestCase):
    def test_title_comes_from_ai_reply_first_sentence(self):
        title = heuristic_title(
            "tan 100 kaçtır",
            "tan(100°) ≈ -5.67. Bu değer, 100 derecenin tanjantıdır.",
        )
        self.assertEqual(title, "tan(100°) ≈ -5.67")

    def test_falls_back_to_user_message_when_reply_is_empty(self):
        title = heuristic_title("log 400 kaçtır", "")
        self.assertIn("log 400", title)

    def test_markdown_and_metadata_are_stripped(self):
        title = heuristic_title(
            "selam",
            "🤖 Model: test | HTTP: 200\n**Merhaba!** Size nasıl yardımcı olabilirim?",
        )
        self.assertEqual(title, "Merhaba! Size nasıl yardımcı olabilirim?")


class MaybeTitleConversationTests(TestCase):
    def test_default_title_is_replaced(self):
        conv = Conversation.objects.create(title="Yeni Sohbet")
        with patch("agent_app.chat_titles._refine_in_background") as refine:
            maybe_title_conversation(conv, "tan 100 kaçtır", "tan(100°) ≈ -5.67. Tanjant budur.")
        conv.refresh_from_db()
        self.assertEqual(conv.title, "tan(100°) ≈ -5.67")
        refine.assert_called_once()

    def test_user_chosen_title_is_not_overwritten(self):
        conv = Conversation.objects.create(title="Benim başlığım")
        with patch("agent_app.chat_titles._refine_in_background") as refine:
            maybe_title_conversation(conv, "tan 100 kaçtır", "tan(100°) ≈ -5.67")
        conv.refresh_from_db()
        self.assertEqual(conv.title, "Benim başlığım")
        refine.assert_not_called()


class AppendMessageTitleTests(TestCase):
    def test_first_assistant_reply_sets_conversation_title(self):
        from agent_app.views import _append_chat_message

        conv = Conversation.objects.create(title="Yeni Sohbet")
        with patch("agent_app.chat_titles._refine_in_background"):
            _append_chat_message(conv, "user", "tan 100 kaçtır")
            _append_chat_message(conv, "assistant", "tan(100°) ≈ -5.67. Tanjant budur.")
        conv.refresh_from_db()
        self.assertEqual(conv.title, "tan(100°) ≈ -5.67")


class LLMTitleTests(TestCase):
    def test_model_result_is_cleaned(self):
        from agent_app.chat_titles import _llm_title

        fake = {"ok": True, "text": '"Tanjant ve logaritma hesabı."'}
        with patch("agent_app.model_gateway.model_gateway.complete", return_value=fake):
            title = _llm_title("tan 100 kaçtır", "tan(100°) ≈ -5.67")
        self.assertEqual(title, "Tanjant ve logaritma hesabı")
