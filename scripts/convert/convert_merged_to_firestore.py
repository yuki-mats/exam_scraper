#!/usr/bin/env python3
"""
merged.jsonファイルをFirestore用のフォーマットに変換するスクリプト
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Any
import re
import shutil
from datetime import datetime

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from scripts.common.image_storage_urls import (
        infer_qualification_from_path,
        normalize_image_url_fields,
    )
    from scripts.check.check_question_intent_correct_choice_text_distribution import (
        raise_on_violations as raise_on_question_intent_correct_choice_violations,
    )
else:
    from scripts.common.image_storage_urls import (
        infer_qualification_from_path,
        normalize_image_url_fields,
    )
    from scripts.check.check_question_intent_correct_choice_text_distribution import (
        raise_on_violations as raise_on_question_intent_correct_choice_violations,
    )

# 試験名定義（ここに必要な試験名を追加して使う）
EXAM_NAME_PSY = "二級建築士"
# 上書き用（コマンドライン引数で設定可能）
OVERRIDE_EXAM_NAME = None

# デフォルトの出力ディレクトリ
DEFAULT_BASE_DIR = Path("")
MERGED_SUBDIR_NAME = "30_merged_2"
CONVERT_SUBDIR_NAME = "40_convert"
UPLOAD_SUBDIR_NAME = "upload_to_firestore"
TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})$")
QUESTION_SET_PATCH_TAG = "questionSetId_linked"


def normalize_payload_image_urls(payload: Any, qualification: str | None) -> int:
    if not qualification:
        return 0
    return normalize_image_url_fields(payload, qualification)


def build_timestamped_firestore_filename(list_group_id: str, timestamp: str | None = None) -> str:
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{list_group_id}_firestore_{timestamp}.json"


def archive_existing_entries(target_dir: Path, *, only_prefix: str | None = None) -> Path | None:
    """
    target_dir 配下の既存エントリを old/<timestamp>/ へ退避する。

    only_prefix を指定した場合は、その prefix で始まるファイル/フォルダのみ退避対象とする。
    例: upload_to_firestore は資格全体で共有ディレクトリのため、
        list_group_id 単位のファイルだけを退避し、他の list_group_id の出力は残す。
    """
    existing: list[Path] = []
    for path in target_dir.iterdir():
        if path.name == "old":
            continue
        if only_prefix and not path.name.startswith(only_prefix):
            continue
        existing.append(path)
    if not existing:
        return None

    archive_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = target_dir / "old"
    archive_dir = archive_root / archive_timestamp
    suffix = 1
    while archive_dir.exists():
        archive_dir = archive_root / f"{archive_timestamp}_{suffix:02d}"
        suffix += 1
    archive_dir.mkdir(parents=True, exist_ok=False)
    for entry in existing:
        shutil.move(str(entry), str(archive_dir / entry.name))
    return archive_dir


def strip_timestamp_suffix(stem: str) -> str:
    return TIMESTAMP_SUFFIX_PATTERN.sub("", stem)


def source_stem_from_patch_filename(filename: str, patch_tag: str) -> str | None:
    path = Path(filename)
    if path.suffix.lower() != ".json":
        return None
    stem = strip_timestamp_suffix(path.stem)
    suffix = f"_{patch_tag}"
    if not stem.endswith(suffix):
        return None
    return stem[: -len(suffix)]


def _timestamp_sort_key(path: Path) -> tuple[int, str, str]:
    match = TIMESTAMP_SUFFIX_PATTERN.search(path.stem)
    if not match:
        return (0, "", path.name)
    return (1, match.group(1), path.name)


def select_latest_patch_files(paths: list[Path], patch_tag: str) -> list[Path]:
    selected: dict[str, Path] = {}
    for path in sorted(paths, key=_timestamp_sort_key):
        source_stem = source_stem_from_patch_filename(path.name, patch_tag)
        if source_stem is None:
            continue
        selected[source_stem] = path
    return sorted(selected.values())


def latest_question_set_patch_files(patch_dir: Path) -> list[Path]:
    if not patch_dir.exists():
        return []
    return select_latest_patch_files(sorted(patch_dir.glob("*.json")), QUESTION_SET_PATCH_TAG)


def format_explanation_text(explanation_list: list) -> str:
    """explanationTextの配列を結合して文字列に"""
    if not explanation_list:
        return ""
    return "\n".join(explanation_list)


def format_suggested_questions(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [question.strip() for question in value if isinstance(question, str) and question.strip()]


def format_suggested_question_details(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized_details: list[dict[str, str]] = []
    for detail in value:
        if not isinstance(detail, dict):
            continue
        question = detail.get("question")
        answer = detail.get("answer")
        if not isinstance(question, str) or not question.strip():
            continue
        if not isinstance(answer, str) or not answer.strip():
            continue
        normalized_details.append(
            {
                "question": question.strip(),
                "answer": answer.strip(),
            }
        )
    return normalized_details


def _normalize_law_reference_entry(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    normalized: dict[str, object] = {}
    for key in (
        "role",
        "scope",
        "lawTitle",
        "lawId",
        "lawRevisionId",
        "lawAlias",
        "article",
        "paragraph",
        "item",
        "referenceDate",
        "reason",
        "verificationStatus",
        "comparisonStatus",
    ):
        raw = value.get(key)
        if raw is None:
            continue
        text = raw.strip() if isinstance(raw, str) else raw
        if isinstance(text, str) and not text:
            continue
        normalized[key] = text

    choice_index = value.get("choiceIndex")
    if isinstance(choice_index, int):
        normalized["choiceIndex"] = choice_index
    elif isinstance(choice_index, str) and choice_index.strip().isdigit():
        normalized["choiceIndex"] = int(choice_index.strip())

    return normalized or None


def format_choice_law_references(value: object, choice_index: int) -> list[dict[str, object]]:
    if not isinstance(value, list) or choice_index >= len(value):
        return []
    choice_refs = value[choice_index]
    if not isinstance(choice_refs, list):
        return []

    normalized_refs: list[dict[str, object]] = []
    for reference in choice_refs:
        normalized = _normalize_law_reference_entry(reference)
        if normalized is None:
            continue
        normalized_refs.append(normalized)
    return normalized_refs


def format_flat_law_references(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    if value and isinstance(value[0], list):
        flattened: list[dict[str, object]] = []
        for idx, _ in enumerate(value):
            flattened.extend(format_choice_law_references(value, idx))
        return flattened

    normalized_refs: list[dict[str, object]] = []
    for reference in value:
        normalized = _normalize_law_reference_entry(reference)
        if normalized is None:
            continue
        normalized_refs.append(normalized)
    return normalized_refs


def resolve_law_grounded_explanation_not_needed(
    question_body: dict,
    choice_index: int | None = None,
) -> bool | None:
    value = question_body.get("lawGroundedExplanationNotNeeded")
    if isinstance(value, bool):
        return value
    if choice_index is not None and isinstance(value, list) and choice_index < len(value):
        choice_value = value[choice_index]
        if isinstance(choice_value, bool):
            return choice_value
    return None


def get_exam_name(question_body: dict) -> str:
    """
    question_body から試験名を取得する。存在しなければデフォルトを返す。
    探すキーは複数候補を試す。
    """
    # コマンドラインで明示的に指定があればそれを最優先
    if OVERRIDE_EXAM_NAME:
        return OVERRIDE_EXAM_NAME

    for key in ("qualificationName", "examName", "qualification", "examTitle", "examTitleName", "exam"):
        v = question_body.get(key)
        if v:
            return v
    return EXAM_NAME_PSY


def get_exam_session(question_body: dict) -> str:
    """
    question_body 内の試験ラベル等から「午前」または「午後」を抽出して返す。
    見つからなければ空文字を返す。
    """
    for key in ("examLabel", "examTitle", "examTitleName", "exam"):
        v = question_body.get(key) or ""
        if not v:
            continue
        if "午前" in v:
            return "午前"
        if "午後" in v:
            return "午後"
    return ""


def get_exam_year(question_body: dict) -> int:
    """
    examYear は必須項目。
    - question_body["examYear"] があればそれを優先
    - 無い/不正な場合は examLabel 等から (YYYY年) / YYYY年 を抽出
    抽出できない場合は例外を投げる。
    """
    raw = question_body.get("examYear")
    if isinstance(raw, int):
        if 1900 <= raw <= 2100:
            return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.isdigit() and len(stripped) == 4:
            year = int(stripped)
            if 1900 <= year <= 2100:
                return year

    import re

    # 和暦→西暦変換（code.py と同等の最低限）
    era_start_year = {
        "令和": 2019,
        "平成": 1989,
        "昭和": 1926,
        "大正": 1912,
        "明治": 1868,
    }

    def to_western_year(era: str, token: str) -> int | None:
        base = era_start_year.get(era)
        if base is None:
            return None
        token = token.translate(str.maketrans("０１２３４５６７８９", "0123456789")).strip()
        if token == "元":
            era_year = 1
        elif token.isdigit():
            era_year = int(token)
        else:
            return None
        if era_year <= 0:
            return None
        return base + era_year - 1

    candidates: list[str] = []
    for key in ("examLabel", "examTitle", "examTitleName", "exam", "exam_name", "exam_label"):
        v = question_body.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    for text in candidates:
        # 優先: "(2023年)" のような西暦
        match = re.search(r"[（(]\s*(\d{4})\s*年\s*[)）]", text)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year

        # 次: "2023年" など
        match = re.search(r"((?:19|20)\d{2})\s*年(?:度)?", text)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year

        # 最後: "令和元年度" / "平成25年度" など（「年度」は「年+度」なのでこの正規表現で拾える）
        match = re.search(r"(令和|平成|昭和|大正|明治)\s*(元|[0-9０-９]+)\s*年(?:度)?", text)
        if match:
            year = to_western_year(match.group(1), match.group(2))
            if year is not None and 1900 <= year <= 2100:
                return year

    raise RuntimeError(
        f"examYear is required but missing/unparseable: {raw!r} (examLabel={question_body.get('examLabel')!r})"
    )


def get_original_question_body_text(question_body: dict) -> str:
    """originalQuestionBodyText を取得する。

    優先順位:
    1. original_question_body_text
    2. originalQuestionBodyText
    3. questionBodyText
    """
    for key in ("original_question_body_text", "originalQuestionBodyText", "questionBodyText"):
        value = question_body.get(key, "")
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
        elif value:
            return str(value)
    return ""


CORRECT_CHOICE_LABELS = {"正しい", "間違い"}
CORRECT_CHOICE_NORMALIZE_MAP = {
    "正解": "正しい",
    "不正解": "間違い",
    "誤り": "間違い",
}


def normalize_correct_choice_text(value: Any) -> Any:
    """correctChoiceText の表記ゆれを補正"""
    if isinstance(value, str):
        return CORRECT_CHOICE_NORMALIZE_MAP.get(value, value)
    if isinstance(value, list):
        return [normalize_correct_choice_text(v) for v in value]
    return value


def normalize_choice_image_urls_by_choice(value: Any) -> list[list[str]]:
    """
    originalQuestionChoiceImageUrls を choice index 対応の配列へ正規化する。
    - 新形式: [["url1"], ["url2", "url3"], ...]
    - 旧/暫定: ["url1", "url2", ...] も許容して [["url1"], ["url2"], ...] に変換
    """
    if not isinstance(value, list):
        return []

    normalized: list[list[str]] = []
    for entry in value:
        if isinstance(entry, list):
            urls = [u.strip() for u in entry if isinstance(u, str) and u.strip()]
            normalized.append(urls)
        elif isinstance(entry, str):
            stripped = entry.strip()
            normalized.append([stripped] if stripped else [])
        else:
            normalized.append([])

    return normalized


def flatten_choice_image_urls(choice_image_urls_by_choice: list[list[str]]) -> list[str]:
    """
    choice index 単位の画像URL配列をフラット化する（重複は除外）。
    """
    flattened: list[str] = []
    for image_urls in choice_image_urls_by_choice:
        for image_url in image_urls:
            if image_url not in flattened:
                flattened.append(image_url)
    return flattened


def get_split_count(*arrays: Any) -> int:
    """
    分割対象の配列群から最大長を返す（最大長ルール）。
    """
    max_len = 0
    for value in arrays:
        if isinstance(value, list):
            max_len = max(max_len, len(value))
    return max_len


def firestore_question_id_for_choice(question_body: dict, choice_index: int) -> str | None:
    """Return an existing Firestore document id for a split choice, when known."""
    firestore_ids = question_body.get("firestoreQuestionIds")
    if not isinstance(firestore_ids, list):
        return None
    if choice_index < 0 or choice_index >= len(firestore_ids):
        return None
    value = firestore_ids[choice_index]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def firestore_source_question_for_choice(question_body: dict, choice_index: int) -> dict | None:
    """Return the original Firestore source question for a split choice, when aligned."""
    source_questions = question_body.get("firestoreSourceQuestions")
    if not isinstance(source_questions, list):
        return None
    if choice_index < 0 or choice_index >= len(source_questions):
        return None
    source_question = source_questions[choice_index]
    if not isinstance(source_question, dict):
        return None

    existing_question_id = firestore_question_id_for_choice(question_body, choice_index)
    if not existing_question_id:
        return None
    source_question_id = str(source_question.get("questionId") or "").strip()
    if source_question_id and source_question_id != existing_question_id:
        return None
    return source_question


def question_set_id_for_choice(question_body: dict, choice_index: int) -> str | None:
    """Return an existing statement-level questionSetId without inventing new classification."""
    source_question = firestore_source_question_for_choice(question_body, choice_index)
    if not source_question:
        return None
    question_set_id = str(source_question.get("questionSetId") or "").strip()
    return question_set_id or None


def single_firestore_question_id(question_body: dict) -> str | None:
    """Return an existing Firestore document id for an unsplit question, when unambiguous."""
    firestore_ids = question_body.get("firestoreQuestionIds")
    if not isinstance(firestore_ids, list):
        return None
    values = [str(value).strip() for value in firestore_ids if str(value or "").strip()]
    if len(values) == 1:
        return values[0]
    return None


def source_unique_key_for_choice(question_body: dict, choice_index: int) -> str | None:
    """Return the deterministic source key for a split choice, when available."""
    source_unique_keys = question_body.get("sourceUniqueKeys")
    if not isinstance(source_unique_keys, list):
        return None
    if choice_index < 0 or choice_index >= len(source_unique_keys):
        return None
    value = source_unique_keys[choice_index]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def question_id_from_source_unique_key(source_unique_key: str) -> str:
    """Build a Firestore-safe deterministic new document id from sourceUniqueKey."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", source_unique_key.strip()).strip("-")


