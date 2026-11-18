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


class ImageApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="img-api@example.com", username="img-api-user")
        self.other = User.objects.create(email="img-api-other@example.com", username="img-api-other")
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.media_root, ignore_errors=True))
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_root,
            IMAGE_GENERATION={"offline": True, "output_dir": "images", "upload_dir": "uploads"},
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client.defaults["HTTP_HOST"] = "testserver"

    def _auth(self, user=None):
        from agent_app import tokens

        u = user or self.user
        self.client.cookies["emog_at"] = tokens.create_access_token(u)

    def _create_completed_job(self, user=None, prompt="hello", **kwargs):
        u = user or self.user
        job = ig._generate_image_impl(prompt=prompt, user=u, width=64, height=64, fmt="png", **kwargs)
        ig._run_generation_impl(str(job.id))
        job.refresh_from_db()
        return job

    def test_generate_creates_job(self):
        self._auth()
        resp = self.client.post(
            "/api/image/generate/",
            data={"prompt": "a sunset", "width": 64, "height": 64, "format": "png", "sync": "1"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        job_id = data.get("id") or data.get("job_id") or (data.get("job") or {}).get("id")
        self.assertTrue(job_id)
        job = ImageJob.objects.get(id=job_id)
        self.assertEqual(str(job.user_id), str(self.user.id))
        self.assertEqual(job.prompt, "a sunset")

    def test_list_scoped_to_user(self):
        mine = self._create_completed_job(user=self.user, prompt="mine")
        theirs = self._create_completed_job(user=self.other, prompt="theirs")
        self._auth(self.user)
        resp = self.client.get("/api/image/")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        jobs = data.get("jobs") or data.get("images") or data.get("results") or data.get("items") or data.get("data") or []
        ids = {str(j.get("id") or j.get("job_id")) for j in jobs}
        self.assertIn(str(mine.id), ids)
        self.assertNotIn(str(theirs.id), ids)

    def test_detail_owner_only(self):
        mine = self._create_completed_job(user=self.user, prompt="mine")
        theirs = self._create_completed_job(user=self.other, prompt="theirs")
        self._auth(self.user)

        resp_ok = self.client.get(f"/api/image/{mine.id}/")
        self.assertEqual(resp_ok.status_code, 200, resp_ok.content)
        data = resp_ok.json()
        self.assertTrue(data.get("ok"))
        payload = data.get("job") or data.get("image") or data.get("data") or data
        self.assertEqual(str(payload.get("id") or payload.get("job_id")), str(mine.id))

        resp_forbidden = self.client.get(f"/api/image/{theirs.id}/")
        self.assertEqual(resp_forbidden.status_code, 404, resp_forbidden.content)

    def test_download_owner_only_and_serves_file(self):
        mine = self._create_completed_job(user=self.user, prompt="download mine")
        theirs = self._create_completed_job(user=self.other, prompt="download theirs")
        self.assertTrue(mine.output_path)
        self.assertTrue((self.media_root / mine.output_path).is_file())

        self._auth(self.user)
        resp_ok = self.client.get(f"/api/image/{mine.id}/download/")
        self.assertEqual(resp_ok.status_code, 200)
        ctype = (resp_ok.get("Content-Type") or "").lower()
        self.assertTrue(
            ctype.startswith("image/") or ctype == "application/octet-stream",
            ctype,
        )

        resp_forbidden = self.client.get(f"/api/image/{theirs.id}/download/")
        self.assertEqual(resp_forbidden.status_code, 404, resp_forbidden.content)

    def test_unauthorized(self):
        mine = self._create_completed_job(user=self.user, prompt="secret")
        for method, url in [
            ("get", "/api/image/"),
            ("post", "/api/image/generate/"),
            ("get", f"/api/image/{mine.id}/"),
            ("get", f"/api/image/{mine.id}/download/"),
        ]:
            client = self.client_class()
            client.defaults["HTTP_HOST"] = "testserver"
            if method == "get":
                resp = client.get(url)
            else:
                resp = client.post(url, data={"prompt": "x"}, content_type="application/json")
            self.assertEqual(resp.status_code, 401, f"{method.upper()} {url} -> {resp.status_code} {resp.content}")

    def test_feature_flag_off(self):
        from unittest.mock import patch

        def _disabled(key):
            if key == "image_generation":
                return False
            return True

        with patch("agent_app.permissions.feature_enabled", side_effect=_disabled):
            self._auth()
            resp = self.client.get("/api/image/")
            self.assertEqual(resp.status_code, 403, resp.content)
            data = resp.json()
            self.assertEqual(data.get("code"), "feature_disabled")

    def test_download_rejects_relative_traversal_output_path(self):
        job = self._create_completed_job(user=self.user, prompt="traversal")
        job.output_path = "../outside-secret.png"
        job.save(update_fields=["output_path"])

        self._auth(self.user)
        resp = self.client.get(f"/api/image/{job.id}/download/")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertIn(data.get("code"), {"invalid_path", "traversal"})

    def test_download_rejects_escaped_absolute_output_path(self):
        job = self._create_completed_job(user=self.user, prompt="escaped")
        escaped = str((self.media_root.parent / "escaped-outside-media.png").resolve())
        job.output_path = escaped
        job.save(update_fields=["output_path"])

        self._auth(self.user)
        resp = self.client.get(f"/api/image/{job.id}/download/")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertIn(data.get("code"), {"invalid_path", "traversal"})

    def test_download_rejects_windows_style_traversal_output_path(self):
        job = self._create_completed_job(user=self.user, prompt="win traversal")
        job.output_path = r"..\..\outside-secret.png"
        job.save(update_fields=["output_path"])

        self._auth(self.user)
        resp = self.client.get(f"/api/image/{job.id}/download/")
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertIn(data.get("code"), {"invalid_path", "traversal"})

    def test_generate_returns_no_false_success_on_sync_run_exception(self):
        from unittest.mock import patch

        self._auth()
        with patch("agent_app.image_views._call_run", side_effect=RuntimeError("boom")):
            resp = self.client.post(
                "/api/image/generate/",
                data={"prompt": "fail", "width": 64, "height": 64, "format": "png", "sync": "1"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 500, resp.content)
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertEqual(data.get("code"), "generation_failed")
        self.assertIn("boom", (data.get("error") or ""))
        job = (data.get("job") or data.get("image") or {})
        self.assertTrue(data.get("id") or job.get("id"))

    def test_generate_returns_no_false_success_when_sync_job_not_completed(self):
        from unittest.mock import patch

        self._auth()

        def _fail_run(job_id):
            job = ImageJob.objects.get(id=job_id)
            job.status = "failed"
            job.error = "quota exceeded"
            job.progress = 100
            job.save(update_fields=["status", "error", "progress"])
            return job

        with patch("agent_app.image_views._call_run", side_effect=_fail_run):
            resp = self.client.post(
                "/api/image/generate/",
                data={"prompt": "fail", "width": 64, "height": 64, "format": "png", "sync": "1"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 500, resp.content)
        data = resp.json()
        self.assertFalse(data.get("ok", True))
        self.assertEqual(data.get("code"), "generation_failed")
        self.assertEqual(data.get("status"), "failed")
        self.assertIn("quota", (data.get("error") or "").lower())
        job = (data.get("job") or data.get("image") or {})
        self.assertEqual(job.get("status"), "failed")
