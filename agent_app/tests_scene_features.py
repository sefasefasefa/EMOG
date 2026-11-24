from django.test import TestCase, Client

from agent_app import entity_system, scene_generation


class EntitySystemHelpersTests(TestCase):
    def test_normalize_animation_defaults(self):
        anim = entity_system.normalize_animation({})
        self.assertIn("clips", anim)
        self.assertIn("pose", anim)
        self.assertIn("keyframes", anim)
        self.assertIsInstance(anim["clips"], list)
        self.assertIsInstance(anim["pose"], dict)

    def test_normalize_physics_defaults(self):
        phys = entity_system.normalize_physics({})
        for key in ["enabled", "body", "mass", "collider", "gravity", "damping", "velocity", "angular_velocity"]:
            self.assertIn(key, phys)
        self.assertEqual(len(phys["gravity"]), 3)
        self.assertEqual(len(phys["velocity"]), 3)

    def test_default_and_normalize_timeline(self):
        tl = entity_system.default_timeline(fps=24, duration=5)
        self.assertEqual(tl["fps"], 24)
        self.assertEqual(tl["duration"], 5.0)
        self.assertIn("tracks", tl)
        self.assertIn("keyframes", tl)

        tl2 = entity_system.normalize_timeline({"fps": 0, "duration": 5, "keyframes": [{"time": 2, "node_id": "n1"}, {"time": 1, "node_id": "n1"}]})
        self.assertGreaterEqual(tl2["fps"], 1)
        self.assertEqual(tl2["duration"], 5.0)
        self.assertEqual(tl2["keyframes"][0]["time"], 1.0)
        self.assertEqual(tl2["keyframes"][1]["time"], 2.0)

        tl3 = entity_system.normalize_timeline({"fps": -1, "duration": -1, "keyframes": [{"time": 5}]})
        self.assertGreaterEqual(tl3["fps"], 1)
        self.assertGreaterEqual(tl3["duration"], 0.1)
        self.assertLessEqual(tl3["keyframes"][0]["time"], tl3["duration"])

    def test_timeline_state_at_interpolation(self):
        tl = {
            "fps": 30,
            "duration": 10,
            "tracks": [],
            "keyframes": [
                {"time": 0, "node_id": "n1", "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}},
                {"time": 2, "node_id": "n1", "transform": {"position": [2, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}},
            ],
        }
        st = entity_system.timeline_state_at(tl, 1.0)
        self.assertIn("n1", st["nodes"])
        pos = st["nodes"]["n1"]["transform"]["position"]
        self.assertAlmostEqual(pos[0], 1.0, places=5)

    def test_step_physics_gravity_and_ground(self):
        tr = {"position": [0, 1, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        phys = {"enabled": True, "body": "dynamic", "mass": 1.0, "velocity": [0, 0, 0], "gravity": [0, -10, 0], "damping": 0.0}
        tr2, ph2 = entity_system.step_physics(tr, phys, 0.1)
        self.assertLess(tr2["position"][1], 1.0)
        self.assertLess(ph2["velocity"][1], 0.0)

        tr3 = {"position": [0, -1, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}
        ph3 = {"enabled": True, "body": "dynamic", "mass": 1.0, "velocity": [0, -2, 0], "gravity": [0, -10, 0], "damping": 0.0}
        tr4, ph4 = entity_system.step_physics(tr3, ph3, 0.1)
        self.assertGreaterEqual(tr4["position"][1], 0.0)
        self.assertEqual(ph4["velocity"][1], 0.0)

    def test_apply_action_to_components(self):
        comps = {"animation": entity_system.normalize_animation({}), "physics": entity_system.normalize_physics({}), "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}}
        new_comps, action = entity_system.apply_action_to_components(comps, "walk")
        self.assertIsNotNone(action)
        self.assertEqual(new_comps["animation"].get("active_action"), "walk")
        self.assertTrue(isinstance(new_comps["animation"].get("pose"), dict))

        new_comps2, action2 = entity_system.apply_action_to_components(new_comps, "idle")
        self.assertIsNotNone(action2)
        self.assertEqual(new_comps2["animation"].get("pose"), {})

    def test_get_pose_preset(self):
        pose = entity_system.get_pose_preset("wave")
        self.assertIsInstance(pose, dict)
        self.assertIsNone(entity_system.get_pose_preset("nonexistent_preset_xyz"))


class SceneGenerationInfoTests(TestCase):
    def test_provider_info_structure(self):
        info = scene_generation.get_provider_info()
        self.assertTrue(info.get("ok"))
        providers = info.get("providers") or []
        self.assertTrue(any(p.get("id") == "procedural" and p.get("available") for p in providers))
        # external providers present (may be unavailable without keys)
        ids = {p.get("id") for p in providers}
        self.assertIn("meshy", ids)
        self.assertIn("tripo", ids)
        self.assertIn("external_text_to_3d", ids)


class LearnedModelsExportTests(TestCase):
    def test_export_functions_return_lists(self):
        from agent_app.learned_models import export_entity_training_examples, export_video_training_examples
        ent = export_entity_training_examples(limit=5)
        vid = export_video_training_examples(limit=5)
        self.assertIsInstance(ent, list)
        self.assertIsInstance(vid, list)