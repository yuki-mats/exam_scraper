from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, TypeVar


SOURCE_IDENTITY_BINDING_FIELDS = (
    "sourceQuestionKey",
    "reviewQuestionId",
    "sourceRecordRef",
)
_SOURCE_QUESTION_KEY_FIELDS = ("sourceQuestionKey", "source_question_key")
_REVIEW_QUESTION_ID_FIELDS = (
    "reviewQuestionId",
    "review_question_id",
    "originalQuestionId",
    "original_question_id",
)
_SOURCE_RECORD_REF_FIELDS = ("sourceRecordRef", "source_record_ref")
SOURCE_PATCH_TAGS = (
    "questionType_fixed",
    "lawContext_prepared",
    "explanationText_added",
    "questionSetId_linked",
    "correctChoiceText_fixed",
)


@dataclass(frozen=True)
class SourceIdentityBinding:
    """Immutable exact join key for one record under ``00_source``."""

    source_question_key: str
    review_question_id: str
    source_record_ref: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceIdentityBinding":
        def first(fields: tuple[str, ...]) -> Any:
            return next((value[field] for field in fields if value.get(field)), "")

        return cls.from_values(
            first(_SOURCE_QUESTION_KEY_FIELDS),
            first(_REVIEW_QUESTION_ID_FIELDS),
            first(_SOURCE_RECORD_REF_FIELDS),
        )

    @classmethod
    def from_values(
        cls,
        source_question_key: Any,
        review_question_id: Any,
        source_record_ref: Any,
    ) -> "SourceIdentityBinding":
        return cls(
            str(source_question_key or "").strip(),
            str(review_question_id or "").strip(),
            str(source_record_ref or "").strip(),
        )

    def as_tuple(self) -> tuple[str, str, str]:
        return (
            self.source_question_key,
            self.review_question_id,
            self.source_record_ref,
        )

    def as_mapping(self) -> dict[str, str]:
        return dict(zip(SOURCE_IDENTITY_BINDING_FIELDS, self.as_tuple()))

    def is_complete(self) -> bool:
        return all(self.as_tuple())


@dataclass(frozen=True)
class SourceRecordIdentity:
    binding: SourceIdentityBinding
    aliases: frozenset[str]
    source_stem: str


@dataclass(frozen=True)
class SourceRecordInventoryEntry:
    """One immutable source record together with its exact identity."""

    identity: SourceRecordIdentity
    record: Mapping[str, Any]
    path: Path
    record_index: int


def source_json_paths(source_dir: Path) -> list[Path]:
    """Select immutable source files with the same rule as physical Merge."""

    from scripts.merge.merge_utils import is_patch_filename_for_tag

    return [
        path
        for path in sorted(source_dir.glob("*.json"))
        if not path.name.endswith("_merged.json")
        and not any(
            is_patch_filename_for_tag(path.name, tag)
            for tag in SOURCE_PATCH_TAGS
        )
    ]


@dataclass(frozen=True)
class IdentityCandidateIndex:
    by_binding: Mapping[SourceIdentityBinding, tuple[Any, ...]]
    errors_by_binding: Mapping[SourceIdentityBinding, tuple[str, ...]]
    unmatched_count: int = 0
    unmatched_candidates: tuple[Any, ...] = ()


T = TypeVar("T")


