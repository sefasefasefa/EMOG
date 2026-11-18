# agent_app/tests_image_generation.py
import tempfile, asyncio, inspect
from pathlib import Path
from django.test import TestCase, override_settings
from agent_app.models import User, ImageJob
from agent_app import image_generation as ig


class ImageGenerationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ig@example.com", username="ig-user")
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.media_root, ignore_errors=True))
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root, IMAGE_GENERATION={"offline": True})
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)

    def test_generate_creates_job_and_runs_local(self):
        job = ig._generate_image_impl(prompt="a cat", user=self.user, width=64, height=64, fmt="png")
        self.assertIsInstance(job, ImageJob)
        self.assertEqual(job.status, "pending")
        finished = ig._run_generation_impl(str(job.id))
        finished.refresh_from_db()
        self.assertEqual(finished.status, "completed", finished.error)
        self.assertTrue(finished.output_path)
        self.assertTrue((self.media_root / finished.output_path).is_file())

    def test_provider_registry_has_local(self):
        self.assertIn("local", ig.available_providers({"offline": True}))
