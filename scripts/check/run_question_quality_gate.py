#!/usr/bin/env python3
"""
Run the standard mechanical quality gate for question maintenance.

This script intentionally wraps existing focused checks instead of replacing
them. Use it before merge/upload work to avoid qualification-specific drift.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.requirements import (  # noqa: E402
    DEFAULT_REQUIREMENTS_PATH,
    RequirementsError,
    get_stage_rules,
    load_requirements,
    validate_records,
)

TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})$")


@dataclass(frozen=True)
class PatchStage:
    label: str
    subdir: str
    tag: str
    checker: str
    extra_args: tuple[str, ...] = ()


PATCH_STAGES = (
    PatchStage(
        label="questionType",
        subdir="10_questionType_fixed",
        tag="questionType_fixed",
        checker="scripts/check/check_questiontype_patch_coverage.py",
    ),
    PatchStage(
        label="questionIntent",
        subdir="15_correctChoiceText_fixed",
        tag="correctChoiceText_fixed",
        checker="scripts/check/check_question_intent_patch_coverage.py",
    ),
    PatchStage(
        label="explanationText",
        subdir="21_explanationText_added",
        tag="explanationText_added",
        checker="scripts/check/check_explanation_patch_coverage.py",
    ),
    PatchStage(
        label="questionSetId",
        subdir="22_questionSetId_linked",
        tag="questionSetId_linked",
        checker="scripts/check/check_question_set_patch_coverage.py",
    ),
)


def strip_timestamp_suffix(stem: str) -> str:
    return TIMESTAMP_SUFFIX_PATTERN.sub("", stem)


def source_stem_from_patch_filename(filename: str, patch_tag: str) -> str | None:
    path = Path(filename)
    if path.suffix.lower() != ".json":
        return None
    stem = strip_timestamp_suffix(path.stem)
    suffix = f"_{patch_tag}"
    if not stem.endswith(suffix):
        return None
    return stem[: -len(suffix)]


def timestamp_sort_key(path: Path) -> tuple[int, str, str]:
    match = TIMESTAMP_SUFFIX_PATTERN.search(path.stem)
    if not match:
        return (0, "", path.name)
    return (1, match.group(1), path.name)


def latest_patch_map(patch_dir: Path, patch_tag: str) -> dict[str, Path]:
    selected: dict[str, Path] = {}
    for path in sorted(patch_dir.glob("*.json"), key=timestamp_sort_key):
        source_stem = source_stem_from_patch_filename(path.name, patch_tag)
        if source_stem:
            selected[source_stem] = path
    return selected


def load_records(path: Path, array_key: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get(array_key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def infer_qualification(base_dir: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if base_dir.name == "questions_json":
        return base_dir.parent.name
    return None


def resolve_base_dir(args: argparse.Namespace) -> Path:
    if args.base_dir:
        return Path(args.base_dir).expanduser().resolve()
    if not args.qualification:
        raise ValueError("--qualification or --base-dir is required")
    return (REPO_ROOT / "output" / args.qualification / "questions_json").resolve()


def resolve_category_path(args: argparse.Namespace, base_dir: Path) -> Path | None:
    if args.category:
        return Path(args.category).expanduser().resolve()
    candidate = base_dir.parent / "category" / "category.json"
    return candidate if candidate.exists() else None


def resolve_list_group_dirs(base_dir: Path, list_group_id: str | None) -> list[Path]:
    if list_group_id:
        return [base_dir / list_group_id]
    return sorted(
        path
        for path in base_dir.iterdir()
        if path.is_dir() and (path / "00_source").exists()
    )


def print_heading(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run_command(cmd: list[str]) -> int:
    print("$ " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode


def stage_files(group_dir: Path, stage: str) -> tuple[str, list[Path]]:
    if stage == "source":
        return "question_bodies", sorted((group_dir / "00_source").glob("*.json"))
    if stage == "merged":
        files: list[Path] = []
        for subdir in ("20_merged_1", "30_merged_2"):
            files.extend(sorted((group_dir / subdir).glob("*.json")))
        return "question_bodies", files
    if stage == "firestore":
        return "questions", sorted((group_dir / "40_convert").glob("*_firestore_*.json"))
    raise ValueError(f"unsupported stage: {stage}")


def run_required_checks(
    *,
    base_dir: Path,
    list_group_dirs: Iterable[Path],
    qualification: str | None,
    requirements_path: Path,
    stages: Iterable[str],
) -> int:
    print_heading("requirements")
    try:
        requirements = load_requirements(requirements_path)
    except RequirementsError as exc:
        print(f"[ERROR] {exc}")
        return 1

    failed = 0
    for group_dir in list_group_dirs:
        if not group_dir.exists():
            print(f"[ERROR] list group not found: {group_dir}")
            failed += 1
            continue
        for stage in stages:
            array_key, files = stage_files(group_dir, stage)
            if not files:
                print(f"[ERROR] {group_dir.name}: no files for stage={stage}")
                failed += 1
                continue
            rules = get_stage_rules(
                requirements,
                stage=stage,
                record_array=array_key,
                qualification=qualification,
            )
            stage_errors: list[str] = []
            id_keys = ("questionId",) if stage == "firestore" else (
                "public_question_id",
                "original_question_id",
            )
            for path in files:
                records = load_records(path, array_key)
                stage_errors.extend(
                    validate_records(
                        records=records,
                        rules=rules,
                        source_path=path,
                        id_keys=id_keys,
                    )
                )
            if stage_errors:
                print(f"[NG] {group_dir.name}: stage={stage} errors={len(stage_errors)}")
                for line in stage_errors[:80]:
                    print(line)
                if len(stage_errors) > 80:
                    print(f"... and {len(stage_errors) - 80} more")
                failed += 1
            else:
                print(f"[OK] {group_dir.name}: stage={stage}")
    return 1 if failed else 0


def run_patch_checks(
    *,
    list_group_dirs: Iterable[Path],
    category_path: Path | None,
    require_law_grounded_flag: bool,
    questionset_only: bool,
) -> int:
    print_heading("patch coverage")
    failed = 0
    for group_dir in list_group_dirs:
        source_files = sorted((group_dir / "00_source").glob("question_*.json"))
        if not source_files:
            print(f"[ERROR] {group_dir.name}: no source files")
            failed += 1
            continue

        for stage in PATCH_STAGES:
            if stage.label == "questionSetId" and category_path is None:
                print(f"[ERROR] {group_dir.name}: category.json is required for questionSetId check")
                failed += 1
                continue

            patch_dir = group_dir / stage.subdir
            if not patch_dir.exists():
                print(f"[ERROR] {group_dir.name}: patch directory not found: {patch_dir}")
                failed += 1
                continue

            patch_map = latest_patch_map(patch_dir, stage.tag)
            source_stems = {path.stem for path in source_files}
            for source_path in source_files:
                patch_path = patch_map.get(source_path.stem)
                if patch_path is None:
                    print(f"[ERROR] {group_dir.name}: {stage.label}: patch not found for {source_path.name}")
                    failed += 1
                    continue
                cmd = [
                    sys.executable,
                    stage.checker,
                    "--source",
                    str(source_path),
                    "--patch",
                    str(patch_path),
                    *stage.extra_args,
                ]
                if stage.label == "explanationText" and require_law_grounded_flag:
                    cmd.append("--require-law-grounded-flag")
                if stage.label == "questionSetId":
                    cmd.extend(["--category", str(category_path)])
                    if questionset_only:
                        cmd.append("--questionset-only")
                if run_command(cmd) != 0:
                    failed += 1

            for source_stem, patch_path in sorted(patch_map.items()):
                if source_stem not in source_stems:
                    print(f"[ERROR] {group_dir.name}: {stage.label}: patch has no source: {patch_path.name}")
                    failed += 1
    return 1 if failed else 0


def run_firestore_dry_run(*, list_group_dirs: Iterable[Path]) -> int:
    print_heading("Firestore upload dry-run")
    failed = 0
    for group_dir in list_group_dirs:
        convert_dir = group_dir / "40_convert"
        files = sorted(convert_dir.glob("*_firestore_*.json"))
        if not files:
            print(f"[ERROR] {group_dir.name}: no Firestore JSON in {convert_dir}")
            failed += 1
            continue
        if run_command(
            [
                sys.executable,
                "scripts/upload/upload_questions_to_firestore.py",
                str(convert_dir),
                "--dry-run",
            ]
        ) != 0:
            failed += 1
    return 1 if failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the standard question maintenance quality gate.",
    )
    parser.add_argument("--qualification", help="Qualification code under output/<qualification>.")
    parser.add_argument("--base-dir", help="questions_json base dir.")
    parser.add_argument("--list-group-id", help="Limit checks to one list_group_id.")
    parser.add_argument("--category", help="category.json path. Defaults to output/<qualification>/category/category.json.")
    parser.add_argument(
        "--mode",
        choices=("full", "required", "patches", "firestore"),
        default="full",
        help="Which gate subset to run.",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=DEFAULT_REQUIREMENTS_PATH,
        help="requirements TOML path.",
    )
    parser.add_argument(
        "--skip-upload-dry-run",
        action="store_true",
        help="Skip upload_questions_to_firestore.py --dry-run in full/firestore mode.",
    )
    parser.add_argument(
        "--require-law-grounded-flag",
        action="store_true",
        help="Require lawGroundedExplanationNotNeeded on every explanation patch entry.",
    )
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="Pass --questionset-only to questionSetId coverage checks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_dir = resolve_base_dir(args)
    if not base_dir.exists():
        print(f"[ERROR] base dir not found: {base_dir}")
        return 2

    qualification = infer_qualification(base_dir, args.qualification)
    list_group_dirs = resolve_list_group_dirs(base_dir, args.list_group_id)
    if not list_group_dirs:
        print(f"[ERROR] no list groups found under {base_dir}")
        return 2

    category_path = resolve_category_path(args, base_dir)
    print(f"base_dir: {base_dir}", flush=True)
    print(f"qualification: {qualification or '(unknown)'}", flush=True)
    print("list_group_ids: " + ", ".join(path.name for path in list_group_dirs), flush=True)
    print(f"category: {category_path or '(not found)'}", flush=True)

    failures = 0
    if args.mode in ("full", "required"):
        stages = ("source", "merged", "firestore") if args.mode == "full" else ("source", "merged")
        failures += run_required_checks(
            base_dir=base_dir,
            list_group_dirs=list_group_dirs,
            qualification=qualification,
            requirements_path=args.requirements,
            stages=stages,
        )

    if args.mode in ("full", "patches"):
        failures += run_patch_checks(
            list_group_dirs=list_group_dirs,
            category_path=category_path,
            require_law_grounded_flag=args.require_law_grounded_flag,
            questionset_only=args.questionset_only,
        )

    if args.mode in ("full", "firestore") and not args.skip_upload_dry_run:
        failures += run_firestore_dry_run(list_group_dirs=list_group_dirs)

    if failures:
        print(f"\n[NG] question quality gate failed: sections_with_failures={failures}")
        return 1
    print("\n[OK] question quality gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
