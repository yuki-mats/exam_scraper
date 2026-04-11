#!/usr/bin/env python3
"""category.jsonのquestionCountフィールドを問題JSONファイルから更新します。

使い方:
  python3 scripts/question_count/110_update_category_counts.py <category.json> <source_dir> [--write]

例:
  python3 scripts/question_count/110_update_category_counts.py \
    output/2nd-class-kenchikushi/category/category.json \
    output/2nd-class-kenchikushi/questions_json/upload_to_firestore

--write: 実際にcategory.jsonを上書きします（タイムスタンプ付きバックアップを作成）。
このフラグがない場合はドライランとなり、更新内容を表示するだけです。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.questions_json_paths import is_list_group_dir


# ===== ANSI カラーコード =====
class Color:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    GRAY = '\033[90m'


def find_key_values(obj, key_name: str) -> list:
    """JSON風の構造体からkey_nameに一致するキーの値を再帰的に収集する。"""
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


def analyze_file(p: Path) -> Counter:
    """JSONファイルからquestionSetIdごとのカウントを集計する。"""
    try:
        with open(p, 'r', encoding='utf-8') as f:
            obj = json.load(f)
    except Exception as e:
        print(f"{Color.YELLOW}Warning: failed to load {p}: {e}{Color.RESET}", file=sys.stderr)
        return Counter()

    records = extract_question_records(obj)
    counter = Counter()
    for record in records:
        v = record.get('questionSetId')
        if v is not None and str(v) != '':
            counter[str(v)] += 1
    return counter


def load_category_json(path: str) -> dict:
    """category.jsonを読み込む。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_archived_path(path: Path) -> bool:
    return "old" in path.parts


def latest_final_file_for_list_group_dir(list_group_dir: Path) -> Path | None:
    convert_dir = list_group_dir / "40_convert"
    convert_candidates = sorted(p for p in convert_dir.glob("*.json") if not is_archived_path(p))
    if convert_candidates:
        return convert_candidates[-1]

    merged_dir = list_group_dir / "30_merged_2"
    merged_candidates = sorted(p for p in merged_dir.glob("*.json") if not is_archived_path(p))
    if merged_candidates:
        return merged_candidates[-1]

    return None


def gather_latest_final_files(source_dir: str) -> list[Path]:
    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"Source path {src} not found")
    if src.is_file():
        return [src]
    if not src.is_dir():
        return []

    if is_list_group_dir(src):
        latest = latest_final_file_for_list_group_dir(src)
        return [latest] if latest else []

    files: list[Path] = []
    for child in sorted(src.iterdir()):
        if not is_list_group_dir(child):
            continue
        latest = latest_final_file_for_list_group_dir(child)
        if latest is not None:
            files.append(latest)
    return files


def gather_files(source_dir: str) -> list[Path]:
    """指定されたディレクトリ配下の*.jsonファイルを集める。"""
    src = Path(source_dir)
    if not src.exists():
        raise FileNotFoundError(f"Source path {src} not found")
    if src.is_file():
        return [src]
    elif src.is_dir():
        return [p for p in src.rglob("*.json") if not is_archived_path(p)]
    else:
        return []


def aggregate_counts(files: list[Path]) -> Counter:
    """全ファイルからquestionSetIdごとの件数を集計する。"""
    counter: Counter = Counter()
    for p in files:
        file_counter = analyze_file(p)
        counter.update(file_counter)
    return counter


def detect_unknown_question_set_ids(counts: Counter, category: dict) -> list[str]:
    """category.jsonに存在しないquestionSetIdを返す。"""
    known_ids = {
        str(q.get("questionSetId"))
        for q in category.get("questionSets", [])
        if q.get("questionSetId") is not None and str(q.get("questionSetId")) != ""
    }
    unknown_ids = sorted(qid for qid in counts.keys() if qid not in known_ids)
    return unknown_ids

def find_files_with_unknown_ids(files: list[Path], unknown_ids: list[str]) -> dict:
    """各unknown_idがどのファイルに含まれるかを返す。"""


    result = {qid: [] for qid in unknown_ids}
    for p in files:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                obj = json.load(f)
        except Exception:
            continue
        qset_vals = {
            str(record.get("questionSetId"))
            for record in extract_question_records(obj)
            if record.get("questionSetId") is not None and str(record.get("questionSetId")) != ""
        }
        for qid in unknown_ids:
            if qid in qset_vals:
                result[qid].append(str(p))
    return result


