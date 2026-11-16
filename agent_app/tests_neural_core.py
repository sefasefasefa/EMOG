"""NEURAL CORE entegrasyon testleri.

Testler çevrimdışı modda çalışır; hiçbir LLM veya arama sağlayıcısına
gerçek istek gitmez.
"""
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from agent_app.models import User


OFFLINE = {
    "OFFLINE": True,
    "MAX_PLAN_STEPS": 8,
    "MAX_ALTERNATIVES": 3,
    "MAX_RESEARCH_QUERIES": 2,
    "RESEARCH_RESULTS": 4,
}


class ConfigTests(SimpleTestCase):
    def test_offline_flag_is_read_from_settings(self):
        from agent_app.neural_core import config

        with override_settings(NEURAL_CORE=OFFLINE):
            self.assertTrue(config.offline())

    def test_missing_settings_fall_back_to_safe_defaults(self):
        from agent_app.neural_core import config

        with override_settings(NEURAL_CORE={}):
            self.assertFalse(config.offline())
            self.assertEqual(config.get_config()["MAX_PLAN_STEPS"], 10)


class ModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            email="neural@example.com",
            username="neural-user",
            is_active=True,
        )

    def test_run_defaults_to_pending(self):
        from agent_app.neural_core.models import NeuralRun

        run = NeuralRun.objects.create(user=self.user, goal="Test hedefi")
        self.assertEqual(run.status, "pending")
        self.assertEqual(run.mode, "full")
        self.assertTrue(run.id)

    def test_preference_is_unique_per_user_and_key(self):
        from agent_app.neural_core.models import NeuralPreference

        NeuralPreference.objects.create(
            user=self.user, key="language", value="tr"
        )
        with self.assertRaises(Exception):
            NeuralPreference.objects.create(
                user=self.user, key="language", value="en"
            )


class GatewayTests(SimpleTestCase):
    def test_offline_gateway_never_calls_network(self):
        from agent_app.neural_core.gateway import ModelGateway

        with override_settings(NEURAL_CORE={**OFFLINE}):
            gateway = ModelGateway()
            self.assertEqual(gateway.available(), [])
            self.assertEqual(gateway.complete("sistem", "kullanıcı"), "")

    def test_provider_without_key_is_not_available(self):
        from agent_app.neural_core.gateway import ModelGateway

        with override_settings(NEURAL_CORE={**OFFLINE, "OFFLINE": False}), \
                patch.dict("os.environ", {}, clear=True):
            gateway = ModelGateway()
            self.assertNotIn("openai", gateway.available())

    def test_gateway_falls_back_to_next_provider(self):
        from agent_app.neural_core import gateway as gateway_mod

        config = {
            **OFFLINE,
            "OFFLINE": False,
            "PROVIDER_ORDER": ["openai", "deepseek"],
            "PROVIDERS": {
                "openai": {"base_url": "http://a", "api_key_env": "OPENAI_API_KEY", "model": "a"},
                "deepseek": {"base_url": "http://b", "api_key_env": "DEEPSEEK_API_KEY", "model": "b"},
            },
        }
        with override_settings(NEURAL_CORE=config), \
                patch.dict("os.environ", {"OPENAI_API_KEY": "x", "DEEPSEEK_API_KEY": "y"}), \
                patch.object(gateway_mod.ModelGateway, "_call_openai_compatible",
                             side_effect=[RuntimeError("boom"), "cevap"]):
            gateway = gateway_mod.ModelGateway()
            self.assertEqual(gateway.complete("s", "u"), "cevap")


class PlannerTests(SimpleTestCase):
    def test_offline_plan_has_actionable_steps(self):
        from agent_app.neural_core.gateway import ModelGateway
        from agent_app.neural_core.planner import build_plan

        with override_settings(NEURAL_CORE=OFFLINE):
            plan = build_plan("Bir todo uygulaması yap", "full", {}, ModelGateway())
        self.assertGreaterEqual(len(plan), 4)
        self.assertLessEqual(len(plan), OFFLINE["MAX_PLAN_STEPS"])
        for step in plan:
            self.assertTrue(step["key"])
            self.assertTrue(step["title"])
            self.assertEqual(step["status"], "pending")

    def test_solve_mode_adds_root_cause_step(self):
        from agent_app.neural_core.gateway import ModelGateway
        from agent_app.neural_core.planner import build_plan

        with override_settings(NEURAL_CORE=OFFLINE):
            plan = build_plan("Giriş hatasını düzelt", "solve", {}, ModelGateway())
        self.assertTrue(any("kök" in step["title"].lower() for step in plan))


