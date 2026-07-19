from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.projection import record_identity_aliases
from tools.question_review_console.review_store import atomic_write


SCHEMA_VERSION = "question-maintenance-preparation/v1"
MAX_SUMMARY_LENGTH = 200_000
_RECORD_CONTAINER_KEYS = (
    "entries",
    "patched_questions",
    "question_bodies",
    "questions",
)


class QuestionPatchProposalError(ValueError):
    pass


def _safe_relative(repo_root: Path, value: str | Path) -> Path:
    relative = Path(str(value))
    absolute = (repo_root / relative).resolve()
    if relative.is_absolute() or not absolute.is_relative_to(repo_root):
        raise QuestionPatchProposalError("一問workspaceのpathがrepository外です。")
    return relative


def _payload_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        if not all(isinstance(value, Mapping) for value in payload):
            raise QuestionPatchProposalError("patch配列にobject以外があります。")
        return payload
    if isinstance(payload, dict):
        for key in _RECORD_CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                if not all(isinstance(item, Mapping) for item in value):
                    raise QuestionPatchProposalError(
                        f"patchの{key}にobject以外があります。"
                    )
                return value
        return [payload]
    raise QuestionPatchProposalError("patchの問題配列を特定できません。")


def _load_record_payload(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line_number, raw_line in enumerate(lines, 1):
                if not raw_line.strip():
                    continue
                value = json.loads(raw_line)
                if not isinstance(value, Mapping):
                    raise QuestionPatchProposalError(
                        f"JSONLの{line_number}行目がobjectではありません。"
                    )
                records.append(dict(value))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuestionPatchProposalError(
                f"一問workspaceのJSONLを読み取れません: {path.name}"
            ) from exc
        return records, records
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuestionPatchProposalError(
            f"一問workspaceのJSONを読み取れません: {path.name}"
        ) from exc
    return payload, _payload_records(payload)


def _dump_record_payload(path: Path, payload: Any) -> str:
    if path.suffix.lower() == ".jsonl":
        return "".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            for value in payload
        )
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _single_record_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and not any(
        isinstance(payload.get(key), list) for key in _RECORD_CONTAINER_KEYS
    )


def _target_index(
    records: list[dict[str, Any]],
    binding: SourceIdentityBinding,
    aliases: set[str],
) -> int | None:
    exact = [
        index
        for index, record in enumerate(records)
        if SourceIdentityBinding.from_mapping(record) == binding
    ]
    if len(exact) > 1:
        raise QuestionPatchProposalError("完全一致する対象recordが重複しています。")
    if exact:
        return exact[0]
    scored = [
        (len(record_identity_aliases(record) & aliases), index)
        for index, record in enumerate(records)
        if record_identity_aliases(record) & aliases
    ]
    best = max((score for score, _index in scored), default=0)
    matches = [index for score, index in scored if score == best and score]
    if len(matches) > 1:
        raise QuestionPatchProposalError("対象recordを一意に特定できません。")
    return matches[0] if matches else None


def assert_target_resolvable(
    repo_root: Path,
    relative_path: str | Path,
    *,
    binding: SourceIdentityBinding,
    aliases: set[str],
) -> None:
    """Fail before model work when an existing patch target is ambiguous."""

    relative = _safe_relative(repo_root.resolve(), relative_path)
    path = repo_root.resolve() / relative
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise QuestionPatchProposalError(f"候補反映先が通常fileではありません: {relative}")
    _payload, records = _load_record_payload(path)
    record_aliases = {str(value) for value in aliases if str(value).strip()}
    record_aliases.update(binding.as_tuple())
    _target_index(records, binding, record_aliases)


