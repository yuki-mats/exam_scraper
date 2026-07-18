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
from tools.question_bank.question_issue_reports import (  # noqa: E402
    add_question_issue_report_parsers,
    run_question_issue_report_command,
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
    require_law_revision_facts: bool,
    require_law_evidence_utilization: bool,
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
                if stage.label == "explanationText" and require_law_revision_facts:
                    cmd.append("--require-law-revision-facts")
                if stage.label == "explanationText" and require_law_evidence_utilization:
                    cmd.append("--require-law-evidence-utilization")
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
        correction_dir = group_dir / "24_questionIssueCorrections"
        for correction_path in sorted(correction_dir.glob("*.json")):
            if run_command(
                [
                    sys.executable,
                    "scripts/check/check_question_issue_correction_patch.py",
                    "--patch",
                    str(correction_path),
                ]
            ) != 0:
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


def run_law_revision_fact_coverage_checks(
    *,
    list_group_dirs: Iterable[Path],
    stage: str,
    fail_on_hold: bool,
    require_evidence_summary: bool,
    require_law_references: bool,
    require_current_correct_choice: bool,
) -> int:
    print_heading("lawRevisionFacts coverage")
    failed = 0
    for group_dir in list_group_dirs:
        cmd = [
            sys.executable,
            "scripts/check/check_law_revision_fact_coverage.py",
            "--list-group-dir",
            str(group_dir),
            "--stage",
            stage,
            "--require-all-law-related",
        ]
        if fail_on_hold:
            cmd.append("--fail-on-hold")
        if require_evidence_summary:
            cmd.append("--require-evidence-summary")
        if require_law_references:
            cmd.append("--require-law-references")
        if require_current_correct_choice:
            cmd.append("--require-current-correct-choice")
        if run_command(cmd) != 0:
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
    parser.add_argument(
        "--require-law-revision-facts",
        action="store_true",
        help="Require lawRevisionFacts when isLawRelated=true.",
    )
    parser.add_argument(
        "--require-law-evidence-utilization",
        action="store_true",
        help=(
            "Require law-related explanationText/suggestedQuestions/"
            "suggestedQuestionDetails to reflect existing law evidence."
        ),
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


def add_law_revision_fact_coverage_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "check-law-revision-facts",
        help="Check lawRevisionFacts coverage on merged or Firestore records.",
    )
    parser.set_defaults(command="check-law-revision-facts")
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--stage", choices=("merged", "firestore"), default="firestore")
    parser.add_argument("--require-all-law-related", action="store_true")
    parser.add_argument("--fail-on-hold", action="store_true")
    parser.add_argument("--require-evidence-summary", action="store_true")
    parser.add_argument("--require-law-references", action="store_true")
    parser.add_argument("--require-current-correct-choice", action="store_true")
    parser.add_argument("--report", type=Path)


def add_law_revision_audit_queue_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "build-law-revision-audit-queue",
        help="Build a JSONL review queue for law-related records missing lawRevisionFacts.",
    )
    parser.set_defaults(command="build-law-revision-audit-queue")
    parser.add_argument("--list-group-dir", required=True, type=Path)
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--include-hold", action="store_true")
    parser.add_argument("--require-snapshots", action="store_true")
    parser.add_argument("--snippet-chars", type=int, default=600)


def add_law_revision_hold_materialize_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "materialize-law-revision-hold-facts",
        help="Materialize hold lawRevisionFacts from an audit queue into an explanation patch.",
    )
    parser.set_defaults(command="materialize-law-revision-hold-facts")
    parser.add_argument("--queue-jsonl", required=True, type=Path)
    parser.add_argument("--explanation-patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite-existing", action="store_true")
    parser.add_argument("--skip-missing-patch-ids", action="store_true")


def add_review_ui_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "review-ui",
        help="Open the local exception-first question review console.",
    )
    parser.set_defaults(command="review-ui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--qualification")
    parser.add_argument("--list-group-id")
    parser.add_argument("--tailscale-origin")
    parser.add_argument("--tailscale-login", action="append", default=[])
    parser.add_argument("--tailscale-source-ip", action="append", default=[])