class DiscoveryTests(SimpleTestCase):
    def test_offline_discovery_returns_ranked_alternatives(self):
        from agent_app.neural_core.discovery import generate
        from agent_app.neural_core.gateway import ModelGateway

        with override_settings(NEURAL_CORE=OFFLINE):
            candidates = generate("Mobil uygulama için API tasarla", "explore", ModelGateway())
        self.assertGreaterEqual(len(candidates), 2)
        self.assertLessEqual(len(candidates), OFFLINE["MAX_ALTERNATIVES"])
        scores = [item["score"] for item in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for item in candidates:
            self.assertTrue(item["name"])
            self.assertIsInstance(item["pros"], list)
            self.assertIsInstance(item["cons"], list)


class DiscoveryExtensionTests(SimpleTestCase):
    def test_hypotheses_non_empty(self):
        from agent_app.neural_core.discovery import hypotheses
        from agent_app.neural_core.gateway import ModelGateway

        with override_settings(NEURAL_CORE=OFFLINE):
            result = hypotheses("Mobil uygulama", ModelGateway(), max_items=3)
        self.assertGreaterEqual(len(result), 1)
        for item in result:
            self.assertIn("statement", item)
            self.assertIn("confidence", item)

    def test_merge_technologies_returns_combined(self):
        from agent_app.neural_core.discovery import merge_technologies

        cands = [{"name": "A", "score": 80}, {"name": "B", "score": 70}]
        merged = merge_technologies(cands)
        self.assertGreaterEqual(len(merged), 2)
        self.assertTrue(any("Birleşik" in m["name"] or "combined" in m for m in merged))

    def test_experiment_plan_has_steps_and_criteria(self):
        from agent_app.neural_core.discovery import experiment_plan

        plan = experiment_plan({"name": "MVP"})
        self.assertIn("steps", plan)
        self.assertIn("success_criteria", plan)
        self.assertGreaterEqual(len(plan["steps"]), 1)


class ResearchTests(SimpleTestCase):
    def test_disabled_research_makes_no_calls(self):
        from agent_app.neural_core.research import run

        with override_settings(NEURAL_CORE=OFFLINE):
            result = run("konu", enabled=False)
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["offline"])

    def test_offline_research_returns_query_plan_without_network(self):
        from agent_app.neural_core import research as research_mod

        with override_settings(NEURAL_CORE=OFFLINE), \
                patch.object(research_mod, "_safe_search") as search:
            result = research_mod.run("Python ile API", enabled=True)
        search.assert_not_called()
        self.assertTrue(result["offline"])
        self.assertTrue(result["queries"])
        self.assertIn("Python", result["queries"][0])


class EvaluationTests(SimpleTestCase):
    def test_score_is_bounded_and_reports_gaps(self):
        from agent_app.neural_core.evaluation import score_run

        weak = score_run([], [], [], {})
        strong = score_run(
            [{"title": "a"}] * 8,
            [{"name": "b"}] * 4,
            [{"title": "c"}] * 5,
            {"summary": "sonuç"},
        )
        self.assertGreaterEqual(weak["score"], 0)
        self.assertLessEqual(strong["score"], 100)
        self.assertGreater(strong["score"], weak["score"])
        self.assertTrue(weak["gaps"])


class ExperienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="exp@example.com", username="exp-user")
        self.other = User.objects.create(email="other@example.com", username="other-user")

    def test_record_and_read_experience(self):
        from agent_app.neural_core.experience import lessons, recent, record

        record(self.user, "API tasarla", "success", 85, lesson="Küçük adımlar işe yaradı")
        record(self.user, "Testleri düzelt", "failure", 30)
        rows = recent(self.user)
        self.assertEqual(len(rows), 2)
        self.assertTrue(lessons(self.user))
        self.assertTrue(any("Küçük adımlar" in item for item in lessons(self.user)))

    def test_experience_is_user_scoped(self):
        from agent_app.neural_core.experience import recent, record

        record(self.user, "Bir hedef", "partial", 50)
        self.assertEqual(recent(self.other), [])


class PreferenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="pref@example.com", username="pref-user")

    def test_set_and_get_value(self):
        from agent_app.neural_core.preferences import get_value, set_value

        set_value(self.user, "language", "tr")
        self.assertEqual(get_value(self.user, "language"), "tr")
        self.assertEqual(get_value(self.user, "missing", "varsayılan"), "varsayılan")

    def test_set_value_updates_existing_row(self):
        from agent_app.neural_core.models import NeuralPreference
        from agent_app.neural_core.preferences import set_value

        set_value(self.user, "tone", "kısa")
        set_value(self.user, "tone", "ayrıntılı")
        self.assertEqual(NeuralPreference.objects.filter(user=self.user, key="tone").count(), 1)
        self.assertEqual(NeuralPreference.objects.get(user=self.user, key="tone").value, "ayrıntılı")


class EvolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="evo@example.com", username="evo-user")
        self.other = User.objects.create(email="evo2@example.com", username="evo-other")

    def test_failures_produce_safe_policy_proposals(self):
        from agent_app.neural_core import config
        from agent_app.neural_core.evolution import propose
        from agent_app.neural_core.experience import record

        for index in range(4):
            record(self.user, f"Başarısız hedef {index}", "failure", 20)
        proposals = propose(self.user)
        self.assertTrue(proposals)
        safe = set(config.get_config()["SAFE_POLICY_KEYS"])
        for proposal in proposals:
            self.assertTrue(set(proposal.changes).issubset(safe))

    def test_apply_writes_preference_and_marks_applied(self):
        from agent_app.neural_core.evolution import apply
        from agent_app.neural_core.models import NeuralProposal
        from agent_app.neural_core.preferences import get_value

        proposal = NeuralProposal.objects.create(
            user=self.user,
            title="Daha derin plan",
            changes={"planner.depth": "deep"},
        )
        self.assertTrue(apply(self.user, proposal))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "applied")
        self.assertEqual(get_value(self.user, "planner.depth"), "deep")

    def test_apply_rejects_unsafe_key(self):
        from agent_app.neural_core.evolution import apply
        from agent_app.neural_core.models import NeuralProposal

        proposal = NeuralProposal.objects.create(
            user=self.user,
            title="Model ağırlığını değiştir",
            changes={"model.weights": "evil"},
        )
        self.assertFalse(apply(self.user, proposal))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "pending")

    def test_apply_rejects_another_users_proposal(self):
        from agent_app.neural_core.evolution import apply
        from agent_app.neural_core.models import NeuralProposal

        proposal = NeuralProposal.objects.create(
            user=self.user,
            title="Ayar",
            changes={"planner.depth": "deep"},
        )
        self.assertFalse(apply(self.other, proposal))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "pending")