def new_question_id_for_choice(
    question_body: dict,
    choice_index: int,
    fallback_question_id: str,
) -> str:
    """Return the new-document id for a non-Firestore-derived choice."""
    source_unique_key = source_unique_key_for_choice(question_body, choice_index)
    if source_unique_key:
        return question_id_from_source_unique_key(source_unique_key)
    return fallback_question_id


def is_groupable_question(question: dict) -> bool:
    """複数選択肢モードの候補かどうか判定"""
    if question.get("isChoiceOnly"):
        return False
    if not question.get("originalQuestionId"):
        return False
    if not question.get("originalQuestionBodyText"):
        return False
    correct_choice_text = question.get("correctChoiceText")
    return isinstance(correct_choice_text, str) and correct_choice_text in CORRECT_CHOICE_LABELS


def finalize_firestore_question(question: dict) -> dict:
    """共通フィールドの補正（isChoiceOnly/isGroupableなど）"""
    question.setdefault("isChoiceOnly", False)
    question["correctChoiceText"] = normalize_correct_choice_text(question.get("correctChoiceText"))
    question["isGroupable"] = is_groupable_question(question)
    return question


def create_firestore_question_base(
    question_id: str,
    original_question_id: str,
    question_body: dict,
    question_type: str,
    question_text: str,
    correct_choice_text: str,
    explanation_text: str,
    exam_source: str,
    original_question_choice_text: str = None,
    original_question_choice_image_urls: list[str] | None = None,
    question_set_id: str | None = None,
    **additional_fields
) -> dict:
    """
    Firestore用問題レコードの基本構造を作成するヘルパー関数
    """
    firestore_question = {
        "questionId": question_id,
        "originalQuestionId": original_question_id,
        "originalQuestionBodyText": get_original_question_body_text(question_body),
    }

    # place originalQuestionChoiceText near other original_* fields when provided
    if original_question_choice_text is not None:
        firestore_question["originalQuestionChoiceText"] = original_question_choice_text
    if original_question_choice_image_urls:
        firestore_question["originalQuestionChoiceImageUrls"] = original_question_choice_image_urls

    # remaining common fields
    firestore_question.update({
        "questionBodyText": question_body.get('questionBodyText', '').replace('\n', ''),
        "questionSetId": (
            question_set_id
            if question_set_id is not None
            else question_body.get("questionSetId", "")
        ),
        "questionText": question_text,
        "questionType": question_type,
        "correctChoiceText": correct_choice_text,
        "explanationText": explanation_text,
        "examYear": get_exam_year(question_body),
        "examSource": exam_source,
        "isOfficial": True,
        "isDeleted": False,
    })
    suggested_questions = format_suggested_questions(question_body.get("suggestedQuestions", []))
    if suggested_questions:
        firestore_question["suggestedQuestions"] = suggested_questions
    suggested_question_details = format_suggested_question_details(
        question_body.get("suggestedQuestionDetails", [])
    )
    if suggested_question_details:
        firestore_question["suggestedQuestionDetails"] = suggested_question_details
    law_references = format_flat_law_references(question_body.get("lawReferences", []))
    if law_references:
        firestore_question["lawReferences"] = law_references
    law_grounded_explanation_not_needed = resolve_law_grounded_explanation_not_needed(
        question_body
    )
    if law_grounded_explanation_not_needed is not None:
        firestore_question["lawGroundedExplanationNotNeeded"] = (
            law_grounded_explanation_not_needed
        )

    # 追加フィールドをマージ
    firestore_question.update(additional_fields)
    
    # optionalフィールド: 空でない場合のみ追加
    image_urls = question_body.get("questionImageStorageUrls", [])
    if image_urls:
        firestore_question["questionImageUrls"] = image_urls
    
    # Noneの項目を除外
    firestore_question = {k: v for k, v in firestore_question.items() if v is not None}
    
    return firestore_question