def filter_counts_to_known_question_set_ids(counts: Counter, category: dict) -> Counter:
    """category.jsonに存在するquestionSetIdのみのカウントを返す。"""
    known_ids = {
        str(q.get("questionSetId"))
        for q in category.get("questionSets", [])
        if q.get("questionSetId") is not None and str(q.get("questionSetId")) != ""
    }
    return Counter({qid: cnt for qid, cnt in counts.items() if qid in known_ids})


def iso_utc_now() -> str:
    """UTC現在時刻をISO8601文字列（Z付き）で返す。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def apply_counts_to_category(category: dict, counts: Counter) -> tuple[int, int, str]:
    """カウント結果をcategoryに適用する。"""
    now_iso = iso_utc_now()
    qset_updated = 0
    folder_updated = 0

    # 各questionSetIdごとにquestionCountを設定
    for q in category.get("questionSets", []):
        qid = q.get("questionSetId")
        new_count = int(counts.get(str(qid), 0))
        old_count = normalize_count(q.get("questionCount", 0))
        q["questionCount"] = new_count
        if old_count != new_count:
            q["updatedAt"] = now_iso
            qset_updated += 1

    # 各folderIdごとに、questionSets.questionCount から合計を再計算
    folder_counts: dict[str, int] = {}
    for q in category.get("questionSets", []):
        folder_id = q.get("folderId")
        qcount = normalize_count(q.get("questionCount", 0))
        folder_counts.setdefault(folder_id, 0)
        folder_counts[folder_id] += qcount

    for f in category.get("folders", []):
        fid = f.get("folderId")
        new_count = folder_counts.get(fid, 0)
        old_count = normalize_count(f.get("questionCount", 0))
        f["questionCount"] = new_count
        if old_count != new_count:
            f["updatedAt"] = now_iso
            folder_updated += 1

    category["updatedAt"] = now_iso

    return qset_updated, folder_updated, now_iso


def write_category(path: Path, data: dict) -> Path:
    """category.jsonを書き込み、バックアップを作成する。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = path.parent / "old"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.bak_{timestamp}"
    copyfile(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return backup


def print_diff_line(label: str, name: str, before: int, after: int) -> None:
    """更新前後の差分を色付きで表示する。"""
    diff = after - before
    if diff == 0:
        # 変化なし → グレーで表示
        print(f"{Color.GRAY}  {label} {name}: {before} → {after} (±0){Color.RESET}")
    elif diff > 0 and before == 0:
        # 新規追加 → 青
        print(f"{Color.BLUE}  {label} {name}: {before} → {after} (+{diff}) [NEW]{Color.RESET}")
    elif diff > 0:
        # 増加 → 緑
        print(f"{Color.GREEN}  {label} {name}: {before} → {after} (+{diff}){Color.RESET}")
    else:
        # 減少 → 赤
        print(f"{Color.RED}  {label} {name}: {before} → {after} ({diff}){Color.RESET}")


def normalize_count(value) -> int:
    """questionCountの値をintに正規化する（Noneは0）。"""
    if value is None:
        return 0
    return int(value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="category.jsonのquestionCountを問題JSONファイルから更新します。"
    )
    parser.add_argument("category_json", type=str, help="category.jsonのパス")
    parser.add_argument("source_dir", type=str, help="集計対象ディレクトリのパス")
    parser.add_argument(
        "--latest-final-only",
        action="store_true",
        help="questions_json 配下では各 list_group_id の最新 40_convert を優先し、なければ最新 30_merged_2 のみを集計する",
    )
    parser.add_argument("--write", action="store_true", help="category.jsonを書き込みます（バックアップ作成）")
    args = parser.parse_args(argv)

    cat_path = Path(args.category_json)
    if not cat_path.exists():
        print(f"{Color.RED}エラー: category.jsonが見つかりません: {cat_path}{Color.RESET}", file=sys.stderr)
        return 2

    # category.json読み込み
    category = load_category_json(str(cat_path))

    # 更新前のquestionCountを記録
    before_qset = {
        q.get("questionSetId"): normalize_count(q.get("questionCount", 0))
        for q in category.get("questionSets", [])
    }
    before_folder = {
        f.get("folderId"): normalize_count(f.get("questionCount", 0))
        for f in category.get("folders", [])
    }
    before_root_updated_at = category.get("updatedAt")

    # ファイル収集
    try:
        if args.latest_final_only:
            files = gather_latest_final_files(args.source_dir)
        else:
            files = gather_files(args.source_dir)
    except FileNotFoundError as e:
        print(f"{Color.RED}エラー: {e}{Color.RESET}", file=sys.stderr)
        return 2

    if not files:
        print(f"{Color.YELLOW}警告: 集計対象のソースファイルが見つかりません。{Color.RESET}")
        return 2

    # カウント集計
    counts = aggregate_counts(files)

    # category と整合しない questionSetId を検出（古いファイル混在の可能性）

    unknown_qset_ids = detect_unknown_question_set_ids(counts, category)
    if unknown_qset_ids:
        print(f"\n{Color.YELLOW}{Color.BOLD}警告: category.json に存在しない questionSetId が見つかりました。{Color.RESET}")
        print(f"{Color.YELLOW}古い upload_to_firestore ファイルが混在している可能性があります。既知questionSetIdのみで集計を続行します。{Color.RESET}")
        print(f"{Color.YELLOW}source_dir: {args.source_dir}{Color.RESET}")

        # どのファイルに古いIDが含まれるかを表示
        file_map = find_files_with_unknown_ids(files, unknown_qset_ids)
        preview = unknown_qset_ids[:20]
        for qid in preview:
            print(f"{Color.YELLOW}  - unknown questionSetId: {qid} (count={counts[qid]}){Color.RESET}")
            for f in file_map[qid]:
                print(f"{Color.GRAY}      in: {f}{Color.RESET}")
        if len(unknown_qset_ids) > len(preview):
            remain = len(unknown_qset_ids) - len(preview)
            print(f"{Color.YELLOW}  ... and {remain} more{Color.RESET}")

    # category.jsonに存在するquestionSetIdのみを集計対象にする
    counts = filter_counts_to_known_question_set_ids(counts, category)

    # サマリ表示
    print(f"\n{Color.BOLD}{'='*60}{Color.RESET}")
    print(f"{Color.BOLD}  questionCount 更新チェック{Color.RESET}")
    print(f"{Color.BOLD}{'='*60}{Color.RESET}")
    print(f"  スキャンしたファイル数: {Color.CYAN}{len(files)}{Color.RESET}")
    print(f"  見つかった異なるquestionSetId数: {Color.CYAN}{len(counts)}{Color.RESET}")

    # 更新後を適用
    updated_qset_timestamps, updated_folder_timestamps, root_updated_at = apply_counts_to_category(category, counts)
    root_updated_at_changed = before_root_updated_at != root_updated_at

    # ===== questionSets の差分表示 =====
    print(f"\n{Color.BOLD}[questionSets] before → after (差分){Color.RESET}")
    print(f"{Color.GRAY}  {'─'*50}{Color.RESET}")
    changed_qsets = 0
    for q in category.get("questionSets", []):
        qid = q.get("questionSetId")
        before = before_qset.get(qid, 0)
        after = q.get("questionCount", 0)
        if before != after:
            changed_qsets += 1
        print_diff_line("QuestionSet", qid, before, after)

    # ===== folders の差分表示 =====
    print(f"\n{Color.BOLD}[folders] before → after (差分){Color.RESET}")
    print(f"{Color.GRAY}  {'─'*50}{Color.RESET}")
    changed_folders = 0
    for f in category.get("folders", []):
        fid = f.get("folderId")
        before = before_folder.get(fid, 0)
        after = f.get("questionCount", 0)
        if before != after:
            changed_folders += 1
        print_diff_line("Folder", fid, before, after)

    # ===== サマリ =====
    print(f"\n{Color.BOLD}{'='*60}{Color.RESET}")
    if changed_qsets > 0 or changed_folders > 0:
        print(f"  {Color.YELLOW}変更あり: QuestionSets={changed_qsets}, Folders={changed_folders}{Color.RESET}")
        print(
            f"  updatedAt付与: QuestionSets={updated_qset_timestamps}, "
            f"Folders={updated_folder_timestamps}, Root={'yes' if root_updated_at_changed else 'no'}"
        )
    else:
        print(f"  {Color.GREEN}変更なし: すべてのquestionCountは最新です。{Color.RESET}")
        print(f"  updatedAt更新: Root={'yes' if root_updated_at_changed else 'no'} ({root_updated_at})")
    print(f"{Color.BOLD}{'='*60}{Color.RESET}")

    # 書き込み
    if args.write:
        if changed_qsets == 0 and changed_folders == 0 and not root_updated_at_changed:
            print(f"\n{Color.GRAY}変更がないため書き込みはスキップしました。{Color.RESET}")
        else:
            try:
                backup = write_category(cat_path, category)
                print(f"\n{Color.GREEN}✓ category.jsonを更新しました。{Color.RESET}")
                print(f"  バックアップ: {backup}")
            except Exception as e:
                print(f"{Color.RED}category.jsonの書き込みに失敗しました: {e}{Color.RESET}", file=sys.stderr)
                return 1
    else:
        if changed_qsets > 0 or changed_folders > 0:
            print(f"\n{Color.YELLOW}※ --write オプションで実際に書き込みを行います。{Color.RESET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
