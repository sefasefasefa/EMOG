import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from agent_app.admin_models import AdminAuditLog, FeatureToggle, SystemSetting
from agent_app.models import Conversation, ImageJob, User, VideoJob


MIDDLEWARE_WITHOUT_APP_AUTH = tuple(
    middleware
    for middleware in settings.MIDDLEWARE
    if middleware != "agent_app.auth_middleware.AuthorizationMiddleware"
)


@override_settings(MIDDLEWARE=MIDDLEWARE_WITHOUT_APP_AUTH)
class AdminViewTests(TestCase):
    def setUp(self):
        auth_user_model = get_user_model()
        self.staff = auth_user_model.objects.create_user(
            username="panel-admin",
            email="panel-admin@example.com",
            password="test-password",
            is_staff=True,
        )
        self.regular_user = auth_user_model.objects.create_user(
            username="regular-user",
            email="regular@example.com",
            password="test-password",
        )
        self.client.force_login(self.staff)

    def test_admin_routes_are_registered(self):
        self.assertEqual(reverse("admin_dashboard"), "/emog-admin/")
        self.assertEqual(reverse("admin_feature_toggles"), "/emog-admin/toggles/")
        self.assertEqual(reverse("admin_toggle_maintenance"), "/emog-admin/maintenance/")
        self.assertEqual(reverse("admin_system_logs"), "/emog-admin/logs/")

    def test_non_staff_users_are_forbidden(self):
        self.client.logout()
        self.client.force_login(self.regular_user)

        for url_name in (
            "admin_dashboard",
            "admin_feature_toggles",
            "admin_toggle_maintenance",
            "admin_system_logs",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 403)

    def test_dashboard_returns_statistics_and_five_recent_logs(self):
        app_user = User.objects.create(
            email="app-user@example.com",
            username="app-user",
        )
        User.objects.create(email="second@example.com", username="second-user")
        Conversation.objects.create(app_user=app_user)
        VideoJob.objects.create(user=app_user)
        ImageJob.objects.create(user=app_user)
        ImageJob.objects.create(user=app_user)
        FeatureToggle.objects.create(name="active_feature", is_active=True)
        FeatureToggle.objects.create(name="disabled_feature", is_active=False)
        SystemSetting.objects.create(key="maintenance_mode", value="true")

        for index in range(6):
            AdminAuditLog.objects.create(
                admin_user=self.staff,
                action="test_action",
                target=f"target-{index}",
            )

        response = self.client.get(reverse("admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        if response["Content-Type"].startswith("application/json"):
            payload = response.json()
            stats = payload["stats"]
            self.assertEqual(stats["total_users"], 2)
            self.assertEqual(stats["total_conversations"], 1)
            self.assertEqual(stats["total_video_jobs"], 1)
            self.assertEqual(stats["total_image_jobs"], 2)
            self.assertEqual(stats["total_jobs"], 3)
            self.assertEqual(stats["active_feature_toggles"], 1)
            self.assertTrue(stats["maintenance_mode"])
            self.assertEqual(len(payload["recent_logs"]), 5)
        else:
            self.assertContains(response, "active_feature", status_code=200)

    def test_feature_toggles_get_lists_all_toggles(self):
        FeatureToggle.objects.create(
            name="vision",
            is_active=True,
            description="Vision analysis",
        )
        FeatureToggle.objects.create(name="legacy", is_active=False)

        response = self.client.get(reverse("admin_feature_toggles"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["toggles"]), 2)
        self.assertEqual(payload["toggles"][0]["name"], "legacy")
        self.assertEqual(payload["toggles"][1]["name"], "vision")
        self.assertEqual(payload["toggles"][1]["description"], "Vision analysis")

    def test_feature_toggle_post_updates_toggle_and_writes_audit_log(self):
        toggle = FeatureToggle.objects.create(name="vision", is_active=False)

        response = self.client.post(
            reverse("admin_feature_toggles"),
            {"name": "vision", "is_active": "true"},
            REMOTE_ADDR="10.0.0.5",
        )

        self.assertEqual(response.status_code, 200)
        toggle.refresh_from_db()
        self.assertTrue(toggle.is_active)
        audit_log = AdminAuditLog.objects.get()
        self.assertEqual(audit_log.admin_user, self.staff)
        self.assertEqual(audit_log.action, "toggle_feature")
        self.assertEqual(audit_log.target, "vision")
        self.assertEqual(audit_log.ip, "10.0.0.5")

    def test_feature_toggle_post_rejects_missing_toggle(self):
        response = self.client.post(
            reverse("admin_feature_toggles"),
            {"name": "missing", "is_active": "true"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(AdminAuditLog.objects.exists())

    def test_maintenance_post_updates_setting_and_writes_audit_log(self):
        response = self.client.get(reverse("admin_toggle_maintenance"))
        self.assertEqual(response.status_code, 405)

        response = self.client.post(
            reverse("admin_toggle_maintenance"),
            {"enabled": "true"},
            REMOTE_ADDR="10.0.0.6",
        )

        self.assertEqual(response.status_code, 200)
        setting = SystemSetting.objects.get(key="maintenance_mode")
        self.assertEqual(setting.value, "true")
        audit_log = AdminAuditLog.objects.get()
        self.assertEqual(audit_log.admin_user, self.staff)
        self.assertEqual(audit_log.action, "maintenance_on")
        self.assertEqual(audit_log.target, "maintenance_mode")
        self.assertEqual(audit_log.ip, "10.0.0.6")

    def test_system_logs_returns_only_the_latest_fifty_entries(self):
        for index in range(55):
            AdminAuditLog.objects.create(
                admin_user=self.staff,
                action="test_action",
                target=f"target-{index}",
                ip="127.0.0.1",
            )

        response = self.client.get(reverse("admin_system_logs"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["logs"]), 50)
        self.assertEqual(payload["logs"][0]["admin_user"], self.staff.username)
        self.assertEqual(payload["logs"][0]["action"], "test_action")
        self.assertEqual(payload["logs"][0]["ip"], "127.0.0.1")
        self.assertIn("timestamp", payload["logs"][0])


class AdminCsrfTests(TestCase):
    def setUp(self):
        auth_user_model = get_user_model()
        self.staff = auth_user_model.objects.create_user(
            username="csrf-admin",
            email="csrf-admin@example.com",
            password="test-password",
            is_staff=True,
        )

    @override_settings(MIDDLEWARE=MIDDLEWARE_WITHOUT_APP_AUTH)
    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        toggle = FeatureToggle.objects.create(name="vision", is_active=False)

        response = client.post(
            reverse("admin_feature_toggles"),
            {"name": "vision", "is_active": "true"},
        )

        self.assertEqual(response.status_code, 403)
        toggle.refresh_from_db()
        self.assertFalse(toggle.is_active)
        self.assertFalse(AdminAuditLog.objects.exists())

    @override_settings(MIDDLEWARE=MIDDLEWARE_WITHOUT_APP_AUTH)
    def test_json_post_is_supported(self):
        client = Client()
        client.force_login(self.staff)
        FeatureToggle.objects.create(name="vision", is_active=False)

        response = client.post(
            reverse("admin_feature_toggles"),
            data=json.dumps({"name": "vision", "is_active": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(FeatureToggle.objects.get(name="vision").is_active)
