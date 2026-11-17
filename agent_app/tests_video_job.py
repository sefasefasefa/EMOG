from django.test import TestCase

from agent_app.models import User, VideoJob


class VideoJobModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="vj@example.com", username="vj-user")

    def test_defaults(self):
        job = VideoJob.objects.create(user=self.user, prompt="bir video")
        self.assertTrue(job.id)
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.mode, "generate")
        self.assertEqual(job.progress, 0)
        self.assertEqual(job.duration, 10)
        self.assertEqual(job.resolution, (1920, 1080))
        self.assertEqual(job.fmt, "mp4")
        self.assertEqual(job.output_path, "")
        self.assertFalse(job.is_finished)

    def test_resolution_and_format_aliases(self):
        job = VideoJob.objects.create(user=self.user, prompt="x")
        job.resolution = (1280, 720)
        job.fmt = "gif"
        job.save()
        job.refresh_from_db()
        self.assertEqual(job.width, 1280)
        self.assertEqual(job.height, 720)
        self.assertEqual(job.resolution, (1280, 720))
        self.assertEqual(job.format, "gif")
        self.assertEqual(job.fmt, "gif")

    def test_user_scoping(self):
        other = User.objects.create(email="vj2@example.com", username="vj-user2")
        VideoJob.objects.create(user=self.user, prompt="a")
        VideoJob.objects.create(user=other, prompt="b")
        self.assertEqual(VideoJob.objects.filter(user=self.user).count(), 1)
        self.assertEqual(VideoJob.objects.filter(user=other).count(), 1)

    def test_finished_statuses(self):
        job = VideoJob.objects.create(user=self.user, prompt="x", status="completed")
        self.assertTrue(job.is_finished)
        job.status = "failed"
        self.assertTrue(job.is_finished)
