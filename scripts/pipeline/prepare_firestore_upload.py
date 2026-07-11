#!/usr/bin/env python3
"""Firestoreアップロード前の前処理を一括実行する統合スクリプト。

実行内容:
1. 00_merge_all.py で 20_merged_1 / 30_merged_2 を更新
2. answer_result_text / questionIntent から correctChoiceText を自動割当
3. convert_merged_to_firestore.py で 40_convert/<list_group_id>_firestore_<timestamp>.json を生成
4. 同時に upload_to_firestore/<list_group_id>_firestore_<timestamp>.json へ保存
    （既存ファイル/既存フォルダは old/<timestamp>/ へ移動）
5. 任意で questionSetId チェック / 件数集計 / category更新 / upload dry-run
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import json


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.questions_json_paths import (
    is_list_group_dir,
    list_group_ids_in_base_dir,
    resolve_category_json_path,
    resolve_list_group_base_dir,
    resolve_qualification_questions_json_dir,
)
from scripts.common.requirements import (
    DEFAULT_REQUIREMENTS_PATH,
    RequirementsError,
    get_stage_rules,
    load_requirements,
    validate_records,
)

SCRIPT_MERGE_ALL = ROOT_DIR / "scripts" / "merge" / "00_merge_all.py"
SCRIPT_AUTO_ASSIGN_CORRECT_CHOICE = ROOT_DIR / "scripts" / "fix" / "auto_assign_correct_choice_text.py"
SCRIPT_CONVERT = ROOT_DIR / "scripts" / "convert" / "convert_merged_to_firestore.py"
SCRIPT_QSET_CHECK = ROOT_DIR / "scripts" / "check" / "check_questionSetId.py"
SCRIPT_COUNT = ROOT_DIR / "scripts" / "count_questions" / "1_update_question_count.py"
SCRIPT_UPDATE_CATEGORY = ROOT_DIR / "scripts" / "count_questions" / "2_update_category_counts.py"
SCRIPT_UPLOAD_DRY_RUN = ROOT_DIR / "scripts" / "upload" / "upload_questions_to_firestore.py"

CONVERT_SUBDIR = "40_convert"
UPLOAD_SUBDIR = "upload_to_firestore"
MERGED2_SUBDIR = "30_merged_2"


def run_step(name: str, command: list[str], dry_run: bool) -> None:
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"\n[STEP] {name}")
    print(f"$ {printable}")
    if dry_run:
        print("[DRY RUN] 実行をスキップしました。")
        return

    env = os.environ.copy()
    pythonpath_parts = [str(ROOT_DIR)]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    result = subprocess.run(command, cwd=ROOT_DIR, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"ステップ失敗: {name}")


def is_list_group_id(value: str) -> bool:
    return value.isdigit()


def resolve_single_base_dir(list_group_id: str, base_dir: str | None) -> Path:
    return resolve_list_group_base_dir(list_group_id, base_dir, repo_root=ROOT_DIR)


def resolve_base_dir(list_group_id: str, base_dir: str | None) -> Path:
    """後方互換のために残す単一 list_group_id 向け base_dir 解決関数。"""
    return resolve_single_base_dir(list_group_id, base_dir)


def resolve_bulk_base_dir(qualification: str, base_dir: str | None) -> Path:
    return resolve_qualification_questions_json_dir(qualification, base_dir, repo_root=ROOT_DIR)


def resolve_targets(target_id: str, base_dir: str | None) -> tuple[Path, list[str], bool]:
    if is_list_group_id(target_id):
        resolved_base_dir = resolve_single_base_dir(target_id, base_dir)
        return resolved_base_dir, [target_id], False

    resolved_base_dir = resolve_bulk_base_dir(target_id, base_dir)
    list_group_ids = list_group_ids_in_base_dir(resolved_base_dir)
    if not list_group_ids:
        raise FileNotFoundError(f"questions_json 配下に list_group_id ディレクトリが見つかりません: {resolved_base_dir}")
    return resolved_base_dir, list_group_ids, True


def resolve_category_json(base_dir: Path, category_json: str | None) -> Path:
    return resolve_category_json_path(base_dir, category_json)


def _build_projected_output_path(dir_path: Path, list_group_id: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return dir_path / f"{list_group_id}_firestore_{timestamp}.json"


def _find_latest_output_path(dir_path: Path, list_group_id: str) -> Path | None:
    candidates = sorted(dir_path.glob(f"{list_group_id}_firestore_*.json"))
    if not candidates:
        return None
    return candidates[-1]


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        response = input(f"{prompt} {suffix}: ").strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("'yes' か 'no' で回答してください。")


def log_target_summary(target_id: str, base_dir: Path, list_group_ids: list[str], bulk_mode: bool) -> None:
    mode_label = "qualification" if bulk_mode else "list_group_id"
    print("\n[STEP] resolve targets")
    print(f"mode      : {mode_label}")
    print(f"target_id : {target_id}")
    print(f"base_dir  : {base_dir}")
    print(f"targets   : {', '.join(list_group_ids)}")


def find_missing_answers_in_source(base_dir: Path, list_group_id: str) -> list[str]:
    """00_source 配下の JSON をスキャンし、answer_result_text が欠損している問題を特定する。"""
    source_dir = base_dir / list_group_id / "00_source"
    if not source_dir.exists():
        return []

    missing_info = []
    for json_file in sorted(source_dir.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            bodies = data.get("question_bodies", [])
            for i, body in enumerate(bodies):
                ans = body.get("answer_result_text")
                if ans is None or (isinstance(ans, str) and ans.strip() == ""):
                    q_id = (
                        body.get("public_question_id")
                        or body.get("original_question_id")
                        or f"index_{i}"
                    )
                    missing_info.append(f"{json_file.name} (index {i}, ID: {q_id})")
        except Exception:  # noqa: S110
            continue
    return missing_info


def count_unuploadable_questions_from_invalid_merged2(*, base_dir: Path, list_group_id: str) -> tuple[int, list[str]]:
    """
    30_merged_2 配下の *_invalid.json に外出しされた（=アップロード対象外）question_bodies を数える。
    """
    invalid_dir = base_dir / list_group_id / MERGED2_SUBDIR
    if not invalid_dir.exists():
        return 0, []

    total = 0
    details: list[str] = []
    for invalid_path in sorted(invalid_dir.glob("*_invalid.json")):
        try:
            payload = json.loads(invalid_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: S110
            continue
        bodies = payload.get("question_bodies")
        if not isinstance(bodies, list):
            continue
        count = sum(1 for b in bodies if isinstance(b, dict))
        if count:
            total += count
            details.append(f"{invalid_path.name}: {count}")
    return total, details


def process_list_group(
    *,
    python_cmd: str,
    list_group_id: str,
    base_dir: Path,
    upload_dir: Path,
    category_json: Path,
    exam_name: str | None,
    skip_merge: bool,
    allow_missing_answer_result: bool,
    skip_qset_check: bool,
    questionset_only: bool,
    requirements_path: Path,
    skip_requirements_check: bool,
    requirements_warn_only: bool,
    dry_run: bool,
) -> tuple[Path, list[str]]:
    group_dir = (base_dir / list_group_id).resolve()

    print(f"\n[STEP] check missing answers in source ({list_group_id})")
    missing_answers = find_missing_answers_in_source(base_dir, list_group_id)
    if missing_answers:
        print(f"警告: {len(missing_answers)} 件のレコードで answer_result_text が欠損しています。")
        for info in missing_answers:
            print(f"  - {info}")
    else:
        print("answer_result_text の欠損は見つかりませんでした。")

    if not skip_merge:
        merge_command = [
            python_cmd,
            str(SCRIPT_MERGE_ALL),
            list_group_id,
            "--base-dir",
            str(base_dir),
        ]
        if allow_missing_answer_result:
            merge_command.append("--allow-missing-answer-result")
        run_step(
            f"merge ({list_group_id})",
            merge_command,
            dry_run,
        )
    else:
        print(f"\n[STEP] merge ({list_group_id})")
        print("スキップしました。")

    auto_assign_command = [
        python_cmd,
        str(SCRIPT_AUTO_ASSIGN_CORRECT_CHOICE),
        list_group_id,
        "--base-dir",
        str(base_dir),
        "--apply",
    ]
    if not allow_missing_answer_result:
        auto_assign_command.append("--fail-on-unresolved")
    run_step(
        f"auto assign correctChoiceText ({list_group_id})",
        auto_assign_command,
        dry_run,
    )

    if skip_requirements_check:
        print(f"\n[STEP] requirements check (merged) ({list_group_id})")
        print("スキップしました。")
    else:
        print(f"\n[STEP] requirements check (merged) ({list_group_id})")
        try:
            requirements = load_requirements(requirements_path)
        except RequirementsError as e:
            raise RuntimeError(f"requirements load failed: {e}") from e

        qualification = base_dir.parent.name if base_dir.name == "questions_json" else None
        merged_rules = get_stage_rules(
            requirements,
            stage="merged",
            record_array="question_bodies",
            qualification=qualification,
        )
        merged_files: list[Path] = []
        for subdir in ("20_merged_1", "30_merged_2"):
            merged_files.extend(sorted((group_dir / subdir).glob("*.json")))
        errors: list[str] = []
        for merged_file in merged_files:
            try:
                payload = json.loads(merged_file.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{merged_file}: JSON parse failed ({e})")
                continue
            records = payload.get("question_bodies")
            if not isinstance(records, list):
                continue
            records = [r for r in records if isinstance(r, dict)]
            errors.extend(validate_records(records=records, rules=merged_rules, source_path=merged_file))

        if errors:
            print(f"[NG] requirements errors={len(errors)}")
            for line in errors[:50]:
                print(f"  - {line}")
            if len(errors) > 50:
                print(f"  ... truncated ({len(errors) - 50} more)")
            if not requirements_warn_only:
                raise RuntimeError("requirements check failed (merged)")
        else:
            print("[OK] requirements check passed (merged)")

    convert_cmd = [python_cmd, str(SCRIPT_CONVERT), list_group_id, "-b", str(base_dir)]
    if exam_name:
        convert_cmd.extend(["--exam-name", exam_name])
    if allow_missing_answer_result:
        convert_cmd.append("--skip-intent-correct-choice-check")
    run_step(f"convert ({list_group_id})", convert_cmd, dry_run)

    print(f"\n[STEP] locate outputs ({list_group_id})")
    if dry_run:
        converted_path = _build_projected_output_path(group_dir / CONVERT_SUBDIR, list_group_id)
        copied_path = _build_projected_output_path(upload_dir, list_group_id)
        print(f"projected convert path: {converted_path}")
        print(f"projected upload path : {copied_path}")
    else:
        converted_path = _find_latest_output_path(group_dir / CONVERT_SUBDIR, list_group_id)
        copied_path = _find_latest_output_path(upload_dir, list_group_id)
        if converted_path is None:
            raise FileNotFoundError(f"40_convert の出力が見つかりません: {group_dir / CONVERT_SUBDIR}")
        if copied_path is None:
            raise FileNotFoundError(f"upload_to_firestore の出力が見つかりません: {upload_dir}")
        print(f"convert output: {converted_path}")
        print(f"upload output : {copied_path}")

    run_step(
        f"count summary ({list_group_id})",
        [python_cmd, str(SCRIPT_COUNT), "--source", str(copied_path)],
        dry_run,
    )

    if skip_qset_check:
        print(f"\n[STEP] questionSetId check ({list_group_id})")
        print("スキップしました。")
    elif not category_json.exists():
        print(f"\n[STEP] questionSetId check ({list_group_id})")
        print(f"category.json が見つからないためスキップ: {category_json}")
    else:
        run_step(
            f"questionSetId check ({list_group_id})",
            [
                python_cmd,
                str(SCRIPT_QSET_CHECK),
                "--category",
                str(category_json),
                "--fixed",
                str(copied_path),
                *(["--questionset-only"] if questionset_only else []),
            ],
            dry_run,
        )

    if not skip_requirements_check and not dry_run:
        print(f"\n[STEP] requirements check (firestore output) ({list_group_id})")
        try:
            requirements = load_requirements(requirements_path)
        except RequirementsError as e:
            raise RuntimeError(f"requirements load failed: {e}") from e

        qualification = base_dir.parent.name if base_dir.name == "questions_json" else None
        firestore_rules = get_stage_rules(
            requirements,
            stage="firestore",
            record_array="questions",
            qualification=qualification,
        )
        payload = json.loads(copied_path.read_text(encoding="utf-8"))
        questions = payload.get("questions")
        records = [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []
        errors = validate_records(
            records=records,
            rules=firestore_rules,
            source_path=copied_path,
            id_keys=("questionId",),
        )
        if errors:
            print(f"[NG] requirements errors={len(errors)}")
            for line in errors[:50]:
                print(f"  - {line}")
            if len(errors) > 50:
                print(f"  ... truncated ({len(errors) - 50} more)")
            if not requirements_warn_only:
                raise RuntimeError("requirements check failed (firestore)")
        else:
            print("[OK] requirements check passed (firestore)")

    return copied_path, missing_answers


def update_category_counts(*, python_cmd: str, category_json: Path, base_dir: Path, dry_run: bool) -> None:
    if not category_json.exists():
        if dry_run:
            print(f"[STEP] update category counts (skip): category.json が見つかりません: {category_json}")
            return
        raise FileNotFoundError(f"category.json が見つかりません: {category_json}")
    upload_dir = base_dir / UPLOAD_SUBDIR
    if not upload_dir.exists():
        if dry_run:
            print(f"[STEP] update category counts (skip): upload_to_firestore が見つかりません: {upload_dir}")
            return
        raise FileNotFoundError(f"upload_to_firestore が見つかりません: {upload_dir}")
    run_step(
        "update category counts (2_update_category_counts.py --write --latest-upload-only)",
        [
            python_cmd,
            str(SCRIPT_UPDATE_CATEGORY),
            str(category_json),
            str(upload_dir),
            "--latest-upload-only",
            "--write",
        ],
        dry_run,
    )


def run_upload_dry_run(*, python_cmd: str, copied_path: Path) -> None:
    run_step(
        "upload (upload_questions_to_firestore.py) --dry-run",
        [python_cmd, str(SCRIPT_UPLOAD_DRY_RUN), str(copied_path), "--dry-run"],
        False,
    )


def print_execution_summary(
    *,
    successes: list[tuple[str, Path | None]],
    failures: list[tuple[str, str]],
    skipped_for_failure: list[str],
    missing_answers_report: dict[str, list[str]],
    unuploadable_report: dict[str, list[str]],
    unuploadable_total: int,
    unuploadable_missing_answers_total: int,
    unuploadable_invalid_total: int,
) -> None:
    print("\n=== 実行サマリ ===")
    if successes:
        print("成功:")
        for list_group_id, copied_path in successes:
            if copied_path is None:
                print(f"  - {list_group_id}")
            else:
                print(f"  - {list_group_id}: {copied_path}")

    if missing_answers_report:
        print("\n[重要] answer_result_text 欠損レコード (00_source):")
        for list_group_id, details in missing_answers_report.items():
            if details:
                print(f"  - {list_group_id}:")
                for info in details:
                    print(f"    * {info}")

    if failures:
        print("\n失敗:")
        for list_group_id, reason in failures:
            print(f"  - {list_group_id}: {reason}")
    if skipped_for_failure:
        print("未処理:")
        for item in skipped_for_failure:
            print(f"  - {item}")

    print("\n=== アップロード不能レコード数（対応必要） ===")
    print(f"合計: {unuploadable_total}")
    print(f"  - 00_source answer_result_text 欠損: {unuploadable_missing_answers_total}")
    print(f"  - 30_merged_2/*_invalid.json 外出し: {unuploadable_invalid_total}")
    if unuploadable_report:
        print("内訳（30_merged_2/*_invalid.json 由来）:")
        for list_group_id, details in sorted(unuploadable_report.items()):
            if not details:
                continue
            print(f"  - {list_group_id}:")
            for line in details:
                print(f"    * {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Firestoreアップロード前の前処理（merge/convert/save/check）を一括実行します。"
    )
    parser.add_argument("target_id", help="対象の list_group_id（例: 85010）または資格コード（例: 2nd-class-kenchikushi）")
    parser.add_argument(
        "--base-dir",
        "-b",
        default=None,
        help="questions_json ルート（例: output/2nd-class-kenchikushi/questions_json）",
    )
    parser.add_argument(
        "--exam-name",
        default=None,
        help="convert時の examSource 用試験名上書き",
    )
    parser.add_argument(
        "--upload-dir",
        default=None,
        help="変換後JSONの保存先ディレクトリ（デフォルト: <base-dir>/upload_to_firestore）",
    )
    parser.add_argument(
        "--category-json",
        default=None,
        help="questionSetIdチェックや件数更新に使う category.json のパス",
    )
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="00_merge_all.py をスキップ",
    )
    parser.add_argument(
        "--allow-missing-answer-result",
        action="store_true",
        help=(
            "Firestore snapshot 由来など answer_result_text がない既存正誤保持データの "
            "merge/convert を許可する"
        ),
    )
    parser.add_argument(
        "--skip-qset-check",
        action="store_true",
        help="check_questionSetId.py をスキップ",
    )
    parser.add_argument(
        "--questionset-only",
        action="store_true",
        help="questionSetId チェックで category.json の questionSets[].questionSetId のみを有効IDとして扱う",
    )
    parser.add_argument(
        "--update-category-counts",
        action="store_true",
        help="（互換オプション）category件数更新は既定で有効です。",
    )
    parser.add_argument(
        "--upload-dry-run",
        action="store_true",
        help="単一 list_group_id 実行時のみ、最後に questions upload の dry-run まで実行する",
    )
    parser.add_argument(
        "--skip-update-category-counts",
        action="store_true",
        help="2_update_category_counts.py --write をスキップ",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実行内容のみ表示し、ファイル更新は行わない",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=DEFAULT_REQUIREMENTS_PATH,
        help="必須項目チェック用 requirements TOML のパス",
    )
    parser.add_argument(
        "--skip-requirements-check",
        action="store_true",
        help="requirements(必須項目)チェックをスキップ",
    )
    parser.add_argument(
        "--requirements-warn-only",
        action="store_true",
        help="requirements違反があっても停止せず警告のみ出す",
    )
    args = parser.parse_args(argv)

    try:
        python_cmd = sys.executable
        base_dir, list_group_ids, bulk_mode = resolve_targets(args.target_id, args.base_dir)
        upload_dir = (
            Path(args.upload_dir).expanduser().resolve()
            if args.upload_dir
            else (base_dir / UPLOAD_SUBDIR).resolve()
        )
        category_json = resolve_category_json(base_dir, args.category_json)
        should_update_category_counts = not args.skip_update_category_counts

        log_target_summary(args.target_id, base_dir, list_group_ids, bulk_mode)

        successes: list[tuple[str, Path | None]] = []
        failures: list[tuple[str, str]] = []
        missing_answers_report: dict[str, list[str]] = {}
        unuploadable_report: dict[str, list[str]] = {}
        unuploadable_total = 0
        unuploadable_missing_answers_total = 0
        unuploadable_invalid_total = 0
        last_copied_path: Path | None = None

        for list_group_id in list_group_ids:
            try:
                copied_path, missing_answers = process_list_group(
                    python_cmd=python_cmd,
                    list_group_id=list_group_id,
                    base_dir=base_dir,
                    upload_dir=upload_dir,
                    category_json=category_json,
                    exam_name=args.exam_name,
                    skip_merge=args.skip_merge,
                    allow_missing_answer_result=args.allow_missing_answer_result,
                    skip_qset_check=args.skip_qset_check,
                    questionset_only=args.questionset_only,
                    requirements_path=args.requirements,
                    skip_requirements_check=args.skip_requirements_check,
                    requirements_warn_only=args.requirements_warn_only,
                    dry_run=args.dry_run,
                )
                if missing_answers:
                    missing_answers_report[list_group_id] = missing_answers
                    unuploadable_missing_answers_total += len(missing_answers)
                last_copied_path = copied_path
                successes.append((list_group_id, copied_path))

                # 30_merged_2 の invalid 外出し件数を集計（「アップロードできる状態にできなかった件数」）
                count, details = count_unuploadable_questions_from_invalid_merged2(
                    base_dir=base_dir,
                    list_group_id=list_group_id,
                )
                unuploadable_invalid_total += count
                if details:
                    unuploadable_report[list_group_id] = details
            except Exception as exc:  # noqa: BLE001
                failures.append((list_group_id, str(exc)))
                print(f"[ERROR] list_group_id={list_group_id}: {exc}", file=sys.stderr)

        skipped_for_failure: list[str] = []
        if should_update_category_counts:
            if failures:
                skipped_for_failure.append("update category counts")
                print("\n[STEP] update category counts")
                print("list_group_id の失敗があるためスキップしました。")
            else:
                update_category_counts(
                    python_cmd=python_cmd,
                    category_json=category_json,
                    base_dir=base_dir,
                    dry_run=args.dry_run,
                )
        else:
            print("\n[STEP] update category counts")
            print("スキップしました。")

        if args.dry_run:
            print("\n[STEP] Firestore upload")
            print("[DRY RUN] アップロード確認と実行はスキップしました。")
        elif bulk_mode:
            print("\n[STEP] Firestore upload")
            print("資格コード一括実行では upload は行いません。")
            if args.upload_dry_run:
                print("注記: --upload-dry-run は単一 list_group_id 実行時のみ有効です。")
        elif last_copied_path is not None and args.upload_dry_run:
            run_upload_dry_run(python_cmd=python_cmd, copied_path=last_copied_path)
        elif last_copied_path is not None:
            print("\n[STEP] Firestore upload prompt")
            should_upload = ask_yes_no("Firestoreにアップロードしますか？", default=False)
            if should_upload:
                as_dry_run = ask_yes_no("dry run で実行しますか？", default=True)
                upload_cmd = [
                    python_cmd,
                    str(SCRIPT_UPLOAD_DRY_RUN),
                    str(last_copied_path),
                ]
                upload_step_name = "upload (upload_questions_to_firestore.py)"
                if as_dry_run:
                    upload_cmd.append("--dry-run")
                    upload_step_name += " --dry-run"
                run_step(upload_step_name, upload_cmd, False)
            else:
                print("Firestoreアップロードをスキップしました。")

        unuploadable_total = unuploadable_missing_answers_total + unuploadable_invalid_total

        print_execution_summary(
            successes=successes,
            failures=failures,
            skipped_for_failure=skipped_for_failure,
            missing_answers_report=missing_answers_report,
            unuploadable_report=unuploadable_report,
            unuploadable_total=unuploadable_total,
            unuploadable_missing_answers_total=unuploadable_missing_answers_total,
            unuploadable_invalid_total=unuploadable_invalid_total,
        )

        print("\n=== 完了 ===")
        if not bulk_mode and last_copied_path is not None:
            print(f"Firestoreアップロード用JSON: {last_copied_path}")
        else:
            print(f"対象資格: {args.target_id}")
            print(f"questions_json: {base_dir}")
        return 0 if not failures else 1
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
