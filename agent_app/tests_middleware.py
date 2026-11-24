from django.contrib.auth import get_user_model
from django.test import TestCase

from agent_app.admin_models import FeatureToggle, SystemSetting


MAINTENANCE_MESSAGE = "Sistem şu anda bakımda. Lütfen daha sonra tekrar deneyin."
FEATURE_DISABLED_MESSAGE = "Bu özellik şu anda kapalı."


class MaintenanceModeMiddlewareTests(TestCase):
    def test_non_staff_request_receives_503_maintenance_page(self):
        SystemSetting.objects.create(key="maintenance_mode", value="true")

        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertTemplateUsed(response, "agent_app/admin_maintenance.html")
        self.assertContains(response, MAINTENANCE_MESSAGE, status_code=503)

    def test_staff_user_can_access_admin_during_maintenance(self):
        SystemSetting.objects.create(key="maintenance_mode", value="true")
        staff = get_user_model().objects.create_user(
            username="maintenance-staff",
            email="maintenance-staff@example.com",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get("/emog-admin/")

        self.assertEqual(response.status_code, 200)

    def test_staff_user_can_access_regular_page_during_maintenance(self):
        SystemSetting.objects.create(key="maintenance_mode", value="true")
        staff = get_user_model().objects.create_user(
            username="maintenance-page-staff",
            email="maintenance-page-staff@example.com",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

    def test_admin_path_is_not_hidden_by_maintenance_response(self):
        SystemSetting.objects.create(key="maintenance_mode", value="true")

        response = self.client.get("/emog-admin/")

        self.assertEqual(response.status_code, 403)


class FeatureToggleMiddlewareTests(TestCase):
    def test_inactive_video_toggle_forbids_video_generation(self):
        FeatureToggle.objects.create(name="video_generation", is_active=False)

        response = self.client.post(
            "/api/video/generate/",
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), FEATURE_DISABLED_MESSAGE)

    def test_inactive_image_toggle_forbids_image_generation(self):
        FeatureToggle.objects.create(name="image_generation", is_active=False)

        response = self.client.post(
            "/api/image/generate/",
            data={},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), FEATURE_DISABLED_MESSAGE)

    def test_active_video_toggle_does_not_forbid_video_generation(self):
        FeatureToggle.objects.create(name="video_generation", is_active=True)

        response = self.client.post(
            "/api/video/generate/",
            data={},
            content_type="application/json",
        )

        self.assertNotEqual(response.status_code, 403)
        self.assertNotEqual(response.content.decode(), FEATURE_DISABLED_MESSAGE)

    def test_inactive_video_toggle_does_not_block_non_exact_video_path(self):
        FeatureToggle.objects.create(name="video_generation", is_active=False)

        response = self.client.post(
            "/api/video/generate/extra/",
            data={},
            content_type="application/json",
        )

        self.assertNotEqual(response.status_code, 403)

    def test_missing_toggle_does_not_forbid_image_generation(self):
        response = self.client.post(
            "/api/image/generate/",
            data={},
            content_type="application/json",
        )

        self.assertNotEqual(response.status_code, 403)
        self.assertNotEqual(response.content.decode(), FEATURE_DISABLED_MESSAGE)