def resolve_identity_candidates(
    candidates: Iterable[T],
    *,
    sources: Iterable[SourceRecordIdentity],
    record_of: Callable[[T], Mapping[str, Any]],
    aliases_of: Callable[[Mapping[str, Any]], set[str]],
    source_stem_of: Callable[[T], str],
    label: str,
) -> IdentityCandidateIndex:
    """Resolve artifacts to immutable source records without alias overwrite."""

    candidate_records = list(candidates)
    source_records = list(sources)
    source_by_binding = {source.binding: source for source in source_records}
    indexed: dict[SourceIdentityBinding, list[tuple[int, T]]] = {}
    errors: dict[SourceIdentityBinding, list[str]] = {}
    unmatched_count = 0
    unmatched_candidates: list[T] = []

    def resolve_unique_alias(
        entry_aliases: set[str],
        available: list[SourceRecordIdentity],
    ) -> tuple[SourceIdentityBinding | None, set[SourceIdentityBinding], bool]:
        owners: dict[str, set[SourceIdentityBinding]] = {}
        for source in available:
            for alias in source.aliases & entry_aliases:
                owners.setdefault(alias, set()).add(source.binding)
        unique_owners = {
            next(iter(bindings))
            for bindings in owners.values()
            if len(bindings) == 1
        }
        matched = {
            binding for bindings in owners.values() for binding in bindings
        }
        if len(unique_owners) == 1:
            return next(iter(unique_owners)), matched, False
        return None, matched, len(unique_owners) > 1

    def add_error(bindings: Iterable[SourceIdentityBinding], message: str) -> None:
        for binding in bindings:
            errors.setdefault(binding, []).append(message)

    legacy_candidates: list[tuple[int, T]] = []
    for position, candidate in enumerate(candidate_records):
        record = record_of(candidate)
        candidate_binding = SourceIdentityBinding.from_mapping(record)
        if not candidate_binding.is_complete():
            legacy_candidates.append((position, candidate))
            continue
        if candidate_binding in source_by_binding:
            indexed.setdefault(candidate_binding, []).append((position, candidate))
            continue
        aliases = aliases_of(record)
        possible = {
            source.binding
            for source in source_records
            if source.aliases & aliases
        }
        add_error(
            possible,
            f"{label}のexact bindingがsourceと一致しません。",
        )
        if not possible:
            unmatched_count += 1
            unmatched_candidates.append(candidate)

    # Resolve complete bindings over the full candidate set before considering
    # legacy aliases.  Position is retained so layered artifacts keep order.
    for position, candidate in legacy_candidates:
        record = record_of(candidate)
        aliases = aliases_of(record)
        source_stem = source_stem_of(candidate)
        scoped = [
            source
            for source in source_records
            if source_stem
            and source_stem
            in {source.source_stem, f"{source.source_stem}_merged"}
        ]
        scoped_binding: SourceIdentityBinding | None = None
        scoped_matches: set[SourceIdentityBinding] = set()
        if scoped:
            scoped_binding, scoped_matches, conflict = resolve_unique_alias(
                aliases,
                scoped,
            )
            if conflict:
                add_error(
                    scoped_matches,
                    f"{label}のfile内固有aliasが競合しています。",
                )
                continue
            if scoped_binding is not None:
                indexed.setdefault(scoped_binding, []).append((position, candidate))
                continue

        global_binding, global_matches, conflict = resolve_unique_alias(
            aliases,
            source_records,
        )
        if conflict:
            add_error(
                global_matches,
                f"{label}の資格内固有aliasが競合しています。",
            )
        elif global_binding is not None:
            indexed.setdefault(global_binding, []).append((position, candidate))
        else:
            possible = global_matches or scoped_matches
            if possible:
                add_error(
                    possible,
                    f"{label}をsource recordへ一意に対応できません。",
                )
            else:
                unmatched_count += 1
                unmatched_candidates.append(candidate)

    return IdentityCandidateIndex(
        by_binding={
            binding: tuple(
                candidate
                for _position, candidate in sorted(
                    values,
                    key=lambda item: item[0],
                )
            )
            for binding, values in indexed.items()
        },
        errors_by_binding={
            binding: tuple(dict.fromkeys(values))
            for binding, values in errors.items()
        },
        unmatched_count=unmatched_count,
        unmatched_candidates=tuple(unmatched_candidates),
    )


SOURCE_IDENTITY_FIELDS = (
    "original_question_id",
    "public_question_id",
    "originalQuestionId",
    "questionId",
    "question_url",
    "questionUrl",
    "source_question_id",
    "sourceQuestionId",
    "source_public_question_id",
    "sourcePublicQuestionId",
    "sourceQuestionKey",
    "source_question_key",
    "sourceRecordRef",
    "source_record_ref",
    "uploadOriginalQuestionId",
)

# These fields are created by review/workflow artifacts.  Their values may be
# derived from a source identity, but the fields themselves are not source
# evidence.  Callers that persist them must validate the value against
# ``source_identity_aliases`` from the corresponding source record.
WORKFLOW_IDENTITY_FIELDS = (
    "reviewQuestionId",
    "review_question_id",
)