def convert_true_false_to_firestore(question_body: dict) -> list[dict]:
    """
    true_false問題を選択肢ごとに分割してFirestore用フォーマットに変換
    各選択肢を個別の問題として扱い、正誤を回答とする
    """
    choice_list = question_body.get("choiceTextList", [])
    correct_choices = question_body.get("correctChoiceText", [])
    explanation_list = question_body.get("explanationText", [])
    choice_image_urls_by_choice = normalize_choice_image_urls_by_choice(
        question_body.get("originalQuestionChoiceImageUrls", [])
    )
    original_question_id = question_body.get("original_question_id", "")
    
    firestore_questions = []
    split_count = get_split_count(
        choice_list,
        correct_choices,
        explanation_list,
        choice_image_urls_by_choice,
    )

    for i in range(split_count):
        choice = choice_list[i] if i < len(choice_list) else ""
        choice_images = (
            choice_image_urls_by_choice[i]
            if i < len(choice_image_urls_by_choice)
            else []
        )
        # 既存Firestore由来データでは document ID を維持する。
        question_id = (
            firestore_question_id_for_choice(question_body, i)
            or new_question_id_for_choice(
                question_body,
                i,
                f"{original_question_id}_{i + 1}" if original_question_id else "",
            )
        )

        # questionText: questionBodyText（改行除去） + 該当の選択肢1つ（改行除去）を[quote][/quote]で囲む
        question_body_text = question_body.get('questionBodyText', '').replace('\n', '')
        choice_text = choice.replace('\n', '') if isinstance(choice, str) else str(choice)
        if choice_text:
            question_text = f"{question_body_text}[quote]{choice_text}[/quote]"
        else:
            question_text = question_body_text

        # correctChoiceText: その選択肢に対する正誤
        correct_text = correct_choices[i] if i < len(correct_choices) else ""

        # explanationText: 対応する解説（あれば）
        explanation_text = explanation_list[i] if i < len(explanation_list) else ""

        # examSource: 試験名（question_body由来）, examYear年, 問x, 設問x
        exam_year = question_body.get("examYear", "")
        question_label = question_body.get("questionLabel", "")
        exam_name = get_exam_name(question_body)
        session = get_exam_session(question_body)
        if session:
            exam_source = f"{exam_name}, {exam_year}年 {session}, {question_label}, 設問{i + 1}"
        else:
            exam_source = f"{exam_name}, {exam_year}年, {question_label}, 設問{i + 1}"

        # Firestore用問題レコードを作成
        firestore_question = create_firestore_question_base(
            question_id=question_id,
            original_question_id=original_question_id,
            question_body=question_body,
            question_type="true_false",
            question_text=question_text,
            correct_choice_text=correct_text,
            explanation_text=explanation_text,
            exam_source=exam_source,
            original_question_choice_text=choice_text,
            original_question_choice_image_urls=choice_images,
            question_set_id=question_set_id_for_choice(question_body, i),
            lawReferences=format_choice_law_references(question_body.get("lawReferences", []), i),
            lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                question_body, i
            ),
        )

        firestore_questions.append(finalize_firestore_question(firestore_question))
    
    return firestore_questions


