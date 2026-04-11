#!/usr/bin/env python3
"""`output/2nd-class-kenchikushi/questions_json/upload_to_firestore`配下の問題数をカウントします。

このスクリプトは対象ディレクトリ（または--sourceで指定されたディレクトリ）のJSONファイルを走査し、
`original_question_id`またはリスト項目を見つけて問題数をカウントし、
`questionSetId`ごとにその出現数を集計します。

使い方:
  python3 scripts/update_question_count.py [--source PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def is_archived_path(path: Path) -> bool:
    return "old" in path.parts


def find_key_values(obj, key_name: str):
    """JSON風の構造体から`key_name`に一致するキーの値を再帰的に収集します。"""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key_name:
                found.append(v)
            found.extend(find_key_values(v, key_name))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_key_values(item, key_name))
    return found


def extract_question_records(obj) -> list[dict]:
    """Firestore投入向けJSONから問題レコード配列を抽出する。"""
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]

    if isinstance(obj, dict):
        for key in ("questions", "items"):
            value = obj.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        if "questionSetId" in obj or "originalQuestionId" in obj or "original_question_id" in obj:
            return [obj]

    return []


def analyze_file(p: Path):
    try:
        with open(p, 'r', encoding='utf-8') as f:
            obj = json.load(f)
    except Exception as e:
        print(f"Warning: failed to load {p}: {e}", file=sys.stderr)
        return 0, Counter(), None

    records = extract_question_records(obj)

    total_questions = len(records)
    if total_questions == 0:
        pids = find_key_values(obj, 'original_question_id')
        if pids:
            total_questions = len(pids)

    counter = Counter()
    for record in records:
        v = record.get('questionSetId')
        if v is not None and str(v) != '':
            counter[str(v)] += 1

    return total_questions, counter, obj


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=str, default=None, help='スキャンするファイルまたはディレクトリ（デフォルトはupload_to_firestoreディレクトリ）')
    args = parser.parse_args(argv)

    files = []
    if args.source:
        src = Path(args.source)
        if src.is_file():
            files = [src]
        elif src.is_dir():
            files = [p for p in src.rglob('*.json') if not is_archived_path(p)]
        else:
            print(f"Source path {src} not found", file=sys.stderr)
            return 2
    else:
        # output/2nd-class-kenchikushi/questions_json/upload_to_firestore配下のみをカウント対象に限定
        upload_dir = Path('output/2nd-class-kenchikushi/questions_json/upload_to_firestore')
        if not upload_dir.exists():
            print(f"Upload dir not found: {upload_dir}", file=sys.stderr)
            return 2
        files = [p for p in upload_dir.rglob('*.json') if not is_archived_path(p)]

    if not files:
        print('解析対象のファイルが見つかりません')
        return 0

    total_questions = 0
    total_files = 0
    qset_counter = Counter()
    per_file_summary = []

    for p in files:
        tq, counter, _ = analyze_file(p)
        total_questions += tq
        total_files += 1
        qset_counter.update(counter)
        per_file_summary.append((p, tq, sum(counter.values())))

    print(f"スキャンしたファイル数: {total_files}")
    print(f"カウントした問題数: {total_questions}")
    print(f"見つかった異なるquestionSetId数: {len(qset_counter)}")

    # questionSetIdごとのカウントを表示
    for qid, cnt in qset_counter.most_common():
        print(f"QuestionSet {qid}: {cnt}")

    # ファイルごとのサマリ（ファイル数が少ない場合のみ表示）
    if len(per_file_summary) <= 10:
        for p, tq, qcnt in per_file_summary:
            print(f"ファイル {p.name}: 問題数={tq}, questionSetId出現数={qcnt}")
    else:
        print(f"ファイルごとのエントリ数: {len(per_file_summary)}（一覧は省略）")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
