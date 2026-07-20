from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.common.question_identity import (
    SourceIdentityBinding,
    load_source_record_inventory,
    source_json_paths,
)
from scripts.scrape.qualification_presets import load_qualification_catalog
from scripts.merge.question_issue_corrections import (
    selected_question_issue_correction_paths,
)
from scripts.merge.record_projection import ensure_projection_indexes_valid
from tools.question_review_console.projection import (
    IdentityCandidateIndex,
    IdentityResolutionError,
    PROJECTED_COMPARE_FIELDS,
    STAGE_SPECS,
    ProjectionResult,
    SourceRecordIdentity,
    api_question_id,
    build_identity_candidate_index,
    build_question_issue_index,
    build_stage_map,
    build_stage_maps,
    explanation_prefix_matches,
    extract_records,
    find_patch_entry,
    load_json,
    normalize_text,
    normalize_verdict,
    project_record,
    record_diff,
    review_key,
    selected_patch_paths,
    sha256_json,
)
from tools.question_review_console.patch_validation import (
    law_audit_quality_warnings,
    patch_entry_required_warnings,
    projected_required_warnings,
    upload_document_required_warnings,
)
from scripts.common.explanation_contract import public_explanation_text


SOURCE_SUBDIR = "00_source"
WATCH_SUBDIRS = (
    "00_source",
    "10_questionType_fixed",
    "15_correctChoiceText_fixed",
    "18_law_context_prepared",
    "21_explanationText_added",
    "22_questionSetId_linked",
    "23_correctChoiceText_fixed",
    "24_questionIssueCorrections",
    "30_merged_2",
    "40_convert",
)
FIRESTORE_COMPARE_FIELDS = (
    "correctChoiceText",
    "explanationText",
    "suggestedQuestionDetailsByChoice",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
    "questionType",
    "questionSetId",
    "originalQuestionId",
    "originalQuestionBodyText",
    "originalQuestionChoiceText",
)
ISSUE_PRIORITY = {
    "identity_mismatch": 0,
    "answer_explanation_mismatch": 1,
    "required_field_missing": 2,
    "law_audit_verdict_mismatch": 3,
    "law_audit_metadata_incomplete": 4,
    "law_hold": 5,
    "merge_stale": 6,
    "convert_stale": 7,
    "upload_stale": 8,
    "upload_missing": 9,
    "law_basis_missing": 10,
    "explanation_missing": 11,
    "projection_error": 12,
}


@dataclass
class GroupCache:
    fingerprint: str
    payload: dict[str, Any]


@dataclass
class ProjectionCache:
    fingerprint: str
    payload: Any


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid path segment: {value}")
    return value


def _latest_json(paths: Iterable[Path]) -> Path | None:
    values = [path for path in paths if path.is_file() and not path.name.endswith("_invalid.json")]
    if not values:
        return None
    return max(values, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _is_dataless(path: Path) -> bool:
    flags = int(getattr(path.stat(), "st_flags", 0))
    return bool(flags & int(getattr(stat, "SF_DATALESS", 0)))


def _current_json_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob("*.json")
        if path.is_file() and not path.name.endswith("_invalid.json")
    )


def _records_with_paths(paths: Iterable[Path]) -> list[tuple[dict[str, Any], Path]]:
    result: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(paths, key=lambda value: (value.stat().st_mtime_ns, value.name)):
        if _is_dataless(path):
            continue
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        result.extend((record, path) for record in extract_records(payload))
    return result


def _merged_source_stem(path: Path) -> str:
    match = re.fullmatch(r"(.+)_merged(?:_\d{8}_\d{4})?", path.stem)
    return match.group(1) if match else path.stem


def _record_index(
    paths: Iterable[Path],
    sources: Iterable[SourceRecordIdentity],
) -> IdentityCandidateIndex:
    return build_identity_candidate_index(
        _records_with_paths(paths),
        sources=sources,
        record_of=lambda candidate: candidate[0],
        source_stem_of=lambda candidate: _merged_source_stem(candidate[1]),
        label="merged record",
    )


def _find_record(
    index: IdentityCandidateIndex,
    source_binding: SourceIdentityBinding,
) -> tuple[dict[str, Any], Path] | None:
    errors = index.errors_by_binding.get(source_binding, ())
    if errors:
        raise IdentityResolutionError(" ".join(errors))
    candidates = index.by_binding.get(source_binding, ())
    return candidates[-1] if candidates else None


def _optional_patch_entry(
    mapping: Mapping[str, Any],
    aliases: set[str],
    source_binding: SourceIdentityBinding,
):
    try:
        return find_patch_entry(mapping, aliases, source_binding)
    except IdentityResolutionError:
        return None