_GAS_GRADE_BY_QUALIFICATION = {
    "gas-shunin-kou": "kou",
    "gas-shunin-otsu": "otsu",
    "gas-shunin-hei": "hei",
}
_GAS_GRADE_ALIASES = {
    "koushu": "kou",
    "kou": "kou",
    "otsushu": "otsu",
    "otsu": "otsu",
    "heishu": "hei",
    "hei": "hei",
}
_GAS_SUBJECT_ALIASES = {
    "hourei": "law",
    "law": "law",
    "kiso": "kiso",
    "seizo": "seizo",
    "kyokyu": "kyokyu",
    "shohi": "shohi",
}
_GAS_SUBJECT_LABELS = {
    "法令": "law",
    "基礎理論": "kiso",
    "製造": "seizo",
    "供給": "kyokyu",
    "消費機器": "shohi",
}
_GAS_SOURCE_ID_PATTERN = re.compile(
    r"gasushunin-(?P<grade>[^-,]+)-(?P<subject>[^-,]+)-"
    r"(?P<year>\d{4})-(?P<question_no>\d+)"
)
_QUESTION_NUMBER_PATTERN = re.compile(r"問\s*(?P<question_no>\d+)")
_GAS_URL_PATTERN = re.compile(
    r"#(?P<subject>law|kiso|seizo|kyokyu|shohi)-q(?P<question_no>\d+)$"
)


def review_question_id(question: Mapping[str, Any]) -> str:
    """Return the stable key used by 01-04 review patch files."""
    preserve_decision = str(question.get("sourceConflictReviewDecision") or "")
    preserve_policy = str(question.get("sourceContentConflictPolicy") or "")
    public_question_id = question.get("public_question_id")
    if (
        public_question_id
        and ("preserve_firestore" in preserve_decision or "preserve_firestore" in preserve_policy)
    ):
        return str(public_question_id)

    firestore_ids = question.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        values = [str(value) for value in firestore_ids if value]
        if values:
            return "firestore:" + ",".join(values)

    for field in ("original_question_id", "public_question_id", "question_url"):
        value = question.get(field)
        if value:
            return str(value)
    return ""


def source_question_key(
    qualification: str,
    list_group_id: str,
    record: Mapping[str, Any],
) -> str:
    """Return the canonical source key without rewriting an explicit key.

    A source file is allowed to predate ``sourceQuestionKey``.  In that case
    both the review inventory and batch materializers must derive the same key
    from source identity.  The stable review id is the generic fallback;
    ``questionLabel`` alone is deliberately not used because it repeats across
    sections and source files.
    """

    existing = str(
        record.get("sourceQuestionKey")
        or record.get("source_question_key")
        or ""
    ).strip()
    if existing:
        return existing

    qualification = str(qualification or "").strip()
    list_group_id = str(list_group_id or "").strip()
    if not qualification or not list_group_id:
        return ""

    grade = _GAS_GRADE_BY_QUALIFICATION.get(qualification)
    if grade:
        question_number = 0
        label_match = _QUESTION_NUMBER_PATTERN.search(
            str(record.get("questionLabel") or "")
        )
        if label_match:
            question_number = int(label_match.group("question_no"))

        subject = ""
        url_match = _GAS_URL_PATTERN.search(str(record.get("question_url") or ""))
        if url_match:
            subject = url_match.group("subject")
            question_number = question_number or int(url_match.group("question_no"))

        original_id = str(
            record.get("original_question_id")
            or record.get("originalQuestionId")
            or ""
        )
        source_id_match = _GAS_SOURCE_ID_PATTERN.search(original_id)
        if source_id_match:
            matched_grade = _GAS_GRADE_ALIASES.get(source_id_match.group("grade"))
            matched_subject = _GAS_SUBJECT_ALIASES.get(
                source_id_match.group("subject")
            )
            if matched_grade == grade and matched_subject:
                subject = subject or matched_subject
                question_number = question_number or int(
                    source_id_match.group("question_no")
                )

        exam_label = str(record.get("examLabel") or "")
        if not subject:
            subject = next(
                (
                    value
                    for label, value in _GAS_SUBJECT_LABELS.items()
                    if label in exam_label
                ),
                "",
            )
        if subject and question_number:
            return (
                f"gas-shunin:{grade}:{list_group_id}:{subject}:"
                f"q{question_number:02d}"
            )

    stable_id = review_question_id(record)
    if not stable_id:
        return ""
    return f"{qualification}:{list_group_id}:{stable_id}"