def convert_group_select_to_firestore(
    question_body: dict,
    question_type: str,
) -> list[dict]:
    """
    flash_card / group_choice 問題をFirestore用フォーマットに変換
    正解の選択肢（isChoiceOnly: false）と誤答の選択肢（isChoiceOnly: true）のリストを返す
    """
    choice_text_list = question_body.get("choiceTextList", [])
    correct_choice_list = question_body.get("correctChoiceText", [])
    explanation_list = question_body.get("explanationText", [])
    choice_image_urls_by_choice = normalize_choice_image_urls_by_choice(
        question_body.get("originalQuestionChoiceImageUrls", [])
    )
    original_question_id = question_body.get("original_question_id", "")

    # questionText: questionBodyTextのみ（改行除去）
    question_text = question_body.get("questionBodyText", "").replace('\n', '')

    # examSource: 試験名（question_body由来）, examYear年, 問x
    exam_year = question_body.get("examYear", "")
    question_label = question_body.get("questionLabel", "")
    exam_name = get_exam_name(question_body)
    session = get_exam_session(question_body)
    if session:
        exam_source = f"{exam_name}, {exam_year}年 {session}, {question_label}"
    else:
        exam_source = f"{exam_name}, {exam_year}年, {question_label}"

    firestore_questions = []
    correct_found = False
    wrong_index = 1
    split_count = get_split_count(
        choice_text_list,
        correct_choice_list,
        explanation_list,
        choice_image_urls_by_choice,
    )

    for i in range(split_count):
        correctness = correct_choice_list[i] if i < len(correct_choice_list) else ""
        choice_text = choice_text_list[i] if i < len(choice_text_list) else ""
        explanation_text = explanation_list[i] if i < len(explanation_list) else ""
        choice_images = (
            choice_image_urls_by_choice[i]
            if i < len(choice_image_urls_by_choice)
            else []
        )

        if correctness in ("正解", "正しい") and not correct_found:
            # 正解の選択肢: 既存Firestore IDがあれば維持し、なければ従来IDを使う。
            correct_found = True
            firestore_question = create_firestore_question_base(
                question_id=firestore_question_id_for_choice(question_body, i)
                or new_question_id_for_choice(question_body, i, original_question_id),
                original_question_id=original_question_id,
                question_body=question_body,
                question_type=question_type,
                question_text=question_text,
                correct_choice_text="正しい",
                explanation_text=explanation_text,
                exam_source=exam_source,
                original_question_choice_text=choice_text,
                original_question_choice_image_urls=choice_images,
                question_set_id=question_set_id_for_choice(question_body, i),
                lawReferences=format_choice_law_references(question_body.get("lawReferences", []), i),
                lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                    question_body, i
                ),
            )
            firestore_questions.append(finalize_firestore_question(firestore_question))
        elif correctness in ("不正解", "間違い", "誤り"):
            # 誤答の選択肢: 既存Firestore IDがあれば維持し、なければ従来IDを使う。
            question_id = (
                firestore_question_id_for_choice(question_body, i)
                or new_question_id_for_choice(
                    question_body,
                    i,
                    f"{original_question_id}_w{wrong_index}" if original_question_id else "",
                )
            )
            wrong_index += 1
            firestore_question = create_firestore_question_base(
                question_id=question_id,
                original_question_id=original_question_id,
                question_body=question_body,
                question_type=question_type,
                question_text=question_text,
                correct_choice_text="間違い",
                explanation_text=explanation_text,
                exam_source=exam_source,
                original_question_choice_text=choice_text,
                original_question_choice_image_urls=choice_images,
                question_set_id=question_set_id_for_choice(question_body, i),
                isChoiceOnly=True,
                lawReferences=format_choice_law_references(question_body.get("lawReferences", []), i),
                lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                    question_body, i
                ),
            )
            firestore_questions.append(finalize_firestore_question(firestore_question))

    # フォールバック: 正解が見つからない場合は最初の選択肢を正解として使用
    if not correct_found and split_count > 0:
        firestore_question = create_firestore_question_base(
            question_id=firestore_question_id_for_choice(question_body, 0)
            or new_question_id_for_choice(question_body, 0, original_question_id),
            original_question_id=original_question_id,
            question_body=question_body,
            question_type=question_type,
            question_text=question_text,
            correct_choice_text="正しい",
            explanation_text=explanation_list[0] if explanation_list else "",
            exam_source=exam_source,
            original_question_choice_text=choice_text_list[0] if choice_text_list else "",
            original_question_choice_image_urls=(
                choice_image_urls_by_choice[0]
                if choice_image_urls_by_choice
                else []
            ),
            question_set_id=question_set_id_for_choice(question_body, 0),
            lawReferences=format_choice_law_references(question_body.get("lawReferences", []), 0),
            lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                question_body, 0
            ),
        )
        firestore_questions.append(finalize_firestore_question(firestore_question))

    return firestore_questions


