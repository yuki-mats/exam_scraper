from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple


MERGED2_SUBDIR_NAME = "30_merged_2"
INVALID_SUFFIX = "_invalid"

# パッチファイル名の末尾に付く作業時刻
# 対応:
# - YYYYMMDD_HHMM
# - YYYYMMDD_HHMMSS
# - YYYYMMDD_HHMM(_NN)
# - YYYYMMDD_HHMMSS(_NN)
TIMESTAMP_SUFFIX_PATTERN = re.compile(r"_(\d{8}_\d{4}|\d{8}_\d{6})(?:_(\d+))?$")


def _has_valid_choice_images(question: dict) -> bool:
    """
    originalQuestionChoiceImageUrls に有効なURL文字列が1つでもあるか判定。
    array<string> と array<array<string>> の両方を許容する。
    """
    raw = question.get("originalQuestionChoiceImageUrls")
    if not isinstance(raw, list):
        return False

    for entry in raw:
        if isinstance(entry, str):
            if entry.strip():
                return True
            continue
        if isinstance(entry, list):
            for image_url in entry:
                if isinstance(image_url, str) and image_url.strip():
                    return True
    return False


def _choice_text_list_empty(question: dict) -> str | None:
    if question.get("questionType") == "fill_in_blank":
        return None

    choices = question.get("choiceTextList")
    if not isinstance(choices, list) or not choices:
        if _has_valid_choice_images(question):
            return None
        return "choiceTextList_empty"
    for entry in choices:
        if isinstance(entry, str):
            if entry.strip():
                return None
        elif entry is not None:
            return None
    if _has_valid_choice_images(question):
        return None
    return "choiceTextList_empty"


def _correct_choice_text_has_null(question: dict) -> str | None:
    cct = question.get("correctChoiceText")
    if isinstance(cct, list) and any(x is None for x in cct):
        return "correctChoiceText_has_null"
    return None


INVALID_RULES: List[Callable[[dict], str | None]] = [
    _choice_text_list_empty,
    _correct_choice_text_has_null,
]


def split_invalid_questions(data: dict) -> Tuple[dict, dict | None]:
    question_bodies = data.get("question_bodies")
    if not isinstance(question_bodies, list):
        return data, None

    valid_questions: List[dict] = []
    manual_questions: List[dict] = []

    for question in question_bodies:
        reasons = []
        for rule in INVALID_RULES:
            reason = rule(question)
            if reason:
                reasons.append(reason)
        if reasons:
            manual_entry = dict(question)
            manual_entry["invalid_reasons"] = reasons
            manual_questions.append(manual_entry)
        else:
            valid_questions.append(question)

    if not manual_questions:
        return data, None

    valid_data = dict(data)
    valid_data["question_bodies"] = valid_questions

    manual_data = dict(data)
    manual_data["question_bodies"] = manual_questions
    return valid_data, manual_data


def should_externalize_for_output(output_path: Path) -> bool:
    return output_path.parent.name == MERGED2_SUBDIR_NAME


def build_manual_output_path(output_path: Path) -> Path:
    suffix = output_path.suffix or ".json"
    stem = output_path.stem
    return output_path.with_name(f"{stem}{INVALID_SUFFIX}{suffix}")


def maybe_split_for_manual_output(data: dict, output_path: Path) -> Tuple[dict, dict | None]:
    if not should_externalize_for_output(output_path):
        return data, None
    return split_invalid_questions(data)


def strip_timestamp_suffix(stem: str) -> str:
    """`*_YYYYMMDD_HHMM(_SS)` の末尾時刻を除去した stem を返す。"""
    return TIMESTAMP_SUFFIX_PATTERN.sub("", stem)


def source_stem_from_patch_filename(filename: str, patch_tag: str) -> str | None:
    """
    パッチファイル名から元ファイル stem を抽出する。
    例:
      question_85010_1_questionType_fixed_20260228_1530.json
      -> question_85010_1
    """
    path = Path(filename)
    if path.suffix.lower() != ".json":
        return None
    stem = strip_timestamp_suffix(path.stem)
    suffix = f"_{patch_tag}"
    if not stem.endswith(suffix):
        return None
    return stem[: -len(suffix)]


def is_patch_filename_for_tag(filename: str, patch_tag: str) -> bool:
    return source_stem_from_patch_filename(filename, patch_tag) is not None


def _timestamp_sort_key(path: Path) -> tuple[int, str, str]:
    """
    時刻付きファイルを優先し、同一タグでは新しい時刻を後に並べるためのキー。
    """
    match = TIMESTAMP_SUFFIX_PATTERN.search(path.stem)
    if not match:
        return (0, "", path.name)
    suffix = match.group(2) or ""
    return (1, f"{match.group(1)}_{suffix}", path.name)


def select_latest_patch_files(paths: List[Path], patch_tag: str) -> List[Path]:
    """
    同一元ファイルに対して複数パッチがある場合、最新（時刻付き優先）の1件だけを返す。
    """
    selected: Dict[str, Path] = {}
    for path in sorted(paths, key=_timestamp_sort_key):
        source_stem = source_stem_from_patch_filename(path.name, patch_tag)
        if source_stem is None:
            continue
        selected[source_stem] = path
    return sorted(selected.values())
