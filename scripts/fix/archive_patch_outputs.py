#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.merge.merge_utils import is_patch_filename_for_tag


TASK_CONFIG = {
    "question_type": {
        "subdir": "10_questionType_fixed",
        "tag": "questionType_fixed",
    },
    "question_intent": {
        "subdir": "15_correctChoiceText_fixed",
        "tag": "correctChoiceText_fixed",
    },
    "law_context": {
        "subdir": "18_law_context_prepared",
        "tag": "lawContext_prepared",
    },
    "explanation": {
        "subdir": "21_explanationText_added",
        "tag": "explanationText_added",
    },
    "question_set": {
        "subdir": "22_questionSetId_linked",
        "tag": "questionSetId_linked",
    },
    "correct_choice": {
        "subdir": "23_correctChoiceText_fixed",
        "tag": "correctChoiceText_fixed",
    },
}


def build_old_path(old_dir: Path, original_name: str) -> Path:
    candidate = old_dir / original_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        next_candidate = old_dir / f"{stem}_{index:02d}{suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


def archive_patch_dir(patch_dir: Path, patch_tag: str) -> int:
    if not patch_dir.exists():
        print(f"[INFO] patch dir not found: {patch_dir}")
        return 0

    targets = sorted(
        path
        for path in patch_dir.glob("*.json")
        if path.is_file() and is_patch_filename_for_tag(path.name, patch_tag)
    )
    if not targets:
        print(f"[INFO] archive target not found: {patch_dir}")
        return 0

    old_dir = patch_dir / "old"
    old_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for path in targets:
        destination = build_old_path(old_dir, path.name)
        path.rename(destination)
        print(f"[MOVE] {path} -> {destination}")
        moved += 1

    print(f"[OK] archived {moved} file(s) into {old_dir}")
    return moved


def resolve_patch_dir(base_dir: Path, list_group_id: str, task: str) -> tuple[Path, str]:
    config = TASK_CONFIG[task]
    return base_dir / list_group_id / config["subdir"], config["tag"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="既存のパッチJSONを patch_dir/old へ退避する"
    )
    parser.add_argument(
        "--task",
        choices=sorted(TASK_CONFIG),
        help="対象タスク名。--list-group-id と組み合わせて使う",
    )
    parser.add_argument("--list-group-id", help="対象 list_group_id")
    parser.add_argument(
        "--base-dir",
        default=str(ROOT_DIR / "output" / "2nd-class-kenchikushi" / "questions_json"),
        help="questions_json のベースディレクトリ",
    )
    parser.add_argument("--patch-dir", help="対象パッチディレクトリを直接指定する")
    parser.add_argument("--patch-tag", help="--patch-dir 使用時の patch tag")
    args = parser.parse_args()

    if args.patch_dir:
        if not args.patch_tag:
            raise SystemExit("--patch-dir を使う場合は --patch-tag も指定してください")
        archive_patch_dir(Path(args.patch_dir).resolve(), args.patch_tag)
        return 0

    if not args.task or not args.list_group_id:
        raise SystemExit("--task と --list-group-id を指定してください")

    patch_dir, patch_tag = resolve_patch_dir(
        Path(args.base_dir).resolve(),
        args.list_group_id,
        args.task,
    )
    archive_patch_dir(patch_dir, patch_tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