@dataclass
class IsolatedQuestionPatchWorkspace:
    """Run one model against private patch copies, then rebase one record."""

    repo_root: Path
    root: Path
    qualification: str
    mutable_paths: tuple[Path, ...]
    initial_bytes: dict[Path, bytes | None]

    @classmethod
    def create(
        cls,
        repo_root: Path,
        root: Path,
        *,
        qualification: str,
        mutable_paths: list[str] | tuple[str, ...],
        readonly_paths: list[str] | tuple[str, ...] = (),
    ) -> "IsolatedQuestionPatchWorkspace":
        resolved_repo = repo_root.resolve()
        resolved_root = root.resolve()
        if not resolved_root.is_relative_to(resolved_repo):
            raise QuestionPatchProposalError(
                "一問workspaceはrepository内に作成してください。"
            )
        if resolved_root.exists():
            shutil.rmtree(resolved_root)
        resolved_root.mkdir(parents=True)

        relative_mutable = tuple(
            dict.fromkeys(
                _safe_relative(resolved_repo, value) for value in mutable_paths
            )
        )
        qualification_root = Path("output", qualification)
        if any(not path.is_relative_to(qualification_root) for path in relative_mutable):
            raise QuestionPatchProposalError(
                "一問workspaceの可変fileは対象資格配下に限定してください。"
            )

        # Code and documents remain canonical read-only symlinks.  Only output
        # files selected below are private regular-file copies.
        for source in resolved_repo.iterdir():
            if source.name in {".git", "output"}:
                continue
            destination = resolved_root / source.name
            destination.symlink_to(source, target_is_directory=source.is_dir())

        canonical_qualification = resolved_repo / qualification_root
        isolated_qualification = resolved_root / qualification_root

        def contains_mutable(relative: Path) -> bool:
            return any(
                path == relative or path.is_relative_to(relative)
                for path in relative_mutable
            )

        def mirror_directory(source: Path, destination: Path) -> None:
            destination.mkdir(parents=True, exist_ok=True)
            if not source.is_dir():
                return
            for child in source.iterdir():
                relative = child.relative_to(resolved_repo)
                target = destination / child.name
                if contains_mutable(relative):
                    if child.is_dir():
                        mirror_directory(child, target)
                    elif child.is_file():
                        shutil.copy2(child, target)
                    continue
                target.symlink_to(child, target_is_directory=child.is_dir())

        mirror_directory(canonical_qualification, isolated_qualification)
        for relative in relative_mutable:
            source = resolved_repo / relative
            destination = resolved_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                if source.is_symlink() or not source.is_file():
                    raise QuestionPatchProposalError(
                        f"可変patchが通常fileではありません: {relative}"
                    )
                if destination.is_symlink():
                    destination.unlink()
                shutil.copy2(source, destination)

        for value in readonly_paths:
            relative = _safe_relative(resolved_repo, value)
            source = resolved_repo / relative
            destination = resolved_root / relative
            if not source.is_file() or source.is_symlink():
                raise QuestionPatchProposalError(
                    f"一問workspaceの入力fileを確認できません: {relative}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source)

        initial_bytes = {
            relative: (
                (resolved_root / relative).read_bytes()
                if (resolved_root / relative).is_file()
                else None
            )
            for relative in relative_mutable
        }
        return cls(
            repo_root=resolved_repo,
            root=resolved_root,
            qualification=qualification,
            mutable_paths=relative_mutable,
            initial_bytes=initial_bytes,
        )

    def changed_paths(self) -> tuple[Path, ...]:
        changed: list[Path] = []
        for relative in self.mutable_paths:
            path = self.root / relative
            current = path.read_bytes() if path.is_file() and not path.is_symlink() else None
            if current != self.initial_bytes[relative]:
                changed.append(relative)
        return tuple(changed)

    def apply_record_update(
        self,
        relative_path: str | Path,
        *,
        binding: SourceIdentityBinding,
        aliases: set[str],
        set_fields: Mapping[str, Any],
        unset_fields: tuple[str, ...] = (),
        base_record: Mapping[str, Any],
    ) -> Path:
        """Materialize a validated structured candidate in this private copy."""

        relative = _safe_relative(self.repo_root, relative_path)
        if relative not in self.mutable_paths:
            raise QuestionPatchProposalError("可変範囲外の候補は反映できません。")
        if not binding.is_complete():
            raise QuestionPatchProposalError("候補反映に完全なsource identityが必要です。")
        path = self.root / relative
        if path.is_file() and not path.is_symlink():
            payload, records = _load_record_payload(path)
        elif relative.suffix.lower() == ".jsonl":
            payload = []
            records = payload
        else:
            payload = []
            records = payload

        record_aliases = {str(value) for value in aliases if str(value).strip()}
        record_aliases.update(binding.as_tuple())
        index = _target_index(records, binding, record_aliases)
        if index is None:
            record = json.loads(json.dumps(dict(base_record), ensure_ascii=False))
            for field, value in binding.as_mapping().items():
                record.setdefault(field, value)
            if _single_record_payload(payload):
                if payload:
                    raise QuestionPatchProposalError(
                        f"単一record fileへ別recordを追加できません: {relative}"
                    )
                payload = record
                records = [payload]
            else:
                records.append(record)
                index = len(records) - 1
        else:
            record = records[index]

        # source identityはmodel出力ではなくserverが管理する。既存rowのIDは
        # そのまま保持し、欠けている安定参照だけを補う。新規rowは上で完全な
        # bindingを設定済みである。
        for field, value in binding.as_mapping().items():
            record.setdefault(field, value)
        for field, value in set_fields.items():
            record[str(field)] = json.loads(json.dumps(value, ensure_ascii=False))
        for field in unset_fields:
            record.pop(str(field), None)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, _dump_record_payload(path, payload))
        return relative

    def rebase_into_canonical(
        self,
        changed_paths: list[Path] | tuple[Path, ...],
        *,
        binding: SourceIdentityBinding,
        aliases_by_path: Mapping[str, list[list[str]]],
    ) -> list[str]:
        if not binding.is_complete():
            raise QuestionPatchProposalError(
                "正本反映に完全なsource identityが必要です。"
            )
        pending: list[tuple[Path, str]] = []
        for raw_relative in changed_paths:
            relative = _safe_relative(self.repo_root, raw_relative)
            if relative not in self.mutable_paths:
                raise QuestionPatchProposalError("可変範囲外のfileは反映できません。")
            groups = aliases_by_path.get(relative.as_posix()) or []
            aliases = {
                str(value)
                for group in groups
                for value in group
                if str(value).strip()
            }
            aliases.update(binding.as_tuple())
            if not aliases:
                raise QuestionPatchProposalError(
                    f"対象record scopeがありません: {relative}"
                )

            baseline_path = self.root / ".question_baseline" / relative
            baseline_bytes = self.initial_bytes[relative]
            if baseline_bytes is None:
                baseline_records: list[dict[str, Any]] = []
            else:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_bytes(baseline_bytes)
                baseline_payload, baseline_records = _load_record_payload(
                    baseline_path
                )
            candidate_path = self.root / relative
            candidate_payload, candidate_records = _load_record_payload(candidate_path)
            canonical_path = self.repo_root / relative
            if canonical_path.exists():
                canonical_payload, canonical_records = _load_record_payload(
                    canonical_path
                )
            else:
                canonical_payload = copy_payload_shape(candidate_payload)
                canonical_records = _payload_records(canonical_payload)

            baseline_index = _target_index(baseline_records, binding, aliases)
            candidate_index = _target_index(candidate_records, binding, aliases)
            canonical_index = _target_index(canonical_records, binding, aliases)
            if candidate_index is None:
                raise QuestionPatchProposalError(
                    f"候補patchに対象recordがありません: {relative}"
                )
            if baseline_index is None:
                if canonical_index is not None:
                    raise QuestionPatchProposalError(
                        f"準備後に対象recordが追加されました: {relative}"
                    )
            else:
                if canonical_index is None:
                    raise QuestionPatchProposalError(
                        f"準備後に対象recordが削除されました: {relative}"
                    )
                if _canonical_bytes(baseline_records[baseline_index]) != _canonical_bytes(
                    canonical_records[canonical_index]
                ):
                    raise QuestionPatchProposalError(
                        f"準備後に対象recordが更新されました: {relative}"
                    )

            candidate_record = json.loads(
                json.dumps(candidate_records[candidate_index], ensure_ascii=False)
            )
            if canonical_index is None:
                if _single_record_payload(canonical_payload):
                    if canonical_payload:
                        raise QuestionPatchProposalError(
                            f"単一record fileへ別recordを追加できません: {relative}"
                        )
                    canonical_payload = candidate_record
                    canonical_records = [canonical_payload]
                else:
                    canonical_records.append(candidate_record)
            else:
                if _single_record_payload(canonical_payload):
                    canonical_payload.clear()
                    canonical_payload.update(candidate_record)
                else:
                    canonical_records[canonical_index] = candidate_record
            content = _dump_record_payload(canonical_path, canonical_payload)
            current = canonical_path.read_text(encoding="utf-8") if canonical_path.is_file() else None
            if content != current:
                pending.append((canonical_path, content))

        for path, content in pending:
            atomic_write(path, content)
        return [
            path.relative_to(self.repo_root).as_posix() for path, _content in pending
        ]

    def cleanup(self) -> None:
        if self.root.is_relative_to(self.repo_root):
            shutil.rmtree(self.root, ignore_errors=True)