def convert_flash_card_to_firestore(question_body: dict) -> list[dict]:
    """flash_card問題をFirestore用フォーマットに変換"""
    return convert_group_select_to_firestore(question_body, "flash_card")


def convert_group_choice_to_firestore(question_body: dict) -> list[dict]:
    """group_choice問題をFirestore用フォーマットに変換"""
    return convert_group_select_to_firestore(question_body, "group_choice")


def convert_question_to_firestore(question_body: dict) -> list[dict]:
    """
    1つの問題をFirestore用のフォーマットに変換
    true_false / flash_card / group_choice は複数レコードに分割して返す
    それ以外は1要素のリストで返す
    """
    question_type = question_body.get("questionType", "")
    
    if question_type == "true_false":
        return convert_true_false_to_firestore(question_body)
    elif question_type == "flash_card":
        return convert_flash_card_to_firestore(question_body)
    elif question_type == "group_choice":
        return convert_group_choice_to_firestore(question_body)
    else:
        # その他のタイプ（single_choiceなど）: 従来通りの処理
        choice_list = question_body.get("choiceTextList", [])
        image_urls = question_body.get("questionImageStorageUrls", [])
        choice_image_urls_by_choice = normalize_choice_image_urls_by_choice(
            question_body.get("originalQuestionChoiceImageUrls", [])
        )
        original_question_id = question_body.get("original_question_id", "")
        
        # questionText: questionBodyTextのみ（改行除去）
        question_text = question_body.get("questionBodyText", "").replace('\n', '')
        
        # examSource: 試験名（question_body由来）, examYear年, 問x
        exam_year = get_exam_year(question_body)
        question_label = question_body.get("questionLabel", "")
        exam_name = get_exam_name(question_body)
        session = get_exam_session(question_body)
        if session:
            exam_source = f"{exam_name}, {exam_year}年 {session}, {question_label}"
        else:
            exam_source = f"{exam_name}, {exam_year}年, {question_label}"
        
        original_question_body_text = get_original_question_body_text(question_body)
        firestore_question = {
            "questionId": single_firestore_question_id(question_body)
            or new_question_id_for_choice(question_body, 0, original_question_id),
            "questionSetId": question_set_id_for_choice(question_body, 0)
            or question_body.get("questionSetId", ""),
            "originalQuestionId": original_question_id,
            "originalQuestionBodyText": original_question_body_text,
            "originalQuestionChoiceText": choice_list,
            "questionBodyText": question_text,
            "questionText": question_text,
            "questionType": question_type,
            "correctChoiceText": question_body.get("correctChoiceText", []),
            "explanationText": format_explanation_text(question_body.get("explanationText", [])),
            "examYear": exam_year,
            "examSource": exam_source,
            "isOfficial": True,
            "isDeleted": False,
        }
        suggested_questions = format_suggested_questions(question_body.get("suggestedQuestions", []))
        if suggested_questions:
            firestore_question["suggestedQuestions"] = suggested_questions
        suggested_question_details = format_suggested_question_details(
            question_body.get("suggestedQuestionDetails", [])
        )
        if suggested_question_details:
            firestore_question["suggestedQuestionDetails"] = suggested_question_details
        law_references = format_flat_law_references(question_body.get("lawReferences", []))
        if law_references:
            firestore_question["lawReferences"] = law_references
        law_grounded_explanation_not_needed = resolve_law_grounded_explanation_not_needed(
            question_body
        )
        if law_grounded_explanation_not_needed is not None:
            firestore_question["lawGroundedExplanationNotNeeded"] = (
                law_grounded_explanation_not_needed
            )
        flat_choice_image_urls = flatten_choice_image_urls(choice_image_urls_by_choice)
        if flat_choice_image_urls:
            firestore_question["originalQuestionChoiceImageUrls"] = flat_choice_image_urls
        
        # optionalフィールド: 空でない場合のみ追加
        if image_urls:
            firestore_question["questionImageUrls"] = image_urls
        
        # Noneの項目を除外
        firestore_question = {k: v for k, v in firestore_question.items() if v is not None}
        
        return [finalize_firestore_question(firestore_question)]