def source_record_ref(
    source_relative_path: str,
    record_index: int,
) -> str:
    """Return the 00_source-relative locator of one immutable source record."""

    relative = PurePosixPath(str(source_relative_path or "").replace("\\", "/"))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or isinstance(record_index, bool)
        or not isinstance(record_index, int)
        or record_index < 0
    ):
        return ""
    return f"{relative.as_posix()}#{record_index}"


def question_id_from_source_unique_key(source_unique_key: str) -> str:
    """Build the deterministic Firestore document ID for a source choice key."""

    return re.sub(
        r"[^A-Za-z0-9_-]+", "-", str(source_unique_key or "").strip()
    ).strip("-")


def source_identity_aliases(record: Mapping[str, Any]) -> set[str]:
    """Return identifiers carried by, or deterministically derived from, source."""

    aliases = {
        str(record[field])
        for field in SOURCE_IDENTITY_FIELDS
        if record.get(field)
    }
    firestore_ids = record.get("firestoreQuestionIds")
    if isinstance(firestore_ids, list):
        values = [str(value) for value in firestore_ids if value]
        aliases.update(values)
        if values:
            aliases.add("firestore:" + ",".join(values))
    source_unique_keys = record.get("sourceUniqueKeys")
    if isinstance(source_unique_keys, list):
        for value in source_unique_keys:
            if not value:
                continue
            source_key = str(value)
            aliases.add(source_key)
            document_id = question_id_from_source_unique_key(source_key)
            if document_id:
                aliases.add(document_id)
    source_unique_key = record.get("sourceUniqueKey")
    if source_unique_key:
        source_key = str(source_unique_key)
        aliases.add(source_key)
        document_id = question_id_from_source_unique_key(source_key)
        if document_id:
            aliases.add(document_id)
    stable = review_question_id(record)
    if stable and not stable.startswith(("http://", "https://")):
        aliases.add(stable)
    return aliases


def workflow_identity_aliases(record: Mapping[str, Any]) -> set[str]:
    """Return identifiers written by review/workflow artifacts."""

    return {
        str(record[field])
        for field in WORKFLOW_IDENTITY_FIELDS
        if record.get(field)
    }


def load_source_record_inventory(
    source_dir: Path,
    *,
    qualification: str,
    list_group_id: str,
) -> tuple[SourceRecordInventoryEntry, ...]:
    """Load ``00_source`` once and derive fail-closed exact identities."""

    entries: list[SourceRecordInventoryEntry] = []
    seen: set[SourceIdentityBinding] = set()
    for source_path in source_json_paths(source_dir):
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        records = (
            payload.get("question_bodies")
            if isinstance(payload, dict)
            else payload
        )
        if not isinstance(records, list):
            raise ValueError(f"source question array not found: {source_path}")
        relative_path = source_path.relative_to(source_dir).as_posix()
        for record_index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(
                    f"source record must be an object: {source_path}#{record_index}"
                )
            binding = SourceIdentityBinding.from_values(
                source_question_key(qualification, list_group_id, record),
                review_question_id(record),
                source_record_ref(relative_path, record_index),
            )
            if not binding.is_complete():
                raise ValueError(
                    f"source identity binding is incomplete: {source_path}#{record_index}"
                )
            if binding in seen:
                raise ValueError(
                    "duplicate source identity binding: "
                    + " / ".join(binding.as_tuple())
                )
            seen.add(binding)
            entries.append(
                SourceRecordInventoryEntry(
                    identity=SourceRecordIdentity(
                        binding=binding,
                        aliases=frozenset(
                            source_identity_aliases(
                                {**record, **binding.as_mapping()}
                            )
                        ),
                        source_stem=source_path.stem,
                    ),
                    record=record,
                    path=source_path,
                    record_index=record_index,
                )
            )
    if not entries:
        raise ValueError(f"source records not found: {source_dir}")
    return tuple(entries)
