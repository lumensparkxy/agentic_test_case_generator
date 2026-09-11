from pathlib import Path
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.guidance_service import build_manifest, guidance_scope, guidance_text, skill_catalog, submit_with_guidance, current_guidance
from app.contracts.guidance import MemoryGuidance


class GuidanceTests(unittest.TestCase):
    def test_defaults_do_not_enable_features(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(build_manifest("use_cases", "test").skills, ())
            self.assertFalse(build_manifest("use_cases", "test").memory_enabled)

    def test_skills_load_through_adk_and_hash_stably(self):
        self.assertEqual(len(skill_catalog()), 4)
        with patch.dict("os.environ", {"ADK_SKILLS_ENABLED": "true"}):
            a = build_manifest("use_cases", "test")
            self.assertEqual(a, build_manifest("use_cases", "test"))
            self.assertIn("boundary", a.skills[0].description)
            with self.assertRaises(Exception):
                a.stage = "test_cases"

    def test_bounds_and_explicit_bypass(self):
        entries = [MemoryGuidance(id=str(i), revision=1, scope="project", text="x" * 1000, source="review", reason="stage") for i in range(8)]
        with patch.dict("os.environ", {"ADK_MEMORY_ENABLED": "true"}):
            manifest = build_manifest("test_cases", "test", memories=entries)
            self.assertEqual(len(manifest.memories), 6)
            self.assertEqual(len(manifest.omitted), 2)
            self.assertEqual(build_manifest("test_cases", "test", memories=entries, memory_bypass=True).memories, ())

    def test_parallel_workers_receive_same_immutable_manifest_and_reset(self):
        with guidance_scope(build_manifest("use_cases", "test")) as manifest, ThreadPoolExecutor(2) as pool:
            self.assertEqual(submit_with_guidance(pool, current_guidance).result(), manifest)
        self.assertIsNone(current_guidance())
        self.assertEqual(guidance_text(), "")