def add_work_version_backfill_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "backfill-work-versions",
        help="Assign legacy v0.0 work versions to every active published question.",
    )
    parser.set_defaults(command="backfill-work-versions")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write local work_versions.json files. Without this flag, read-only dry-run.",
    )
    parser.add_argument("--credentials-json", type=Path)


def add_work_version_migration_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "migrate-work-versions",
        help="Normalize local work versions to MAJOR.MINOR strings.",
    )
    parser.set_defaults(command="migrate-work-versions")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Rewrite validated work_versions.json files. Without this flag, dry-run.",
    )


def add_work_version_invalidation_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "invalidate-work-version-run",
        help="Invalidate one successful run/stage so the same questions can be maintained again.",
    )
    parser.set_defaults(command="invalidate-work-version-run")
    parser.add_argument("--qualification", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write invalidation records and receipt. Without this flag, dry-run.",
    )


def add_quality_gate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--qualification", help="Qualification code under output/<qualification>.")
    parser.add_argument("--base-dir", help="questions_json base dir.")
    parser.add_argument("--list-group-id", help="Limit checks to one list_group_id.")
    parser.add_argument("--category", help="category.json path. Defaults to output/<qualification>/category/category.json.")
    parser.add_argument(
        "--mode",
        choices=("full", "source", "required", "patches", "firestore"),
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
        "--require-law-revision-facts",
        action="store_true",
        help=(
            "Require lawRevisionFacts when isLawRelated=true in explanation patches "
            "and in merged/Firestore records for the selected quality-gate mode."
        ),
    )
    parser.add_argument(
        "--require-law-evidence-utilization",
        action="store_true",
        help=(
            "Require law-related explanation patches to use existing law evidence "
            "in explanationText/suggestedQuestions/suggestedQuestionDetails."
        ),
    )
    parser.add_argument(
        "--fail-on-law-revision-hold",
        action="store_true",
        help="Fail quality-gate when lawRevisionFacts.auditStatus=hold remains.",
    )
    parser.add_argument(
        "--require-law-revision-evidence-summary",
        action="store_true",
        help="Require lawRevisionFacts.evidenceSummary on law-related records.",
    )
    parser.add_argument(
        "--require-law-references-for-law-related",
        action="store_true",
        help="Require lawReferences on records where isLawRelated=true during lawRevisionFacts gate.",
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
    add_law_revision_fact_coverage_parser(subparsers)
    add_law_revision_audit_queue_parser(subparsers)
    add_law_revision_hold_materialize_parser(subparsers)
    add_review_ui_parser(subparsers)
    add_work_version_backfill_parser(subparsers)
    add_work_version_migration_parser(subparsers)
    add_work_version_invalidation_parser(subparsers)
    add_question_issue_report_parsers(subparsers)
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
    if args.require_law_revision_facts:
        cmd.append("--require-law-revision-facts")
    if args.require_law_evidence_utilization:
        cmd.append("--require-law-evidence-utilization")
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


def run_law_revision_fact_coverage(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/check/check_law_revision_fact_coverage.py",
        "--list-group-dir",
        str(args.list_group_dir),
        "--stage",
        args.stage,
    ]
    if args.require_all_law_related:
        cmd.append("--require-all-law-related")
    if args.fail_on_hold:
        cmd.append("--fail-on-hold")
    if args.require_evidence_summary:
        cmd.append("--require-evidence-summary")
    if args.require_law_references:
        cmd.append("--require-law-references")
    if args.require_current_correct_choice:
        cmd.append("--require-current-correct-choice")
    if args.report:
        cmd.extend(["--report", str(args.report)])
    return run_command(cmd)


def run_law_revision_audit_queue(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/pipeline/build_law_revision_audit_queue.py",
        "--list-group-dir",
        str(args.list_group_dir),
        "--snapshots",
        str(args.snapshots),
        "--output",
        str(args.output),
        "--snippet-chars",
        str(args.snippet_chars),
    ]
    if args.summary:
        cmd.extend(["--summary", str(args.summary)])
    if args.include_existing:
        cmd.append("--include-existing")
    if args.include_hold:
        cmd.append("--include-hold")
    if args.require_snapshots:
        cmd.append("--require-snapshots")
    return run_command(cmd)


def run_law_revision_hold_materialize(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/pipeline/materialize_law_revision_hold_facts_from_queue.py",
        "--queue-jsonl",
        str(args.queue_jsonl),
        "--explanation-patch",
        str(args.explanation_patch),
        "--output",
        str(args.output),
    ]
    if args.overwrite_existing:
        cmd.append("--overwrite-existing")
    if args.skip_missing_patch_ids:
        cmd.append("--skip-missing-patch-ids")
    return run_command(cmd)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "review-ui":
        from tools.question_review_console.server import run_server

        return run_server(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            qualification=args.qualification,
            list_group_id=args.list_group_id,
            tailscale_origin=args.tailscale_origin,
            tailscale_logins=args.tailscale_login,
            tailscale_source_ips=args.tailscale_source_ip,
        )
    if args.command == "backfill-work-versions":
        from tools.question_review_console.work_version_backfill import (
            backfill_published_work_versions,
        )

        result = backfill_published_work_versions(
            REPO_ROOT,
            execute=bool(args.execute),
            credentials_json=args.credentials_json,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["status"] in {"ready", "succeeded"} else 1
    if args.command == "migrate-work-versions":
        from tools.question_review_console.work_version_backfill import (
            migrate_work_versions,
        )

        result = migrate_work_versions(REPO_ROOT, execute=bool(args.execute))
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "invalidate-work-version-run":
        from tools.question_review_console.work_version_backfill import (
            invalidate_work_version_run,
        )

        result = invalidate_work_version_run(
            REPO_ROOT,
            qualification=args.qualification,
            run_id=args.run_id,
            stage_id=args.stage,
            reason=args.reason,
            execute=bool(args.execute),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command in {
        "report-inventory",
        "report-snapshot",
        "report-run",
        "report-retry-publish",
        "check-question-issue-correction",
    }:
        return run_question_issue_report_command(args)
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
    if args.command == "check-law-revision-facts":
        return run_law_revision_fact_coverage(args)
    if args.command == "build-law-revision-audit-queue":
        return run_law_revision_audit_queue(args)
    if args.command == "materialize-law-revision-hold-facts":
        return run_law_revision_hold_materialize(args)

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
    if args.mode in ("full", "source", "required"):
        if args.mode == "full":
            stages = ("source", "merged", "firestore")
        elif args.mode == "source":
            stages = ("source",)
        else:
            stages = ("source", "merged")
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
            require_law_revision_facts=args.require_law_revision_facts,
            require_law_evidence_utilization=args.require_law_evidence_utilization,
            require_law_context_stage=args.require_law_context_stage,
            questionset_only=args.questionset_only,
        )

    if args.mode in ("full", "firestore") and not args.skip_upload_dry_run:
        failures += run_firestore_dry_run(list_group_dirs=list_group_dirs)

    if args.require_law_revision_facts:
        fact_stage = "firestore" if args.mode in ("full", "firestore") else "merged"
        failures += run_law_revision_fact_coverage_checks(
            list_group_dirs=list_group_dirs,
            stage=fact_stage,
            fail_on_hold=args.fail_on_law_revision_hold,
            require_evidence_summary=args.require_law_revision_evidence_summary,
            require_law_references=args.require_law_references_for_law_related,
            # Patch mode validates the latest patch projection above.  Other
            # modes validate merged/Firestore artifacts as well.
            require_current_correct_choice=args.mode != "patches",
        )

    if failures:
        print(f"\n[NG] question quality gate failed: sections_with_failures={failures}")
        return 1
    print("\n[OK] question quality gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
