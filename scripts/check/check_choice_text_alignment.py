#!/usr/bin/env python3
"""
選択肢一覧（choiceTextList）と正答（correctChoiceText）のテキスト整合性を検査するスクリプト。
別問題の解答・解説の誤混入やインデックスずれを機械的に検出する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_answer_contract import choice_text_content_alignment_issue
from scripts.common.question_identity import review_question_id


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_target_files(base_dir: Path, stage: str, list_group_id: str | None) -> list[Path]:
    target_dirs: list[Path] = []
    if list_group_id:
        target_dirs.append(base_dir / list_group_id)
    else:
        target_dirs.extend([d for d in base_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])

    files: list[Path] = []
    for d in sorted(target_dirs):
        if stage == "merged":
            merged_dir = d / "30_merged_2"
            if merged_dir.exists():
                # oldディレクトリは除外
                files.extend(sorted([f for f in merged_dir.glob("question_*_merged_*.json") if "old" not in f.parts]))
        elif stage == "patch":
            # 15または23の最新パッチ
            for patch_sub in ("23_correctChoiceText_fixed", "15_correctChoiceText_fixed"):
                p_dir = d / patch_sub
                if p_dir.exists():
                    files.extend(sorted(p_dir.glob("*.json")))
                    break
        elif stage == "source":
            s_dir = d / "00_source"
            if s_dir.exists():
                files.extend(sorted(s_dir.glob("question_*.json")))
    return files


def check_file(file_path: Path) -> list[str]:
    data = load_json(file_path)
    records: list[dict[str, Any]] = []
    if isinstance(data, dict):
        records = data.get("question_bodies", [])
    elif isinstance(data, list):
        records = data

    issues: list[str] = []
    for idx, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        issue = choice_text_content_alignment_issue(record)
        if issue:
            qid = review_question_id(record) or record.get("public_question_id") or f"index_{idx}"
            q_label = record.get("questionLabel") or f"問{idx}"
            issues.append(f"{file_path.name} {q_label} (ID: {qid}): {issue}")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="選択肢（choiceTextList）と正答（correctChoiceText）のテキスト整合性を検査する"
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        required=True,
        help="対象資格のquestions_jsonディレクトリ（例: output/anma/questions_json）",
    )
    parser.add_argument(
        "--stage",
        choices=("merged", "source"),
        default="merged",
        help="検査対象ステージ（merged: 30_merged_2, source: 00_source）",
    )
    parser.add_argument(
        "--list-group-id",
        type=str,
        default=None,
        help="特定の年度（例: 1994）。未指定時は全年度を検査",
    )
    args = parser.parse_args(argv)

    base_dir = args.base_dir.resolve()
    if not base_dir.exists():
        print(f"[ERROR] base_dir not found: {base_dir}", file=sys.stderr)
        return 2

    files = iter_target_files(base_dir, args.stage, args.list_group_id)
    if not files:
        print(f"[WARN] No files found in {base_dir} for stage={args.stage} list_group_id={args.list_group_id}")
        return 0

    all_issues: list[str] = []
    total_files = len(files)
    for f in files:
        issues = check_file(f)
        if issues:
            all_issues.extend(issues)

    if all_issues:
        print(f"[FAIL] {len(all_issues)} 件のテキスト不整合（別問題の混入疑い）が検出されました:")
        for issue in all_issues:
            print(f"  - {issue}")
        return 1

    print(f"[OK] {total_files} ファイルの全問題において、選択肢と正答テキストの一致を確認しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
