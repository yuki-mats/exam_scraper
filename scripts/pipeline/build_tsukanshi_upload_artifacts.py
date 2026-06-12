#!/usr/bin/env python3
"""Build stable tsukanshi 01-04 artifacts for Firestore preparation.

This script intentionally writes deterministic patch filenames without a
timestamp so reruns overwrite the same artifacts instead of creating another
generation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.fix.auto_assign_correct_choice_text import build_expected_correct_choice_text


DEFAULT_BASE_DIR = ROOT_DIR / "output" / "tsukanshi" / "questions_json"
DEFAULT_CATEGORY_JSON = ROOT_DIR / "output" / "tsukanshi" / "category" / "category.json"

QUESTION_SETS = {
    "通関業法": {
        "folderId": "tsukanshi_f01_gyoho",
        "folderName": "通関業法",
        "folderDescription": "通関業の許可、通関士、通関業者の義務、監督処分など、通関業法に関する問題を扱う。",
        "questionSetId": "tsukanshi_qs01_gyoho",
        "questionSetName": "通関業法",
        "questionSetDescription": "通関業法の目的、定義、許可、欠格事由、営業所、通関士、記帳・届出、監督処分などを扱う。",
    },
    "関税法、関税定率法その他関税に関する法律及び外国為替及び外国貿易法": {
        "folderId": "tsukanshi_f02_kanzei_hourei",
        "folderName": "関税法等",
        "folderDescription": "関税法、関税定率法、関税暫定措置法、外為法など、通関士試験の関税関係法令を扱う。",
        "questionSetId": "tsukanshi_qs02_kanzei_hourei",
        "questionSetName": "関税法等",
        "questionSetDescription": "課税、申告、保税、輸出入規制、関税率表、減免税、犯則、外為法などの関税関係法令を扱う。",
    },
    "通関書類の作成要領その他通関手続の実務": {
        "folderId": "tsukanshi_f03_jitsumu",
        "folderName": "通関実務",
        "folderDescription": "申告書作成、課税価格計算、税額計算、品目分類など、通関手続の実務問題を扱う。",
        "questionSetId": "tsukanshi_qs03_jitsumu",
        "questionSetName": "通関実務",
        "questionSetDescription": "輸出入申告書、課税価格、関税額、消費税額、関税率表分類など、通関実務上の計算・書類作成を扱う。",
    },
}

SUGGESTED_QUESTIONS = [
    "この問題の正答根拠は何か。",
    "誤りの選択肢はどこが違うか。",
    "同じ論点を再出題されたら何を確認するか。",
]
SUGGESTED_DETAILS = [
    {
        "question": SUGGESTED_QUESTIONS[0],
        "answer": "問題文、正答番号、解説素材を照合し、根拠となる条文、制度趣旨、計算関係を確認する。",
    },
    {
        "question": SUGGESTED_QUESTIONS[1],
        "answer": "主体、対象、期間、数値、手続、適用範囲のどれが正しい内容とずれているかを確認する。",
    },
    {
        "question": SUGGESTED_QUESTIONS[2],
        "answer": "同じ条文・制度・計算パターンで問われるキーワードと例外をセットで確認する。",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[DRY RUN] write: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def source_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.glob("*/00_source/question_*.json"))


def source_questions(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    questions = payload.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"question_bodies not found: {path}")
    return [question for question in questions if isinstance(question, dict)]


def original_id(question: dict[str, Any]) -> str:
    value = question.get("original_question_id") or question.get("public_question_id")
    if not value:
        raise ValueError(f"original_question_id/public_question_id not found: {question.get('question_url')}")
    return str(value)


def question_subject(question: dict[str, Any]) -> str:
    label = str(question.get("examLabel") or "")
    for subject in QUESTION_SETS:
        if subject in label:
            return subject
    raise ValueError(f"通関士 category 未定義の examLabel です: {label}")


def patch_path(source_path: Path, subdir: str, tag: str) -> Path:
    list_group_dir = source_path.parents[1]
    return list_group_dir / subdir / f"{source_path.stem}_{tag}.json"


def normalize_snippet_list(source_snippets: Any) -> list[list[str]]:
    if not isinstance(source_snippets, list):
        return []
    normalized: list[list[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list):
            normalized.append([text for text in entry if isinstance(text, str) and text.strip()])
        elif isinstance(entry, str) and entry.strip():
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def normalize_first_snippet_list(source_snippets: Any) -> list[list[str]]:
    if not isinstance(source_snippets, list):
        return []
    normalized: list[list[str]] = []
    for entry in source_snippets:
        if isinstance(entry, list) and entry:
            first = entry[0]
            normalized.append([first] if isinstance(first, str) and first.strip() else [])
        elif isinstance(entry, str) and entry.strip():
            normalized.append([entry])
        else:
            normalized.append([])
    return normalized


def resolved_correct_choice_text(question: dict[str, Any]) -> tuple[list[str], bool, str]:
    source_labels = question.get("correctChoiceText")
    source_list = source_labels if isinstance(source_labels, list) else []
    expected, reason = build_expected_correct_choice_text(question)
    if expected is None:
        detail = ""
        if reason:
            detail = f"source correctChoiceText を維持: {reason}"
        return source_list, False, detail

    if expected == source_list:
        return expected, False, ""

    answer_text = str(question.get("answer_result_text") or "").strip()
    intent = str(question.get("questionIntent") or "").strip()
    detail = f"answer_result_text={answer_text} questionIntent={intent}"
    return expected, True, detail


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flatten_strings(item))
        return result
    return []


def compact_join(parts: list[str], *, max_chars: int = 1800) -> str:
    seen: set[str] = set()
    output: list[str] = []
    total = 0
    for raw in parts:
        text = " ".join(str(raw).split())
        if not text or text in seen:
            continue
        seen.add(text)
        if total + len(text) + 2 > max_chars:
            break
        output.append(text)
        total += len(text) + 2
    return "\n\n".join(output)


def build_explanation_text(question: dict[str, Any], correct_labels: list[str]) -> list[str]:
    choices = question.get("choiceTextList")
    choice_count = len(choices) if isinstance(choices, list) and choices else 1
    snippets_by_choice = normalize_snippet_list(question.get("explanation_choice_snippets"))
    common_parts = (
        flatten_strings(question.get("explanation_common_prefix"))
        + flatten_strings(question.get("explanation_common_summary"))
    )
    answer_text = str(question.get("answer_result_text") or "").strip()
    explanations: list[str] = []

    for index in range(choice_count):
        snippets = snippets_by_choice[index] if index < len(snippets_by_choice) else []
        label = ""
        if isinstance(correct_labels, list) and index < len(correct_labels):
            label = str(correct_labels[index] or "").strip()
        choice_text = ""
        if isinstance(choices, list) and index < len(choices):
            choice_text = str(choices[index] or "").strip()

        prefix_parts = []
        if label:
            prefix_parts.append(f"選択肢{index + 1}は「{label}」です。")
        if choice_text:
            prefix_parts.append(f"選択肢本文: {choice_text}")

        body = compact_join(snippets or common_parts)
        if not body:
            body = "この選択肢は、正答番号と問題文の条件を照合して判断します。"
        if answer_text:
            body = f"{body}\n\n正答情報: {answer_text}"
        explanations.append("\n".join(prefix_parts + [body]).strip())

    return explanations


def build_question_type_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "questionBodyText": question.get("questionBodyText", ""),
            "choiceTextList": question.get("choiceTextList", []),
            "questionType": question.get("questionType", ""),
            "original_question_id": original_id(question),
            "question_url": question.get("question_url", ""),
        }
        for question in questions
    ]


def build_intent_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        correct_choice_text, changed, detail = resolved_correct_choice_text(question)
        payload.append(
            {
                "questionIntent_changed": False,
                "questionIntent_change_detail": "",
                "original_question_id": original_id(question),
                "questionIntent": question.get("questionIntent", "select_correct"),
                "questionIntent_change_reason": "",
                "correctChoiceText_changed": changed,
                "correctChoiceText_change_detail": detail if changed else "",
                "correctChoiceText_change_reason": (
                    "answer_result_text と questionIntent を正本として correctChoiceText を補正"
                    if changed
                    else ""
                ),
                "correctChoiceText": correct_choice_text,
                "explanation_choice_snippets": normalize_first_snippet_list(
                    question.get("explanation_choice_snippets")
                ),
                "question_url": question.get("question_url", ""),
            }
        )
    return payload


def build_explanation_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for question in questions:
        correct_choice_text, _, _ = resolved_correct_choice_text(question)
        payload.append(
            {
                "explanationText": build_explanation_text(question, correct_choice_text),
                "suggestedQuestions": list(SUGGESTED_QUESTIONS),
                "suggestedQuestionDetails": list(SUGGESTED_DETAILS),
                "original_question_id": original_id(question),
                "question_url": question.get("question_url", ""),
                "lawGroundedExplanationNotNeeded": False,
            }
        )
    return payload


def build_question_set_patch(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for question in questions:
        subject = question_subject(question)
        entries.append(
            {
                "questionSetId": QUESTION_SETS[subject]["questionSetId"],
                "original_question_id": original_id(question),
                "question_url": question.get("question_url", ""),
            }
        )
    return entries


def build_category(all_questions: list[dict[str, Any]], existing: dict[str, Any] | None) -> dict[str, Any]:
    counts = Counter(question_subject(question) for question in all_questions)
    now = utc_now()
    existing_folders = {
        str(folder.get("folderId")): folder
        for folder in (existing or {}).get("folders", [])
        if isinstance(folder, dict)
    }
    existing_qsets = {
        str(qset.get("questionSetId")): qset
        for qset in (existing or {}).get("questionSets", [])
        if isinstance(qset, dict)
    }

    folders: list[dict[str, Any]] = []
    question_sets: list[dict[str, Any]] = []
    for subject, spec in QUESTION_SETS.items():
        count = int(counts.get(subject, 0))
        folder_id = spec["folderId"]
        qset_id = spec["questionSetId"]
        prev_folder = existing_folders.get(folder_id, {})
        prev_qset = existing_qsets.get(qset_id, {})
        folder_changed = (
            prev_folder.get("name") != spec["folderName"]
            or int(prev_folder.get("questionCount") or 0) != count
            or bool(prev_folder.get("isDeleted", False)) != (count <= 0)
        )
        qset_changed = (
            prev_qset.get("name") != spec["questionSetName"]
            or prev_qset.get("folderId") != folder_id
            or int(prev_qset.get("questionCount") or 0) != count
            or bool(prev_qset.get("isDeleted", False)) != (count <= 0)
        )
        folders.append(
            {
                "folderId": folder_id,
                "name": spec["folderName"],
                "description": spec["folderDescription"],
                "questionCount": count,
                "isDeleted": count <= 0,
                "updatedAt": now if folder_changed else prev_folder.get("updatedAt", now),
            }
        )
        question_sets.append(
            {
                "questionSetId": qset_id,
                "folderId": folder_id,
                "name": spec["questionSetName"],
                "description": spec["questionSetDescription"],
                "questionCount": count,
                "isDeleted": count <= 0,
                "updatedAt": now if qset_changed else prev_qset.get("updatedAt", now),
            }
        )

    root_changed = existing is None or existing.get("folders") != folders or existing.get("questionSets") != question_sets
    return {
        "folders": folders,
        "questionSets": question_sets,
        "updatedAt": now if root_changed else str(existing.get("updatedAt") or now),
    }


def build_artifacts(*, base_dir: Path, category_json: Path, dry_run: bool) -> None:
    files = source_files(base_dir)
    if not files:
        raise FileNotFoundError(f"通関士の source JSON が見つかりません: {base_dir}")

    total_questions = 0
    all_questions: list[dict[str, Any]] = []
    for source_path in files:
        questions = source_questions(source_path)
        all_questions.extend(questions)
        total_questions += len(questions)
        outputs = [
            ("10_questionType_fixed", "questionType_fixed", build_question_type_patch(questions)),
            ("15_correctChoiceText_fixed", "correctChoiceText_fixed", build_intent_patch(questions)),
            ("21_explanationText_added", "explanationText_added", build_explanation_patch(questions)),
            ("22_questionSetId_linked", "questionSetId_linked", build_question_set_patch(questions)),
        ]
        for subdir, tag, payload in outputs:
            write_json(patch_path(source_path, subdir, tag), payload, dry_run=dry_run)

    existing = load_json(category_json) if category_json.exists() else None
    category = build_category(all_questions, existing)
    write_json(category_json, category, dry_run=dry_run)

    counts = Counter(question_subject(question) for question in all_questions)
    print(f"source files: {len(files)}")
    print(f"source questions: {total_questions}")
    for subject, count in counts.items():
        print(f"{subject}: {count}")
    print(f"category: {category_json}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="通関士の 01-04 固定名patchと category.json を生成します。"
    )
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--category-json", type=Path, default=DEFAULT_CATEGORY_JSON)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    build_artifacts(
        base_dir=args.base_dir.expanduser().resolve(),
        category_json=args.category_json.expanduser().resolve(),
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
