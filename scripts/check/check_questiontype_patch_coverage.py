#!/usr/bin/env python3
"""
Verify that questionType patch files cover every question in the source file(s).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id
from scripts.common.questions_json_paths import resolve_list_group_base_dir


REQUIRED_FIELDS = [
    "questionBodyText",
    "choiceTextList",
    "questionType",
    "original_question_id",
    "question_url",
]

SOURCE_SUBDIR = "00_source"
PATCH_SUBDIR = "10_questionType_fixed"
PATCH_TAG = "questionType_fixed"
TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})$")


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


def _timestamp_sort_key(path: Path) -> tuple[int, str, str]:
    match = TIMESTAMP_SUFFIX_PATTERN.search(path.stem)
    if not match:
        return (0, "", path.name)
    return (1, match.group(1), path.name)


def select_latest_patch_files(paths: List[Path], patch_tag: str) -> List[Path]:
    selected: Dict[str, Path] = {}
    for path in sorted(paths, key=_timestamp_sort_key):
        source_stem = source_stem_from_patch_filename(path.name, patch_tag)
        if source_stem is None:
            continue
        selected[source_stem] = path
    return sorted(selected.values(), key=lambda p: p.name) # Sorted by name to ensure consistent order


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_source_questions(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [q for q in questions if isinstance(q, dict)]


def resolve_source_original_id(question: Dict[str, Any]) -> Any:
    return review_question_id(question)


def get_patch_entries(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError("patch JSON must be an array")
    return [q for q in data if isinstance(q, dict)]


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    issues: List[str] = []
    warnings: List[str] = []

    source_ids = [resolve_source_original_id(q) for q in source_questions]
    patch_ids = [q.get("original_question_id") for q in patch_entries]

    source_id_set = {sid for sid in source_ids if sid}
    patch_id_set = {pid for pid in patch_ids if pid}

    if len(source_questions) != len(patch_entries):
        issues.append(
            f"count mismatch: source={len(source_questions)} patch={len(patch_entries)}"
        )

    missing_ids = sorted(source_id_set - patch_id_set)
    extra_ids = sorted(patch_id_set - source_id_set)
    if missing_ids:
        issues.append(f"missing original_question_id: {missing_ids}")
    if extra_ids:
        issues.append(f"extra original_question_id: {extra_ids}")

    for idx, (src, patch) in enumerate(zip(source_questions, patch_entries), start=1):
        if not isinstance(patch, dict):
            issues.append(f"index {idx}: patch entry is not an object")
            continue

        missing_fields = [k for k in REQUIRED_FIELDS if k not in patch]
        if missing_fields:
            issues.append(f"index {idx}: missing fields {missing_fields}")
            continue

        if "isCalculationQuestion" not in patch:
            warnings.append(
                f"index {idx}: legacy patch has no isCalculationQuestion; "
                "new or updated stage 01 output must add a boolean"
            )
        elif not isinstance(patch.get("isCalculationQuestion"), bool):
            issues.append(f"index {idx}: isCalculationQuestion must be boolean")

        source_id = resolve_source_original_id(src)
        if patch.get("original_question_id") != source_id:
            issues.append(
                "index {}: original_question_id mismatch "
                "(source={} patch={})".format(
                    idx, source_id, patch.get("original_question_id")
                )
            )

        if patch.get("question_url") != src.get("question_url"):
            issues.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, src.get("question_url"), patch.get("question_url")
                )
            )

        # DEBUGGING: Print repr of strings for comparison
        src_qb_text = src.get("questionBodyText")
        patch_qb_text = patch.get("questionBodyText")
        if src_qb_text != patch_qb_text:
            print(f"DEBUG (check script): index {idx}: questionBodyText mismatch detected.")
            print(f"DEBUG (check script): Source repr: {repr(src_qb_text)}")
            print(f"DEBUG (check script): Patch repr:  {repr(patch_qb_text)}")
            issues.append(f"index {idx}: questionBodyText mismatch")

        src_ctl = src.get("choiceTextList")
        patch_ctl = patch.get("choiceTextList")
        if src_ctl != patch_ctl:
            print(f"DEBUG (check script): index {idx}: choiceTextList mismatch detected.")
            print(f"DEBUG (check script): Source repr: {repr(src_ctl)}")
            print(f"DEBUG (check script): Patch repr:  {repr(patch_ctl)}")
            issues.append(f"index {idx}: choiceTextList mismatch")

    if len(patch_id_set) != len([pid for pid in patch_ids if pid]):
        warnings.append("duplicate original_question_id detected in patch")

    return issues, warnings


def latest_patch_map(patch_dir: Path) -> Dict[str, Path]:
    patch_files = select_latest_patch_files(sorted(patch_dir.glob("*.json")), PATCH_TAG)
    result: Dict[str, Path] = {}
    for patch_path in patch_files:
        source_stem = source_stem_from_patch_filename(patch_path.name, PATCH_TAG)
        if source_stem:
            result[source_stem] = patch_path
    return result # Corrected: return the result dictionary directly.


def check_pair(source_path: Path, patch_path: Path) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not source_path.exists():
        return False, [f"source not found: {source_path}"]
    if not patch_path.exists():
        return False, [f"patch not found: {patch_path}"]

    source_data = load_json(source_path)
    patch_data = load_json(patch_path)

    source_questions = get_source_questions(source_data)
    patch_entries = get_patch_entries(patch_data)

    issues, warnings = compare_entries(source_questions, patch_entries)
    for warn in dict.fromkeys(warnings):
        print(f"[WARN] {warn}")
    if issues:
        errors.extend(issues)
        return False, errors
    return True, []


def resolve_base_dir(list_group_id: str, base_dir: str | None) -> Path:
    return resolve_list_group_base_dir(list_group_id, base_dir, repo_root=REPO_ROOT)


def check_list_group(list_group_id: str, base_dir: str | None) -> int:
    try:
        base = resolve_base_dir(list_group_id, base_dir)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2

    list_group_dir = base / list_group_id
    source_dir = list_group_dir / SOURCE_SUBDIR
    patch_dir = list_group_dir / PATCH_SUBDIR

    if not source_dir.exists():
        print(f"[ERROR] source directory not found: {source_dir}")
        return 2
    if not patch_dir.exists():
        print(f"[ERROR] patch directory not found: {patch_dir}")
        return 2

    ok = True
    source_files = sorted(source_dir.glob("question_*.json"))
    patch_map = latest_patch_map(patch_dir)
    for source_path in source_files:
        patch_path = patch_map.get(source_path.stem)
        if patch_path is None:
            ok = False
            print(f"[ERROR] {source_path.name}: patch not found")
            continue
        success, errors = check_pair(source_path, patch_path)
        if success:
            print(f"[OK] {source_path.name}")
        else:
            ok = False
            for err in errors:
                print(f"[ERROR] {source_path.name}: {err}")

    source_names = {p.stem for p in source_files}
    for source_stem, patch_path in sorted(patch_map.items()):
        if source_stem not in source_names:
            ok = False
            print(f"[ERROR] patch has no source: {patch_path.name}")

    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify questionType patch coverage (original_question_id-based)."
    )
    parser.add_argument("--source", help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        help="Path to *_questionType_fixed_YYYYMMDD_HHMM.json (旧形式 *_questionType_fixed.json も可)",
    )
    parser.add_argument("--list-group-id", help="list_group_id to check all files")
    parser.add_argument(
        "--base-dir",
        help="Base dir containing list_group_id (e.g. output/2nd-class-kenchikushi/questions_json)",
    )
    args = parser.parse_args()

    if args.list_group_id:
        return check_list_group(args.list_group_id, args.base_dir)

    if not args.source or not args.patch:
        parser.print_help()
        return 2

    success, errors = check_pair(Path(args.source), Path(args.patch))
    if success:
        print("[OK] coverage check passed")
        return 0
    for err in errors:
        print(f"[ERROR] {err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
