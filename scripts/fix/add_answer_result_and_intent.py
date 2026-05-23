#!/usr/bin/env python3
"""
15_correctChoiceText_fixed配下のJSONファイルに、
00_sourceから answer_result_text と questionIntent を取得して追加するスクリプト。

使用例:
  python3 scripts/fix/add_answer_result_and_intent.py \
    --qualification 2nd-class-kenchikushi
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """JSONファイルを読み込む"""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    """JSONファイルを保存"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def get_source_questions(source_path: Path) -> list[dict[str, Any]]:
    """
    00_source JSONから question_bodies を取得
    """
    try:
        source_data = load_json(source_path)
    except Exception as e:
        print(f"[WARN] Failed to load {source_path}: {e}", file=sys.stderr)
        return []
    
    question_bodies = source_data.get("question_bodies", [])
    if not isinstance(question_bodies, list):
        return []
    
    return [q for q in question_bodies if isinstance(q, dict)]


def process_file(
    file_path: Path,
    source_questions_list: list[dict[str, Any]],
) -> int:
    """
    15_correctChoiceText_fixed ファイルを処理し、
    answer_result_text と questionIntent を追加する
    """
    try:
        data = load_json(file_path)
    except Exception as e:
        print(f"[ERROR] Failed to load {file_path}: {e}", file=sys.stderr)
        return 0
    
    if not isinstance(data, list):
        print(f"[ERROR] {file_path} is not an array", file=sys.stderr)
        return 0
    
    modified = 0
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        
        # インデックスベースでマッチング
        if idx >= len(source_questions_list):
            continue
        
        source_q = source_questions_list[idx]
        
        # answer_result_text を追加（存在しない場合のみ）
        if "answer_result_text" not in item:
            answer_result = source_q.get("answer_result_text")
            item["answer_result_text"] = answer_result
            modified += 1
        
        # questionIntent を追加（存在しない場合のみ）
        if "questionIntent" not in item:
            intent = source_q.get("questionIntent")
            item["questionIntent"] = intent
            modified += 1
    
    if modified > 0:
        save_json(file_path, data)
        print(f"[OK] {file_path.name}: {modified} fields updated")
        return modified
    else:
        print(f"[SKIP] {file_path.name}: no changes")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add answer_result_text and questionIntent from 00_source to 15_correctChoiceText_fixed"
    )
    parser.add_argument(
        "--qualification",
        required=True,
        help="Qualification code (e.g. 2nd-class-kenchikushi)",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Base directory containing questions_json (default: output/<qualification>/questions_json)",
    )
    
    args = parser.parse_args(argv)
    
    # Base directory を決定
    if args.base_dir:
        base_dir = Path(args.base_dir).expanduser().resolve()
    else:
        base_dir = Path("output") / args.qualification / "questions_json"
    
    if not base_dir.exists():
        print(f"[ERROR] Base directory not found: {base_dir}", file=sys.stderr)
        return 1
    
    # list_group_id ごとに処理
    list_group_dirs = sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name.isdigit()])
    
    if not list_group_dirs:
        print(f"[WARN] No list_group_id directories found in {base_dir}", file=sys.stderr)
        return 0
    
    total_modified = 0
    
    for list_group_dir in list_group_dirs:
        list_group_id = list_group_dir.name
        source_dir = list_group_dir / "00_source"
        fixed_dir = list_group_dir / "15_correctChoiceText_fixed"
        
        if not source_dir.exists():
            print(f"[SKIP] {list_group_id}: no 00_source directory", file=sys.stderr)
            continue
        
        if not fixed_dir.exists():
            print(f"[SKIP] {list_group_id}: no 15_correctChoiceText_fixed directory", file=sys.stderr)
            continue
        
        print(f"\n[PROCESS] {list_group_id}")
        
        # 00_source JSON ファイルから source questions を取得
        # ファイル名（例: question_85011_1.json）と fixed ファイル（例: question_85011_1_merged...json）の対応関係を構築
        source_files = sorted(source_dir.glob("question_*.json"))
        fixed_files = sorted(fixed_dir.glob("question_*.json"))
        
        # source file name から list_group_id と number を抽出
        # e.g., question_85011_1.json -> 1
        def extract_num(path: Path) -> int | None:
            name = path.stem  # question_85011_1
            parts = name.split("_")
            if len(parts) >= 3:
                try:
                    return int(parts[-1])
                except ValueError:
                    return None
            return None
        
        source_by_num = {extract_num(f): f for f in source_files if extract_num(f) is not None}
        
        print(f"  Source files found: {len(source_files)}")
        print(f"  Fixed files found: {len(fixed_files)}")
        
        # 15_correctChoiceText_fixed ファイルを処理
        for fixed_file in fixed_files:
            # Fixed ファイル名から数字を抽出
            # e.g., question_85011_1_merged_correctChoiceText_fixed... -> 1
            name = fixed_file.stem
            parts = name.split("_")
            num = None
            for i, part in enumerate(parts):
                if part.isdigit() and i > 1:  # question_<gid>_<num>...
                    try:
                        num = int(part)
                        break
                    except ValueError:
                        pass
            
            if num is None:
                continue
            
            # 対応する source ファイルを取得
            source_file = source_by_num.get(num)
            if source_file:
                source_questions_list = get_source_questions(source_file)
                modified = process_file(fixed_file, source_questions_list)
                total_modified += modified
    
    print(f"\n[DONE] Total modified: {total_modified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
