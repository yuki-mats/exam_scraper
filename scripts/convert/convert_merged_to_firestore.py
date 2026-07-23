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
    from scripts.common.repaso_firestore_schema import (
        LAW_REVISION_AUDIT_STATUSES,
        LAW_REVISION_EVIDENCE_REF_KEYS,
        LAW_REVISION_EVIDENCE_SUMMARY_KEYS,
        LAW_REVISION_FACT_KEYS,
        LAW_REVISION_SNAPSHOT_KEYS,
    )
else:
    from scripts.common.image_storage_urls import (
        infer_qualification_from_path,
        normalize_image_url_fields,
    )
    from scripts.check.check_question_intent_correct_choice_text_distribution import (
        raise_on_violations as raise_on_question_intent_correct_choice_violations,
    )
    from scripts.common.repaso_firestore_schema import (
        LAW_REVISION_AUDIT_STATUSES,
        LAW_REVISION_EVIDENCE_REF_KEYS,
        LAW_REVISION_EVIDENCE_SUMMARY_KEYS,
        LAW_REVISION_FACT_KEYS,
        LAW_REVISION_SNAPSHOT_KEYS,
    )

from scripts.scrape.qualification_presets import publication_qualification_id_for_code
from scripts.common.independent_question_images import (
    INDEPENDENT_IMAGE_REQUIRED_FIELD,
)
from scripts.common.question_identity import question_id_from_source_unique_key
from scripts.common.suggested_question_contract import details_for_choice
from scripts.common.explanation_contract import public_explanation_text
from scripts.common.explanation_references import (
    normalize_explanation_references,
)
from scripts.common.question_answer_contract import (
    question_level_answer_cardinality_issue,
)

# 試験名定義（ここに必要な試験名を追加して使う）
EXAM_NAME_PSY = "二級建築士"
EXAM_NAME_BY_QUALIFICATION = {
    "kounin-shinrishi": "公認心理師",
}
EXAM_NAME_BY_LIST_GROUP_ID = {
    "97001": "公認心理師",
    "97002": "公認心理師",
    "97003": "公認心理師",
    "97004": "公認心理師",
    "97005": "公認心理師",
    "97006": "公認心理師",
    "97007": "公認心理師",
    "97008": "公認心理師",
    "97009": "公認心理師",
}
# 上書き用（コマンドライン引数で設定可能）
OVERRIDE_EXAM_NAME = None

# デフォルトの出力ディレクトリ
DEFAULT_BASE_DIR = Path("")
MERGED_SUBDIR_NAME = "30_merged_2"
CONVERT_SUBDIR_NAME = "40_convert"
UPLOAD_SUBDIR_NAME = "upload_to_firestore"
TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})$")
INDEPENDENT_QUESTION_EXAM_SOURCE = "独自問題"


def is_independent_question(question_body: dict) -> bool:
    return (
        str(question_body.get("examSource") or "").strip()
        == INDEPENDENT_QUESTION_EXAM_SOURCE
    )


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
        "articleTitle",
        "paragraph",
        "item",
        "subitem",
        "referenceDate",
        "effectiveDate",
        "source",
        "sourceUrl",
        "apiUrl",
        "appLinkMode",
        "articleTextHash",
        "rawXmlHash",
        "reason",
        "verificationStatus",
        "comparisonStatus",
        "differenceNote",
    ):
        raw = value.get(key)
        if raw is None:
            continue
        text = raw.strip() if isinstance(raw, str) else raw
        if isinstance(text, str) and not text:
            continue
        normalized[key] = text

    external_primary_source = value.get("externalPrimarySource")
    if isinstance(external_primary_source, bool):
        normalized["externalPrimarySource"] = external_primary_source

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


def format_choice_law_references_with_group_fallback(
    value: object,
    choice_index: int,
    *,
    allow_group_fallback: bool,
) -> list[dict[str, object]]:
    choice_refs = format_choice_law_references(value, choice_index)
    if choice_refs or not allow_group_fallback:
        return choice_refs
    return format_flat_law_references(value)


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


