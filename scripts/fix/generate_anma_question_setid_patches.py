#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
QUESTIONS_ROOT = ROOT_DIR / "output" / "anma" / "questions_json"
OUTPUT_SUBDIR = "22_questionSetId_linked"
OUTPUT_SUFFIX = "_merged_questionSetId_linked.json"
TRAINED_TEXT_FIELDS = (
    "questionBodyText",
    "choiceTextList",
    "explanation_common_prefix",
    "explanation_common_summary",
    "explanation_choice_snippets",
    "answer_result_text",
    "questionType",
    "questionIntent",
    "category",
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


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


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u3000", " ")
    text = text.replace("\n", " ")
    text = text.replace("\t", " ")
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"\s+", " ", text).strip()


def build_feature_text(question: dict[str, Any], *, file_no: int, index_in_file: int) -> str:
    parts = [
        f"FILE{file_no}",
        f"POS{index_in_file}",
        f"TYPE:{normalize_text(question.get('questionType'))}",
        f"INTENT:{normalize_text(question.get('questionIntent'))}",
        f"CATEGORY:{normalize_text(question.get('category'))}",
        f"BODY:{normalize_text(question.get('questionBodyText'))}",
        "CHOICES:" + " ".join(flatten_strings(question.get("choiceTextList"))),
        "EXPL_PREFIX:" + " ".join(flatten_strings(question.get("explanation_common_prefix"))),
        "EXPL_SUMMARY:" + " ".join(flatten_strings(question.get("explanation_common_summary"))),
        "EXPL_SNIPPETS:" + " ".join(flatten_strings(question.get("explanation_choice_snippets"))),
        f"ANSWER:{normalize_text(question.get('answer_result_text'))}",
    ]
    return " ".join(part for part in parts if part)


def source_questions(source_path: Path) -> list[dict[str, Any]]:
    payload = load_json(source_path)
    questions = payload.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError(f"question_bodies missing: {source_path}")
    return [q for q in questions if isinstance(q, dict)]


def source_files() -> list[Path]:
    return sorted(QUESTIONS_ROOT.glob("*/00_source/question_*.json"))


def output_path_for(source_path: Path) -> Path:
    return source_path.parent.parent / OUTPUT_SUBDIR / f"{source_path.stem}{OUTPUT_SUFFIX}"


def output_exists_for(source_path: Path) -> bool:
    stage_dir = source_path.parent.parent / OUTPUT_SUBDIR
    return stage_dir.exists() and any(stage_dir.glob(f"{source_path.stem}*_questionSetId_linked.json"))


def parse_file_no(source_path: Path) -> int:
    match = re.match(r"question_\d+_(\d+)\.json$", source_path.name)
    if not match:
        raise ValueError(f"unexpected source filename: {source_path.name}")
    return int(match.group(1))


def build_label_map() -> dict[str, str]:
    label_map: dict[str, str] = {}
    for patch_path in sorted(QUESTIONS_ROOT.glob("*/22_questionSetId_linked/*.json")):
        payload = load_json(patch_path)
        if not isinstance(payload, list):
            raise ValueError(f"unexpected patch payload: {patch_path}")
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            original_id = str(entry.get("original_question_id") or "").strip()
            qset_id = str(entry.get("questionSetId") or "").strip()
            if original_id and qset_id:
                label_map[original_id] = qset_id
    if not label_map:
        raise RuntimeError("training labels not found")
    return label_map


def build_training_rows(label_map: dict[str, str]) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    labels: list[str] = []
    for source_path in source_files():
        file_no = parse_file_no(source_path)
        for index_in_file, question in enumerate(source_questions(source_path), 1):
            original_id = str(question.get("original_question_id") or question.get("public_question_id") or "").strip()
            label = label_map.get(original_id)
            if not label:
                continue
            texts.append(build_feature_text(question, file_no=file_no, index_in_file=index_in_file))
            labels.append(label)
    if not texts:
        raise RuntimeError("training rows not found")
    return texts, labels


def train_classifier(texts: list[str], labels: list[str]):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.svm import LinearSVC
        from sklearn.pipeline import make_pipeline
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "scikit-learn が必要です。/Users/yuki/development/exam_scraper_venv/bin/python3.13 で実行してください。"
        ) from exc

    model = make_pipeline(
        TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            min_df=3,
            max_features=200_000,
            sublinear_tf=True,
        ),
        LinearSVC(class_weight="balanced", C=1.0),
    )
    model.fit(texts, labels)
    return model


def predict_for_source(model, source_path: Path) -> list[dict[str, Any]]:
    file_no = parse_file_no(source_path)
    rows: list[dict[str, Any]] = []
    for index_in_file, question in enumerate(source_questions(source_path), 1):
        feature_text = build_feature_text(question, file_no=file_no, index_in_file=index_in_file)
        question_set_id = str(model.predict([feature_text])[0])
        original_id = str(question.get("original_question_id") or question.get("public_question_id") or "").strip()
        if not original_id:
            raise ValueError(f"missing original/public question id: {source_path} #{index_in_file}")
        rows.append(
            {
                "questionSetId": question_set_id,
                "original_question_id": original_id,
                "question_url": str(question.get("question_url") or ""),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate missing anma 22_questionSetId_linked patches.")
    parser.add_argument(
        "--years",
        nargs="*",
        help="target years to generate (default: all missing years under output/anma/questions_json)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing 22_questionSetId_linked files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    label_map = build_label_map()
    texts, labels = build_training_rows(label_map)
    model = train_classifier(texts, labels)

    target_years = set(str(y) for y in args.years) if args.years else None
    generated_files = 0
    generated_questions = 0
    skipped_existing = 0

    for source_path in source_files():
        year = source_path.parent.parent.name
        if target_years is not None and year not in target_years:
            continue
        if output_exists_for(source_path) and not args.overwrite:
            skipped_existing += 1
            continue
        rows = predict_for_source(model, source_path)
        out_path = output_path_for(source_path)
        write_json(out_path, rows)
        generated_files += 1
        generated_questions += len(rows)
        print(f"[WRITE] {out_path.relative_to(ROOT_DIR)} ({len(rows)} questions)")

    print(
        f"[DONE] generated_files={generated_files} generated_questions={generated_questions} "
        f"skipped_existing={skipped_existing}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
