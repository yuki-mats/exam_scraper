#!/usr/bin/env python3
"""
Unified CLI for daily question-bank maintenance.

The public entrypoint is this file under tools/question_bank. Existing scripts
under scripts/ remain implementation details or backwards-compatible wrappers.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
    required_by_default: bool = True


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
        checker="tools/question_bank/checks/check_question_intent_patch_coverage.py",
    ),
    PatchStage(
        label="lawContext",
        subdir="18_law_context_prepared",
        tag="lawContext_prepared",
        checker="scripts/check/check_law_context_patch_coverage.py",
        required_by_default=False,
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
    source_stem = stem[: -len(suffix)]
    if source_stem.endswith("_merged"):
        source_stem = source_stem[: -len("_merged")]
    return source_stem


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


def organize_report_files(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root).expanduser().resolve() if args.output_root else REPO_ROOT / "output"
    qualification = str(args.qualification).strip()
    if not qualification:
        print("[ERROR] --qualification is required")
        return 2

    files = sorted(output_root.glob(f"{qualification}-*.json"))
    report_dir = output_root / qualification / "reports"
    if not files:
        print(f"[OK] no root-level reports found for {qualification} under {output_root}")
        return 0

    conflicts = [path for path in files if (report_dir / path.name).exists()]
    if conflicts:
        print(f"[ERROR] destination already exists under {report_dir}")
        for path in conflicts[:50]:
            print(f"- {path.name}")
        if len(conflicts) > 50:
            print(f"... and {len(conflicts) - 50} more")
        return 1

    for source_path in files:
        dest_path = report_dir / source_path.name
        if args.dry_run:
            print(f"[DRY-RUN] {source_path.relative_to(REPO_ROOT)} -> {dest_path.relative_to(REPO_ROOT)}")
            continue
        report_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(dest_path))
        print(f"[MOVE] {source_path.relative_to(REPO_ROOT)} -> {dest_path.relative_to(REPO_ROOT)}")

    action = "would move" if args.dry_run else "moved"
    print(f"[OK] {action} {len(files)} report file(s) into {report_dir.relative_to(REPO_ROOT)}")
    return 0


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
    require_is_law_related: bool,
    require_law_context_stage: bool,
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
                if not stage.required_by_default and not require_law_context_stage:
                    continue
                print(f"[ERROR] {group_dir.name}: patch directory not found: {patch_dir}")
                failed += 1
                continue

            patch_map = latest_patch_map(patch_dir, stage.tag)
            source_stems = {path.stem for path in source_files}
            for source_path in source_files:
                patch_path = patch_map.get(source_path.stem)
                if patch_path is None:
                    if not stage.required_by_default and not require_law_context_stage:
                        continue
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
                if stage.label == "explanationText" and require_is_law_related:
                    cmd.append("--require-is-law-related")
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


def add_quality_gate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "quality-gate",
        aliases=("gate", "check"),
        help="Run the standard mechanical quality gate.",
        description="Run the standard mechanical quality gate for question maintenance.",
    )
    parser.set_defaults(command="quality-gate")
    add_quality_gate_arguments(parser)


def add_explanation_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-explanation-patch",
        help="Validate one explanationText patch file.",
    )
    parser.set_defaults(command="check-explanation-patch")
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to explanationText patch JSON.")
    parser.add_argument(
        "--require-law-grounded-flag",
        action="store_true",
        help="Require compatibility lawGroundedExplanationNotNeeded on every patch entry.",
    )
    parser.add_argument(
        "--require-is-law-related",
        action="store_true",
        help="Require isLawRelated on every patch entry.",
    )


def add_law_context_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-law-context-patch",
        help="Validate one pre-explanation law context patch file.",
    )
    parser.set_defaults(command="check-law-context-patch")
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to law context patch JSON.")


def add_question_set_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-question-set-patch",
        help="Validate one questionSetId patch file.",
    )
    parser.set_defaults(command="check-question-set-patch")
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to questionSetId patch JSON.")
    parser.add_argument("--category", required=True, help="Path to category.json.")
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="Use only category.questionSets[].questionSetId as valid IDs.",
    )


def add_question_type_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-question-type-patch",
        help="Validate one questionType patch file.",
    )
    parser.set_defaults(command="check-question-type-patch")
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to questionType patch JSON.")


def add_question_intent_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-question-intent-patch",
        help="Validate one questionIntent patch file.",
    )
    parser.set_defaults(command="check-question-intent-patch")
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--patch", required=True, help="Path to questionIntent patch JSON.")


def add_materialize_patch_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "materialize-patch",
        help="Convert minimal raw AI JSON into a formal patch JSON.",
    )
    parser.set_defaults(command="materialize-patch")
    parser.add_argument(
        "--task",
        required=True,
        choices=(
            "question_type",
            "question_intent",
            "correct_choice",
            "law_context",
            "explanation",
            "question_set",
        ),
        help="Patch task to materialize.",
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json.")
    parser.add_argument("--raw", required=True, help="Path to raw AI output JSON.")
    parser.add_argument("--output", required=True, help="Output path for the formal patch JSON.")


def add_organize_reports_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "organize-reports",
        help="Move root-level generated report JSONs into output/<qualification>/reports/.",
    )
    parser.set_defaults(command="organize-reports")
    parser.add_argument("--qualification", required=True, help="Report prefix and output qualification directory.")
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "output", help="Output root directory.")
    parser.add_argument("--dry-run", action="store_true", help="Show moves without changing files.")


def add_quality_gate_arguments(parser: argparse.ArgumentParser) -> None:
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
        help="Require compatibility lawGroundedExplanationNotNeeded on every explanation patch entry.",
    )
    parser.add_argument(
        "--require-is-law-related",
        action="store_true",
        help="Require isLawRelated on every explanation patch entry.",
    )
    parser.add_argument(
        "--require-law-context-stage",
        action="store_true",
        help="Require 18_law_context_prepared patches in quality-gate.",
    )
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="Pass --questionset-only to questionSetId coverage checks.",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Unified question-bank maintenance tool. "
            "Use `quality-gate` for daily mechanical checks."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    add_quality_gate_parser(subparsers)
    add_question_type_patch_parser(subparsers)
    add_question_intent_patch_parser(subparsers)
    add_law_context_patch_parser(subparsers)
    add_explanation_patch_parser(subparsers)
    add_question_set_patch_parser(subparsers)
    add_materialize_patch_parser(subparsers)
    add_organize_reports_parser(subparsers)
    add_quality_gate_arguments(parser)
    return parser.parse_args(argv)


def run_question_type_patch_check(args: argparse.Namespace) -> int:
    return run_command(
        [
            sys.executable,
            "scripts/check/check_questiontype_patch_coverage.py",
            "--source",
            args.source,
            "--patch",
            args.patch,
        ]
    )


def run_question_intent_patch_check(args: argparse.Namespace) -> int:
    return run_command(
        [
            sys.executable,
            "tools/question_bank/checks/check_question_intent_patch_coverage.py",
            "--source",
            args.source,
            "--patch",
            args.patch,
        ]
    )


def run_law_context_patch_check(args: argparse.Namespace) -> int:
    return run_command(
        [
            sys.executable,
            "scripts/check/check_law_context_patch_coverage.py",
            "--source",
            args.source,
            "--patch",
            args.patch,
        ]
    )


def run_explanation_patch_check(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/check/check_explanation_patch_coverage.py",
        "--source",
        args.source,
        "--patch",
        args.patch,
    ]
    if args.require_law_grounded_flag:
        cmd.append("--require-law-grounded-flag")
    if args.require_is_law_related:
        cmd.append("--require-is-law-related")
    return run_command(cmd)


def run_question_set_patch_check(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/check/check_question_set_patch_coverage.py",
        "--source",
        args.source,
        "--patch",
        args.patch,
        "--category",
        args.category,
    ]
    if args.questionset_only:
        cmd.append("--questionset-only")
    return run_command(cmd)


def run_materialize_patch(args: argparse.Namespace) -> int:
    return run_command(
        [
            sys.executable,
            "scripts/fix/materialize_minimal_patch.py",
            "--task",
            args.task,
            "--source",
            args.source,
            "--raw",
            args.raw,
            "--output",
            args.output,
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "check-question-type-patch":
        return run_question_type_patch_check(args)
    if args.command == "check-question-intent-patch":
        return run_question_intent_patch_check(args)
    if args.command == "check-law-context-patch":
        return run_law_context_patch_check(args)
    if args.command == "check-explanation-patch":
        return run_explanation_patch_check(args)
    if args.command == "check-question-set-patch":
        return run_question_set_patch_check(args)
    if args.command == "materialize-patch":
        return run_materialize_patch(args)
    if args.command == "organize-reports":
        return organize_report_files(args)

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
            require_is_law_related=args.require_is_law_related,
            require_law_context_stage=args.require_law_context_stage,
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
