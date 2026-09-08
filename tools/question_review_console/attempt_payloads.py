"""Content-addressed attempt payloads; the question state owns their references.

Publication is payload fsync/replace first, question-state fsync/replace last.
Unreferenced payloads never constitute a committed attempt. No live GC runs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.review_store import atomic_write


PAYLOAD_FIELDS = frozenset({"plan", "prompt", "preparedCandidate"})
REFS_FIELD = "attemptPayloadRefs"


class AttemptPayloadError(RuntimeError):
    pass


def _encoded(value: Any) -> str:
    return json.dumps({"value": value}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_digest(digest: str) -> None:
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise AttemptPayloadError("attempt payloadのhashが不正です。")


def _path(run_dir: Path, digest: str) -> Path:
    _validate_digest(digest)
    directory = run_dir / "attempt_payloads" / digest[:2]
    path = directory / (digest + ".json")
    if not path.resolve().is_relative_to(run_dir.resolve()):
        raise AttemptPayloadError("attempt payloadの保存先がrun外です。")
    return path


def read_payload(run_dir: Path, digest: str) -> Any:
    path = _path(run_dir, digest)
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise AttemptPayloadError("attempt payloadのhashが一致しません。")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"value"}:
            raise AttemptPayloadError("attempt payloadの形式が不正です。")
        return payload["value"]
    except (OSError, ValueError) as exc:
        raise AttemptPayloadError(f"attempt payloadを読めません: {path}") from exc


def hydrate_payloads(run_dir: Path, state: dict[str, Any], selection: bool | str) -> dict[str, Any]:
    refs = state.get(REFS_FIELD, {})
    if not isinstance(refs, dict):
        raise AttemptPayloadError("attempt payload参照が不正です。")
    attempts = state.get("attemptArtifacts", {})
    if not isinstance(attempts, dict):
        raise AttemptPayloadError("attempt metadataが不正です。")
    for attempt_id, fields in refs.items():
        if not isinstance(fields, dict) or not isinstance(attempts.get(attempt_id), dict):
            raise AttemptPayloadError("attempt payloadの所有attemptが不正です。")
        for field, digest in fields.items():
            if field not in PAYLOAD_FIELDS or not isinstance(digest, str):
                raise AttemptPayloadError("attempt payloadのfield参照が不正です。")
            # Metadata-only projections validate reference syntax without
            # stat/resolving every historical payload. Content reads below
            # enforce path ownership and hash whenever the payload is used.
            _validate_digest(digest)
            if field in attempts[attempt_id]:
                raise AttemptPayloadError("attempt payloadが二重に定義されています。")
            if selection is True or selection == attempt_id:
                attempts[attempt_id][field] = read_payload(run_dir, digest)
    return state


def detach_payloads(run_dir: Path, state: dict[str, Any], previous: Mapping[str, Any]) -> None:
    # A callback may insert a caller-owned attempt. Strip only owned containers;
    # the caller's candidate/plan must remain untouched.
    attempts = state.get("attemptArtifacts", {})
    if not isinstance(attempts, dict) or any(not isinstance(a, Mapping) for a in attempts.values()):
        raise AttemptPayloadError("attempt metadataが不正です。")
    state["attemptArtifacts"] = {
        attempt_id: dict(attempt)
        for attempt_id, attempt in attempts.items()
    }
    refs = state.setdefault(REFS_FIELD, {})
    # The update callback owns values, never the on-disk reference index.
    if refs != previous.get(REFS_FIELD, {}):
        raise AttemptPayloadError("attempt payload参照は直接変更できません。")
    old_attempts = previous.get("attemptArtifacts", {})
    for attempt_id, attempt in state.get("attemptArtifacts", {}).items():
        old = old_attempts.get(attempt_id, {})
        for field in refs.get(attempt_id, {}):
            if field in old and field not in attempt:
                raise AttemptPayloadError("参照済みattempt payloadは削除できません。")
        for field in PAYLOAD_FIELDS.intersection(attempt):
            value = attempt.pop(field)
            digest = refs.get(attempt_id, {}).get(field)
            if digest and field in old and old[field] == value:
                continue
            if digest:
                raise AttemptPayloadError("確定済みattempt payloadは変更できません。")
            encoded = _encoded(value) + "\n"
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            path = _path(run_dir, digest)
            if path.exists():
                if read_payload(run_dir, digest) != value:
                    raise AttemptPayloadError("attempt payloadの内容が一致しません。")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, encoded)
            refs.setdefault(attempt_id, {})[field] = digest
    if any(attempt_id not in state.get("attemptArtifacts", {}) for attempt_id in refs):
        raise AttemptPayloadError("参照済みattemptは削除できません。")