class ToolTests(TestCase):
    def setUp(self):
        from pathlib import Path

        self.user = User.objects.create(email="tool@example.com", username="tool-user")
        self.other = User.objects.create(email="tool2@example.com", username="tool2-user")
        self.relative_path = f"task_runs/neural/{self.user.id}/neural_test_output.txt"
        self.absolute = Path(__file__).resolve().parent.parent / self.relative_path
        if self.absolute.exists():
            self.absolute.unlink()

    def tearDown(self):
        if self.absolute.exists():
            self.absolute.unlink()

    def _settings(self):
        return {**OFFLINE, "TOOL_ROOTS": ["task_runs"], "TOOL_REQUIRE_APPROVAL": True}

    def test_resolve_rejects_traversal_outside_roots(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            registry = ToolRegistry(self.user)
            with self.assertRaises(ValueError):
                registry._resolve("../db.sqlite3")
            with self.assertRaises(ValueError):
                registry._resolve("C:/Windows/win.ini")

    def test_write_without_approval_has_no_side_effect(self):
        from agent_app.neural_core.models import NeuralToolApproval
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            approval = ToolRegistry(self.user).request_write(
                self.relative_path, "onay bekleyen içerik"
            )
        self.assertEqual(approval.status, "pending")
        self.assertFalse(self.absolute.exists())
        self.assertEqual(NeuralToolApproval.objects.filter(status="pending").count(), 1)

    def test_approved_write_creates_file(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            registry = ToolRegistry(self.user)
            approval = registry.request_write(self.relative_path, "yazıldı")
            result = registry.decide(approval.id, True, self.user)
        self.assertTrue(result["ok"])
        self.assertEqual(self.absolute.read_text(encoding="utf-8"), "yazıldı")
        approval.refresh_from_db()
        self.assertEqual(approval.status, "approved")

    def test_rejected_write_does_not_create_file(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            registry = ToolRegistry(self.user)
            approval = registry.request_write(self.relative_path, "hayır")
            result = registry.decide(approval.id, False, self.user)
        self.assertFalse(result["ok"])
        self.assertFalse(self.absolute.exists())
        approval.refresh_from_db()
        self.assertEqual(approval.status, "rejected")

    def test_another_user_cannot_decide_approval(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            registry = ToolRegistry(self.user)
            approval = registry.request_write(self.relative_path, "gizli")
            with self.assertRaises(LookupError):
                registry.decide(approval.id, True, self.other)
        self.assertFalse(self.absolute.exists())

    def test_command_allowlist_blocks_unknown_executable(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            with self.assertRaises(ValueError):
                ToolRegistry(self.user).request_command("powershell Remove-Item C:/Windows/win.ini")

    def test_approved_command_runs_and_records_output(self):
        import sys

        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            registry = ToolRegistry(self.user)
            approval = registry.request_command(f'"{sys.executable}" --version')
            result = registry.decide(approval.id, True, self.user)
        self.assertTrue(result["ok"], result["output"])
        self.assertIn("Python", result["output"])


class KernelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="kernel@example.com", username="kernel-user")

    def test_full_run_completes_and_records_experience(self):
        from agent_app.neural_core.kernel import CognitiveKernel
        from agent_app.neural_core.models import NeuralExperience, NeuralRun

        with override_settings(NEURAL_CORE=OFFLINE):
            run = CognitiveKernel(self.user).run("Todo API tasarla", "full")
        self.assertEqual(run.status, "completed", run.error)
        self.assertGreaterEqual(len(run.plan), 4)
        self.assertTrue(run.alternatives)
        self.assertIn("summary", run.result)
        self.assertGreaterEqual(run.evaluation["score"], 0)
        self.assertEqual(NeuralRun.objects.filter(user=self.user).count(), 1)
        self.assertEqual(NeuralExperience.objects.filter(user=self.user).count(), 1)

    def test_empty_goal_is_rejected(self):
        from agent_app.neural_core.kernel import CognitiveKernel

        with override_settings(NEURAL_CORE=OFFLINE):
            with self.assertRaises(ValueError):
                CognitiveKernel(self.user).run("   ", "full")

    def test_planner_failure_marks_run_failed_without_raising(self):
        from agent_app.neural_core import kernel as kernel_mod

        with override_settings(NEURAL_CORE=OFFLINE), \
                patch.object(kernel_mod.planner, "build_plan", side_effect=RuntimeError("plan bozuk")):
            run = kernel_mod.CognitiveKernel(self.user).run("Bir hedef", "full")
        self.assertEqual(run.status, "failed")
        self.assertIn("plan bozuk", run.error)

    def test_research_mode_skips_discovery(self):
        from agent_app.neural_core.kernel import CognitiveKernel

        with override_settings(NEURAL_CORE=OFFLINE):
            run = CognitiveKernel(self.user).run("Kaynak taraması", "research")
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.alternatives, [])
        self.assertTrue(run.research.get("queries"))


class AutonomyTests(TestCase):
    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from agent_app.neural_core.models import NeuralGoal

        self.user = User.objects.create(email="goal@example.com", username="goal-user")
        self.goal = NeuralGoal.objects.create(
            user=self.user,
            title="Haftalık teknoloji taraması",
            mode="research",
            interval_minutes=60,
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

    def test_due_goal_runs_and_schedules_next(self):
        from django.utils import timezone

        from agent_app.neural_core.kernel import run_goal_cycle

        with override_settings(NEURAL_CORE=OFFLINE):
            result = run_goal_cycle()
        self.assertEqual(result["ran"], 1, result)
        self.goal.refresh_from_db()
        self.assertIsNotNone(self.goal.last_run_at)
        self.assertGreater(self.goal.next_run_at, timezone.now())
        self.assertEqual(self.goal.last_result["status"], "completed")

    def test_disabled_goal_is_skipped(self):
        from agent_app.neural_core.kernel import run_goal_cycle

        self.goal.enabled = False
        self.goal.save(update_fields=["enabled"])
        with override_settings(NEURAL_CORE=OFFLINE):
            result = run_goal_cycle()
        self.assertEqual(result["ran"], 0)

    def test_management_command_reports_cycle(self):
        from io import StringIO

        from django.core.management import call_command

        output = StringIO()
        with override_settings(NEURAL_CORE=OFFLINE):
            call_command("neural_cycle", stdout=output)
        self.assertIn("1", output.getvalue())


class NeuralApiTests(TestCase):
    def setUp(self):
        from agent_app import tokens

        self.user = User.objects.create(email="api@example.com", username="api-user")
        self.other = User.objects.create(email="api2@example.com", username="api-other")
        self.client.cookies["emog_at"] = tokens.create_access_token(self.user)
        self.settings = {**OFFLINE, "SYNC_RUNS": True}

    def _post(self, path, payload):
        import json

        return self.client.post(path, data=json.dumps(payload), content_type="application/json")

    def test_neural_page_requires_login(self):
        self.client.cookies.clear()
        response = self.client.get("/neural/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login/", response["Location"])

    def test_neural_page_renders_for_authenticated_user(self):
        with override_settings(NEURAL_CORE=self.settings):
            response = self.client.get("/neural/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NEURAL CORE")
        self.assertContains(response, 'id="neural-run-form"')

    def test_status_api_reports_offline_without_network(self):
        with override_settings(NEURAL_CORE=self.settings):
            response = self.client.get("/api/neural/status/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offline"])

    def test_run_api_completes_synchronously_in_test_mode(self):
        with override_settings(NEURAL_CORE=self.settings):
            response = self._post("/api/neural/run/", {"goal": "API testi", "mode": "full"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["run"]["status"], "completed", payload["run"].get("error"))
        self.assertGreaterEqual(len(payload["run"]["plan"]), 4)

    def test_run_detail_is_user_scoped(self):
        from agent_app.neural_core.models import NeuralRun

        foreign = NeuralRun.objects.create(user=self.other, goal="Gizli hedef")
        response = self.client.get(f"/api/neural/run/{foreign.id}/")
        self.assertEqual(response.status_code, 404)

    def test_preferences_api_round_trip(self):
        with override_settings(NEURAL_CORE=self.settings):
            saved = self._post("/api/neural/preferences/", {"key": "language", "value": "tr"})
            listed = self.client.get("/api/neural/preferences/")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(listed.json()["preferences"][0]["key"], "language")

    def test_tool_write_requires_explicit_approval(self):
        from pathlib import Path

        relative = f"task_runs/neural/{self.user.id}/neural_api_test.txt"
        target = Path(__file__).resolve().parent.parent / relative
        if target.exists():
            target.unlink()
        try:
            with override_settings(NEURAL_CORE=self.settings):
                requested = self._post(
                    "/api/neural/tools/write/",
                    {"path": relative, "content": "api-onay"},
                )
                payload = requested.json()
                self.assertTrue(payload["ok"], payload)
                self.assertEqual(payload["approval"]["status"], "pending")
                self.assertFalse(target.exists())
                decided = self._post(
                    f"/api/neural/tools/approvals/{payload['approval']['id']}/decide/",
                    {"approve": True},
                )
            self.assertTrue(decided.json()["ok"], decided.json())
            self.assertEqual(target.read_text(encoding="utf-8"), "api-onay")
        finally:
            if target.exists():
                target.unlink()

    def test_goal_is_clamped_to_minimum_interval(self):
        from agent_app.neural_core.models import NeuralGoal

        with override_settings(NEURAL_CORE=self.settings):
            response = self._post(
                "/api/neural/goals/",
                {"title": "Otonom araştırma", "mode": "research", "interval_minutes": 1},
            )
        self.assertEqual(response.status_code, 200)
        goal = NeuralGoal.objects.get(user=self.user)
        self.assertGreaterEqual(goal.interval_minutes, self.settings.get("AUTONOMY_MIN_INTERVAL_MINUTES", 15))

    def test_evolution_apply_rejects_other_users_proposal(self):
        from agent_app.neural_core.models import NeuralProposal

        proposal = NeuralProposal.objects.create(
            user=self.other, title="Başkasının önerisi", changes={"planner.depth": "deep"}
        )
        response = self._post(f"/api/neural/proposals/{proposal.id}/apply/", {})
        self.assertEqual(response.status_code, 404)


class ToolIsolationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="iso@example.com", username="iso-user")
        self.other = User.objects.create(email="iso2@example.com", username="iso-other")
        self.settings = {
            **OFFLINE,
            "OFFLINE": True,
            "TOOL_ROOTS": ["projects", "task_runs"],
            "TOOL_REQUIRE_APPROVAL": True,
        }

    def _cleanup(self, *paths):
        import shutil
        from pathlib import Path

        for path in paths:
            target = Path(settings.BASE_DIR) / path
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)

    def test_project_tools_are_scoped_to_user_guid(self):
        from django.conf import settings

        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self.settings):
            owner = ToolRegistry(self.user)
            attacker = ToolRegistry(self.other)
            path = f"projects/{owner.user_guid}/secret.txt"
            approval = owner.request_write(path, "gizli veri")
            self.assertTrue(owner.decide(approval.id, True, self.user)["ok"])
            try:
                with self.assertRaises(ValueError):
                    attacker.read_file(path)
                self.assertEqual(attacker.list_files(".")["entries"], [])
            finally:
                self._cleanup(f"projects/{owner.user_guid}")

    def test_task_run_tools_are_scoped_to_user_namespace(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self.settings):
            owner = ToolRegistry(self.user)
            attacker = ToolRegistry(self.other)
            path = f"task_runs/neural/{owner.user_guid}/secret.txt"
            approval = owner.request_write(path, "gizli")
            self.assertTrue(owner.decide(approval.id, True, self.user)["ok"])
            try:
                with self.assertRaises(ValueError):
                    attacker.read_file(path)
            finally:
                self._cleanup(f"task_runs/neural/{owner.user_guid}")

    def test_command_arguments_cannot_escape_tool_roots(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self.settings):
            with self.assertRaises(ValueError):
                ToolRegistry(self.user).request_command("python ../db.sqlite3")

    def test_approval_decide_is_idempotent(self):
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self.settings):
            registry = ToolRegistry(self.user)
            path = f"task_runs/neural/{registry.user_guid}/once.txt"
            approval = registry.request_write(path, "tek")
            with patch.object(
                registry, "_execute_write", wraps=registry._execute_write
            ) as execute:
                first = registry.decide(approval.id, True, self.user)
                second = registry.decide(approval.id, True, self.user)
            self.assertTrue(first["ok"], first)
            self.assertFalse(second["ok"])
            self.assertEqual(execute.call_count, 1)
        self._cleanup(f"task_runs/neural/{registry.user_guid}")


class RobustnessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="robust@example.com", username="robust-user")

    def test_malformed_discovery_payload_falls_back(self):
        from agent_app.neural_core.discovery import generate

        class StubGateway:
            def complete(self, *args, **kwargs):
                return (
                    '[{"name": "A", "summary": "s", "pros": "tek", '
                    '"cons": null, "score": "high"}, {"name": "B", "score": null}]'
                )

        with override_settings(NEURAL_CORE={**OFFLINE, "OFFLINE": False}):
            candidates = generate("hedef", "explore", StubGateway())
        self.assertGreaterEqual(len(candidates), 2)
        for item in candidates:
            self.assertIsInstance(item["score"], int)
            self.assertIsInstance(item["pros"], list)
            self.assertIsInstance(item["cons"], list)

    def test_ollama_is_not_advertised_without_explicit_enable(self):
        import os

        from agent_app.neural_core.gateway import ModelGateway

        config = {
            **OFFLINE,
            "OFFLINE": False,
            "PROVIDER_ORDER": ["ollama"],
            "PROVIDERS": {
                "ollama": {
                    "base_url": "http://localhost:11434",
                    "api_key_env": "",
                    "model": "llama3.1",
                }
            },
        }
        with override_settings(NEURAL_CORE=config), patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("ollama", ModelGateway().available())

    def test_background_failure_marks_run_failed(self):
        from agent_app.neural_core import views as neural_views
        from agent_app.neural_core.models import NeuralRun

        run = NeuralRun.objects.create(user=self.user, goal="Arka plan hatası")
        with patch.object(
            neural_views.CognitiveKernel, "run", side_effect=RuntimeError("arka plan bozuk")
        ):
            neural_views._run_background(self.user.id, run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIn("arka plan bozuk", run.error)

    def test_stale_running_runs_are_swept(self):
        from datetime import timedelta

        from django.utils import timezone

        from agent_app.neural_core.kernel import sweep_stale_runs
        from agent_app.neural_core.models import NeuralRun

        run = NeuralRun.objects.create(user=self.user, goal="Takılı kaldı", status="running")
        NeuralRun.objects.filter(id=run.id).update(
            started_at=timezone.now() - timedelta(hours=2),
            updated_at=timezone.now() - timedelta(hours=2),
        )
        changed = sweep_stale_runs(minutes=30)
        self.assertGreaterEqual(changed, 1)
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")

    def test_cycle_skips_when_features_are_disabled(self):
        from datetime import timedelta

        from django.utils import timezone

        from agent_app.neural_core.kernel import run_goal_cycle
        from agent_app.neural_core.models import NeuralGoal

        NeuralGoal.objects.create(
            user=self.user,
            title="Bayrak testi",
            mode="research",
            interval_minutes=60,
            next_run_at=timezone.now() - timedelta(minutes=1),
        )
        with override_settings(NEURAL_CORE=OFFLINE), \
                patch("audit.feature_store.is_enabled", return_value=False):
            result = run_goal_cycle()
        self.assertEqual(result["ran"], 0)
        self.assertTrue(result.get("disabled"))

    def test_subfeature_endpoint_fails_closed_on_flag_error(self):
        from agent_app import tokens

        client = self.client_class()
        client.cookies["emog_at"] = tokens.create_access_token(self.user)
        with override_settings(NEURAL_CORE={**OFFLINE, "SYNC_RUNS": True}), \
                patch("audit.feature_store.is_enabled", side_effect=RuntimeError("db down")):
            response = client.get("/api/neural/tools/")
        self.assertEqual(response.status_code, 403)

    def test_neural_template_escapes_research_hrefs(self):
        from django.template.loader import render_to_string

        html = render_to_string("agent_app/neural.html", {
            "neural_modes": [],
            "neural_runs": [],
            "neural_proposals": [],
            "neural_goals": [],
            "neural_preferences": [],
            "neural_approvals": [],
            "neural_stats": {},
            "neural_tools": [],
        })
        self.assertIn("function safeUrl", html)
        self.assertIn("escapeAttr(safeUrl(finding.url))", html)


class ReasoningTests(SimpleTestCase):
    def test_offline_reasoning_returns_hypotheses_and_trace(self):
        from agent_app.neural_core.gateway import ModelGateway
        from agent_app.neural_core.reasoning import ReasoningEngine, generate_hypotheses

        with override_settings(NEURAL_CORE=OFFLINE):
            engine = ReasoningEngine(None, ModelGateway())
            result = engine.reason("Bir todo uygulaması", "full", {}, [])
            self.assertGreaterEqual(len(result["hypotheses"]), 1)
            self.assertGreaterEqual(len(result["trace"]), 3)
            self.assertTrue(result["conclusion"])
            self.assertIsInstance(result["confidence"], float)
            hyps = generate_hypotheses("Bir todo uygulaması", ModelGateway())
            self.assertGreaterEqual(len(hyps), 1)
            for item in hyps:
                self.assertTrue(item["statement"])
                self.assertIn("evidence", item)
                self.assertIn("confidence", item)

    def test_reasoning_does_not_call_network_offline(self):
        from unittest.mock import patch

        from agent_app.neural_core import gateway as gateway_mod
        from agent_app.neural_core.gateway import ModelGateway
        from agent_app.neural_core.reasoning import ReasoningEngine

        with override_settings(NEURAL_CORE=OFFLINE),                 patch.object(gateway_mod.ModelGateway, "complete") as complete:
            engine = ReasoningEngine(None, ModelGateway())
            engine.reason("Hedef", "solve", {}, [])
            complete.assert_not_called()

    def test_reasoning_llm_hypotheses_are_coerced(self):
        from agent_app.neural_core.reasoning import generate_hypotheses

        class StubGateway:
            def complete(self, *args, **kwargs):
                return (
                    '[{"statement": "H1", "evidence": "E1", "confidence": "high"}, '
                    '{"statement": "H2", "confidence": 80}]'
                )

        with override_settings(NEURAL_CORE={**OFFLINE, "OFFLINE": False}):
            hyps = generate_hypotheses("hedef", StubGateway())
        self.assertEqual(hyps[0]["statement"], "H1")
        self.assertIsInstance(hyps[0]["confidence"], float)
        self.assertEqual(hyps[1]["statement"], "H2")
        self.assertAlmostEqual(hyps[1]["confidence"], 0.8, places=2)

    def test_reasoning_empty_llm_falls_back(self):
        from agent_app.neural_core.reasoning import generate_hypotheses

        class StubGateway:
            def complete(self, *args, **kwargs):
                return ""

        with override_settings(NEURAL_CORE={**OFFLINE, "OFFLINE": False}):
            hyps = generate_hypotheses("hedef", StubGateway())
        self.assertGreaterEqual(len(hyps), 1)


class LearningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="learn@example.com", username="learn-user")
        self.other = User.objects.create(email="learn2@example.com", username="learn2-user")

    def test_learning_extracts_at_least_one_lesson(self):
        from agent_app.neural_core.learning import LearningEngine
        from agent_app.neural_core.models import NeuralRun

        with override_settings(NEURAL_CORE=OFFLINE):
            run = NeuralRun.objects.create(user=self.user, goal="Ogrenme testi", mode="full")
            result = LearningEngine(self.user).learn(run, 60)
        self.assertGreaterEqual(len(result["lessons"]), 1)
        self.assertIn("lessons", result)
        self.assertIn("preference_updates", result)
        self.assertIn("proposals", result)
        self.assertIn("shared_recorded", result)

    def test_learning_low_score_proposes_deep(self):
        from agent_app.neural_core.learning import LearningEngine
        from agent_app.neural_core.models import NeuralRun

        with override_settings(NEURAL_CORE=OFFLINE):
            run = NeuralRun.objects.create(user=self.user, goal="Zor hedef", mode="full")
            result = LearningEngine(self.user).learn(run, 20)
        self.assertTrue(result["proposals"])
        self.assertTrue(
            any(getattr(p, "changes", {}).get("planner.depth") == "deep" for p in result["proposals"])
        )

    def test_learning_success_records_shared(self):
        from agent_app.neural_core.learning import LearningEngine
        from agent_app.neural_core.models import NeuralExperience, NeuralRun

        with override_settings(NEURAL_CORE=OFFLINE):
            run = NeuralRun.objects.create(user=self.user, goal="Basarili hedef", mode="full")
            result = LearningEngine(self.user).learn(run, 85)
        self.assertTrue(result["shared_recorded"])
        self.assertTrue(NeuralExperience.objects.filter(user=self.user, shared=True).exists())

    def test_learning_preferences_written_via_set_value(self):
        from agent_app.neural_core import preferences
        from agent_app.neural_core.learning import LearningEngine
        from agent_app.neural_core.models import NeuralRun

        with override_settings(NEURAL_CORE=OFFLINE):
            run = NeuralRun.objects.create(user=self.user, goal="Pref hedef", mode="full")
            result = LearningEngine(self.user).learn(
                run, 75, context={"preference_updates": {"tone": "kisa"}}
            )
        self.assertEqual(preferences.get_value(self.user, "tone"), "kisa")
        self.assertEqual(result["preference_updates"].get("tone"), "kisa")