def convert_merged_to_firestore(input_path: Path, output_path: Path = None) -> dict:
    """merged.jsonファイルをFirestore用フォーマットに変換"""
    
    with open(input_path, "r", encoding="utf-8") as f:
        merged_data = json.load(f)
    raise_on_question_intent_correct_choice_violations(payload=merged_data, source_path=input_path)
    invalid_path = input_path.with_name(f"{input_path.stem}_invalid{input_path.suffix}")
    if invalid_path.exists():
        with open(invalid_path, "r", encoding="utf-8") as f:
            invalid_data = json.load(f)
        raise_on_question_intent_correct_choice_violations(payload=invalid_data, source_path=invalid_path)
    qualification = infer_qualification_from_path(input_path)
    normalize_payload_image_urls(merged_data, qualification)
    list_group_id = merged_data.get("list_group_id", "unknown")
    question_bodies = merged_data.get("question_bodies", [])

    # --- questionSetId補完用: original_question_id→questionSetIdマップをパッチファイルから作成 ---
    original_id_to_setid = {}
    merged_dir = input_path.parent
    patch_dir = merged_dir.parent / "22_questionSetId_linked"
    for patch_file in latest_question_set_patch_files(patch_dir):
        try:
            with open(patch_file, "r", encoding="utf-8") as pf:
                patch_data = json.load(pf)
                if isinstance(patch_data, dict) and "question_bodies" in patch_data:
                    entries = patch_data.get("question_bodies") or []
                elif isinstance(patch_data, list):
                    entries = patch_data
                else:
                    entries = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    pid = entry.get("original_question_id")
                    qsid = entry.get("questionSetId", "")
                    if pid and qsid:
                        original_id_to_setid[pid] = qsid
        except Exception as e:
            print(f"[WARN] パッチファイル読込失敗: {patch_file}: {e}")

    # 各問題を変換（複数レコードに分割されるタイプがあるため、extendで展開）
    firestore_questions = []
    for question_body in question_bodies:
        # questionSetIdが空なら補完（original_question_id完全一致）
        pid = question_body.get("original_question_id")
        if (not question_body.get("questionSetId")) and pid:
            if pid in original_id_to_setid:
                question_body["questionSetId"] = original_id_to_setid[pid]
        converted_questions = convert_question_to_firestore(question_body)
        # listGroupId を各レコードに付与
        for q in converted_questions:
            q["listGroupId"] = list_group_id
            if qualification:
                q.setdefault("qualificationId", qualification)
            q.setdefault("questionTags", [])
        # true_falseの場合は分割後の各設問にも正しいquestionSetIdを付与
        if question_body.get("questionType") == "true_false" and question_body.get("questionSetId"):
            for q in converted_questions:
                q.setdefault("questionSetId", question_body["questionSetId"])
                if not q["questionSetId"]:
                    q["questionSetId"] = question_body["questionSetId"]
        firestore_questions.extend(converted_questions)

    # 出力データ
    output_data = {
        "list_group_id": list_group_id,
        "questions": firestore_questions,
        "total_count": len(firestore_questions)
    }
    normalize_payload_image_urls(output_data, qualification)

    # 出力ファイルパスの決定（list_group_idディレクトリ内に出力）
    if output_path is None:
        convert_dir = merged_dir.parent / CONVERT_SUBDIR_NAME
        convert_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = convert_dir / f"{input_path.stem}_firestore_{timestamp}.json"

    # ファイルに書き込み
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"変換完了: {input_path} -> {output_path}")
    print(f"  問題数: {len(firestore_questions)}")

    return output_data


