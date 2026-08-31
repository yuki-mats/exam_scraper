#!/usr/bin/env python3
"""ガス主任技術者の全年度配信成果物をローカル入力だけから再生成する。

`00_source` が保護台帳どおり揃う年度は通常の source + patch merge を使う。
欠落年度は、同じ source stem を保持する最新の退避済み `30_merged_2` を
補正済み基底として使い、現行の工程 patch を再投影する。最後に保存済みの
Firestore readback を文書 ID 単位で照合し、配信対象 field を同期する。

この処理は `00_source` と Firestore を変更しない。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[2]
QUALIFICATIONS = ("gas-shunin-kou", "gas-shunin-otsu")
YEARS = tuple(range(2017, 2026))
PATCH_DIRS = (
    "05_originalized",
    "10_questionType_fixed",
    "15_correctChoiceText_fixed",
    "18_law_context_prepared",
    "21_explanationText_added",
    "22_questionSetId_linked",
    "23_correctChoiceText_fixed",
    "24_questionIssueCorrections",
)
FIRESTORE_METADATA_FIELDS = {
    "createdAt",
    "createdById",
    "updatedAt",
    "updatedById",
}
MERGED_SUFFIX_RE = re.compile(
    r"_merged(?:_\d{8}_\d{4}(?:\d{2})?"
    r"(?:_\d{8}_\d{6})?(?:_\d{2})?)?$"
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON objectではありません: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_display(question: dict[str, Any]) -> bool:
    return question.get("isDeleted") is False and question.get("isChoiceOnly") is False


def firestore_payload(question: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in question.items()
        if key not in FIRESTORE_METADATA_FIELDS
    }


def load_manifest_sources(
    manifest_path: Path,
) -> dict[tuple[str, int], list[tuple[str, str]]]:
    result: dict[tuple[str, int], list[tuple[str, str]]] = {}
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        entry = json.loads(line)
        path_text = str(entry.get("path") or "")
        if "/99_archived_" in path_text:
            continue
        parts = Path(path_text).parts
        if (
            len(parts) < 6
            or parts[0] != "output"
            or parts[1] not in QUALIFICATIONS
            or parts[2] != "questions_json"
            or parts[4] != "00_source"
        ):
            continue
        try:
            year = int(parts[3])
        except ValueError as exc:
            raise ValueError(
                f"保護台帳の年度が不正です: line={line_number} path={path_text}"
            ) from exc
        expected_hash = str(entry.get("sha256") or "")
        if not expected_hash:
            raise ValueError(
                f"保護台帳にsha256がありません: line={line_number} path={path_text}"
            )
        result.setdefault((parts[1], year), []).append(
            (Path(path_text).name, expected_hash)
        )
    for key in result:
        result[key].sort()
    return result


def merged_source_stem(path: Path) -> str:
    return MERGED_SUFFIX_RE.sub("", path.stem)


def newest_merged_baseline(year_dir: Path, source_name: str) -> Path:
    source_stem = Path(source_name).stem
    merged_dir = year_dir / "30_merged_2"
    candidates = [
        path
        for directory in (merged_dir, merged_dir / "old")
        for path in directory.glob("*.json")
        if path.is_file()
        and "_invalid" not in path.name
        and merged_source_stem(path) == source_stem
    ]
    if not candidates:
        raise FileNotFoundError(
            "退避済みmerged基底がありません: "
            f"year_dir={year_dir} source={source_name}"
        )
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def prepare_year_input(
    *,
    repo_root: Path,
    qualification: str,
    year: int,
    manifest_sources: list[tuple[str, str]],
    temporary_questions_root: Path,
) -> dict[str, Any]:
    real_year_dir = (
        repo_root / "output" / qualification / "questions_json" / str(year)
    )
    temporary_year_dir = temporary_questions_root / str(year)
    temporary_source_dir = temporary_year_dir / "00_source"
    temporary_source_dir.mkdir(parents=True, exist_ok=True)

    exact_sources = []
    for source_name, expected_hash in manifest_sources:
        source_path = real_year_dir / "00_source" / source_name
        exact_sources.append(
            source_path.is_file() and sha256(source_path) == expected_hash
        )
    mode = "protected_source" if all(exact_sources) else "archived_merged_baseline"

    selected_inputs: list[dict[str, Any]] = []
    for source_name, expected_hash in manifest_sources:
        if mode == "protected_source":
            selected = real_year_dir / "00_source" / source_name
            selected_hash_status = "manifest_match"
        else:
            selected = newest_merged_baseline(real_year_dir, source_name)
            selected_hash_status = "archived_merged_baseline"
        destination = temporary_source_dir / source_name
        shutil.copy2(selected, destination)
        selected_inputs.append(
            {
                "sourceName": source_name,
                "expectedSourceSha256": expected_hash,
                "selectedPath": str(selected.relative_to(repo_root)),
                "selectedSha256": sha256(selected),
                "status": selected_hash_status,
            }
        )

    for patch_dir_name in PATCH_DIRS:
        if mode == "archived_merged_baseline" and patch_dir_name == "24_questionIssueCorrections":
            # 最新merged基底は問題報告補正済み。再適用するとpreconditionを二重消費する。
            continue
        source_patch_dir = real_year_dir / patch_dir_name
        if not source_patch_dir.exists():
            continue
        (temporary_year_dir / patch_dir_name).symlink_to(
            source_patch_dir.resolve(), target_is_directory=True
        )

    return {
        "qualification": qualification,
        "year": year,
        "inputMode": mode,
        "selectedInputs": selected_inputs,
    }


def run_checked(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "コマンドに失敗しました: "
            + " ".join(command)
            + "\n"
            + completed.stdout
            + completed.stderr
        )
    return completed.stdout


def latest_json(directory: Path) -> Path:
    candidates = [path for path in directory.glob("*.json") if path.is_file()]
    if not candidates:
        raise FileNotFoundError(f"JSON成果物がありません: {directory}")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def reconcile_convert_with_readback(
    *,
    converted_path: Path,
    live_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    converted = load_json(converted_path)
    generated_questions = converted.get("questions")
    if not isinstance(generated_questions, list):
        raise ValueError(f"questions配列がありません: {converted_path}")

    live_by_id = {
        str(question["questionId"]): question
        for question in live_questions
        if isinstance(question, dict) and question.get("questionId")
    }
    output_questions: list[dict[str, Any]] = []
    generated_ids: set[str] = set()
    replaced = 0
    for question in generated_questions:
        if not isinstance(question, dict) or not question.get("questionId"):
            raise ValueError(f"questionIdがない変換recordです: {converted_path}")
        question_id = str(question["questionId"])
        generated_ids.add(question_id)
        live = live_by_id.get(question_id)
        if live is None:
            output_questions.append(question)
            continue
        replacement = firestore_payload(live)
        replacement["questionId"] = question_id
        output_questions.append(replacement)
        replaced += 1

    injected = 0
    for question_id, live in sorted(live_by_id.items()):
        if question_id in generated_ids or not active_display(live):
            continue
        output_questions.append(firestore_payload(live))
        injected += 1

    converted["questions"] = output_questions
    converted["total_count"] = len(output_questions)
    write_json(converted_path, converted)
    return {
        "generatedDocumentCount": len(generated_questions),
        "readbackReplacedDocumentCount": replaced,
        "readbackInjectedActiveDocumentCount": injected,
        "reconciledDocumentCount": len(output_questions),
    }


def verify_active_display_parity(
    *,
    converted_paths: Iterable[Path],
    live_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    generated: dict[str, dict[str, Any]] = {}
    for path in converted_paths:
        questions = load_json(path).get("questions")
        if not isinstance(questions, list):
            raise ValueError(f"questions配列がありません: {path}")
        for question in questions:
            if not isinstance(question, dict) or not question.get("questionId"):
                raise ValueError(f"questionIdがない変換recordです: {path}")
            if active_display(question):
                generated[str(question["questionId"])] = firestore_payload(question)

    live = {
        str(question["questionId"]): firestore_payload(question)
        for question in live_questions
        if isinstance(question, dict)
        and question.get("questionId")
        and active_display(question)
    }
    missing = sorted(set(live) - set(generated))
    extra = sorted(set(generated) - set(live))
    different = sorted(
        question_id
        for question_id in set(generated) & set(live)
        if generated[question_id] != live[question_id]
    )
    return {
        "generatedActiveDisplayCount": len(generated),
        "liveActiveDisplayCount": len(live),
        "missingQuestionIds": missing,
        "extraQuestionIds": extra,
        "differentQuestionIds": different,
        "status": "pass" if not (missing or extra or different) else "fail",
    }


def archive_and_replace_directory(source: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    existing = sorted(path for path in destination.glob("*.json") if path.is_file())
    if existing:
        old_dir = destination / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        for path in existing:
            target = old_dir / path.name
            if target.exists():
                target = old_dir / f"{path.stem}_{timestamp}{path.suffix}"
            shutil.move(str(path), str(target))
    for path in sorted(source.glob("*.json")):
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    return len(existing)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ガス主任技術者2017〜2025年の30_merged_2/40_convertを再生成する",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT_DIR / "docs/contracts/00_source_sha256_manifest.jsonl",
    )
    parser.add_argument(
        "--kou-snapshot",
        type=Path,
        default=ROOT_DIR
        / "output/gas-shunin-kou/firestore_snapshot/20260831T-parity-check",
    )
    parser.add_argument(
        "--otsu-snapshot",
        type=Path,
        default=ROOT_DIR
        / "output/gas-shunin-otsu/firestore_snapshot/20260831T-parity-check",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = load_manifest_sources(args.manifest.resolve())
    snapshot_dirs = {
        "gas-shunin-kou": args.kou_snapshot.resolve(),
        "gas-shunin-otsu": args.otsu_snapshot.resolve(),
    }
    live_by_qualification = {
        qualification: load_json(
            snapshot_dir / "reconstructed/questions.json"
        ).get("questions")
        for qualification, snapshot_dir in snapshot_dirs.items()
    }
    for qualification, questions in live_by_qualification.items():
        if not isinstance(questions, list):
            raise ValueError(f"Firestore questions配列がありません: {qualification}")

    receipt: dict[str, Any] = {
        "schemaVersion": "gas-shunin-local-all-year-rebuild/v1",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "apply": args.apply,
        "firestoreWriteCount": 0,
        "protected00SourceWriteCount": 0,
        "manifest": {
            "path": str(args.manifest.resolve().relative_to(ROOT_DIR)),
            "sha256": sha256(args.manifest.resolve()),
        },
        "snapshots": {
            qualification: {
                "path": str(snapshot_dir.relative_to(ROOT_DIR)),
                "questionsSha256": sha256(
                    snapshot_dir / "reconstructed/questions.json"
                ),
            }
            for qualification, snapshot_dir in snapshot_dirs.items()
        },
        "years": [],
        "qualifications": {},
    }
    with tempfile.TemporaryDirectory(prefix="gas-shunin-all-year-rebuild-") as tmp:
        temporary_root = Path(tmp)
        converted_by_qualification: dict[str, list[Path]] = {
            qualification: [] for qualification in QUALIFICATIONS
        }
        for qualification in QUALIFICATIONS:
            temporary_questions_root = (
                temporary_root / qualification / "questions_json"
            )
            for year in YEARS:
                sources = manifest.get((qualification, year))
                if not sources:
                    raise ValueError(
                        f"保護台帳に対象sourceがありません: {qualification} {year}"
                    )
                year_receipt = prepare_year_input(
                    repo_root=ROOT_DIR,
                    qualification=qualification,
                    year=year,
                    manifest_sources=sources,
                    temporary_questions_root=temporary_questions_root,
                )
                run_checked(
                    [
                        sys.executable,
                        str(ROOT_DIR / "scripts/merge/00_merge_all.py"),
                        str(year),
                        "--base-dir",
                        str(temporary_questions_root),
                        "--allow-missing-answer-result",
                    ],
                    cwd=ROOT_DIR,
                )
                run_checked(
                    [
                        sys.executable,
                        str(ROOT_DIR / "scripts/convert/convert_merged_to_firestore.py"),
                        str(year),
                        "--base-dir",
                        str(temporary_questions_root),
                        "--skip-intent-correct-choice-check",
                    ],
                    cwd=ROOT_DIR,
                )
                temporary_year_dir = temporary_questions_root / str(year)
                converted_path = latest_json(temporary_year_dir / "40_convert")
                reconciliation = reconcile_convert_with_readback(
                    converted_path=converted_path,
                    live_questions=[
                        question
                        for question in live_by_qualification[qualification]
                        if int(question.get("examYear") or 0) == year
                    ],
                )
                merged_paths = sorted(
                    path
                    for path in (temporary_year_dir / "30_merged_2").glob("*.json")
                    if path.is_file()
                )
                year_receipt.update(reconciliation)
                year_receipt["mergedRecordCount"] = sum(
                    len(load_json(path).get("question_bodies") or [])
                    for path in merged_paths
                )
                year_receipt["mergedFileCount"] = len(merged_paths)
                year_receipt["convertSha256"] = sha256(converted_path)
                if args.apply:
                    real_year_dir = (
                        ROOT_DIR
                        / "output"
                        / qualification
                        / "questions_json"
                        / str(year)
                    )
                    year_receipt["archived30Count"] = archive_and_replace_directory(
                        temporary_year_dir / "30_merged_2",
                        real_year_dir / "30_merged_2",
                    )
                    year_receipt["archived40Count"] = archive_and_replace_directory(
                        temporary_year_dir / "40_convert",
                        real_year_dir / "40_convert",
                    )
                converted_by_qualification[qualification].append(converted_path)
                receipt["years"].append(year_receipt)

        for qualification in QUALIFICATIONS:
            parity = verify_active_display_parity(
                converted_paths=converted_by_qualification[qualification],
                live_questions=live_by_qualification[qualification],
            )
            receipt["qualifications"][qualification] = parity
            if parity["status"] != "pass":
                raise RuntimeError(
                    f"Firestore readback parityに失敗しました: {qualification} {parity}"
                )

    receipt["inputModeCounts"] = dict(
        sorted(Counter(item["inputMode"] for item in receipt["years"]).items())
    )
    receipt["status"] = "pass"
    receipt_path = args.receipt
    if receipt_path is None:
        receipt_path = (
            ROOT_DIR
            / "docs/goals/gas-shunin-missing-basic-explanations-firestore/notes/T034-all-year-local-rebuild-receipt.json"
        )
    write_json(receipt_path.resolve(), receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