def _artifact_resolution_blockers(
    stage: str,
    index: IdentityCandidateIndex,
    *,
    patch_dir: str,
    fallback_path: Path | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    paths: dict[str, int] = {}
    for candidate in index.unmatched_candidates:
        path = getattr(candidate, "path", None)
        if path is None and isinstance(candidate, tuple) and len(candidate) > 1:
            path = candidate[1]
        path = path or fallback_path
        if path is None:
            display = "(artifact path unavailable)"
        else:
            resolved = Path(path).resolve()
            display = str(
                resolved.relative_to(repo_root)
                if repo_root is not None and resolved.is_relative_to(repo_root)
                else path
            )
        paths[display] = paths.get(display, 0) + 1
    blockers = [
        {
            "code": "artifact_identity_unmatched",
            "stage": stage,
            "patchDir": patch_dir,
            "path": path,
            "count": count,
            "message": (
                f"{stage}のartifact {count}件をsource recordへ対応できません: "
                f"{path}"
            ),
        }
        for path, count in sorted(paths.items())
    ]
    conflicts: dict[str, int] = {}
    for messages in index.errors_by_binding.values():
        for message in messages:
            conflicts[message] = conflicts.get(message, 0) + 1
    blockers.extend(
        {
            "code": "artifact_identity_conflict",
            "stage": stage,
            "patchDir": patch_dir,
            "path": str(fallback_path or "(競合artifactはmessageを参照)"),
            "count": count,
            "message": f"{stage}のartifact識別が競合しています: {message}",
        }
        for message, count in sorted(conflicts.items())
    )
    return blockers


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _ordered_choice_docs(
    docs: list[dict[str, Any]], choices: list[Any]
) -> list[dict[str, Any]]:
    by_choice: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        key = normalize_text(doc.get("originalQuestionChoiceText"))
        by_choice.setdefault(key, []).append(doc)
    ordered: list[dict[str, Any]] = []
    for choice in choices:
        matches = by_choice.get(normalize_text(choice), [])
        if len(matches) != 1:
            return sorted(docs, key=lambda doc: str(doc.get("questionId") or ""))
        ordered.append(matches[0])
    return ordered


def _aligned_choice_values(value: Any, choices: list[Any]) -> list[Any] | None:
    if not isinstance(value, list) or len(value) != len(choices):
        return None
    return value


def correct_choice_comparison(
    source: Mapping[str, Any],
    projected: Mapping[str, Any],
) -> dict[str, Any]:
    source_raw = source.get("correctChoiceText")
    current_raw = projected.get("correctChoiceText")
    source_values = list(source_raw) if isinstance(source_raw, list) else []
    current_values = list(current_raw) if isinstance(current_raw, list) else []
    comparable = (
        isinstance(source_raw, list)
        and isinstance(current_raw, list)
        and bool(source_values or current_values)
    )
    source_normalized = [normalize_verdict(value) for value in source_values]
    current_normalized = [normalize_verdict(value) for value in current_values]
    changed_indexes = [
        index
        for index in range(max(len(source_normalized), len(current_normalized)))
        if (
            source_normalized[index] if index < len(source_normalized) else None
        ) != (
            current_normalized[index] if index < len(current_normalized) else None
        )
    ]
    return {
        "comparable": comparable,
        "different": comparable and bool(changed_indexes),
        "source": source_values,
        "current": current_values,
        "changedChoiceIndexes": changed_indexes if comparable else [],
    }


def _contains_hold(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key in {"auditStatus", "reviewState"} and str(item) in {"hold", "needs_secondary_review"})
            or _contains_hold(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_hold(item) for item in value)
    return False


def _has_verified_law_basis(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("verificationStatus") == "verified" and (
            value.get("lawId") or value.get("externalPrimarySource")
        ):
            return True
        return any(_has_verified_law_basis(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_verified_law_basis(item) for item in value)
    return False


def detect_issues(
    projected: Mapping[str, Any],
    merged: Mapping[str, Any] | None,
    converted_docs: list[dict[str, Any]],
    upload_docs: list[dict[str, Any]],
    projection_errors: Iterable[str],
    external_required_warnings: Iterable[Mapping[str, Any]] = (),
    quality_warnings: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    issues: dict[str, dict[str, Any]] = {}

    def add(code: str, detail: str, fields: Iterable[str] = ()) -> None:
        if code not in issues:
            issues[code] = {
                "code": code,
                "detail": detail,
                "fields": sorted(set(fields)),
                "priority": ISSUE_PRIORITY.get(code, 99),
            }
            return
        existing = issues[code]
        existing["fields"] = sorted(set(existing.get("fields") or []) | set(fields))
        details = [value.strip() for value in str(existing.get("detail") or "").split(" / ")]
        if detail not in details:
            existing["detail"] = " / ".join([*details, detail])

    choices = projected.get("choiceTextList")
    choices = choices if isinstance(choices, list) else []
    correctness = projected.get("correctChoiceText")
    explanations = projected.get("explanationText")

    for warning in projected_required_warnings(projected):
        add(
            "required_field_missing",
            warning["detail"],
            [warning["field"]],
        )
    for warning in external_required_warnings:
        add(
            "required_field_missing",
            str(warning.get("detail") or "patchの必須fieldが不足しています。"),
            [str(warning.get("field") or "patch")],
        )
    for warning in quality_warnings:
        add(
            str(warning.get("code") or "law_audit_metadata_incomplete"),
            str(warning.get("detail") or "法令監査メタデータの確認が必要です。"),
            [str(warning.get("field") or "lawRevisionFacts")],
        )

    if isinstance(correctness, list) and isinstance(explanations, list):
        mismatch_indexes = [
            index
            for index, (verdict, explanation) in enumerate(zip(correctness, explanations))
            if not explanation_prefix_matches(verdict, explanation)
        ]
        if mismatch_indexes:
            add(
                "answer_explanation_mismatch",
                "正誤と解説先頭が一致しません。",
                [f"choice:{index}" for index in mismatch_indexes],
            )

    merge_fields = record_diff(projected, merged, PROJECTED_COMPARE_FIELDS)
    if (
        merged is not None
        and "correctChoiceText" in merge_fields
        and isinstance(projected.get("correctChoiceText"), list)
        and isinstance(merged.get("correctChoiceText"), list)
        and [normalize_verdict(value) for value in projected["correctChoiceText"]]
        == [normalize_verdict(value) for value in merged["correctChoiceText"]]
    ):
        merge_fields.remove("correctChoiceText")
    if merged is None:
        add("merge_stale", "30_merged_2に対応する問題がありません。")
    elif merge_fields:
        add("merge_stale", "最新patchがmergeへ反映されていません。", merge_fields)

    if choices:
        if len(converted_docs) != len(choices):
            add("identity_mismatch", "選択肢とFirestore変換documentの件数が一致しません。")
        else:
            ordered = _ordered_choice_docs(converted_docs, choices)
            aligned_correctness = _aligned_choice_values(correctness, choices)
            stale_fields: list[str] = []
            for index, doc in enumerate(ordered):
                if (
                    aligned_correctness is not None
                    and normalize_verdict(doc.get("correctChoiceText"))
                    != normalize_verdict(aligned_correctness[index])
                ):
                    stale_fields.append(f"correctChoiceText[{index}]")
                expected_explanation = public_explanation_text(
                    explanations,
                    question_type=projected.get("questionType"),
                    choice_index=index,
                    is_choice_only=doc.get("isChoiceOnly") is True,
                )
                if expected_explanation is None:
                    if "explanationText" in doc:
                        stale_fields.append(f"explanationText[{index}]")
                elif doc.get("explanationText") != expected_explanation:
                    stale_fields.append(f"explanationText[{index}]")
            if stale_fields:
                add("convert_stale", "patch合成後と40_convertが一致しません。", stale_fields)

    if converted_docs and not upload_docs:
        add("upload_missing", "upload-readyに対応する問題がありません。")
    elif upload_docs and converted_docs:
        by_id = {str(doc.get("questionId") or ""): doc for doc in upload_docs}
        stale = []
        for converted in converted_docs:
            qid = str(converted.get("questionId") or "")
            upload = by_id.get(qid)
            if upload is None:
                stale.append(qid)
                continue
            if any(converted.get(field) != upload.get(field) for field in FIRESTORE_COMPARE_FIELDS):
                stale.append(qid)
        if stale:
            add("upload_stale", "40_convertとupload-readyが一致しません。", stale)

    if projected.get("isLawRelated") is True:
        if _contains_hold(projected.get("lawRevisionFacts")):
            add("law_hold", "法令監査がholdです。", ["lawRevisionFacts"])
        if not _has_verified_law_basis(projected.get("lawReferences")):
            add("law_basis_missing", "検証済みの法令根拠がありません。", ["lawReferences"])

    for error in projection_errors:
        add("projection_error", error)

    return sorted(issues.values(), key=lambda issue: (issue["priority"], issue["code"]))


class QuestionInventory:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.output_root = self.repo_root / "output"
        self.qualification_catalog = load_qualification_catalog(
            self.repo_root / "config" / "scrape_presets.json"
        )
        self._cache: dict[tuple[str, str], GroupCache] = {}
        self._source_cache: dict[tuple[str, str], ProjectionCache] = {}
        self._stage_index_cache: dict[tuple[str, str, str], ProjectionCache] = {}
        self._issue_index_cache: dict[tuple[str, str], ProjectionCache] = {}
        self._id_map: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def inventory(self) -> dict[str, Any]:
        qualifications = []
        if not self.output_root.is_dir():
            return {"qualifications": [], "defaultQualification": None}
        for qualification_dir in sorted(self.output_root.iterdir()):
            questions_dir = qualification_dir / "questions_json"
            if not questions_dir.is_dir():
                continue
            groups = [
                path.name
                for path in sorted(questions_dir.iterdir())
                if path.is_dir() and (path / SOURCE_SUBDIR).is_dir()
            ]
            if not groups:
                continue
            metadata = self.qualification_catalog.get(qualification_dir.name, {})
            qualifications.append(
                {
                    "id": qualification_dir.name,
                    "displayName": metadata.get("displayName", qualification_dir.name),
                    "publicationId": metadata.get("publicationId", qualification_dir.name),
                    "listGroupIds": groups,
                    "listGroupCount": len(groups),
                }
            )
        qualifications.sort(key=lambda item: (item["displayName"], item["id"]))
        ids = {item["id"] for item in qualifications}
        default = "gas-shunin-otsu" if "gas-shunin-otsu" in ids else (
            qualifications[0]["id"] if qualifications else None
        )
        return {"qualifications": qualifications, "defaultQualification": default}

    def group(self, qualification: str, list_group_id: str) -> dict[str, Any]:
        qualification = _safe_segment(qualification)
        list_group_id = _safe_segment(list_group_id)
        group_dir = self.output_root / qualification / "questions_json" / list_group_id
        if not (group_dir / SOURCE_SUBDIR).is_dir():
            raise FileNotFoundError(f"question group not found: {qualification}/{list_group_id}")
        fingerprint = self._group_fingerprint(group_dir)
        cache_key = (qualification, list_group_id)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached.fingerprint == fingerprint:
                return cached.payload
            payload = self._build_group(qualification, list_group_id, group_dir, fingerprint)
            if cached:
                for question in cached.payload["questions"]:
                    if self._id_map.get(question["id"]) is question:
                        self._id_map.pop(question["id"], None)
            self._cache[cache_key] = GroupCache(fingerprint=fingerprint, payload=payload)
            for question in payload["questions"]:
                self._id_map[question["id"]] = question
            return payload

    def question(self, question_id: str) -> dict[str, Any]:
        with self._lock:
            question = self._id_map.get(question_id)
        if question is None:
            raise KeyError(f"question not loaded: {question_id}")
        return question

    def projected_input(
        self,
        qualification: str,
        list_group_id: str,
        source_record_ref_value: str,
    ) -> ProjectionResult:
        """Project one immutable source record through the current patch layers."""

        qualification = _safe_segment(qualification)
        list_group_id = _safe_segment(list_group_id)
        group_dir = self.output_root / qualification / "questions_json" / list_group_id
        inventory = self._source_inventory(
            qualification,
            list_group_id,
            group_dir,
        )
        matches = [
            entry
            for entry in inventory
            if entry.identity.binding.source_record_ref == source_record_ref_value
        ]
        if len(matches) != 1:
            raise ValueError(
                "sourceRecordRefを一意に解決できません: "
                f"{qualification}/{list_group_id}/{source_record_ref_value}"
            )
        entry = matches[0]
        identities = tuple(value.identity for value in inventory)
        stage_maps = self._projection_stage_maps(
            qualification,
            list_group_id,
            group_dir,
            identities,
        )
        issue_index = self._projection_issue_index(
            qualification,
            list_group_id,
            group_dir,
            identities,
        )
        ensure_projection_indexes_valid(
            tuple(
                (f"{stage} patch", stage_maps[stage])
                for stage, _subdir, _tag in STAGE_SPECS
            )
            + (("question issue correction", issue_index),)
        )
        return project_record(
            entry.record,
            set(entry.identity.aliases),
            stage_maps,
            issue_index,
            source_binding=entry.identity.binding,
        )

    @staticmethod
    def _paths_fingerprint(paths: Iterable[Path]) -> str:
        parts: list[str] = []
        for path in sorted(paths):
            stat_result = path.stat()
            parts.append(f"{path}:{stat_result.st_size}:{stat_result.st_mtime_ns}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _identity_fingerprint(
        identities: Iterable[SourceRecordIdentity],
    ) -> str:
        return sha256_json(
            [
                {
                    **identity.binding.as_mapping(),
                    "aliases": sorted(identity.aliases),
                    "sourceStem": identity.source_stem,
                }
                for identity in identities
            ]
        )

    def _source_inventory(
        self,
        qualification: str,
        list_group_id: str,
        group_dir: Path,
    ) -> tuple[Any, ...]:
        key = (qualification, list_group_id)
        source_dir = group_dir / SOURCE_SUBDIR
        fingerprint = self._paths_fingerprint(source_json_paths(source_dir))
        with self._lock:
            cached = self._source_cache.get(key)
            if cached and cached.fingerprint == fingerprint:
                return cached.payload
            payload = load_source_record_inventory(
                source_dir,
                qualification=qualification,
                list_group_id=list_group_id,
            )
            self._source_cache[key] = ProjectionCache(fingerprint, payload)
            return payload

    def _projection_stage_maps(
        self,
        qualification: str,
        list_group_id: str,
        group_dir: Path,
        identities: tuple[SourceRecordIdentity, ...],
    ) -> dict[str, IdentityCandidateIndex]:
        maps: dict[str, IdentityCandidateIndex] = {}
        identity_fingerprint = self._identity_fingerprint(identities)
        with self._lock:
            for stage, subdir, tag in STAGE_SPECS:
                key = (qualification, list_group_id, stage)
                paths = selected_patch_paths(group_dir, subdir, tag)
                fingerprint = sha256_json(
                    [self._paths_fingerprint(paths), identity_fingerprint]
                )
                cached = self._stage_index_cache.get(key)
                if not cached or cached.fingerprint != fingerprint:
                    cached = ProjectionCache(
                        fingerprint,
                        build_stage_map(
                            group_dir,
                            identities,
                            stage=stage,
                            subdir=subdir,
                            tag=tag,
                        ),
                    )
                    self._stage_index_cache[key] = cached
                maps[stage] = cached.payload
        return maps

    def _projection_issue_index(
        self,
        qualification: str,
        list_group_id: str,
        group_dir: Path,
        identities: tuple[SourceRecordIdentity, ...],
    ) -> IdentityCandidateIndex:
        key = (qualification, list_group_id)
        paths = selected_question_issue_correction_paths(
            group_dir / "24_questionIssueCorrections"
        )
        identity_fingerprint = self._identity_fingerprint(identities)
        fingerprint = sha256_json(
            [self._paths_fingerprint(paths), identity_fingerprint]
        )
        with self._lock:
            cached = self._issue_index_cache.get(key)
            if not cached or cached.fingerprint != fingerprint:
                cached = ProjectionCache(
                    fingerprint,
                    build_question_issue_index(paths, identities),
                )
                self._issue_index_cache[key] = cached
            return cached.payload

    def invalidate(self, qualification: str, list_group_id: str) -> None:
        with self._lock:
            cache_key = (qualification, list_group_id)
            cached = self._cache.pop(cache_key, None)
            if cached:
                for question in cached.payload["questions"]:
                    if self._id_map.get(question["id"]) is question:
                        self._id_map.pop(question["id"], None)
            self._source_cache.pop(cache_key, None)
            self._issue_index_cache.pop(cache_key, None)
            stale_stage_keys = [
                key for key in self._stage_index_cache if key[:2] == cache_key
            ]
            for key in stale_stage_keys:
                self._stage_index_cache.pop(key, None)

    def _group_fingerprint(self, group_dir: Path) -> str:
        parts = []
        for subdir in WATCH_SUBDIRS:
            directory = group_dir / subdir
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                stat = path.stat()
                parts.append(f"{path.relative_to(group_dir)}:{stat.st_size}:{stat.st_mtime_ns}")
        external_paths = list(
            (group_dir.parent / "upload_to_firestore").glob(
                f"{group_dir.name}_firestore_*.json"
            )
        )
        external_paths.extend(
            group_dir.parent.glob(
                f"upload_ready*/{group_dir.name}*_firestore_*.json"
            )
        )
        for path in sorted(external_paths):
            stat = path.stat()
            parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _build_group(
        self,
        qualification: str,
        list_group_id: str,
        group_dir: Path,
        fingerprint: str,
    ) -> dict[str, Any]:
        publication_qualification_id = self.qualification_catalog.get(
            qualification, {}
        ).get("publicationId", qualification)
        source_inventory = self._source_inventory(
            qualification,
            list_group_id,
            group_dir,
        )
        source_files = sorted({entry.path for entry in source_inventory})
        source_records: list[
            tuple[
                Path,
                int,
                dict[str, Any],
                SourceIdentityBinding,
                set[str],
            ]
        ] = []
        source_identities: list[SourceRecordIdentity] = []
        for entry in source_inventory:
            source = dict(entry.record)
            source_binding = entry.identity.binding
            aliases = set(entry.identity.aliases)
            source_records.append(
                (
                    entry.path,
                    entry.record_index,
                    source,
                    source_binding,
                    aliases,
                )
            )
            source_identities.append(entry.identity)

        stage_maps = build_stage_maps(group_dir, source_identities)
        issue_paths = selected_question_issue_correction_paths(
            group_dir / "24_questionIssueCorrections"
        )
        issue_index = build_question_issue_index(issue_paths, source_identities)
        merged_index = _record_index(
            _current_json_files(group_dir / "30_merged_2"),
            source_identities,
        )
        converted_docs, converted_path = self._converted_docs(group_dir)
        upload_docs, upload_path = self._upload_docs(
            qualification,
            publication_qualification_id,
            list_group_id,
            group_dir,
        )
        converted_index = build_identity_candidate_index(
            [
                doc
                for doc in converted_docs
                if doc.get("qualificationId") == publication_qualification_id
            ],
            sources=source_identities,
            record_of=lambda doc: doc,
            source_stem_of=lambda _doc: "",
            label="converted document",
        )
        upload_index = build_identity_candidate_index(
            [
                doc
                for doc in upload_docs
                if doc.get("qualificationId") == publication_qualification_id
            ],
            sources=source_identities,
            record_of=lambda doc: doc,
            source_stem_of=lambda _doc: "",
            label="upload-ready document",
        )
        artifact_resolution_blockers: list[dict[str, Any]] = []
        for stage, patch_dir, _tag in STAGE_SPECS:
            artifact_resolution_blockers.extend(
                _artifact_resolution_blockers(
                    stage,
                    stage_maps[stage],
                    patch_dir=patch_dir,
                    repo_root=self.repo_root,
                )
            )
        artifact_resolution_blockers.extend(
            _artifact_resolution_blockers(
                "questionIssueCorrection",
                issue_index,
                patch_dir="24_questionIssueCorrections",
                repo_root=self.repo_root,
            )
        )
        artifact_resolution_blockers.extend(
            _artifact_resolution_blockers(
                "merged",
                merged_index,
                patch_dir="30_merged_2",
                repo_root=self.repo_root,
            )
        )
        artifact_resolution_blockers.extend(
            _artifact_resolution_blockers(
                "converted",
                converted_index,
                patch_dir="40_convert",
                fallback_path=converted_path,
                repo_root=self.repo_root,
            )
        )
        artifact_resolution_blockers.extend(
            _artifact_resolution_blockers(
                "uploadReady",
                upload_index,
                patch_dir="upload_to_firestore",
                fallback_path=upload_path,
                repo_root=self.repo_root,
            )
        )

        questions: list[dict[str, Any]] = []
        for (
            source_path,
            source_index,
            source,
            source_binding,
            aliases,
        ) in source_records:
                stable_source_ref = source_binding.source_record_ref
                merged_error = ""
                try:
                    merged_match = _find_record(
                        merged_index,
                        source_binding,
                    )
                except IdentityResolutionError as exc:
                    merged_match = None
                    merged_error = str(exc)
                merged = merged_match[0] if merged_match else None
                merged_path = merged_match[1] if merged_match else None
                projection = project_record(
                    source,
                    aliases,
                    stage_maps,
                    issue_index,
                    source_binding=source_binding,
                    initial_errors=((merged_error,) if merged_error else ()),
                )
                matched_converted = list(
                    converted_index.by_binding.get(source_binding, ())
                )
                matched_upload = list(
                    upload_index.by_binding.get(source_binding, ())
                )
                downstream_errors = [
                    *(
                        f"convert: {error}"
                        for error in converted_index.errors_by_binding.get(
                            source_binding, ()
                        )
                    ),
                    *(
                        f"upload-ready: {error}"
                        for error in upload_index.errors_by_binding.get(
                            source_binding, ()
                        )
                    ),
                ]
                if downstream_errors:
                    projection = ProjectionResult(
                        record=projection.record,
                        applied_files=projection.applied_files,
                        errors=tuple([*projection.errors, *downstream_errors]),
                    )
                required_field_warnings = [
                    {
                        **warning,
                        "code": "required_field_missing",
                        "category": "required",
                        "stage": "projected",
                        "dataPath": warning["field"],
                        "blocksSync": True,
                        "blocksPublish": True,
                    }
                    for warning in projected_required_warnings(projection.record)
                ]
                required_field_warnings.extend(
                    {
                        **warning,
                        "code": "required_field_missing",
                        "category": "required",
                        "stage": f"{stage} patch",
                        "dataPath": warning["field"],
                        "blocksSync": True,
                        "blocksPublish": True,
                    }
                    for stage in ("explanation", "correctChoice")
                    for patch_entry in [
                        _optional_patch_entry(
                            stage_maps.get(stage, {}),
                            aliases,
                            source_binding,
                        )
                    ]
                    if patch_entry is not None
                    for warning in patch_entry_required_warnings(
                        patch_entry.entry,
                        stage,
                        require_question_url=not (
                            source.get("sourceOrigin") == "firestore_snapshot"
                            or source.get("sourceAcquisitionMethod")
                            == "firestore_snapshot"
                        ),
                    )
                )
                upload_required_warnings = [
                    {
                        **warning,
                        "code": "required_field_missing",
                        "category": "required",
                        "blocksSync": True,
                        "blocksPublish": True,
                    }
                    for document in matched_upload
                    for warning in upload_document_required_warnings(document)
                ]
                upload_quality_warnings = [
                    warning
                    for document in matched_upload
                    for warning in law_audit_quality_warnings(document)
                ]
                issues = detect_issues(
                    projection.record,
                    merged,
                    matched_converted,
                    matched_upload,
                    projection.errors,
                    [
                        warning
                        for warning in required_field_warnings
                        if warning.get("stage") != "projected"
                    ],
                    (),
                )
                local_artifacts_current = not {
                    "merge_stale",
                    "convert_stale",
                    "upload_stale",
                    "upload_missing",
                }.intersection(issue["code"] for issue in issues)
                quality_warnings = (
                    upload_quality_warnings if local_artifacts_current else []
                )
                if local_artifacts_current:
                    required_field_warnings.extend(upload_required_warnings)
                    issues = detect_issues(
                        projection.record,
                        merged,
                        matched_converted,
                        matched_upload,
                        projection.errors,
                        [
                            warning
                            for warning in required_field_warnings
                            if warning.get("stage") != "projected"
                        ],
                        quality_warnings,
                    )
                source_stem = source_path.stem
                stable_key = review_key(qualification, list_group_id, source_stem, source)
                question_id = api_question_id(stable_key)
                body = str(
                    projection.record.get("questionBodyText")
                    or projection.record.get("originalQuestionBodyText")
                    or ""
                )
                choices = projection.record.get("choiceTextList")
                choices = choices if isinstance(choices, list) else []
                paths = {
                    "source": str(source_path.relative_to(self.repo_root)),
                    "merged": str(merged_path.relative_to(self.repo_root)) if merged_path else None,
                    "converted": (
                        str(converted_path.relative_to(self.repo_root)) if converted_path else None
                    ),
                    "uploadReady": (
                        str(upload_path.relative_to(self.repo_root)) if upload_path else None
                    ),
                    "patches": [
                        str(Path(path).resolve().relative_to(self.repo_root))
                        for path in projection.applied_files
                        if Path(path).resolve().is_relative_to(self.repo_root)
                    ],
                }
                state_hash = sha256_json(
                    {field: projection.record.get(field) for field in PROJECTED_COMPARE_FIELDS}
                )
                source_correct_choice_comparison = correct_choice_comparison(
                    source,
                    projection.record,
                )
                questions.append(
                    {
                        "id": question_id,
                        "reviewKey": stable_key,
                        "sourceQuestionKey": source_binding.source_question_key,
                        "sourceRecordRef": stable_source_ref,
                        "qualification": qualification,
                        "publicationQualificationId": publication_qualification_id,
                        "listGroupId": list_group_id,
                        "sourceStem": source_stem,
                        "sourceIndex": source_index,
                        "originalQuestionId": source_binding.review_question_id,
                        "questionLabel": str(projection.record.get("questionLabel") or ""),
                        "examLabel": str(projection.record.get("examLabel") or ""),
                        "body": body,
                        "choiceCount": len(choices),
                        "isLawRelated": projection.record.get("isLawRelated") is True,
                        "source": _json_safe(source),
                        "projected": _json_safe(projection.record),
                        "sourceCorrectChoiceComparison": _json_safe(
                            source_correct_choice_comparison
                        ),
                        "merged": _json_safe(merged),
                        "convertedDocs": _json_safe(
                            _ordered_choice_docs(matched_converted, choices)
                        ),
                        "uploadReadyDocs": _json_safe(
                            _ordered_choice_docs(matched_upload, choices)
                        ),
                        "paths": paths,
                        "requiredFieldWarnings": _json_safe(required_field_warnings),
                        "qualityWarnings": _json_safe(quality_warnings),
                        "validationFindings": _json_safe(
                            [*required_field_warnings, *quality_warnings]
                        ),
                        "issues": issues,
                        "issueCodes": [issue["code"] for issue in issues],
                        "stateHash": state_hash,
                        "workflow": {
                            "source": "match",
                            "patch": "match",
                            "merge": (
                                "missing"
                                if merged is None
                                else "stale"
                                if "merge_stale" in {issue["code"] for issue in issues}
                                else "match"
                            ),
                            "convert": (
                                "missing"
                                if not matched_converted
                                else "stale"
                                if "convert_stale" in {issue["code"] for issue in issues}
                                else "match"
                            ),
                            "upload": (
                                "missing"
                                if not matched_upload
                                or upload_path is None
                                or upload_path.parent.name != "upload_to_firestore"
                                else "stale"
                                if "upload_stale" in {issue["code"] for issue in issues}
                                else "match"
                            ),
                            "firestore": "unread",
                        },
                    }
                )

        questions_by_review_key: dict[str, list[dict[str, Any]]] = {}
        for question in questions:
            questions_by_review_key.setdefault(question["reviewKey"], []).append(
                question
            )
        for duplicated in questions_by_review_key.values():
            if len(duplicated) <= 1:
                continue
            for question in duplicated:
                disambiguated = (
                    f"{question['reviewKey']}:{question['sourceRecordRef']}"
                )
                question["reviewKey"] = disambiguated
                question["id"] = api_question_id(disambiguated)

        questions.sort(
            key=lambda question: (
                min((issue["priority"] for issue in question["issues"]), default=99),
                question["sourceStem"],
                question["sourceIndex"],
            )
        )
        source_identity_bindings: set[SourceIdentityBinding] = set()
        identity_blockers: list[dict[str, str]] = []
        for question in questions:
            binding = SourceIdentityBinding.from_mapping(question)
            if not binding.is_complete():
                identity_blockers.append(
                    {
                        "code": "source_identity_missing",
                        "message": (
                            "source由来のsourceQuestionKey/reviewQuestionId/"
                            "sourceRecordRefを一意に導出できません。"
                        ),
                    }
                )
                continue
            if binding in source_identity_bindings:
                identity_blockers.append(
                    {
                        "code": "source_identity_binding_duplicate",
                        "message": (
                            "sourceQuestionKey/reviewQuestionId/sourceRecordRefの"
                            "組が重複しているため03bを開始できません: "
                            f"{' / '.join(binding.as_tuple())}"
                        ),
                    }
                )
            else:
                source_identity_bindings.add(binding)
        return {
            "qualification": qualification,
            "publicationQualificationId": publication_qualification_id,
            "listGroupId": list_group_id,
            "fingerprint": fingerprint,
            "questionCount": len(questions),
            "issueQuestionCount": sum(bool(question["issues"]) for question in questions),
            "sourceFileCount": len(source_files),
            "identityBlockers": identity_blockers,
            "artifactResolutionBlockers": artifact_resolution_blockers,
            "questions": questions,
        }

    def _converted_docs(self, group_dir: Path) -> tuple[list[dict[str, Any]], Path | None]:
        path = _latest_json((group_dir / "40_convert").glob("*_firestore_*.json"))
        if path is None:
            return [], None
        if _is_dataless(path):
            return [], path
        payload = load_json(path)
        values = payload.get("questions") if isinstance(payload, dict) else None
        return (
            [dict(value) for value in values if isinstance(value, dict)]
            if isinstance(values, list)
            else [],
            path,
        )

    def _upload_docs(
        self,
        qualification: str,
        publication_qualification_id: str,
        list_group_id: str,
        group_dir: Path,
    ) -> tuple[list[dict[str, Any]], Path | None]:
        questions_dir = group_dir.parent
        direct = _latest_json(
            (questions_dir / "upload_to_firestore").glob(f"{list_group_id}_firestore_*.json")
        )
        candidates = [direct] if direct else []
        if not candidates:
            for directory in (self.output_root / qualification / "questions_json").glob(
                "upload_ready*"
            ):
                candidates.extend(directory.glob(f"{list_group_id}*_firestore_*.json"))
        skipped_path: Path | None = None
        for path in sorted(
            (value for value in candidates if value is not None),
            key=lambda value: (value.stat().st_mtime_ns, value.name),
            reverse=True,
        ):
            if _is_dataless(path):
                skipped_path = skipped_path or path
                continue
            payload = load_json(path)
            values = payload.get("questions") if isinstance(payload, dict) else None
            if not isinstance(values, list):
                continue
            docs = [
                dict(value)
                for value in values
                if isinstance(value, dict)
                and value.get("qualificationId") == publication_qualification_id
            ]
            if docs:
                return docs, path
        return [], skipped_path
