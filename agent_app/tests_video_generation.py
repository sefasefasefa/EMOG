import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from agent_app import tokens
from agent_app.models import User, VideoJob
from agent_app import video_generation as vg


class VideoGenerationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="vg@example.com", username="vg-user")
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.media_root, ignore_errors=True))
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_root,
            VIDEO_GENERATION={
                **getattr(settings, "VIDEO_GENERATION", {}),
                "offline": True,
                "media_root": str(self.media_root),
                "output_dir": "videos",
            },
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_generate_creates_job_and_runs(self):
        job = vg.generate_video(prompt="test video", user=self.user, duration=5, resolution=(320, 240), fmt="mp4")
        self.assertIsInstance(job, VideoJob)
        self.assertEqual(job.user, self.user)
        self.assertEqual(job.status, "pending")
        # run
        finished = vg.run_generation(str(job.id))
        finished.refresh_from_db()
        self.assertEqual(finished.status, "completed", finished.error)
        self.assertTrue(finished.output_path)
        out = Path(self.media_root) / finished.output_path
        self.assertTrue(out.is_file(), out)

    def test_generate_video_alias_and_sync(self):
        # generate_video returns job (possibly awaitable)
        import asyncio, inspect
        res = vg.generate_video(prompt="x", user=self.user, duration=5)
        if inspect.iscoroutine(res):
            job = asyncio.run(res)
        elif inspect.isawaitable(res):
            # some runtimes patch models to be awaitable; run if coroutine-like, else use directly
            try:
                job = asyncio.run(res)  # type: ignore
            except (ValueError, TypeError):
                job = res  # type: ignore
        else:
            job = res
        self.assertIsInstance(job, VideoJob)
        vg.run_generation(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, "completed", job.error)

    def test_local_provider_generates_gif_webm(self):
        for fmt in ("gif", "webm"):
            job = vg._generate_video_impl(prompt="fmt test", user=self.user, duration=5, resolution=(160, 120), fmt=fmt)
            vg._run_generation_impl(str(job.id))
            job.refresh_from_db()
            self.assertEqual(job.status, "completed", job.error)
            self.assertTrue((Path(self.media_root) / job.output_path).is_file())

    def test_edit_video(self):
        import asyncio, inspect
        job = vg._generate_video_impl(prompt="base", user=self.user, duration=5, resolution=(160, 120), fmt="mp4")
        vg._run_generation_impl(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        edited = vg.edit_video(str(job.id), "make it brighter")
        if inspect.iscoroutine(edited):
            edited = asyncio.run(edited)
        elif inspect.isawaitable(edited):
            try:
                edited = asyncio.run(edited)  # type: ignore
            except (ValueError, TypeError):
                edited = edited  # type: ignore
        edited.refresh_from_db()
        self.assertIn(edited.status, ("completed", "failed"))
        # at least output remains
        self.assertTrue(edited.output_path)

    def test_provider_registry(self):
        self.assertIn("local", vg.PROVIDER_CLASSES)
        p = vg.get_provider("local")
        self.assertTrue(p.available())
        self.assertEqual(vg._select_provider_name({"offline": True}), "local")

    def test_duration_clamp(self):
        job = vg._generate_video_impl(prompt="dur", user=self.user, duration=10000, resolution=(64, 64), fmt="mp4")
        self.assertLessEqual(job.duration, 300)

    def test_resolution_norm(self):
        self.assertEqual(vg._norm_resolution("640x480"), (640, 480))
        self.assertEqual(vg._norm_resolution({"width": 10, "height": 20}), (10, 20))
        self.assertEqual(vg._norm_resolution((1, 2)), (1, 2))