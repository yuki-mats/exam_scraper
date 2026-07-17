"""Compatibility CLI for regenerating publication artifacts after stage 01.

The canonical merge implementation is ``00_merge_all.py``.  This historical
entrypoint remains callable, but no longer owns a second ID join or writes a
questionType-only partial view.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""} and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.merge.merge_utils import is_patch_filename_for_tag


PATCH_TAG = "questionType_fixed"
PATCH_SUBDIR_NAME = "10_questionType_fixed"
MERGE_ALL_MODULE = importlib.import_module("scripts.merge.00_merge_all")


def group_for_patch_file(patch_file: str | Path) -> tuple[str, Path]:
    path = Path(patch_file).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"パッチファイルが見つかりません: {path}")
    if path.parent.name != PATCH_SUBDIR_NAME or not is_patch_filename_for_tag(
        path.name,
        PATCH_TAG,
    ):
        raise ValueError(
            "10_questionType_fixedのquestionType patchを指定してください: "
            f"{path}"
        )
    group_dir = path.parent.parent
    return group_dir.name, group_dir.parent


def process_directory(
    list_group_id: str,
    base_dir: str | Path,
    *,
    require_answer_result_text: bool = True,
) -> None:
    """Regenerate 12/20/30 through the canonical binding-aware merge."""

    MERGE_ALL_MODULE.merge_all(
        str(list_group_id),
        Path(base_dir),
        require_answer_result_text=require_answer_result_text,
    )


def process_patch_file(
    patch_filepath: str | Path,
    *,
    require_answer_result_text: bool = True,
) -> None:
    list_group_id, base_dir = group_for_patch_file(patch_filepath)
    process_directory(
        list_group_id,
        base_dir,
        require_answer_result_text=require_answer_result_text,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "questionType patch保存後に、共通識別契約で12/20/30を再生成します。"
        )
    )
    parser.add_argument(
        "patch_files",
        nargs="*",
        help="10_questionType_fixed内の個別patch。groupごとに一度だけ再生成します。",
    )
    parser.add_argument(
        "--list-group-id",
        "-g",
        help="list_group_idを指定して全成果物を再生成します。",
    )
    parser.add_argument(
        "--base-dir",
        "-d",
        default=str(
            REPO_ROOT
            / "output"
            / "2nd-class-kenchikushi"
            / "questions_json"
        ),
        help="list_group_idを含むquestions_jsonディレクトリ。",
    )
    parser.add_argument(
        "--allow-missing-answer-result",
        action="store_true",
        help="既存Firestore由来など、answer_result_textがない入力を許可します。",
    )
    args = parser.parse_args(argv)
    require_answer_result_text = not args.allow_missing_answer_result

    if args.list_group_id and args.patch_files:
        parser.error("--list-group-idとpatch_filesは同時に指定できません。")
    if args.list_group_id:
        process_directory(
            args.list_group_id,
            args.base_dir,
            require_answer_result_text=require_answer_result_text,
        )
        return 0
    if not args.patch_files:
        parser.print_help()
        return 2

    groups: dict[tuple[str, Path], None] = {}
    for patch_file in args.patch_files:
        groups[group_for_patch_file(patch_file)] = None
    for list_group_id, base_dir in groups:
        process_directory(
            list_group_id,
            base_dir,
            require_answer_result_text=require_answer_result_text,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
