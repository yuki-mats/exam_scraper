#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.common.question_identity import review_question_id
from scripts.fix.auto_assign_correct_choice_text import build_expected_correct_choice_text


SCHEMA_VERSION = "qualification-01-04-manual-review/v1"
PROMPT_01 = "prompt/01_prompt_fix_questionType.md"
PROMPT_02 = "prompt/02_prompt_fix_questionIntent.md"
PROMPT_03 = "prompt/03_prompt_add_explanationText.md"
PROMPT_04 = "prompt/04_prompt_link_questionSetId.md"

STAGE_DEFS = {
    "questionType": ("10_questionType_fixed", "questionType_fixed"),
    "correctChoice": ("15_correctChoiceText_fixed", "correctChoiceText_fixed"),
    "explanation": ("21_explanationText_added", "explanationText_added"),
    "questionSet": ("22_questionSetId_linked", "questionSetId_linked"),
}

STEP_FIELDS = [
    "review01QuestionType",
    "review02QuestionIntent",
    "review02CorrectChoiceText",
    "review03ExplanationText",
    "review04QuestionSetId",
]
VALID_REVIEW_VALUES = {"pending", "ok", "needs_fix", "hold"}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_questions_root(qualification: str) -> Path:
    return ROOT_DIR / "output" / qualification / "questions_json"


def default_category_path(qualification: str) -> Path:
    return ROOT_DIR / "output" / qualification / "category" / "category.json"


def default_output_dir(qualification: str) -> Path:
    return ROOT_DIR / "output" / qualification / "review" / "01_04_manual_review"


def iter_source_files(questions_root: Path) -> list[Path]:
    return sorted(questions_root.glob("*/00_source/question_*.json"))


def source_year(source_path: Path) -> str:
    return source_path.parent.parent.name


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())


def question_id(question: dict[str, Any]) -> str:
    return review_question_id(question)


def patch_path_for(source_path: Path, stage_key: str) -> Path:
    subdir, suffix = STAGE_DEFS[stage_key]
    return source_path.parent.parent / subdir / f"{source_path.stem}_{suffix}.json"


def load_category_question_sets(category_path: Path) -> set[str]:
    category = load_json(category_path)
    question_sets = category.get("questionSets")
    if not isinstance(question_sets, list):
        raise ValueError(f"questionSets missing: {category_path}")
    return {
        str(item.get("questionSetId"))
        for item in question_sets
        if isinstance(item, dict) and item.get("questionSetId")
    }


def stage_entry_base(source_path: Path, question: dict[str, Any]) -> dict[str, Any]:
    source_original_id = question.get("original_question_id")
    return {
        "original_question_id": question_id(question),
        "source_original_question_id": source_original_id,
        "public_question_id": question.get("public_question_id"),
        "question_url": question.get("question_url"),
        "questionLabel": question.get("questionLabel"),
        "examLabel": question.get("examLabel"),
        "questionBodyText": question.get("questionBodyText"),
        "choiceTextList": question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else [],
        "source_filepath": rel(source_path),
    }