def copy_payload_shape(payload: Any) -> Any:
    if isinstance(payload, list):
        return []
    if isinstance(payload, dict):
        shaped = {key: value for key, value in payload.items()}
        for key in _RECORD_CONTAINER_KEYS:
            if isinstance(shaped.get(key), list):
                shaped[key] = []
                return shaped
        return {}
    raise QuestionPatchProposalError("patchの形式を引き継げません。")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class QuestionPatchProposalStore:
    """Persist read-only per-question preparation before the single writer runs."""

    def __init__(self, repo_root: Path, workflow_root: Path):
        self.repo_root = repo_root.resolve()
        self.workflow_root = workflow_root.resolve()
        if not self.workflow_root.is_relative_to(self.repo_root):
            raise QuestionPatchProposalError("workflow runの保存先がrepository外です。")

    def _path(self, qualification: str, run_id: str, work_item_key: str) -> Path:
        segments = (qualification, run_id, work_item_key)
        if any(
            not value
            or value in {".", ".."}
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value)
            for value in segments
        ):
            raise QuestionPatchProposalError("準備記録のIDが不正です。")
        path = (
            self.workflow_root
            / qualification
            / run_id
            / "question_preparations"
            / f"{work_item_key}.json"
        ).resolve()
        expected_root = (self.workflow_root / qualification / run_id).resolve()
        if (
            not expected_root.is_relative_to(self.workflow_root)
            or not path.is_relative_to(expected_root)
        ):
            raise QuestionPatchProposalError("準備記録の保存先がrun外です。")
        return path

    def write(
        self,
        qualification: str,
        run_id: str,
        *,
        work_item_key: str,
        question_id: str,
        stage_id: str,
        input_fingerprint: str,
        summary: str,
        thread_id: str,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        normalized_summary = str(summary or "").strip()
        if not normalized_summary:
            raise QuestionPatchProposalError("一問の準備結果が空です。")
        if len(normalized_summary) > MAX_SUMMARY_LENGTH:
            raise QuestionPatchProposalError("一問の準備結果が上限を超えています。")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "workItemKey": str(work_item_key),
            "questionId": str(question_id),
            "stageId": str(stage_id),
            "inputFingerprint": str(input_fingerprint),
            "summary": normalized_summary,
            "threadId": str(thread_id),
            "sessionId": str(session_id),
            "turnId": str(turn_id),
        }
        raw = _canonical_bytes(payload)
        path = self._path(qualification, run_id, work_item_key)
        atomic_write(path, raw.decode("utf-8"))
        return {
            "path": path.relative_to(self.repo_root).as_posix(),
            "hash": hashlib.sha256(raw).hexdigest(),
            "payload": payload,
        }

    def read(
        self,
        qualification: str,
        run_id: str,
        *,
        work_item_key: str,
        expected_hash: str,
        question_id: str,
        stage_id: str,
        input_fingerprint: str,
    ) -> dict[str, Any]:
        path = self._path(qualification, run_id, work_item_key)
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuestionPatchProposalError("一問の準備記録を読み取れません。") from exc
        if not isinstance(payload, Mapping):
            raise QuestionPatchProposalError("一問の準備記録がobjectではありません。")
        if hashlib.sha256(raw).hexdigest() != str(expected_hash):
            raise QuestionPatchProposalError("一問の準備記録hashが一致しません。")
        expected = {
            "schemaVersion": SCHEMA_VERSION,
            "workItemKey": str(work_item_key),
            "questionId": str(question_id),
            "stageId": str(stage_id),
            "inputFingerprint": str(input_fingerprint),
        }
        if any(str(payload.get(key) or "") != value for key, value in expected.items()):
            raise QuestionPatchProposalError("一問の準備記録とqueue itemが一致しません。")
        if not str(payload.get("summary") or "").strip():
            raise QuestionPatchProposalError("一問の準備記録に修正案がありません。")
        return dict(payload)
