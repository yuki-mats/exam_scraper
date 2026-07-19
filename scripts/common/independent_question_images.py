from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scripts.common.image_storage_urls import extract_filename_from_storage_url


INDEPENDENT_QUESTION_EXAM_SOURCE = "独自問題"
INDEPENDENT_IMAGE_REQUIRED_FIELD = "_independentImageRequired"
ORIGINALIZED_IMAGE_FILENAME_PREFIX = "originalized_"

SOURCE_QUESTION_IMAGE_FIELDS = (
    "questionImageSourceUrls",
    "questionImageStorageUrls",
    "questionImageUrls",
)
SOURCE_CHOICE_IMAGE_FIELDS = (
    "choiceImageSourceUrlsByChoice",
    "originalQuestionChoiceImageUrls",
)
PUBLISHED_QUESTION_IMAGE_FIELDS = (
    "questionImageStorageUrls",
    "questionImageUrls",
)
PUBLISHED_CHOICE_IMAGE_FIELD = "originalQuestionChoiceImageUrls"


def flatten_non_empty_image_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized and normalized not in urls:
                urls.append(normalized)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for nested in item:
                visit(nested)

    visit(value)
    return urls


def _urls_for_fields(record: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    urls: list[str] = []
    for field in fields:
        for url in flatten_non_empty_image_urls(record.get(field)):
            if url not in urls:
                urls.append(url)
    return urls


def _choice_image_urls_by_index(record: Mapping[str, Any]) -> dict[int, list[str]]:
    by_index: dict[int, list[str]] = {}
    for field in SOURCE_CHOICE_IMAGE_FIELDS:
        value = record.get(field)
        if not isinstance(value, list):
            continue
        for index, item in enumerate(value):
            urls = flatten_non_empty_image_urls(item)
            if not urls:
                continue
            bucket = by_index.setdefault(index, [])
            for url in urls:
                if url not in bucket:
                    bucket.append(url)
    return by_index


def source_image_requirements(
    source: Mapping[str, Any],
) -> tuple[bool, tuple[int, ...]]:
    question_image_required = bool(
        _urls_for_fields(source, SOURCE_QUESTION_IMAGE_FIELDS)
    )
    choice_indices = tuple(sorted(_choice_image_urls_by_index(source)))
    return question_image_required, choice_indices


def independent_image_required(source: Mapping[str, Any]) -> bool:
    question_required, choice_indices = source_image_requirements(source)
    return question_required or bool(choice_indices)


def published_image_urls(record: Mapping[str, Any]) -> list[str]:
    return _urls_for_fields(
        record,
        (*PUBLISHED_QUESTION_IMAGE_FIELDS, PUBLISHED_CHOICE_IMAGE_FIELD),
    )


def _validate_generated_urls(
    *,
    published_urls: Iterable[str],
    source_urls: set[str],
) -> None:
    for url in published_urls:
        if url in source_urls:
            raise ValueError(
                "05_originalizedに取得元画像と同じURLが指定されています。"
            )
        filename = extract_filename_from_storage_url(url)
        if filename is None or not filename.startswith(
            ORIGINALIZED_IMAGE_FILENAME_PREFIX
        ):
            raise ValueError(
                "05_originalizedの公開用画像はFirebase Storageの正規URLを使い、"
                f"ファイル名を{ORIGINALIZED_IMAGE_FILENAME_PREFIX}で始めてください。"
            )


def validate_originalized_image_entry(
    source: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> bool:
    """独自問題用画像が必要な位置に生成画像が指定されているか検証する。"""

    question_required, source_choice_indices = source_image_requirements(source)
    published_question_urls = _urls_for_fields(
        entry, PUBLISHED_QUESTION_IMAGE_FIELDS
    )
    published_choice_by_index = _choice_image_urls_by_index(entry)

    if question_required and not published_question_urls:
        raise ValueError(
            "取得元の問題画像に対応する独自生成画像がありません。"
            "05_originalized.questionImageStorageUrlsを設定してください。"
        )

    missing_choice_indices = [
        index + 1
        for index in source_choice_indices
        if not published_choice_by_index.get(index)
    ]
    if missing_choice_indices:
        raise ValueError(
            "取得元の選択肢画像に対応する独自生成画像がありません: "
            + ", ".join(f"選択肢{index}" for index in missing_choice_indices)
        )

    published_urls = published_image_urls(entry)
    source_urls = set(
        _urls_for_fields(
            source,
            (*SOURCE_QUESTION_IMAGE_FIELDS, *SOURCE_CHOICE_IMAGE_FIELDS),
        )
    )
    _validate_generated_urls(
        published_urls=published_urls,
        source_urls=source_urls,
    )
    return question_required or bool(source_choice_indices)


def validate_independent_upload_image_gate(
    questions: list[dict],
    source_label: str,
) -> None:
    """独自問題の画像要否証跡と公開用生成画像をupload直前に検証する。"""

    groups: dict[str, list[dict]] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        if question.get("isOfficial") is not True or str(
            question.get("examSource") or ""
        ).strip() != INDEPENDENT_QUESTION_EXAM_SOURCE:
            continue

        question_id = str(question.get("questionId") or "").strip()
        marker = question.get(INDEPENDENT_IMAGE_REQUIRED_FIELD)
        if not isinstance(marker, bool):
            raise ValueError(
                f"{INDEPENDENT_IMAGE_REQUIRED_FIELD} is required for independent question: "
                f"{question_id or source_label}"
            )
        original_id = str(question.get("originalQuestionId") or "").strip()
        groups.setdefault(original_id or question_id, []).append(question)

    for original_id, group in groups.items():
        marker_values = {
            question[INDEPENDENT_IMAGE_REQUIRED_FIELD] for question in group
        }
        if len(marker_values) != 1:
            raise ValueError(
                "独自問題の画像要否が同じoriginalQuestionId内で一致しません: "
                f"{original_id}"
            )

        urls: list[str] = []
        for question in group:
            for url in published_image_urls(question):
                if url not in urls:
                    urls.append(url)
        _validate_generated_urls(published_urls=urls, source_urls=set())

        if next(iter(marker_values)) and not urls:
            raise ValueError(
                "画像が必要な独自問題に独自生成画像がありません: "
                f"{original_id or source_label}"
            )
