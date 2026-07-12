#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.check.check_gas_shunin_law_explanation_publication import (  # noqa: E402
    DEFAULT_REVIEW_DIR,
    DEFAULT_UPLOAD_DIR,
    load_upload_questions,
    review_choice_map,
)
from scripts.pipeline.finalize_gas_shunin_law_explanations import (  # noqa: E402
    load_json,
    load_review_records,
    normalize_label,
    normalize_text,
    rel,
)


DEFAULT_OUTPUT_DIR = ROOT_DIR / "docs" / "reviews" / "gas-shunin"
DEFAULT_STEM = "20260712_source_final_correct_choice_diff_review"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_source_question(review: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    source_path = ROOT_DIR / str(review.get("sourceFile") or "")
    if not source_path.is_file():
        return None, f"source file not found: {rel(source_path)}"
    payload = load_json(source_path)
    questions = payload.get("question_bodies") if isinstance(payload, dict) else None
    if not isinstance(questions, list):
        return None, f"question_bodies not found: {rel(source_path)}"

    source_key = str(review.get("sourceQuestionKey") or "")
    exact = [
        question
        for question in questions
        if isinstance(question, dict) and str(question.get("sourceQuestionKey") or "") == source_key
    ]
    if len(exact) == 1:
        return exact[0], None

    body = normalize_text(review.get("sourceQuestionBodyText"))
    exact_body = [
        question
        for question in exact
        if normalize_text(question.get("questionBodyText") or question.get("originalQuestionBodyText"))
        == body
    ]
    if len(exact_body) == 1:
        return exact_body[0], None

    fallback = [
        question
        for question in questions
        if isinstance(question, dict)
        and normalize_text(question.get("questionBodyText") or question.get("originalQuestionBodyText"))
        == body
    ]
    if len(fallback) == 1:
        return fallback[0], None
    return None, (
        f"source question match count={len(exact)} exact_body={len(exact_body)} "
        f"fallback={len(fallback)}: {source_key}"
    )


def load_decisions(decision_dir: Path) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for path in sorted(decision_dir.glob("*.json")):
        decision = load_json(path)
        if not isinstance(decision, dict):
            continue
        audit_key = str(decision.get("auditKey") or "")
        if audit_key:
            decisions[audit_key.rsplit(":", 1)[0]] = decision
    return decisions


def normalized_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [normalize_label(item) for item in value]


def compact_reference(reference: Any) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        return None
    return {
        key: reference.get(key)
        for key in (
            "lawTitle",
            "article",
            "paragraph",
            "item",
            "sourceUrl",
            "apiUrl",
            "verificationStatus",
        )
        if reference.get(key) not in (None, "")
    }


def build_report(
    *,
    review_dir: Path,
    upload_dir: Path,
    decision_dir: Path,
) -> dict[str, Any]:
    records = load_review_records(review_dir)
    questions, source_by_id = load_upload_questions(upload_dir)
    mapped, mapping_errors = review_choice_map(records, questions)
    questions_by_id = {str(question.get("questionId") or ""): question for question in questions}
    mapped_by_review: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for question_id, mapping in mapped.items():
        mapped_by_review[id(mapping["review"])].append((int(mapping["choiceIndex"]), question_id))
    decisions = load_decisions(decision_dir)

    comparison_errors = list(mapping_errors)
    diffs: list[dict[str, Any]] = []
    compared_choices = 0
    decision_changed_keys: set[str] = set()
    for source_key, decision in decisions.items():
        if decision.get("reviewDecision", {}).get("correctChoiceTextChanged"):
            decision_changed_keys.add(source_key)

    for review in records:
        source_key = str(review.get("sourceQuestionKey") or "")
        source_question, source_error = load_source_question(review)
        if source_error:
            comparison_errors.append(source_error)
            continue
        assert source_question is not None

        mapped_choices = sorted(mapped_by_review.get(id(review), []))
        source_choices = source_question.get("choiceTextList") or source_question.get(
            "originalQuestionChoiceText"
        )
        source_labels = normalized_labels(source_question.get("correctChoiceText"))
        if not isinstance(source_choices, list) or len(source_choices) != len(source_labels):
            comparison_errors.append(f"invalid source choice arrays: {source_key}")
            continue
        if len(mapped_choices) != len(source_labels):
            comparison_errors.append(
                f"final choice count={len(mapped_choices)} source={len(source_labels)}: {source_key}"
            )
            continue

        final_labels: list[str] = []
        choice_rows: list[dict[str, Any]] = []
        upload_files: set[str] = set()
        for expected_index, (choice_index, question_id) in enumerate(mapped_choices):
            if choice_index != expected_index:
                comparison_errors.append(
                    f"non-contiguous final choice index={choice_index} expected={expected_index}: {source_key}"
                )
            question = questions_by_id[question_id]
            final_label = normalize_label(question.get("correctChoiceText"))
            final_labels.append(final_label)
            upload_files.add(source_by_id.get(question_id, ""))
            references = [
                compact
                for reference in question.get("lawReferences") or []
                if (compact := compact_reference(reference)) is not None
            ]
            choice_rows.append(
                {
                    "choiceNumber": choice_index + 1,
                    "choiceMarker": (
                        source_question.get("judgeChoiceMarkers", [])[choice_index]
                        if choice_index < len(source_question.get("judgeChoiceMarkers") or [])
                        else str(choice_index + 1)
                    ),
                    "choiceText": str(source_choices[choice_index]),
                    "sourceCorrectChoiceText": source_labels[choice_index],
                    "finalCorrectChoiceText": final_label,
                    "changed": source_labels[choice_index] != final_label,
                    "explanationText": str(question.get("explanationText") or ""),
                    "questionId": question_id,
                    "originalQuestionId": question.get("originalQuestionId"),
                    "questionSetId": question.get("questionSetId"),
                    "lawReferences": references,
                }
            )
        compared_choices += len(choice_rows)
        changed_choice_numbers = [row["choiceNumber"] for row in choice_rows if row["changed"]]
        if not changed_choice_numbers:
            continue

        decision = decisions.get(source_key, {})
        evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
        diffs.append(
            {
                "sourceQuestionKey": source_key,
                "qualification": review.get("qualification"),
                "examYear": review.get("examYear"),
                "questionLabel": review.get("questionLabel"),
                "questionBodyText": source_question.get("questionBodyText")
                or source_question.get("originalQuestionBodyText"),
                "answerResultText": source_question.get("answer_result_text"),
                "sourceUrl": source_question.get("sourceUrl") or source_question.get("question_url"),
                "sourceFile": review.get("sourceFile"),
                "finalUploadFiles": sorted(upload_file for upload_file in upload_files if upload_file),
                "sourceCorrectChoiceText": source_labels,
                "finalCorrectChoiceText": final_labels,
                "changedChoiceNumbers": changed_choice_numbers,
                "decisionCorrectChoiceTextChanged": bool(
                    decision.get("reviewDecision", {}).get("correctChoiceTextChanged")
                ),
                "reviewNotes": decision.get("reviewNotes") or [],
                "officialEvidence": {
                    key: evidence.get(key)
                    for key in (
                        "questionPdfUrl",
                        "questionPdfSha256",
                        "answerPdfUrl",
                        "answerPdfSha256",
                        "officialAnswerNumber",
                        "officialCombination",
                        "verificationStatus",
                    )
                    if evidence.get(key) not in (None, "")
                },
                "choices": choice_rows,
                "userReview": {
                    "status": "pending",
                    "correctChoiceTextApproved": None,
                    "explanationTextApproved": None,
                    "firestoreUploadApproved": None,
                    "reviewer": None,
                    "reviewedAt": None,
                    "notes": "",
                },
            }
        )

    diff_keys = {str(row["sourceQuestionKey"]) for row in diffs}
    return {
        "schemaVersion": "gas-shunin-source-final-correct-choice-diff-review/v1",
        "generatedAt": utc_now(),
        "scope": "gas-shunin law-section final upload artifacts vs immutable 00_source",
        "reviewStatus": "pending_user_review" if diffs else "no_differences",
        "firestoreUploadApproved": False,
        "sourceQuestionCount": len(records),
        "comparedChoiceCount": compared_choices,
        "finalUploadQuestionCount": len(questions),
        "differenceQuestionCount": len(diffs),
        "differenceChoiceCount": sum(len(row["changedChoiceNumbers"]) for row in diffs),
        "decisionCorrectChoiceTextChangedCount": len(decision_changed_keys),
        "decisionDiffSetMatchesActualDiffSet": decision_changed_keys == diff_keys,
        "comparisonErrorCount": len(comparison_errors),
        "comparisonErrors": comparison_errors,
        "diffs": diffs,
    }


def md_text(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ガス主任技術者 法令 correctChoiceText 差分確認票",
        "",
        f"生成日時: `{report['generatedAt']}`",
        "Firestoreアップロード判定: **未承認（ユーザー確認待ち）**",
        "",
        "## 集計",
        "",
        f"- 比較対象: {report['sourceQuestionCount']}問 / {report['comparedChoiceCount']}肢",
        f"- 差分: **{report['differenceQuestionCount']}問 / {report['differenceChoiceCount']}肢**",
        f"- 比較エラー: {report['comparisonErrorCount']}件",
        f"- decisionの変更宣言と実差分集合の一致: `{str(report['decisionDiffSetMatchesActualDiffSet']).lower()}`",
        "",
        "## 確認手順",
        "",
        "1. 問題文と公式問題PDFの選択肢組合せが一致するか確認する。",
        "2. 各肢の最終正誤と解説が、問題文の問い方に対して妥当か確認する。",
        "3. 根拠法令・公式解答を確認する。",
        "4. 問題なければ末尾の承認欄を更新してからFirestore投入へ進む。",
        "",
    ]
    for diff_index, diff in enumerate(report["diffs"], start=1):
        lines.extend(
            [
                f"## 差分{diff_index}: {diff['qualification']} {diff['examYear']}年 {diff['questionLabel']}",
                "",
                f"- sourceQuestionKey: `{diff['sourceQuestionKey']}`",
                f"- 00_source: `{diff['sourceFile']}`",
                f"- 問題文: {md_text(diff['questionBodyText'])}",
                f"- 00_sourceの解答表示: `{md_text(diff['answerResultText'])}`",
                f"- 00_source判定: `{', '.join(diff['sourceCorrectChoiceText'])}`",
                f"- 最終判定: `{', '.join(diff['finalCorrectChoiceText'])}`",
                f"- 変更肢: `{', '.join(str(value) for value in diff['changedChoiceNumbers'])}`",
                "",
                "| 肢 | 選択肢 | 00_source | 最終 | 差分 | 最終解説 | Firestore questionId |",
                "|---:|---|---|---|---|---|---|",
            ]
        )
        for choice in diff["choices"]:
            lines.append(
                "| {number}（{marker}） | {text} | {source} | {final} | {changed} | {explanation} | `{qid}` |".format(
                    number=choice["choiceNumber"],
                    marker=md_text(choice["choiceMarker"]),
                    text=md_text(choice["choiceText"]),
                    source=choice["sourceCorrectChoiceText"],
                    final=choice["finalCorrectChoiceText"],
                    changed="**変更**" if choice["changed"] else "—",
                    explanation=md_text(choice["explanationText"]),
                    qid=choice["questionId"],
                )
            )
        lines.extend(["", "### 公式根拠", ""])
        evidence = diff.get("officialEvidence") or {}
        if evidence.get("questionPdfUrl"):
            lines.append(f"- [JIA公式問題PDF]({evidence['questionPdfUrl']})")
        if evidence.get("answerPdfUrl"):
            lines.append(f"- [JIA公式解答PDF]({evidence['answerPdfUrl']})")
        law_urls = sorted(
            {
                ref.get("sourceUrl") or ref.get("apiUrl")
                for choice in diff["choices"]
                for ref in choice.get("lawReferences") or []
                if ref.get("sourceUrl") or ref.get("apiUrl")
            }
        )
        for law_url in law_urls:
            lines.append(f"- [根拠法令]({law_url})")
        if diff.get("reviewNotes"):
            lines.extend(["", "### 監査メモ", ""])
            lines.extend(f"- {md_text(note)}" for note in diff["reviewNotes"])
        lines.extend(
            [
                "",
                "### ユーザー確認欄",
                "",
                "- [ ] 問題文・公式問題の対応が妥当",
                "- [ ] final correctChoiceTextが妥当",
                "- [ ] 5肢の解説文が妥当",
                "- [ ] Firestoreアップロードを承認",
                "- 確認者:",
                "- 確認日時:",
                "- コメント:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare immutable gas-shunin law 00_source labels with final upload labels."
    )
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--upload-dir", type=Path, default=DEFAULT_UPLOAD_DIR)
    parser.add_argument(
        "--decision-dir",
        type=Path,
        default=ROOT_DIR
        / "output"
        / "gas-shunin-all"
        / "review"
        / "law_explanation_refresh"
        / "decisions",
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_OUTPUT_DIR / f"{DEFAULT_STEM}.json")
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_OUTPUT_DIR / f"{DEFAULT_STEM}.md"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        review_dir=args.review_dir.expanduser().resolve(),
        upload_dir=args.upload_dir.expanduser().resolve(),
        decision_dir=args.decision_dir.expanduser().resolve(),
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "sourceQuestionCount",
                    "comparedChoiceCount",
                    "differenceQuestionCount",
                    "differenceChoiceCount",
                    "decisionCorrectChoiceTextChangedCount",
                    "decisionDiffSetMatchesActualDiffSet",
                    "comparisonErrorCount",
                    "reviewStatus",
                    "firestoreUploadApproved",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if report["comparisonErrorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
