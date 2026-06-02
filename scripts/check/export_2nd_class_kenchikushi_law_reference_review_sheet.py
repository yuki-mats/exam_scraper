#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


QUALIFICATION = "2nd-class-kenchikushi"
SOURCE_SUBDIR = "20_merged_1"
PATCH_SUBDIR = "21_explanationText_added"
PATCH_GLOB = "question_*_law_merged_explanationText_added_*.json"
REVIEW_SCHEMA_VERSION = "2nd-class-kenchikushi-law-reference-review/v1"
PROMPT_SOURCE_PATH = "prompt/03_prompt_add_explanationText.md"
QUALIFICATION_POLICY_PATH = "prompt/qualification_docs/2nd-class-kenchikushi/01_law_reference_manual_review.md"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latest_patch_files(questions_root: Path) -> list[Path]:
    paths: list[Path] = []
    for list_group_dir in sorted(path for path in questions_root.iterdir() if path.is_dir() and path.name.isdigit()):
        candidates = sorted((list_group_dir / PATCH_SUBDIR).glob(PATCH_GLOB))
        if candidates:
            paths.append(candidates[-1])
    return paths


def source_questions_by_id(questions_root: Path) -> dict[str, list[dict[str, Any]]]:
    questions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_path in sorted(questions_root.glob(f"*/{SOURCE_SUBDIR}/question_*.json")):
        payload = load_json(source_path)
        for question in payload.get("question_bodies") or []:
            question_id = str(question.get("original_question_id") or "")
            if not question_id:
                continue
            copied = dict(question)
            copied["_source_file"] = str(source_path)
            copied["_list_group_id"] = source_path.parent.parent.name
            questions[question_id].append(copied)
    return questions


def flatten_law_references(law_references: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    if not isinstance(law_references, list):
        return flattened
    for choice_index, refs in enumerate(law_references):
        if not isinstance(refs, list):
            continue
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                continue
            flattened.append(
                {
                    "choiceIndex": ref.get("choiceIndex", choice_index),
                    "refIndex": ref_index,
                    "lawTitle": ref.get("lawTitle"),
                    "lawAlias": ref.get("lawAlias"),
                    "lawId": ref.get("lawId"),
                    "article": ref.get("article"),
                    "paragraph": ref.get("paragraph"),
                    "item": ref.get("item"),
                    "role": ref.get("role"),
                    "scope": ref.get("scope"),
                    "verificationStatus": ref.get("verificationStatus"),
                    "reason": ref.get("reason"),
                }
            )
    return flattened


def build_review_row(
    *,
    patch_path: Path,
    patch_entry: dict[str, Any],
    source_question: dict[str, Any],
    occurrence_index: int,
) -> dict[str, Any]:
    question_id = str(patch_entry.get("original_question_id") or "")
    list_group_id = source_question.get("_list_group_id") or patch_path.parent.parent.name
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "reviewId": f"{list_group_id}:{question_id}:{occurrence_index + 1}",
        "reviewOccurrenceIndex": occurrence_index,
        "workflow": "03_prompt_add_explanationText -> explanation patch -> law reference audit -> manual review -> repair -> strict audit",
        "promptSourcePath": PROMPT_SOURCE_PATH,
        "qualificationPolicyPath": QUALIFICATION_POLICY_PATH,
        "qualification": QUALIFICATION,
        "listGroupId": str(list_group_id),
        "originalQuestionId": question_id,
        "questionUrl": patch_entry.get("question_url") or source_question.get("question_url"),
        "sourceFile": source_question.get("_source_file"),
        "patchFile": str(patch_path),
        "questionBodyText": source_question.get("questionBodyText"),
        "choiceTextList": source_question.get("choiceTextList") or [],
        "correctChoiceText": source_question.get("correctChoiceText") or [],
        "explanationChoiceSnippets": source_question.get("explanation_choice_snippets") or [],
        "explanationText": patch_entry.get("explanationText") or [],
        "lawReferences": patch_entry.get("lawReferences") or [],
        "lawReferenceSummary": flatten_law_references(patch_entry.get("lawReferences") or []),
        "reviewDecision": "pending",
        "reviewer": "",
        "reviewedAt": "",
        "reviewNotes": "",
        "fixRequired": False,
        "fixInstructions": "",
        "requiredManualChecks": [
            "03_prompt_add_explanationText.md の法令問題ルールに沿っているか確認する",
            "問題文と選択肢が法令条文を正誤根拠にしているか確認する",
            "各 choiceIndex の lawReferences がその選択肢の根拠条文だけを指しているか確認する",
            "lawTitle と lawId が e-Gov の正式法令と一致しているか確認する",
            "article / paragraph / item が explanationText と source snippet の根拠説明に一致しているか確認する",
            "汎用表記の 法 / 令 / 規則 が建築基準法系以外を指す文脈ではないか確認する",
        ],
    }


