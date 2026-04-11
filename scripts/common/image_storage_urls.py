from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote, urlparse


FIREBASE_STORAGE_BUCKET = "repaso-rbaqy4.appspot.com"
FIREBASE_STORAGE_BASE_URL = (
    f"https://firebasestorage.googleapis.com/v0/b/{FIREBASE_STORAGE_BUCKET}/o"
)
OFFICIAL_IMAGE_ROOT_PARTS = ("question_images", "official")
IMAGE_URL_FIELD_KEYS = frozenset(
    {
        "questionImageStorageUrls",
        "questionImageUrls",
        "originalQuestionChoiceImageUrls",
        "correctChoiceImageUrls",
        "explanationImageUrls",
        "hintImageUrls",
    }
)


def build_storage_object_path(qualification: str, filename: str) -> str:
    """公開用の画像 object path を返す。"""
    return "/".join((*OFFICIAL_IMAGE_ROOT_PARTS, qualification, filename))


def build_public_storage_url(qualification: str, filename: str) -> str:
    """公開用の画像 URL を返す。"""
    encoded_path = quote(build_storage_object_path(qualification, filename), safe="")
    return f"{FIREBASE_STORAGE_BASE_URL}/{encoded_path}?alt=media"


def extract_storage_object_path(url: str) -> str | None:
    """Firebase Storage の公開 URL / gs URL から object path を抽出する。"""
    if not isinstance(url, str):
        return None

    stripped = url.strip()
    if not stripped:
        return None

    parsed = urlparse(stripped)
    if parsed.scheme == "gs":
        if parsed.netloc != FIREBASE_STORAGE_BUCKET:
            return None
        return parsed.path.lstrip("/") or None

    if parsed.netloc != "firebasestorage.googleapis.com":
        return None

    expected_prefix = f"/v0/b/{FIREBASE_STORAGE_BUCKET}/o/"
    if not parsed.path.startswith(expected_prefix):
        return None

    encoded_path = parsed.path[len(expected_prefix) :]
    if not encoded_path:
        return None

    return unquote(encoded_path)


def _split_official_image_object_path(object_path: str) -> tuple[str, tuple[str, ...]] | None:
    parts = tuple(part for part in object_path.split("/") if part)
    if len(parts) < 4:
        return None
    if parts[:2] != OFFICIAL_IMAGE_ROOT_PARTS:
        return None
    qualification = parts[2]
    remainder = parts[3:]
    if not remainder:
        return None
    return qualification, remainder


def extract_filename_from_storage_url(url: str) -> str | None:
    """Firebase Storage URL から画像ファイル名だけを抽出する。"""
    object_path = extract_storage_object_path(url)
    if object_path is None:
        return None

    parsed = _split_official_image_object_path(object_path)
    if parsed is None:
        return None

    _, remainder = parsed
    filename = remainder[-1]
    return filename or None


def canonicalize_storage_url(url: str, qualification: str) -> str:
    """Firebase Storage の画像 URL を正規形へ揃える。"""
    object_path = extract_storage_object_path(url)
    if object_path is None:
        return url

    parsed = _split_official_image_object_path(object_path)
    if parsed is None:
        return url

    source_qualification, remainder = parsed
    if source_qualification != qualification:
        return url

    filename = remainder[-1]
    if not filename:
        return url

    return build_public_storage_url(qualification, filename)


def canonicalize_image_field_value(value: Any, qualification: str) -> Any:
    """画像 URL の field value を再帰的に正規化する。"""
    if isinstance(value, str):
        return canonicalize_storage_url(value, qualification)
    if isinstance(value, list):
        return [canonicalize_image_field_value(item, qualification) for item in value]
    return value


def normalize_image_url_fields(
    payload: Any,
    qualification: str,
    *,
    field_keys: Iterable[str] = IMAGE_URL_FIELD_KEYS,
) -> int:
    """指定キーにぶら下がる画像 URL を payload 全体から正規化する。"""
    normalized_keys = set(field_keys)
    changes = 0

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in normalized_keys:
                new_value = canonicalize_image_field_value(value, qualification)
                if new_value != value:
                    payload[key] = new_value
                    changes += 1
                continue
            changes += normalize_image_url_fields(
                value,
                qualification,
                field_keys=normalized_keys,
            )
        return changes

    if isinstance(payload, list):
        for item in payload:
            changes += normalize_image_url_fields(
                item,
                qualification,
                field_keys=normalized_keys,
            )

    return changes


def infer_qualification_from_path(path: Path) -> str | None:
    """`output/<qualification>/...` または `.../<qualification>/questions_json/...` から資格コードを推定する。"""
    parts = path.expanduser().resolve().parts

    if "questions_json" in parts:
        idx = parts.index("questions_json")
        if idx > 0:
            return parts[idx - 1]

    if "question_images" in parts:
        idx = parts.index("question_images")
        if idx > 0:
            return parts[idx - 1]

    if "output" in parts:
        idx = parts.index("output")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    return None
