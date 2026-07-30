from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tools.question_review_console.review_store import atomic_write


PRIMARY_LAW_EVIDENCE_SCHEMA = "primary-law-evidence/v2"
_LAW_FILE_ENDPOINT = "https://laws.e-gov.go.jp/api/2/law_file/xml"
_SPACE_RE = re.compile(r"\s+")
_JAPANESE_NUMBER_PATTERN = r"[0-9０-９一二三四五六七八九十百]+"
_ARTICLE_RE = re.compile(
    rf"第?\s*({_JAPANESE_NUMBER_PATTERN})\s*条"
    rf"((?:\s*の\s*{_JAPANESE_NUMBER_PATTERN})*)"
)
_ARTICLE_BRANCH_RE = re.compile(
    rf"の\s*({_JAPANESE_NUMBER_PATTERN})"
)
_OMITTED_ARTICLE_RE = re.compile(
    rf"^\s*({_JAPANESE_NUMBER_PATTERN})\s*"
    rf"第\s*{_JAPANESE_NUMBER_PATTERN}\s*項"
)
_APPENDIX_RE = re.compile(r"別表第?\s*([0-9０-９一二三四五六七八九十百]+)")
_REVISION_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')
_KANJI_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


class PrimaryLawEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class LawFileSnapshot:
    law_id: str
    as_of: str
    source_url: str
    revision_id: str
    xml_text: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _japanese_number(value: str) -> int | None:
    text = str(value or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if text.isdigit():
        return int(text)
    if not text or any(character not in {*_KANJI_DIGITS, "十", "百"} for character in text):
        return None
    total = 0
    current = 0
    for character in text:
        if character in _KANJI_DIGITS:
            current = _KANJI_DIGITS[character]
        elif character == "十":
            total += (current or 1) * 10
            current = 0
        elif character == "百":
            total += (current or 1) * 100
            current = 0
    return total + current


LocatorNumber = int | tuple[int, ...]


def _locator_number_parts(value: LocatorNumber) -> tuple[int, ...]:
    return value if isinstance(value, tuple) else (value,)


def _locator_num(value: LocatorNumber) -> str:
    return "_".join(str(number) for number in _locator_number_parts(value))


def _locator_payload(kind: str, number: LocatorNumber) -> dict[str, Any]:
    parts = _locator_number_parts(number)
    payload: dict[str, Any] = {
        "kind": kind,
        "number": parts[0],
        "num": _locator_num(number),
    }
    if len(parts) > 1:
        payload["branchNumbers"] = list(parts[1:])
    return payload


def _xml_locator_parts(value: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for component in str(value or "").split("_"):
        number = _japanese_number(component)
        if number is None:
            return None
        parts.append(number)
    return tuple(parts) if parts else None


def locator_parts(value: Any) -> tuple[tuple[str, LocatorNumber], ...]:
    """Extract every article/appendix locator declared in one reference."""

    text = str(value or "")
    parts: list[tuple[str, LocatorNumber]] = []
    for match in _ARTICLE_RE.finditer(text):
        article_number = _japanese_number(match.group(1))
        if article_number is None:
            continue
        branch_numbers = tuple(
            number
            for raw_number in _ARTICLE_BRANCH_RE.findall(match.group(2) or "")
            for number in [_japanese_number(raw_number)]
            if number is not None
        )
        number: LocatorNumber = (
            (article_number, *branch_numbers)
            if branch_numbers
            else article_number
        )
        parts.append(("article", number))
    for match in _APPENDIX_RE.finditer(text):
        number = _japanese_number(match.group(1))
        if number is not None:
            parts.append(("appendix_table", number))
    if not parts:
        omitted_match = _OMITTED_ARTICLE_RE.search(text)
        raw_number = (
            omitted_match.group(1)
            if omitted_match is not None
            else text.strip()
        )
        number = _japanese_number(raw_number)
        if number is not None:
            parts.append(("article", number))
    return tuple(dict.fromkeys(parts))


def _element_text(element: ET.Element) -> str:
    return _normalize_text(" ".join(element.itertext()))


def extract_locator_text(
    xml_text: str,
    kind: str,
    number: LocatorNumber,
) -> str:
    root = ET.fromstring(xml_text)
    tag = "Article" if kind == "article" else "AppdxTable"
    values: list[str] = []
    target_parts = _locator_number_parts(number)
    for element in root.iter(tag):
        raw_number = str(element.attrib.get("Num") or "").strip()
        parsed_parts = _xml_locator_parts(raw_number)
        if parsed_parts is None and kind == "appendix_table":
            title = _normalize_text(
                element.findtext("AppdxTableTitle") or ""
            )
            title_match = _APPENDIX_RE.search(title)
            if title_match:
                parsed = _japanese_number(title_match.group(1))
                parsed_parts = (parsed,) if parsed is not None else None
        if parsed_parts != target_parts:
            continue
        text = _element_text(element)
        if text:
            values.append(text)
    if not values:
        raise PrimaryLawEvidenceError(
            f"e-Gov法令XMLに{kind}:{_locator_num(number)}がありません。"
        )
    return "\n".join(dict.fromkeys(values))


def _flatten_references(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if not isinstance(value, list):
        return []
    result: list[Mapping[str, Any]] = []
    for item in value:
        result.extend(_flatten_references(item))
    return result


class PrimaryLawEvidenceResolver:
    """Resolve declared law references through deterministic e-Gov API v2 reads."""

    def __init__(
        self,
        repo_root: Path,
        *,
        fetcher: Callable[[str, str], LawFileSnapshot] | None = None,
        timeout_seconds: float = 30.0,
        retry_count: int = 3,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.cache_root = (
            self.repo_root
            / "output"
            / "question_review_console"
            / "cache"
            / "primary_law_evidence"
        )
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self._fetcher = fetcher or self._fetch_official
        self._registry_lock = threading.Lock()
        self._key_locks: dict[tuple[str, str], threading.Lock] = {}
        self._exam_dates = self._load_exam_dates()

    def _load_exam_dates(self) -> dict[tuple[str, str], tuple[str, str]]:
        result: dict[tuple[str, str], tuple[str, str]] = {}
        source_root = self.repo_root / "document" / "sources"
        for path in sorted(source_root.glob("*/official_exam_pdf_catalog.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PrimaryLawEvidenceError(
                    f"公式試験日catalogを読めません: {path}"
                ) from exc
            qualification_ids = payload.get("qualificationIds")
            exam_dates = payload.get("examDates")
            if not isinstance(qualification_ids, list) or not isinstance(
                exam_dates,
                Mapping,
            ):
                continue
            for qualification in qualification_ids:
                qualification_id = str(qualification or "").strip()
                if not qualification_id:
                    continue
                for list_group_id, raw_exam_date in exam_dates.items():
                    exam_date = str(raw_exam_date or "").strip()
                    try:
                        date.fromisoformat(exam_date)
                    except ValueError as exc:
                        raise PrimaryLawEvidenceError(
                            f"公式試験日catalogの値が不正です: "
                            f"{path} / {list_group_id} / {exam_date}"
                        ) from exc
                    key = (qualification_id, str(list_group_id))
                    previous = result.get(key)
                    source = path.relative_to(self.repo_root).as_posix()
                    if previous is not None and previous[0] != exam_date:
                        raise PrimaryLawEvidenceError(
                            "公式試験日catalogが競合しています: "
                            f"{qualification_id} / {list_group_id} / "
                            f"{previous[0]} / {exam_date}"
                        )
                    result[key] = (exam_date, source)
        return result

    @staticmethod
    def _record_exam_date(record: Mapping[str, Any]) -> str:
        value = record.get("examDate")
        if isinstance(value, Mapping):
            timestamp = value.get("_seconds")
            if timestamp is None:
                timestamp = value.get("seconds")
            if timestamp is not None:
                try:
                    return datetime.fromtimestamp(
                        float(timestamp),
                        tz=timezone.utc,
                    ).date().isoformat()
                except (OSError, OverflowError, TypeError, ValueError):
                    return ""
            value = value.get("date") or value.get("isoDate")
        candidate = str(value or "").strip()[:10]
        if not candidate:
            return ""
        try:
            date.fromisoformat(candidate)
        except ValueError:
            return ""
        return candidate

    def _default_exam_date(
        self,
        record: Mapping[str, Any],
        *,
        qualification: str,
        list_group_id: str,
    ) -> tuple[str, str]:
        record_exam_date = self._record_exam_date(record)
        if record_exam_date:
            return record_exam_date, "record.examDate"
        return self._exam_dates.get(
            (str(qualification or ""), str(list_group_id or "")),
            ("", ""),
        )

    def _key_lock(self, law_id: str, as_of: str) -> threading.Lock:
        key = (law_id, as_of)
        with self._registry_lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def _cache_path(self, law_id: str, as_of: str) -> Path:
        safe_law_id = re.sub(r"[^0-9A-Za-z_-]+", "_", law_id)
        return self.cache_root / safe_law_id / f"{as_of}.json"

    def _fetch_official(self, law_id: str, as_of: str) -> LawFileSnapshot:
        encoded_id = urllib.parse.quote(law_id, safe="")
        encoded_date = urllib.parse.quote(as_of, safe="")
        url = f"{_LAW_FILE_ENDPOINT}/{encoded_id}?asof={encoded_date}"
        last_error = ""
        for attempt in range(self.retry_count):
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "exam-scraper-question-maintenance/1"},
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    body = response.read().decode("utf-8")
                    disposition = str(
                        response.headers.get("Content-Disposition") or ""
                    )
                ET.fromstring(body)
                filename_match = _REVISION_FILENAME_RE.search(disposition)
                revision_id = (
                    Path(filename_match.group(1)).stem
                    if filename_match
                    else f"{law_id}@{as_of}"
                )
                return LawFileSnapshot(
                    law_id=law_id,
                    as_of=as_of,
                    source_url=url,
                    revision_id=revision_id,
                    xml_text=body,
                )
            except (
                OSError,
                UnicodeDecodeError,
                urllib.error.URLError,
                ET.ParseError,
            ) as exc:
                last_error = str(exc)
                if attempt + 1 < self.retry_count:
                    time.sleep(0.5 * (attempt + 1))
        raise PrimaryLawEvidenceError(
            f"e-Gov法令API v2から取得できません: {law_id} / {as_of} / {last_error}"
        )

    def _read_cache(self, path: Path) -> LawFileSnapshot | None:
        if not path.is_file() or path.is_symlink():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            xml_text = str(payload["xmlText"])
            if str(payload.get("xmlHash") or "") != _sha256(xml_text):
                return None
            ET.fromstring(xml_text)
            return LawFileSnapshot(
                law_id=str(payload["lawId"]),
                as_of=str(payload["asOf"]),
                source_url=str(payload["sourceUrl"]),
                revision_id=str(payload["revisionId"]),
                xml_text=xml_text,
            )
        except (OSError, KeyError, ValueError, ET.ParseError, json.JSONDecodeError):
            return None

    def law_file(self, law_id: str, as_of: str) -> LawFileSnapshot:
        law_id = str(law_id or "").strip()
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise PrimaryLawEvidenceError(
                f"法令根拠の基準日が不正です: {as_of}"
            ) from exc
        if not law_id:
            raise PrimaryLawEvidenceError("法令根拠にlawIdがありません。")
        path = self._cache_path(law_id, as_of)
        with self._key_lock(law_id, as_of):
            cached = self._read_cache(path)
            if cached is not None:
                return cached
            snapshot = self._fetcher(law_id, as_of)
            atomic_write(
                path,
                json.dumps(
                    {
                        "schemaVersion": "primary-law-file-cache/v1",
                        "lawId": snapshot.law_id,
                        "asOf": snapshot.as_of,
                        "sourceUrl": snapshot.source_url,
                        "revisionId": snapshot.revision_id,
                        "xmlHash": _sha256(snapshot.xml_text),
                        "xmlText": snapshot.xml_text,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n",
            )
            reread = self._read_cache(path)
            if reread is None:
                raise PrimaryLawEvidenceError(
                    f"法令根拠cacheを再読検証できません: {path}"
                )
            return reread

    @staticmethod
    def _snapshot_payload(
        snapshot: LawFileSnapshot,
        *,
        kind: str,
        number: LocatorNumber,
    ) -> dict[str, Any]:
        text = extract_locator_text(snapshot.xml_text, kind, number)
        return {
            "asOf": snapshot.as_of,
            "revisionId": snapshot.revision_id,
            "sourceUrl": snapshot.source_url,
            "locator": _locator_payload(kind, number),
            "textHash": _sha256(text),
            "text": text[:12_000],
            "textTruncated": len(text) > 12_000,
        }

    def resolve(
        self,
        record: Mapping[str, Any],
        *,
        current_as_of: str,
        qualification: str = "",
        list_group_id: str = "",
    ) -> dict[str, Any]:
        default_exam_as_of, default_exam_source = self._default_exam_date(
            record,
            qualification=qualification,
            list_group_id=list_group_id,
        )
        references = _flatten_references(record.get("lawReferences"))
        declared = [
            reference
            for reference in references
            if str(reference.get("lawId") or "").strip()
            and locator_parts(reference.get("article"))
        ]
        if not declared:
            return {
                "schemaVersion": PRIMARY_LAW_EVIDENCE_SCHEMA,
                "status": "not_applicable",
                "currentAsOf": current_as_of,
                "examAsOf": default_exam_as_of or None,
                "examAsOfSource": default_exam_source or None,
                "items": [],
            }

        items: list[dict[str, Any]] = []
        errors: list[str] = []
        seen: set[tuple[Any, ...]] = set()
        for reference in declared:
            law_id = str(reference.get("lawId") or "").strip()
            role = str(reference.get("role") or "current_basis").strip()
            explicit_exam_as_of = (
                str(reference.get("referenceDate") or "").strip()[:10]
                if role == "exam_time_basis"
                else ""
            )
            exam_as_of = explicit_exam_as_of or default_exam_as_of
            exam_as_of_source = (
                "lawReference.referenceDate"
                if explicit_exam_as_of
                else default_exam_source
            )
            for kind, number in locator_parts(reference.get("article")):
                key = (
                    law_id,
                    role,
                    exam_as_of,
                    kind,
                    number,
                    reference.get("choiceIndex"),
                )
                if key in seen:
                    continue
                seen.add(key)
                item: dict[str, Any] = {
                    "lawId": law_id,
                    "lawTitle": str(reference.get("lawTitle") or ""),
                    "role": role,
                    "scope": str(reference.get("scope") or ""),
                    "choiceIndex": reference.get("choiceIndex"),
                    "declaredArticle": str(reference.get("article") or ""),
                    "locator": _locator_payload(kind, number),
                }
                try:
                    current = self._snapshot_payload(
                        self.law_file(law_id, current_as_of),
                        kind=kind,
                        number=number,
                    )
                    item["currentSnapshot"] = current
                    if exam_as_of:
                        exam = self._snapshot_payload(
                            self.law_file(law_id, exam_as_of),
                            kind=kind,
                            number=number,
                        )
                        item["examSnapshot"] = exam
                        item["examAsOfSource"] = exam_as_of_source
                        item["comparison"] = (
                            "unchanged"
                            if exam["textHash"] == current["textHash"]
                            else "changed"
                        )
                    else:
                        item["comparison"] = "current_only"
                    item["status"] = "complete"
                except (OSError, ValueError, PrimaryLawEvidenceError) as exc:
                    item["status"] = "partial"
                    item["error"] = str(exc)
                    errors.append(str(exc))
                items.append(item)

        return {
            "schemaVersion": PRIMARY_LAW_EVIDENCE_SCHEMA,
            "status": "complete" if not errors else "partial",
            "currentAsOf": current_as_of,
            "examAsOf": default_exam_as_of or None,
            "examAsOfSource": default_exam_source or None,
            "items": items,
            "errors": list(dict.fromkeys(errors)),
        }
