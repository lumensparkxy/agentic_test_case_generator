import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts import cleanup_generated_artifacts


class CleanupGeneratedArtifactsTests(unittest.TestCase):
    def test_default_targets_include_execution_client_and_e2e_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            targets = cleanup_generated_artifacts.default_targets(root)

        self.assertEqual(targets[0], root / ".execution_artifacts")
        self.assertEqual(targets[1], root / "client_submission")
        self.assertEqual(targets[2], Path("/tmp/pw_workflow_out"))

    def test_dry_run_selects_only_files_older_than_retention_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "client_submission"
            old_file = target / "screenshots" / "old.png"
            fresh_file = target / "screenshots" / "fresh.png"
            old_file.parent.mkdir(parents=True)
            old_file.write_text("old", encoding="utf-8")
            fresh_file.write_text("fresh", encoding="utf-8")
            now = time.time()
            old_mtime = now - (3 * 24 * 60 * 60)
            fresh_mtime = now - (60 * 60)

            os.utime(old_file, (old_mtime, old_mtime))
            os.utime(fresh_file, (fresh_mtime, fresh_mtime))

            plan = cleanup_generated_artifacts.build_cleanup_plan(
                repo_root=root,
                target_paths=[target],
                max_age_days=1,
                now=now,
                allow_unignored_targets=True,
                tracked_paths=set(),
            )

            self.assertEqual([candidate.path for candidate in plan.candidates], [old_file])
            self.assertEqual(plan.retained, [fresh_file])
            self.assertTrue(old_file.exists())
            self.assertTrue(fresh_file.exists())

    def test_apply_deletes_old_files_and_prunes_empty_child_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / ".execution_artifacts"
            old_file = target / "exec_old" / "trace.zip"
            old_file.parent.mkdir(parents=True)
            old_file.write_text("trace", encoding="utf-8")
            now = time.time()
            old_mtime = now - (15 * 24 * 60 * 60)

            os.utime(old_file, (old_mtime, old_mtime))

            plan = cleanup_generated_artifacts.build_cleanup_plan(
                repo_root=root,
                target_paths=[target],
                max_age_days=14,
                now=now,
                apply=True,
                allow_unignored_targets=True,
                tracked_paths=set(),
            )

            self.assertEqual([candidate.path for candidate in plan.candidates], [old_file])
            self.assertFalse(old_file.exists())
            self.assertFalse(old_file.parent.exists())
            self.assertTrue(target.exists())

    def test_tracked_files_are_skipped_even_inside_cleanup_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "client_submission"
            tracked_file = target / "tracked.txt"
            tracked_file.parent.mkdir(parents=True)
            tracked_file.write_text("source", encoding="utf-8")
            now = time.time()
            old_mtime = now - (30 * 24 * 60 * 60)

            os.utime(tracked_file, (old_mtime, old_mtime))

            plan = cleanup_generated_artifacts.build_cleanup_plan(
                repo_root=root,
                target_paths=[target],
                max_age_days=1,
                now=now,
                allow_unignored_targets=True,
                tracked_paths={"client_submission/tracked.txt"},
            )

            self.assertEqual(plan.candidates, [])
            self.assertEqual(plan.skipped_tracked, [tracked_file])

    def test_in_repo_unignored_target_is_rejected_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "docs"
            target.mkdir()

            plan = cleanup_generated_artifacts.build_cleanup_plan(
                repo_root=root,
                target_paths=[target],
                max_age_days=1,
                tracked_paths=set(),
            )

            self.assertEqual(plan.candidates, [])
            self.assertEqual(
                plan.unsafe_targets,
                [(target, "target is inside the repository but is not ignored by git")],
            )

    def test_apply_does_not_delete_candidates_when_any_target_is_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as repo_tmpdir, tempfile.TemporaryDirectory() as outside_tmpdir:
            root = Path(repo_tmpdir)
            safe_target = Path(outside_tmpdir)
            old_file = safe_target / "old-export.json"
            old_file.write_text("{}", encoding="utf-8")
            unsafe_target = root / "docs"
            unsafe_target.mkdir()
            now = time.time()
            old_mtime = now - (30 * 24 * 60 * 60)
            os.utime(old_file, (old_mtime, old_mtime))

            plan = cleanup_generated_artifacts.build_cleanup_plan(
                repo_root=root,
                target_paths=[safe_target, unsafe_target],
                max_age_days=1,
                now=now,
                apply=True,
                tracked_paths=set(),
            )

            self.assertEqual([candidate.path for candidate in plan.candidates], [old_file])
            self.assertEqual(
                plan.unsafe_targets,
                [(unsafe_target, "target is inside the repository but is not ignored by git")],
            )
            self.assertTrue(old_file.exists())


if __name__ == "__main__":
    unittest.main()