def find_merged_files(list_group_id: str, base_dir: Path = DEFAULT_BASE_DIR) -> list[Path]:
    """list_group_idから全てのmerged.jsonファイルを探す"""
    # If a concrete base_dir was provided (not the DEFAULT_EMPTY) and exists, prefer it
    if base_dir != DEFAULT_BASE_DIR and base_dir.exists():
        group_dir = base_dir / list_group_id / MERGED_SUBDIR_NAME
        if not group_dir.exists():
            raise FileNotFoundError(f"ディレクトリが見つかりません: {group_dir}")
    else:
        # Fallback: search workspace for any matching "{list_group_id}/30_merged_2"
        cwd = Path.cwd()
        matches = list(cwd.glob(f"**/{list_group_id}/{MERGED_SUBDIR_NAME}"))
        if not matches:
            raise FileNotFoundError(f"mergedディレクトリが見つかりません（検索）: {list_group_id}/{MERGED_SUBDIR_NAME}")
        if len(matches) > 1:
            print(f"[WARN] 複数の候補が見つかりました。先頭を使用します: {matches[0]}")
        group_dir = matches[0]
    
    # *_merged*.json を探し、同一ベース名（末尾タイムスタンプ違い）は最新1件のみ採用
    merged_candidates = [
        f for f in group_dir.glob("*_merged*.json")
        if not f.name.endswith("_invalid.json")
    ]
    selected: dict[str, Path] = {}
    for path in sorted(merged_candidates):
        stem = path.stem
        canonical_stem = TIMESTAMP_SUFFIX_PATTERN.sub("", stem)
        selected[canonical_stem] = path
    merged_files = sorted(selected.values())
    if not merged_files:
        raise FileNotFoundError(f"merged.jsonファイルが見つかりません: {group_dir}")
    
    # ソートして返す
    return sorted(merged_files)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="merged.jsonファイルをFirestore用フォーマットに変換"
    )
    parser.add_argument(
        "list_group_id",
        type=str,
        help="list_group_id (例: 85010)"
    )
    parser.add_argument(
        "--exam-name",
        dest="exam_name",
        type=str,
        default=None,
        help="出力のexamSourceで使用する試験名（例: 公認心理師）。指定するとquestion_bodyの値を上書きします。"
    )
    parser.add_argument(
        "-b", "--base-dir",
        type=str,
        default=str(DEFAULT_BASE_DIR),
        help="ベースディレクトリ"
    )
    
    args = parser.parse_args(argv)
    
    try:
        base_dir = Path(args.base_dir)
        qualification = infer_qualification_from_path(base_dir)
        # CLIで指定があれば OVERRIDE_EXAM_NAME を設定
        global OVERRIDE_EXAM_NAME
        if args.exam_name:
            OVERRIDE_EXAM_NAME = args.exam_name
        else:
            # list_group_idごとの既知の試験名をここで補完（必要なら追加）
            if args.list_group_id == "97009":
                OVERRIDE_EXAM_NAME = "公認心理師"
        merged_files = find_merged_files(args.list_group_id, base_dir)
        
        # 全てのmergedファイルからの問題を結合
        all_firestore_questions = []
        for input_path in merged_files:
            with open(input_path, "r", encoding="utf-8") as f:
                merged_data = json.load(f)
            raise_on_question_intent_correct_choice_violations(payload=merged_data, source_path=input_path)
            invalid_path = input_path.with_name(f"{input_path.stem}_invalid{input_path.suffix}")
            if invalid_path.exists():
                with open(invalid_path, "r", encoding="utf-8") as f:
                    invalid_data = json.load(f)
                raise_on_question_intent_correct_choice_violations(payload=invalid_data, source_path=invalid_path)
            normalize_payload_image_urls(merged_data, qualification)
            
            question_bodies = merged_data.get("question_bodies", [])
            for question_body in question_bodies:
                converted_questions = convert_question_to_firestore(question_body)
                for q in converted_questions:
                    q["listGroupId"] = args.list_group_id
                    if qualification:
                        q["qualificationId"] = qualification
                    # questionTags は repaso 側の required fields。欠損/None の場合は空配列で埋める。
                    if q.get("questionTags") is None:
                        q["questionTags"] = []
                all_firestore_questions.extend(converted_questions)
            
            print(f"読み込み: {input_path.name} ({len(question_bodies)}問)")
        
        # 出力データ
        output_data = {
            "list_group_id": args.list_group_id,
            "questions": all_firestore_questions,
            "total_count": len(all_firestore_questions)
        }
        normalize_payload_image_urls(output_data, qualification)
        # --- パッチファイルから original_question_id -> questionSetId マップを作成 ---
        original_id_to_setid = {}
        group_dir = base_dir / args.list_group_id
        patch_dir = group_dir / "22_questionSetId_linked"
        for pf in latest_question_set_patch_files(patch_dir):
            try:
                with open(pf, "r", encoding="utf-8") as f:
                    entries = json.load(f)
                    if isinstance(entries, dict):
                        entries = (
                            entries.get("question_bodies")
                            or entries.get("patched_questions")
                            or []
                        )
                    elif not isinstance(entries, list):
                        entries = []
                    for e in entries:
                        if not isinstance(e, dict):
                            continue
                        pid = e.get("original_question_id")
                        qsid = e.get("questionSetId")
                        if pid and qsid:
                            original_id_to_setid[pid] = qsid
            except Exception as e:
                print(f"[WARN] パッチファイル読み込み失敗: {pf}: {e}")

        # original_question_id でマッピングして questionSetId を埋める
        fixed_count = 0
        for q in all_firestore_questions:
            if q.get("questionSetId"):
                continue
            qid = q.get("questionId", "")
            if not qid:
                continue
            # true_false は "originalid_index" 形式、それ以外はそのまま original id の場合がある
            pid = qid.split("_")[0]
            if pid in original_id_to_setid:
                q["questionSetId"] = original_id_to_setid[pid]
                fixed_count += 1
        print(f"[INFO] questionSetId を補完した件数: {fixed_count}")


        # mergedファイルのパスから「questions_json/{list_group_id}」を検出し、
        # 40_convert / upload_to_firestore へタイムスタンプ付きで保存する
        if merged_files:
            merged_path = merged_files[0].resolve()
            parts = list(merged_path.parts)
            try:
                # "questions_json" ディレクトリのインデックスを取得
                qidx = parts.index("questions_json")
                # list_group_id ディレクトリのインデックス
                gid_idx = qidx + 1
                # list_group_id ディレクトリまでのパスを取得
                listgroup_dir = Path(*parts[:gid_idx+1])
                convert_dir = listgroup_dir / CONVERT_SUBDIR_NAME
                upload_dir = listgroup_dir.parent / UPLOAD_SUBDIR_NAME
                convert_dir.mkdir(parents=True, exist_ok=True)
                upload_dir.mkdir(parents=True, exist_ok=True)

                archived_convert = archive_existing_entries(convert_dir)
                archived_upload = archive_existing_entries(
                    upload_dir,
                    only_prefix=f"{args.list_group_id}_firestore_",
                )

                filename = build_timestamped_firestore_filename(args.list_group_id)
                convert_output_path = convert_dir / filename
                upload_output_path = upload_dir / filename
            except (ValueError, IndexError):
                raise FileNotFoundError("mergedファイルのパスにquestions_json/{list_group_id}が含まれていません")
        else:
            raise FileNotFoundError("mergedファイルが見つかりません")

        # ファイルに書き込み（40_convert と upload_to_firestore の両方）
        with open(convert_output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        with open(upload_output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        if archived_convert:
            print(f"[INFO] 旧40_convertファイル/フォルダを退避: {archived_convert}")
        if archived_upload:
            print(f"[INFO] 旧upload_to_firestoreファイル/フォルダを退避: {archived_upload}")

        print(f"\n変換完了(40_convert): {convert_output_path}")
        print(f"保存完了(upload_to_firestore): {upload_output_path}")
        print(f"  合計問題数: {len(all_firestore_questions)}")
        return 0
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