def build_stage_entries(source_path: Path, questions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    entries: dict[str, list[dict[str, Any]]] = {
        "questionType": [],
        "correctChoice": [],
        "explanation": [],
        "questionSet": [],
    }

    for question in questions:
        base = stage_entry_base(source_path, question)

        entries["questionType"].append({
            **base,
            "questionType": question.get("questionType"),
        })
        entries["correctChoice"].append({
            **base,
            "questionIntent": question.get("questionIntent"),
            "correctChoiceText": question.get("correctChoiceText"),
            "answer_result_text": question.get("answer_result_text"),
        })
        entries["explanation"].append({
            **base,
            "explanationText": None,
            "suggestedQuestions": None,
            "suggestedQuestionDetails": None,
            "lawGroundedExplanationNotNeeded": None,
            "sourceExplanationCommonPrefix": question.get("explanation_common_prefix") or [],
            "sourceExplanationCommonSummary": question.get("explanation_common_summary") or [],
            "sourceExplanationChoiceSnippets": question.get("explanation_choice_snippets") or [],
            "sourceExplanationChoiceCorrectness": question.get("explanation_choice_correctness") or [],
        })
        entries["questionSet"].append({
            **base,
            "questionSetId": question.get("questionSetId") or "",
            "category": question.get("category"),
        })

    return entries


def create_stage_skeletons(
    source_path: Path,
    questions: list[dict[str, Any]],
    *,
    overwrite: bool,
) -> dict[str, str]:
    stage_entries = build_stage_entries(source_path, questions)
    result: dict[str, str] = {}
    for stage_key, entries in stage_entries.items():
        path = patch_path_for(source_path, stage_key)
        if path.exists() and not overwrite:
            result[stage_key] = "exists"
            continue
        save_json(path, entries)
        result[stage_key] = "written"
    return result


def build_review_row(
    *,
    qualification: str,
    qualification_name: str,
    source_path: Path,
    source_file_index: int,
    question_index_in_file: int,
    global_index: int,
    question: dict[str, Any],
    category_path: Path,
) -> dict[str, Any]:
    expected_correct_choice_text, correct_choice_reason = build_expected_correct_choice_text(question)
    choice_text_list = question.get("choiceTextList") if isinstance(question.get("choiceTextList"), list) else []
    correct_choice_text = (
        question.get("correctChoiceText") if isinstance(question.get("correctChoiceText"), list) else []
    )
    explanation_text = question.get("explanationText")
    question_set_id = question.get("questionSetId")
    qid = question_id(question)
    review_id = f"{source_year(source_path)}:{source_path.stem}:{qid}"

    stage_paths = {
        key: rel(patch_path_for(source_path, key))
        for key in STAGE_DEFS
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "reviewId": review_id,
        "workflow": "01_questionType -> 02_questionIntent_correctChoiceText -> 03_explanationText -> 04_questionSetId",
        "qualification": qualification,
        "qualificationName": qualification_name,
        "prompt01Path": PROMPT_01,
        "prompt02Path": PROMPT_02,
        "prompt03Path": PROMPT_03,
        "prompt04Path": PROMPT_04,
        "categoryPath": rel(category_path),
        "listGroupId": source_year(source_path),
        "examYear": int(question.get("examYear") or source_year(source_path)),
        "examLabel": str(question.get("examLabel") or ""),
        "sourceFile": rel(source_path),
        "sourceFileIndex": source_file_index,
        "questionIndexInFile": question_index_in_file,
        "globalQuestionIndex": global_index,
        "reviewQuestionId": qid,
        "originalQuestionId": str(question.get("original_question_id") or ""),
        "publicQuestionId": str(question.get("public_question_id") or ""),
        "questionUrl": str(question.get("question_url") or ""),
        "questionLabel": str(question.get("questionLabel") or ""),
        "questionBodyText": str(question.get("questionBodyText") or ""),
        "choiceTextList": choice_text_list,
        "answerResultText": str(question.get("answer_result_text") or ""),
        "sourceCategory": question.get("category"),
        "questionType": str(question.get("questionType") or ""),
        "questionIntent": str(question.get("questionIntent") or ""),
        "correctChoiceText": correct_choice_text,
        "expectedCorrectChoiceText": expected_correct_choice_text or [],
        "explanationText": explanation_text if isinstance(explanation_text, list) else [],
        "questionSetId": str(question_set_id or ""),
        "stagePatchFiles": stage_paths,
        "autoAudit": {
            "choiceCount": len(choice_text_list),
            "questionTypePresent": bool(question.get("questionType")),
            "questionIntentPresent": question.get("questionIntent") in {"select_correct", "select_incorrect"},
            "answerResultTextPresent": bool(str(question.get("answer_result_text") or "").strip()),
            "correctChoiceReason": correct_choice_reason or "",
            "correctChoiceMatchesExpected": (
                expected_correct_choice_text is not None and correct_choice_text == expected_correct_choice_text
            ),
            "explanationTextPresent": isinstance(explanation_text, list) and len(explanation_text) > 0,
            "explanationLengthMatchesChoices": isinstance(explanation_text, list)
            and len(explanation_text) == len(choice_text_list),
            "questionSetIdPresent": bool(question_set_id),
        },
        "requiredManualChecks": [
            "01: questionType が設問形式と選択肢構造に合っているか確認する",
            "02a: questionIntent が『正しいもの/誤っているもの』の設問要求に合っているか確認する",
            "02b: correctChoiceText が answer_result_text と選択肢位置に合っているか確認する",
            "03: explanationText が各選択肢の正誤理由を誤学習なく説明しているか確認する",
            "04: questionSetId が公式出題基準ベースの category.json に合っているか確認する",
        ],
        "review01QuestionType": "pending",
        "review02QuestionIntent": "pending",
        "review02CorrectChoiceText": "pending",
        "review03ExplanationText": "pending",
        "review04QuestionSetId": "pending",
        "reviewDecision": "pending",
        "reviewer": "",
        "reviewedAt": "",
        "reviewNotes": "",
        "fixInstructions": "",
    }


def build_rows(
    *,
    qualification: str,
    qualification_name: str,
    questions_root: Path,
    category_path: Path,
    create_stage_files: bool,
    overwrite_stage_files: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stage_counts: Counter[str] = Counter()
    source_files = iter_source_files(questions_root)
    question_type_counts: Counter[str] = Counter()
    question_intent_counts: Counter[str] = Counter()
    source_category_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()
    questions_per_file: dict[str, int] = {}

    global_index = 0
    for source_file_index, source_path in enumerate(source_files, start=1):
        payload = load_json(source_path)
        questions = payload.get("question_bodies")
        if not isinstance(questions, list):
            raise ValueError(f"question_bodies missing: {source_path}")
        question_dicts = [q for q in questions if isinstance(q, dict)]
        questions_per_file[rel(source_path)] = len(question_dicts)

        if create_stage_files:
            result = create_stage_skeletons(
                source_path,
                question_dicts,
                overwrite=overwrite_stage_files,
            )
            for stage_key, status in result.items():
                stage_counts[f"{stage_key}.{status}"] += 1

        for question_index_in_file, question in enumerate(question_dicts, start=1):
            global_index += 1
            question_type_counts[str(question.get("questionType"))] += 1
            question_intent_counts[str(question.get("questionIntent"))] += 1
            source_category_counts[str(question.get("category"))] += 1
            year_counts[source_year(source_path)] += 1
            rows.append(
                build_review_row(
                    qualification=qualification,
                    qualification_name=qualification_name,
                    source_path=source_path,
                    source_file_index=source_file_index,
                    question_index_in_file=question_index_in_file,
                    global_index=global_index,
                    question=question,
                    category_path=category_path,
                )
            )

    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "qualification": qualification,
        "qualificationName": qualification_name,
        "generatedAt": now_utc(),
        "questionsRoot": rel(questions_root),
        "categoryPath": rel(category_path),
        "sourceFileCount": len(source_files),
        "questionCount": len(rows),
        "yearCounts": dict(sorted(year_counts.items())),
        "questionTypeCounts": dict(sorted(question_type_counts.items())),
        "questionIntentCounts": dict(sorted(question_intent_counts.items())),
        "sourceCategoryCounts": dict(sorted(source_category_counts.items())),
        "stageSkeletonCounts": dict(sorted(stage_counts.items())),
        "questionsPerFile": questions_per_file,
    }
    return rows, summary


def build_work_order_markdown(summary: dict[str, Any], output_dir: Path) -> str:
    qualification = summary["qualification"]
    review_jsonl = output_dir / f"{qualification}_01_04_manual_review.jsonl"
    lines = [
        f"# {summary['qualificationName']} 01-04 manual review prep",
        "",
        "## Scope",
        f"- qualification: `{qualification}`",
        f"- questionsRoot: `{summary['questionsRoot']}`",
        f"- categoryPath: `{summary['categoryPath']}`",
        f"- source files: {summary['sourceFileCount']}",
        f"- questions: {summary['questionCount']}",
        "",
        "## Workflow",
        "- 01: `10_questionType_fixed/` の固定名ファイルを上書きする。",
        "- 02: `15_correctChoiceText_fixed/` で `questionIntent` と `correctChoiceText` を上書きする。",
        "- 03: `21_explanationText_added/` で `explanationText`、`suggestedQuestions`、`suggestedQuestionDetails` を上書きする。",
        "- 04: `22_questionSetId_linked/` で `category.json` の `questionSets[].questionSetId` だけを付与する。",
        "- 各問の `reviewDecision` は、一問ずつ確認が済むまで `pending` のままにする。",
        "",
        "## Verification",
        "```bash",
        f".venv/bin/python scripts/check/prepare_qualification_01_04_manual_review.py check {review_jsonl} \\",
        f"  --expected-total {summary['questionCount']} \\",
        "  --require-stage-files \\",
        f"  --category {summary['categoryPath']} \\",
        "  --allow-pending",
        "```",
        "",
        "## Merge Per Year",
        "```bash",
        f"for y in {' '.join(summary['yearCounts'].keys())}; do",
        f"  .venv/bin/python scripts/merge/00_merge_all.py \"$y\" --base-dir output/{qualification}/questions_json",
        "done",
        "```",
        "",
        "## Year Counts",
    ]
    for year, count in summary["yearCounts"].items():
        lines.append(f"- {year}: {count}")
    lines.append("")
    return "\n".join(lines)


def build_year_markdown(year: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# {rows[0]['qualificationName']} 01-04 manual review {year}",
        "",
        "- `reviewDecision` は `pending` / `ok` / `needs_fix` / `hold` を使う。",
        "- `ok` にする場合は 01〜04 の各 review 欄も `ok` にする。",
        "- `needs_fix` と `hold` は `reviewNotes` を必須にし、修正が必要なら `fixInstructions` を書く。",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row['reviewId']}",
                "",
                f"- source: `{row['sourceFile']}`",
                f"- question: {row['questionLabel']} / {row['questionUrl']}",
                f"- questionType: `{row['questionType']}`",
                f"- questionIntent: `{row['questionIntent']}`",
                f"- questionSetId: `{row['questionSetId'] or 'pending'}`",
                f"- autoAudit: `{json.dumps(row['autoAudit'], ensure_ascii=False, sort_keys=True)}`",
                "",
                "### 問題文",
                "",
                row["questionBodyText"],
                "",
                "### 選択肢",
                "",
            ]
        )
        correct_choice = row["correctChoiceText"]
        expected = row["expectedCorrectChoiceText"]
        for idx, choice in enumerate(row["choiceTextList"], start=1):
            current_label = correct_choice[idx - 1] if idx - 1 < len(correct_choice) else ""
            expected_label = expected[idx - 1] if idx - 1 < len(expected) else ""
            lines.append(f"{idx}. {choice}")
            lines.append(f"   - currentCorrectChoiceText: {current_label}")
            lines.append(f"   - expectedCorrectChoiceText: {expected_label}")
        lines.extend(
            [
                "",
                f"- answerResultText: {row['answerResultText']}",
                "- review01QuestionType: pending",
                "- review02QuestionIntent: pending",
                "- review02CorrectChoiceText: pending",
                "- review03ExplanationText: pending",
                "- review04QuestionSetId: pending",
                "- reviewDecision: pending",
                "- reviewNotes:",
                "- fixInstructions:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def export_artifacts(
    *,
    qualification: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    output_dir: Path,
    write_year_markdown: bool,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{qualification}_01_04_manual_review.jsonl"
    summary_path = output_dir / f"{qualification}_01_04_progress_summary.json"
    work_order_path = output_dir / "README.md"

    write_jsonl(jsonl_path, rows)
    save_json(summary_path, summary)
    write_text(work_order_path, build_work_order_markdown(summary, output_dir))

    if write_year_markdown:
        rows_by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            rows_by_year[str(row["examYear"])].append(row)
        years_dir = output_dir / "years"
        for year, year_rows in sorted(rows_by_year.items()):
            write_text(
                years_dir / f"{qualification}_01_04_manual_review_{year}.md",
                build_year_markdown(year, year_rows),
            )

    return {
        "jsonl": jsonl_path,
        "summary": summary_path,
        "workOrder": work_order_path,
    }


def render_year_markdowns_from_rows(
    *,
    qualification: str,
    rows: list[dict[str, Any]],
    output_dir: Path,
) -> int:
    rows_by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_year[str(row["examYear"])].append(row)

    years_dir = output_dir / "years"
    years_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for year, year_rows in sorted(rows_by_year.items()):
        write_text(
            years_dir / f"{qualification}_01_04_manual_review_{year}.md",
            build_year_markdown(year, year_rows),
        )
        written += 1
    return written


def export_command(args: argparse.Namespace) -> int:
    qualification = args.qualification
    questions_root = (args.questions_root or default_questions_root(qualification)).resolve()
    category_path = (args.category or default_category_path(qualification)).resolve()
    output_dir = (args.output_dir or default_output_dir(qualification)).resolve()

    category_payload = load_json(category_path)
    qualification_name = (
        args.qualification_name
        or category_payload.get("metadata", {}).get("licenseName")
        or category_payload.get("metadata", {}).get("qualificationName")
        or qualification
    )

    rows, summary = build_rows(
        qualification=qualification,
        qualification_name=str(qualification_name),
        questions_root=questions_root,
        category_path=category_path,
        create_stage_files=args.create_stage_skeletons,
        overwrite_stage_files=args.overwrite_stage_skeletons,
    )
    if args.expected_total is not None and len(rows) != args.expected_total:
        raise RuntimeError(f"question count mismatch: expected={args.expected_total} actual={len(rows)}")

    paths = export_artifacts(
        qualification=qualification,
        rows=rows,
        summary=summary,
        output_dir=output_dir,
        write_year_markdown=args.write_year_markdown,
    )
    print(json.dumps({
        "questionCount": len(rows),
        "sourceFileCount": summary["sourceFileCount"],
        "outputs": {key: rel(path) for key, path in paths.items()},
        "stageSkeletonCounts": summary["stageSkeletonCounts"],
    }, ensure_ascii=False, indent=2))
    return 0


def validate_rows(
    rows: list[dict[str, Any]],
    *,
    expected_total: int | None,
    allow_pending: bool,
    require_stage_files: bool,
    category_ids: set[str] | None,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    review_ids = [str(row.get("reviewId") or "") for row in rows]
    duplicated = sorted(item for item, count in Counter(review_ids).items() if count > 1)
    if duplicated:
        errors.append(f"duplicated reviewId: {duplicated[:20]}")
    if expected_total is not None and len(rows) != expected_total:
        errors.append(f"rowCount mismatch: expected={expected_total} actual={len(rows)}")

    decision_counts = Counter(str(row.get("reviewDecision") or "") for row in rows)
    step_counts = {field: Counter(str(row.get(field) or "") for row in rows) for field in STEP_FIELDS}
    qset_present = 0

    for index, row in enumerate(rows, start=1):
        prefix = f"line {index} reviewId={row.get('reviewId')}:"
        if row.get("schemaVersion") != SCHEMA_VERSION:
            errors.append(f"{prefix} schemaVersion must be {SCHEMA_VERSION}")
        for key in (
            "reviewId",
            "qualification",
            "sourceFile",
            "questionBodyText",
            "questionType",
        ):
            if not str(row.get(key) or "").strip():
                errors.append(f"{prefix} {key} is required")
        if not any(str(row.get(key) or "").strip() for key in ("originalQuestionId", "publicQuestionId", "questionUrl")):
            errors.append(f"{prefix} one of originalQuestionId/publicQuestionId/questionUrl is required")
        for field in STEP_FIELDS:
            if row.get(field) not in VALID_REVIEW_VALUES:
                errors.append(f"{prefix} {field} must be one of {sorted(VALID_REVIEW_VALUES)}")
        decision = row.get("reviewDecision")
        if decision not in VALID_REVIEW_VALUES:
            errors.append(f"{prefix} reviewDecision must be one of {sorted(VALID_REVIEW_VALUES)}")
        if decision == "pending" and not allow_pending:
            errors.append(f"{prefix} reviewDecision is still pending")
        if decision in {"ok", "needs_fix", "hold"}:
            if not str(row.get("reviewer") or "").strip():
                errors.append(f"{prefix} reviewer is required when reviewDecision={decision}")
            if not str(row.get("reviewedAt") or "").strip():
                errors.append(f"{prefix} reviewedAt is required when reviewDecision={decision}")
        if decision == "ok":
            for field in STEP_FIELDS:
                if row.get(field) != "ok":
                    errors.append(f"{prefix} {field} must be ok when reviewDecision=ok")
        if decision in {"needs_fix", "hold"} and not str(row.get("reviewNotes") or "").strip():
            errors.append(f"{prefix} reviewNotes is required when reviewDecision={decision}")
        if decision == "needs_fix" and not str(row.get("fixInstructions") or "").strip():
            errors.append(f"{prefix} fixInstructions is required when reviewDecision=needs_fix")

        stage_files = row.get("stagePatchFiles")
        if require_stage_files:
            if not isinstance(stage_files, dict):
                errors.append(f"{prefix} stagePatchFiles must be an object")
            else:
                for stage_key in STAGE_DEFS:
                    value = stage_files.get(stage_key)
                    if not value:
                        errors.append(f"{prefix} stagePatchFiles.{stage_key} is required")
                        continue
                    if not (ROOT_DIR / value).exists():
                        errors.append(f"{prefix} stage file missing: {value}")

        qset_id = str(row.get("questionSetId") or "")
        if qset_id:
            qset_present += 1
            if category_ids is not None and qset_id not in category_ids:
                errors.append(f"{prefix} questionSetId not found in category.json: {qset_id}")

    summary = {
        "rowCount": len(rows),
        "reviewDecisionCounts": dict(sorted(decision_counts.items())),
        "stepDecisionCounts": {field: dict(sorted(counts.items())) for field, counts in step_counts.items()},
        "questionSetIdPresentCount": qset_present,
        "duplicatedReviewIds": duplicated,
        "errorCount": len(errors),
    }
    return summary, errors


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
    return rows


def check_command(args: argparse.Namespace) -> int:
    rows = load_jsonl(args.review_jsonl)
    category_ids = load_category_question_sets(args.category.resolve()) if args.category else None
    summary, errors = validate_rows(
        rows,
        expected_total=args.expected_total,
        allow_pending=args.allow_pending,
        require_stage_files=args.require_stage_files,
        category_ids=category_ids,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for error in errors:
        print(error)
    return 1 if errors else 0


def render_command(args: argparse.Namespace) -> int:
    rows = load_jsonl(args.review_jsonl)
    if not rows:
        raise RuntimeError(f"review rows not found: {args.review_jsonl}")
    qualification = args.qualification or str(rows[0].get("qualification") or "").strip()
    if not qualification:
        raise RuntimeError("--qualification is required when qualification is missing in review rows")
    output_dir = (args.output_dir or args.review_jsonl.parent).resolve()
    written = render_year_markdowns_from_rows(
        qualification=qualification,
        rows=rows,
        output_dir=output_dir,
    )
    print(json.dumps({
        "qualification": qualification,
        "yearMarkdownCount": written,
        "outputDir": rel(output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="資格別 01〜04 manual review の準備台帳と固定名パッチ骨格を生成・検証する",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("qualification")
    export_parser.add_argument("--qualification-name")
    export_parser.add_argument("--questions-root", type=Path)
    export_parser.add_argument("--category", type=Path)
    export_parser.add_argument("--output-dir", type=Path)
    export_parser.add_argument("--expected-total", type=int)
    export_parser.add_argument("--create-stage-skeletons", action="store_true")
    export_parser.add_argument("--overwrite-stage-skeletons", action="store_true")
    export_parser.add_argument("--write-year-markdown", action="store_true")
    export_parser.set_defaults(func=export_command)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("review_jsonl", type=Path)
    check_parser.add_argument("--expected-total", type=int)
    check_parser.add_argument("--allow-pending", action="store_true")
    check_parser.add_argument("--require-stage-files", action="store_true")
    check_parser.add_argument("--category", type=Path)
    check_parser.set_defaults(func=check_command)

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("review_jsonl", type=Path)
    render_parser.add_argument("--qualification")
    render_parser.add_argument("--output-dir", type=Path)
    render_parser.set_defaults(func=render_command)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