def resolve_is_law_related(
    question_body: dict,
    choice_index: int | None = None,
) -> bool | None:
    value = question_body.get("isLawRelated")
    if isinstance(value, bool):
        return value
    if choice_index is not None and isinstance(value, list) and choice_index < len(value):
        choice_value = value[choice_index]
        if isinstance(choice_value, bool):
            return choice_value
    return None


def drop_none_values(value):
    if isinstance(value, dict):
        return {
            key: drop_none_values(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [drop_none_values(item) for item in value if item is not None]
    return value


def non_empty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def sanitize_optional_string_map(
    value: Any,
    *,
    allowed_keys: set[str],
    list_keys: set[str] | None = None,
    bool_keys: set[str] | None = None,
) -> dict:
    if not isinstance(value, dict):
        return {}
    list_keys = list_keys or set()
    bool_keys = bool_keys or set()
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed_keys or item is None:
            continue
        if key in list_keys:
            if isinstance(item, list):
                cleaned = [entry for entry in item if non_empty_string(entry)]
                if cleaned:
                    sanitized[key] = cleaned
        elif key in bool_keys:
            if isinstance(item, bool):
                sanitized[key] = item
        else:
            text = non_empty_string(item)
            if text is not None:
                sanitized[key] = text
    return sanitized


def sanitize_law_revision_evidence_summary(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    normalized = dict(value)
    normalized.setdefault("verdict", value.get("summary") or value.get("basis"))
    normalized.setdefault("differenceSummary", value.get("revisionImpact"))
    sanitized: dict[str, Any] = {}
    for key, item in normalized.items():
        if key not in LAW_REVISION_EVIDENCE_SUMMARY_KEYS or item is None:
            continue
        if key == "displayRefIds":
            if isinstance(item, list):
                refs = [entry for entry in item if non_empty_string(entry)]
                if refs:
                    sanitized[key] = refs
        elif key == "refs":
            if isinstance(item, list):
                cleaned_refs = []
                for ref in item:
                    cleaned_ref = sanitize_optional_string_map(
                        ref,
                        allowed_keys=LAW_REVISION_EVIDENCE_REF_KEYS,
                        list_keys={"highlightElms"},
                        bool_keys={"primaryBasis"},
                    )
                    if cleaned_ref:
                        cleaned_refs.append(cleaned_ref)
                if cleaned_refs:
                    sanitized[key] = cleaned_refs
        else:
            text = non_empty_string(item)
            if text is not None:
                sanitized[key] = text
    return sanitized


def sanitize_law_revision_facts(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    value = drop_none_values(value)
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if key not in LAW_REVISION_FACT_KEYS or item is None:
            continue
        if key == "auditStatus":
            if item in LAW_REVISION_AUDIT_STATUSES:
                sanitized[key] = item
        elif key in {"examTime", "current"}:
            cleaned = sanitize_optional_string_map(
                item,
                allowed_keys=LAW_REVISION_SNAPSHOT_KEYS,
            )
            if cleaned:
                sanitized[key] = cleaned
        elif key in {"differenceFacts", "answerImpactFacts", "notes"}:
            if isinstance(item, list):
                cleaned = [entry for entry in item if non_empty_string(entry)]
                if cleaned:
                    sanitized[key] = cleaned
        elif key == "evidenceSummary":
            cleaned = sanitize_law_revision_evidence_summary(item)
            if cleaned:
                sanitized[key] = cleaned
        else:
            text = non_empty_string(item)
            if text is not None:
                sanitized[key] = text
    if sanitized.get("auditStatus") not in LAW_REVISION_AUDIT_STATUSES:
        return None
    return sanitized


def resolve_law_revision_facts(
    question_body: dict,
    choice_index: int | None = None,
) -> dict | None:
    value = question_body.get("lawRevisionFacts")
    if isinstance(value, dict):
        resolved = dict(value)
        if choice_index is not None:
            for snapshot_key in ("examTime", "current"):
                snapshot = value.get(snapshot_key)
                if not isinstance(snapshot, dict):
                    continue
                resolved_snapshot = dict(snapshot)
                verdicts = snapshot.get("correctChoiceText")
                if isinstance(verdicts, list) and choice_index < len(verdicts):
                    resolved_snapshot["correctChoiceText"] = verdicts[choice_index]
                resolved[snapshot_key] = resolved_snapshot
        return sanitize_law_revision_facts(resolved)
    if choice_index is not None and isinstance(value, list) and choice_index < len(value):
        choice_value = value[choice_index]
        if isinstance(choice_value, dict):
            return sanitize_law_revision_facts(choice_value)
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
    inferred = infer_exam_name_from_question_body(question_body)
    if inferred:
        return inferred
    return EXAM_NAME_PSY


def resolve_exam_name_override(
    *,
    explicit_exam_name: str | None,
    qualification: str | None,
    list_group_id: str,
) -> str | None:
    """CLI指定、資格コード、listGroupIdの順に試験名上書きを解決する。"""
    normalized_explicit_name = str(explicit_exam_name or "").strip()
    if normalized_explicit_name:
        return normalized_explicit_name

    qualification_exam_name = EXAM_NAME_BY_QUALIFICATION.get(
        str(qualification or "").strip()
    )
    if qualification_exam_name:
        return qualification_exam_name

    return EXAM_NAME_BY_LIST_GROUP_ID.get(str(list_group_id).strip())


def infer_exam_name_from_question_body(question_body: dict) -> str | None:
    source_keys: list[str] = []
    for key in ("sourceQuestionKey", "source_question_key", "sourceUniqueKey", "source_unique_key"):
        value = question_body.get(key)
        if isinstance(value, str) and value.strip():
            source_keys.append(value.strip())
    source_unique_keys = question_body.get("sourceUniqueKeys")
    if isinstance(source_unique_keys, list):
        source_keys.extend(str(value).strip() for value in source_unique_keys if str(value or "").strip())

    joined_source_keys = "\n".join(source_keys)
    if "gas-shunin:kou:" in joined_source_keys:
        return "ガス主任技術者（甲種）"
    if "gas-shunin:otsu:" in joined_source_keys:
        return "ガス主任技術者（乙種）"

    qualification_id = str(question_body.get("qualificationId") or "").strip()
    if qualification_id == "gas-shunin-kou":
        return "ガス主任技術者（甲種）"
    if qualification_id == "gas-shunin-otsu":
        return "ガス主任技術者（乙種）"
    return None


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


def should_preserve_firestore_source(question_body: dict) -> bool:
    """Return true when a reviewed source conflict should keep existing Firestore text."""
    review_decision = str(question_body.get("sourceConflictReviewDecision") or "")
    conflict_policy = str(question_body.get("sourceContentConflictPolicy") or "")
    return "preserve_firestore" in review_decision or "preserve_firestore" in conflict_policy


def upload_choice_text_for_choice(question_body: dict, choice_index: int, fallback: Any) -> str:
    """Return the choice text that should be written to upload JSON."""
    if should_preserve_firestore_source(question_body):
        source_question = firestore_source_question_for_choice(question_body, choice_index)
        if source_question:
            text = str(source_question.get("originalQuestionChoiceText") or "").strip()
            if text:
                return text
    return fallback if isinstance(fallback, str) else str(fallback)


def upload_question_body_text_for_choice(question_body: dict, choice_index: int) -> str:
    """Return the body text that should be written to upload JSON."""
    if should_preserve_firestore_source(question_body):
        source_question = firestore_source_question_for_choice(question_body, choice_index)
        if source_question:
            text = str(source_question.get("originalQuestionBodyText") or "").strip()
            if text:
                return text
    return get_original_question_body_text(question_body)


def question_set_id_for_choice(question_body: dict, choice_index: int) -> str | None:
    """Return an existing statement-level questionSetId without inventing new classification."""
    for key in ("choiceQuestionSetIds", "questionSetIds"):
        question_set_ids = question_body.get(key)
        if not isinstance(question_set_ids, list):
            continue
        if choice_index < 0 or choice_index >= len(question_set_ids):
            continue
        question_set_id = str(question_set_ids[choice_index] or "").strip()
        if question_set_id:
            return question_set_id

    source_question = firestore_source_question_for_choice(question_body, choice_index)
    if not source_question:
        return None
    question_set_id = str(source_question.get("questionSetId") or "").strip()
    return question_set_id or None


def original_question_id_for_upload(question_body: dict) -> str:
    """Return the stable originalQuestionId, falling back to site public ids for new docs."""
    explicit_upload_id = question_body.get("uploadOriginalQuestionId")
    if explicit_upload_id is not None:
        text = str(explicit_upload_id).strip()
        if text:
            return text

    for key in ("canonical_question_id", "canonicalQuestionId", "canonical_public_question_id"):
        value = question_body.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    snake_original_id = str(question_body.get("original_question_id") or "").strip()
    if snake_original_id and not snake_original_id.startswith("firestore:"):
        return snake_original_id

    # Some review patches use original_question_id="firestore:<doc ids>" as a
    # matching key. Keep that out of Firestore's originalQuestionId field when
    # the source-side stable ID is still available.
    for key in ("originalQuestionId", "public_question_id", "publicQuestionId"):
        value = question_body.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return snake_original_id


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
    if question["isChoiceOnly"]:
        question.pop("explanationText", None)
        question.pop("explanationReferences", None)
        question.pop("suggestedQuestions", None)
        question.pop("suggestedQuestionDetails", None)
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
    explanation_text: str | None,
    exam_source: str,
    original_question_choice_text: str = None,
    original_question_choice_image_urls: list[str] | None = None,
    question_set_id: str | None = None,
    suggested_choice_index: int | None = None,
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
        "examSource": (
            INDEPENDENT_QUESTION_EXAM_SOURCE
            if is_independent_question(question_body)
            else exam_source
        ),
        "isOfficial": True,
        "isDeleted": False,
    })
    if is_independent_question(question_body) and isinstance(
        question_body.get(INDEPENDENT_IMAGE_REQUIRED_FIELD), bool
    ):
        firestore_question[INDEPENDENT_IMAGE_REQUIRED_FIELD] = question_body[
            INDEPENDENT_IMAGE_REQUIRED_FIELD
        ]
    if not is_independent_question(question_body):
        firestore_question["examYear"] = get_exam_year(question_body)
    suggested_question_details = (
        details_for_choice(
            question_body.get("suggestedQuestionDetailsByChoice"),
            suggested_choice_index,
        )
        if suggested_choice_index is not None
        else []
    )
    if suggested_question_details:
        firestore_question["suggestedQuestionDetails"] = suggested_question_details
        firestore_question["suggestedQuestions"] = [
            detail["question"] for detail in suggested_question_details
        ]
    law_references = format_flat_law_references(question_body.get("lawReferences", []))
    if law_references:
        firestore_question["lawReferences"] = law_references
    explanation_references = normalize_explanation_references(
        question_body.get("explanationReferences"),
        choice_index=suggested_choice_index,
    )
    if explanation_references:
        firestore_question["explanationReferences"] = explanation_references
    is_law_related = resolve_is_law_related(question_body)
    if is_law_related is not None:
        firestore_question["isLawRelated"] = is_law_related
    law_grounded_explanation_not_needed = resolve_law_grounded_explanation_not_needed(
        question_body
    )
    if law_grounded_explanation_not_needed is not None:
        firestore_question["lawGroundedExplanationNotNeeded"] = (
            law_grounded_explanation_not_needed
        )
    law_revision_facts = resolve_law_revision_facts(question_body)
    if law_revision_facts:
        firestore_question["lawRevisionFacts"] = law_revision_facts

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
    original_question_id = original_question_id_for_upload(question_body)
    
    firestore_questions = []
    split_count = get_split_count(
        choice_list,
        correct_choices,
        explanation_list,
        choice_image_urls_by_choice,
    )

    for i in range(split_count):
        choice = choice_list[i] if i < len(choice_list) else ""
        upload_choice = upload_choice_text_for_choice(question_body, i, choice)
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
        upload_question_body = upload_question_body_text_for_choice(question_body, i)
        question_body_text = upload_question_body.replace('\n', '')
        choice_text = upload_choice.replace('\n', '')
        if choice_text:
            question_text = f"{question_body_text}[quote]{choice_text}[/quote]"
        else:
            question_text = question_body_text

        # correctChoiceText: その選択肢に対する正誤
        correct_text = correct_choices[i] if i < len(correct_choices) else ""

        # explanationText: true_falseは選択肢ごとの解説を使う。
        explanation_text = public_explanation_text(
            explanation_list,
            question_type="true_false",
            choice_index=i,
            is_choice_only=False,
        )
        is_law_related = resolve_is_law_related(question_body, i)

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
            suggested_choice_index=i,
            originalQuestionBodyText=upload_question_body,
            isLawRelated=is_law_related,
            lawReferences=format_choice_law_references_with_group_fallback(
                question_body.get("lawReferences", []),
                i,
                allow_group_fallback=is_law_related is True,
            ),
            lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                question_body, i
            ),
            lawRevisionFacts=resolve_law_revision_facts(question_body, i),
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
    cardinality_error = question_level_answer_cardinality_issue(
        question_type,
        correct_choice_list,
    )
    if cardinality_error:
        raise ValueError(cardinality_error)
    explanation_list = question_body.get("explanationText", [])
    choice_image_urls_by_choice = normalize_choice_image_urls_by_choice(
        question_body.get("originalQuestionChoiceImageUrls", [])
    )
    original_question_id = original_question_id_for_upload(question_body)

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
        is_law_related = resolve_is_law_related(question_body, i)
        law_references = format_choice_law_references_with_group_fallback(
            question_body.get("lawReferences", []),
            i,
            allow_group_fallback=is_law_related is True,
        )
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
                explanation_text=public_explanation_text(
                    explanation_list,
                    question_type=question_type,
                    choice_index=i,
                    is_choice_only=False,
                ),
                exam_source=exam_source,
                original_question_choice_text=choice_text,
                original_question_choice_image_urls=choice_images,
                question_set_id=question_set_id_for_choice(question_body, i),
                suggested_choice_index=i,
                isLawRelated=is_law_related,
                lawReferences=law_references,
                lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                    question_body, i
                ),
                lawRevisionFacts=resolve_law_revision_facts(question_body, i),
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
                explanation_text=public_explanation_text(
                    explanation_list,
                    question_type=question_type,
                    choice_index=i,
                    is_choice_only=True,
                ),
                exam_source=exam_source,
                original_question_choice_text=choice_text,
                original_question_choice_image_urls=choice_images,
                question_set_id=question_set_id_for_choice(question_body, i),
                isChoiceOnly=True,
                isLawRelated=is_law_related,
                lawReferences=law_references,
                lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                    question_body, i
                ),
                lawRevisionFacts=resolve_law_revision_facts(question_body, i),
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
            explanation_text=public_explanation_text(
                explanation_list,
                question_type=question_type,
                choice_index=0,
                is_choice_only=False,
            ),
            exam_source=exam_source,
            original_question_choice_text=choice_text_list[0] if choice_text_list else "",
            original_question_choice_image_urls=(
                choice_image_urls_by_choice[0]
                if choice_image_urls_by_choice
                else []
            ),
            question_set_id=question_set_id_for_choice(question_body, 0),
            suggested_choice_index=0,
            isLawRelated=resolve_is_law_related(question_body, 0),
            lawReferences=format_choice_law_references_with_group_fallback(
                question_body.get("lawReferences", []),
                0,
                allow_group_fallback=resolve_is_law_related(question_body, 0) is True,
            ),
            lawGroundedExplanationNotNeeded=resolve_law_grounded_explanation_not_needed(
                question_body, 0
            ),
            lawRevisionFacts=resolve_law_revision_facts(question_body, 0),
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
        original_question_id = original_question_id_for_upload(question_body)
        
        # questionText: questionBodyTextのみ（改行除去）
        question_text = question_body.get("questionBodyText", "").replace('\n', '')
        
        # 独自問題は年度と取得元を公開せず、表示を一定にする。
        if is_independent_question(question_body):
            exam_year = None
            exam_source = INDEPENDENT_QUESTION_EXAM_SOURCE
        else:
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
            "examSource": exam_source,
            "isOfficial": True,
            "isDeleted": False,
        }
        if is_independent_question(question_body) and isinstance(
            question_body.get(INDEPENDENT_IMAGE_REQUIRED_FIELD), bool
        ):
            firestore_question[INDEPENDENT_IMAGE_REQUIRED_FIELD] = question_body[
                INDEPENDENT_IMAGE_REQUIRED_FIELD
            ]
        if exam_year is not None:
            firestore_question["examYear"] = exam_year
        # 新しい選択肢別fieldが存在するrecordでは、空配列も含めてその値が正本である。
        # single_choice等の非分割docへ選択肢別データを推測投影せず、旧flat fieldも
        # 再公開しない。新fieldがまだない旧recordだけは読取互換を維持する。
        if "suggestedQuestionDetailsByChoice" not in question_body:
            suggested_questions = format_suggested_questions(
                question_body.get("suggestedQuestions", [])
            )
            if suggested_questions:
                firestore_question["suggestedQuestions"] = suggested_questions
            suggested_question_details = format_suggested_question_details(
                question_body.get("suggestedQuestionDetails", [])
            )
            if suggested_question_details:
                firestore_question["suggestedQuestionDetails"] = (
                    suggested_question_details
                )
        law_references = format_flat_law_references(question_body.get("lawReferences", []))
        if law_references:
            firestore_question["lawReferences"] = law_references
        explanation_references = normalize_explanation_references(
            question_body.get("explanationReferences")
        )
        if explanation_references:
            firestore_question["explanationReferences"] = explanation_references
        is_law_related = resolve_is_law_related(question_body)
        if is_law_related is not None:
            firestore_question["isLawRelated"] = is_law_related
        law_grounded_explanation_not_needed = resolve_law_grounded_explanation_not_needed(
            question_body
        )
        if law_grounded_explanation_not_needed is not None:
            firestore_question["lawGroundedExplanationNotNeeded"] = (
                law_grounded_explanation_not_needed
            )
        law_revision_facts = resolve_law_revision_facts(question_body)
        if law_revision_facts:
            firestore_question["lawRevisionFacts"] = law_revision_facts
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
    local_qualification = infer_qualification_from_path(input_path)
    qualification = publication_qualification_id_for_code(local_qualification)
    normalize_payload_image_urls(merged_data, qualification)
    list_group_id = merged_data.get("list_group_id", "unknown")
    question_bodies = merged_data.get("question_bodies", [])
    merged_dir = input_path.parent

    # 各問題を変換（複数レコードに分割されるタイプがあるため、extendで展開）
    firestore_questions = []
    for question_body in question_bodies:
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
    parser.add_argument(
        "--skip-intent-correct-choice-check",
        action="store_true",
        help="既存Firestore由来など answer_result_text がない変換で questionIntent/correctChoiceText 分布チェックをスキップする",
    )
    
    args = parser.parse_args(argv)
    
    try:
        base_dir = Path(args.base_dir)
        local_qualification = infer_qualification_from_path(base_dir)
        qualification = publication_qualification_id_for_code(local_qualification)
        # CLI指定を最優先し、既知の資格コード/listGroupIdからも試験名を補完する
        global OVERRIDE_EXAM_NAME
        OVERRIDE_EXAM_NAME = resolve_exam_name_override(
            explicit_exam_name=args.exam_name,
            qualification=local_qualification,
            list_group_id=args.list_group_id,
        )
        merged_files = find_merged_files(args.list_group_id, base_dir)
        
        # 全てのmergedファイルからの問題を結合
        all_firestore_questions = []
        for input_path in merged_files:
            with open(input_path, "r", encoding="utf-8") as f:
                merged_data = json.load(f)
            if not args.skip_intent_correct_choice_check:
                raise_on_question_intent_correct_choice_violations(payload=merged_data, source_path=input_path)
            invalid_path = input_path.with_name(f"{input_path.stem}_invalid{input_path.suffix}")
            if invalid_path.exists() and not args.skip_intent_correct_choice_check:
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
