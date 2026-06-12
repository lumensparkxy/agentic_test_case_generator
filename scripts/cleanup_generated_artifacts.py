#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_AGE_DAYS = 14.0


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    size_bytes: int
    age_days: float


@dataclass
class CleanupPlan:
    repo_root: Path
    targets: list[Path]
    candidates: list[CleanupCandidate] = field(default_factory=list)
    retained: list[Path] = field(default_factory=list)
    missing_targets: list[Path] = field(default_factory=list)
    unsafe_targets: list[tuple[Path, str]] = field(default_factory=list)
    skipped_tracked: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def candidate_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)


def default_targets(repo_root: Path = REPO_ROOT) -> list[Path]:
    repo_root = _absolute_path(repo_root)
    return [
        repo_root / ".execution_artifacts",
        repo_root / "client_submission",
        Path("/tmp/pw_workflow_out"),
    ]


def normalize_targets(values: Iterable[str] | None, repo_root: Path = REPO_ROOT) -> list[Path]:
    if values is None:
        return default_targets(repo_root)
    repo_root = _absolute_path(repo_root)
    targets: list[Path] = []
    for value in values:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        targets.append(_absolute_path(path))
    return targets


def build_cleanup_plan(
    *,
    repo_root: Path = REPO_ROOT,
    target_paths: Iterable[Path] | None = None,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    now: float | None = None,
    apply: bool = False,
    allow_unignored_targets: bool = False,
    tracked_paths: set[str] | None = None,
) -> CleanupPlan:
    if max_age_days < 0:
        raise ValueError("max_age_days must be greater than or equal to 0")

    repo_root = _absolute_path(repo_root)
    targets = list(target_paths) if target_paths is not None else default_targets(repo_root)
    targets = [_absolute_path(target) for target in targets]
    tracked = tracked_paths if tracked_paths is not None else _git_tracked_paths(repo_root)
    cutoff = (now if now is not None else time.time()) - (max_age_days * 24 * 60 * 60)
    plan = CleanupPlan(repo_root=repo_root, targets=targets)

    for target in targets:
        if not target.exists() and not target.is_symlink():
            plan.missing_targets.append(target)
            continue

        repo_relative = _repo_relative(target, repo_root)
        if repo_relative is not None and not allow_unignored_targets and not _is_git_ignored(repo_root, repo_relative):
            plan.unsafe_targets.append((target, "target is inside the repository but is not ignored by git"))
            continue

        if target.is_file() or target.is_symlink():
            _review_path(target, cutoff=cutoff, now=now, plan=plan, tracked_paths=tracked)
            continue

        for current_root, dirnames, filenames in os.walk(target, topdown=True, followlinks=False):
            dirnames[:] = [name for name in dirnames if not (Path(current_root) / name).is_symlink()]
            for filename in filenames:
                _review_path(Path(current_root) / filename, cutoff=cutoff, now=now, plan=plan, tracked_paths=tracked)

    if apply and not plan.unsafe_targets and not plan.errors:
        apply_cleanup(plan)
    return plan


def apply_cleanup(plan: CleanupPlan) -> None:
    for candidate in plan.candidates:
        try:
            candidate.path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            plan.errors.append(f"failed to delete {candidate.path}: {exc}")

    for target in plan.targets:
        if not target.exists() or target.is_file() or target.is_symlink():
            continue
        for current_root, dirnames, _filenames in os.walk(target, topdown=False, followlinks=False):
            current = Path(current_root)
            if current == target:
                continue
            try:
                current.rmdir()
            except OSError:
                pass


def _review_path(
    path: Path,
    *,
    cutoff: float,
    now: float | None,
    plan: CleanupPlan,
    tracked_paths: set[str],
) -> None:
    repo_relative = _repo_relative(path, plan.repo_root)
    if repo_relative is not None and repo_relative in tracked_paths:
        plan.skipped_tracked.append(path)
        return

    try:
        stat_result = path.lstat()
    except OSError as exc:
        plan.errors.append(f"failed to stat {path}: {exc}")
        return

    if stat_result.st_mtime <= cutoff:
        current_time = now if now is not None else time.time()
        plan.candidates.append(
            CleanupCandidate(
                path=path,
                size_bytes=stat_result.st_size,
                age_days=max(0.0, (current_time - stat_result.st_mtime) / (24 * 60 * 60)),
            )
        )
    else:
        plan.retained.append(path)


def _git_tracked_paths(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return set()
    return {path.decode("utf-8") for path in result.stdout.split(b"\0") if path}


def _is_git_ignored(repo_root: Path, relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative_path],
        cwd=repo_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _repo_relative(path: Path, repo_root: Path) -> str | None:
    absolute = _absolute_path(path)
    try:
        relative = absolute.relative_to(repo_root)
    except ValueError:
        return None
    return "" if relative == Path(".") else relative.as_posix()


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _human_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def print_plan(plan: CleanupPlan, *, apply: bool, max_age_days: float) -> None:
    mode = "Deleted" if apply else "Dry run: would delete"
    print(f"Generated artifact cleanup ({'apply' if apply else 'dry-run'})")
    print(f"Retention window: files older than {max_age_days:g} day(s)")
    print("Targets:")
    for target in plan.targets:
        print(f"- {target}")

    for target in plan.missing_targets:
        print(f"Missing target skipped: {target}")
    for target, reason in plan.unsafe_targets:
        print(f"Unsafe target skipped: {target} ({reason})")
    for path in plan.skipped_tracked:
        print(f"Tracked file skipped: {path}")

    for candidate in sorted(plan.candidates, key=lambda item: str(item.path)):
        print(f"{mode}: {candidate.path} ({_human_bytes(candidate.size_bytes)}, {candidate.age_days:.1f} days old)")

    print(
        "Summary: "
        f"{len(plan.candidates)} file(s), "
        f"{_human_bytes(plan.candidate_bytes)}, "
        f"{len(plan.retained)} retained, "
        f"{len(plan.skipped_tracked)} tracked skipped"
    )
    for error in plan.errors:
        print(f"Error: {error}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Dry-run or delete ignored generated artifacts from .execution_artifacts, client_submission, and /tmp/pw_workflow_out.")
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete matching files. Without this flag the command only prints a dry-run plan.",
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"Delete files older than this many days when --apply is used. Default: {DEFAULT_MAX_AGE_DAYS:g}.",
    )
    parser.add_argument(
        "--target",
        action="append",
        help="Generated artifact directory or file to scan. May be repeated. Defaults to the documented artifact roots.",
    )
    parser.add_argument(
        "--allow-unignored-target",
        action="store_true",
        help="Allow an in-repository target that is not ignored by git. Tracked files are still skipped.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = _absolute_path(Path(args.repo_root))
    targets = normalize_targets(args.target, repo_root=repo_root)
    try:
        plan = build_cleanup_plan(
            repo_root=repo_root,
            target_paths=targets,
            max_age_days=args.max_age_days,
            apply=args.apply,
            allow_unignored_targets=args.allow_unignored_target,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print_plan(plan, apply=args.apply, max_age_days=args.max_age_days)
    if plan.unsafe_targets or plan.errors:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