def build_review_rows(questions_root: Path) -> list[dict[str, Any]]:
    questions = source_questions_by_id(questions_root)
    rows: list[dict[str, Any]] = []
    occurrence_by_question_id: dict[str, int] = defaultdict(int)
    for patch_path in latest_patch_files(questions_root):
        patch_entries = load_json(patch_path)
        for patch_entry in patch_entries:
            question_id = str(patch_entry.get("original_question_id") or "")
            source_candidates = questions.get(question_id) or []
            if not source_candidates:
                raise ValueError(f"source question not found: {question_id} in {patch_path}")
            occurrence_index = occurrence_by_question_id[question_id]
            source_question = source_candidates[min(occurrence_index, len(source_candidates) - 1)]
            occurrence_by_question_id[question_id] += 1
            if not patch_entry.get("lawReferences"):
                continue
            rows.append(
                build_review_row(
                    patch_path=patch_path,
                    patch_entry=patch_entry,
                    source_question=source_question,
                    occurrence_index=occurrence_index,
                )
            )
    return rows


def format_ref(ref: dict[str, Any]) -> str:
    article = ref.get("article") or ""
    paragraph = ref.get("paragraph") or ""
    item = ref.get("item") or ""
    locator = "".join(part for part in (f"第{article}" if article else "", f"第{paragraph}" if paragraph else "", str(item or "")) if part)
    return (
        f"- ref#{ref.get('refIndex')} `{ref.get('lawId')}` {ref.get('lawTitle')} {locator}\n"
        f"  - alias: {ref.get('lawAlias')} / status: {ref.get('verificationStatus')}\n"
        f"  - reason: {ref.get('reason')}"
    )


def build_markdown_for_group(list_group_id: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# 二級建築士 lawReferences 目視監査 {list_group_id}",
        "",
        "この資料は `prompt/03_prompt_add_explanationText.md` で作成した解説 patch の QA 工程で使う。",
        "解説生成ルールの正本は `prompt/03_prompt_add_explanationText.md`、二級建築士固有の監査手順は `prompt/qualification_docs/2nd-class-kenchikushi/01_law_reference_manual_review.md` である。",
        "",
        "## 作業者ルール",
        "",
        "- `reviewDecision` は `ok` / `needs_fix` / `hold` のいずれかにする。",
        "- `ok` は、法令名、lawId、条、項、号、選択肢との対応を e-Gov XML/API または官公庁一次情報で確認できた場合だけ使う。",
        "- `needs_fix` は、誤った lawId、誤った条項、余分な参照、漏れた参照がある場合に使う。",
        "- `hold` は、出題当時法令や改正経緯が必要で、現行法だけでは断定できない場合に使う。",
        "- JSONL 台帳側の `reviewNotes` に、確認した根拠と修正方針を短く書く。",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row['reviewId']}",
                "",
                f"- URL: {row.get('questionUrl')}",
                f"- source: `{row.get('sourceFile')}`",
                f"- patch: `{row.get('patchFile')}`",
                "",
                "### 問題文",
                "",
                str(row.get("questionBodyText") or ""),
                "",
                "### 選択肢 / 解説 / 参照",
                "",
            ]
        )
        choices = row.get("choiceTextList") or []
        labels = row.get("correctChoiceText") or []
        explanations = row.get("explanationText") or []
        snippets = row.get("explanationChoiceSnippets") or []
        refs_by_choice: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for ref in row.get("lawReferenceSummary") or []:
            refs_by_choice[int(ref.get("choiceIndex") or 0)].append(ref)
        for index, choice in enumerate(choices):
            lines.append(f"#### choiceIndex {index}")
            lines.append("")
            lines.append(f"- 判定: {labels[index] if index < len(labels) else ''}")
            lines.append(f"- 選択肢: {choice}")
            if index < len(explanations):
                lines.append(f"- explanationText: {explanations[index]}")
            if index < len(snippets):
                lines.append(f"- source snippets: {json.dumps(snippets[index], ensure_ascii=False)}")
            lines.append("- lawReferences:")
            refs = refs_by_choice.get(index) or []
            if refs:
                lines.extend(format_ref(ref) for ref in refs)
            else:
                lines.append("  - なし")
            lines.append("")
        lines.extend(
            [
                "### 監査結果記入",
                "",
                "- reviewDecision: pending",
                "- reviewNotes:",
                "- fixInstructions:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def export_review_artifacts(rows: list[dict[str, Any]], output_dir: Path, timestamp: str) -> dict[str, Path]:
    jsonl_path = output_dir / f"2nd_class_kenchikushi_law_reference_review_{timestamp}.jsonl"
    write_jsonl(jsonl_path, rows)

    for list_group_id in sorted({str(row["listGroupId"]) for row in rows}):
        group_rows = [row for row in rows if str(row["listGroupId"]) == list_group_id]
        markdown = build_markdown_for_group(list_group_id, group_rows)
        write_text(output_dir / f"2nd_class_kenchikushi_law_reference_review_{list_group_id}_{timestamp}.md", markdown)

    return {"jsonl": jsonl_path}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--questions-root",
        type=Path,
        help="defaults to output/2nd-class-kenchikushi/questions_json under repo root",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="defaults to output/2nd-class-kenchikushi/review/law_reference_manual_review",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root
    questions_root = args.questions_root or repo_root / "output" / QUALIFICATION / "questions_json"
    output_dir = args.output_dir or repo_root / "output" / QUALIFICATION / "review" / "law_reference_manual_review"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    rows = build_review_rows(questions_root)
    paths = export_review_artifacts(rows, output_dir, timestamp)
    print(f"review_rows={len(rows)}")
    print(f"jsonl={paths['jsonl']}")
    print(f"markdown_dir={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