class BinaryControlTests(TestCase):
    def setUp(self):
        from pathlib import Path

        self.user = User.objects.create(email="bin@example.com", username="bin-user")
        self.other = User.objects.create(email="bin2@example.com", username="bin2-user")
        self.relative_path = f"task_runs/neural/{self.user.id}/binary_test_output.txt"
        self.absolute = Path(__file__).resolve().parent.parent / self.relative_path
        if self.absolute.exists():
            self.absolute.unlink()

    def tearDown(self):
        if self.absolute.exists():
            self.absolute.unlink()

    def _settings(self):
        return {**OFFLINE, "TOOL_ROOTS": ["task_runs"], "TOOL_REQUIRE_APPROVAL": True}

    def test_binary_control_delegates_list_tools(self):
        from agent_app.neural_core.binary_control import BinaryControlCore
        from agent_app.neural_core.tools import ToolRegistry

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            registry = ToolRegistry(self.user)
            self.assertEqual(core.list_tools(), registry.list_tools())

    def test_binary_control_read_traversal_rejected(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            with self.assertRaises(ValueError):
                core.read_file("../db.sqlite3")
            with self.assertRaises(ValueError):
                core.read_file("C:/Windows/win.ini")

    def test_binary_control_write_without_approval_no_side_effect(self):
        from agent_app.neural_core.binary_control import BinaryControlCore
        from agent_app.neural_core.models import NeuralToolApproval

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            approval = core.request_write(self.relative_path, "onay bekleyen icerik")
        self.assertEqual(approval.status, "pending")
        self.assertFalse(self.absolute.exists())
        self.assertEqual(NeuralToolApproval.objects.filter(status="pending").count(), 1)

    def test_binary_control_approved_write_creates_file(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            approval = core.request_write(self.relative_path, "yazildi")
            result = core.decide(approval.id, True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.absolute.read_text(encoding="utf-8"), "yazildi")

    def test_binary_control_rejected_write_no_file(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            approval = core.request_write(self.relative_path, "hayir")
            result = core.decide(approval.id, False)
        self.assertFalse(result["ok"])
        self.assertFalse(self.absolute.exists())

    def test_binary_control_another_user_cannot_decide(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            approval = core.request_write(self.relative_path, "gizli")
            other_core = BinaryControlCore(self.other)
            with self.assertRaises(LookupError):
                other_core.decide(approval.id, True)
        self.assertFalse(self.absolute.exists())

    def test_binary_control_command_allowlist(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            with self.assertRaises(ValueError):
                core.request_command("powershell Remove-Item C:/Windows/win.ini")

    def test_binary_control_permission_summary(self):
        from agent_app.neural_core.binary_control import BinaryControlCore

        with override_settings(NEURAL_CORE=self._settings()):
            core = BinaryControlCore(self.user)
            summary = core.permission_summary()
        self.assertTrue(summary.get("require_approval") or summary.get("requires_approval"))
        allowlist = summary.get("allowlist") or summary.get("command_allowlist") or []
        self.assertIn("python", allowlist)
