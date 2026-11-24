from django.apps import apps
from django.test import SimpleTestCase, TestCase


def get_admin_model(model_name):
    return next(
        (
            model
            for model in apps.get_models()
            if model._meta.app_label == "agent_app"
            and model.__name__ == model_name
        ),
        None,
    )


class AdminModelRegistrationTests(SimpleTestCase):
    def test_admin_models_are_registered_in_agent_app(self):
        for model_name in (
            "FeatureToggle",
            "SystemSetting",
            "AdminAuditLog",
        ):
            with self.subTest(model_name=model_name):
                self.assertIsNotNone(get_admin_model(model_name))


class AdminModelBehaviorTests(TestCase):
    def test_feature_toggle_defaults_and_string_representation(self):
        feature_toggle = get_admin_model("FeatureToggle")
        self.assertIsNotNone(feature_toggle)

        toggle = feature_toggle.objects.create(name="local_image_admin")

        self.assertFalse(toggle.is_active)
        self.assertEqual(toggle.description, "")
        self.assertEqual(str(toggle), "local_image_admin (inactive)")

    def test_system_setting_defaults_and_string_representation(self):
        system_setting = get_admin_model("SystemSetting")
        self.assertIsNotNone(system_setting)

        setting = system_setting.objects.create(key="maintenance_mode", value="off")

        self.assertEqual(setting.description, "")
        self.assertEqual(str(setting), "maintenance_mode = off")

    def test_admin_audit_log_allows_missing_admin_and_ip(self):
        admin_audit_log = get_admin_model("AdminAuditLog")
        self.assertIsNotNone(admin_audit_log)

        log = admin_audit_log.objects.create(
            action="toggle_feature",
            target="local_image_admin",
        )

        self.assertIsNone(log.admin_user)
        self.assertIsNone(log.ip)
        self.assertIn("toggle_feature", str(log))
