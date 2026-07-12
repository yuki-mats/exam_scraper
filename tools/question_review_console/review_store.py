from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.prompt_builder import build_codex_prompt


REVIEW_STATUSES = {
    "unreviewed",
    "needs_review",
    "awaiting_codex",
    "post_fix_review",
    "approved",
    "hold",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _safe_segment(value: str) -> str:
    safe = "".join(char for char in value if char.isalnum() or char in "-._")
    if not safe or safe != value:
        raise ValueError(f"invalid review path segment: {value}")
    return safe


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


class ReviewStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console"
        self._cache: dict[
            tuple[str, str],
            tuple[str, dict[str, tuple[Path, dict[str, Any]]]],
        ] = {}
        self._lock = threading.RLock()

    def create(
        self,
        question: Mapping[str, Any],
        request: Mapping[str, Any],
        *,
        status: str = "awaiting_codex",
    ) -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"unsupported review status: {status}")
        qualification = _safe_segment(str(question["qualification"]))
        list_group_id = _safe_segment(str(question["listGroupId"]))
        now = iso_now()
        digest = hashlib.sha256(
            f"{question['reviewKey']}:{now}:{request.get('note', '')}".encode("utf-8")
        ).hexdigest()[:8]
        review_id = datetime.now().strftime("%Y%m%dT%H%M%S%f") + f"-{digest}"
        directory = self.root / qualification / list_group_id
        review_path = directory / "reviews" / f"{review_id}.json"
        prompt_path = directory / "prompts" / f"{review_id}.md"
        selection = request.get("selection")
        if isinstance(selection, Mapping):
            selection_payload = {
                "targetLabel": str(selection.get("targetLabel") or "").strip(),
                "dataPath": str(selection.get("dataPath") or "").strip(),
                "fields": [str(value) for value in selection.get("fields") or []],
                "choiceIndexes": sorted(
                    {
                        int(value)
                        for value in selection.get("choiceIndexes") or []
                        if isinstance(value, int) or str(value).isdigit()
                    }
                ),
                "selectedText": str(selection.get("selectedText") or "").strip(),
            }
        else:
            selection_payload = None
        investigation_scope = str(request.get("investigationScope") or "current_question")
        if investigation_scope not in {
            "current_question",
            "current_group",
            "qualification",
            "all_qualifications",
        }:
            investigation_scope = "current_question"
        payload = {
            "schemaVersion": "local-question-review/v1",
            "reviewId": review_id,
            "reviewKey": question["reviewKey"],
            "questionId": question["id"],
            "status": status,
            "qualification": qualification,
            "listGroupId": list_group_id,
            "sourceQuestionKey": question.get("sourceQuestionKey"),
            "originalQuestionId": question.get("originalQuestionId"),
            "choiceIndexes": sorted(
                {
                    int(value)
                    for value in request.get("choiceIndexes") or []
                    if isinstance(value, int) or str(value).isdigit()
                }
            ),
            "issueTypes": [str(value) for value in request.get("issueTypes") or []],
            "fields": [str(value) for value in request.get("fields") or []],
            "note": str(request.get("note") or "").strip(),
            "expectedOutcome": str(request.get("expectedOutcome") or "").strip(),
            "selection": selection_payload,
            "investigationScope": investigation_scope,
            "snapshots": {
                "projectedHash": question["stateHash"],
                "sourceHash": _hash(question.get("source")),
                "uploadReadyHash": _hash(question.get("uploadReadyDocs")),
                "liveHash": (
                    _hash(question.get("liveReadback"))
                    if question.get("liveReadback") is not None
                    else None
                ),
            },
            "files": question.get("paths") or {},
            "createdAt": now,
            "updatedAt": now,
        }
        atomic_write(
            review_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        prompt = build_codex_prompt(self.repo_root, review_path, question, payload)
        atomic_write(prompt_path, prompt)
        self._invalidate(qualification, list_group_id)
        return {
            **payload,
            "reviewPath": str(review_path),
            "promptPath": str(prompt_path),
            "prompt": prompt,
        }

    def latest_for(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        qualification = str(question["qualification"])
        list_group_id = str(question["listGroupId"])
        latest = self._latest_by_review_key(qualification, list_group_id).get(
            str(question.get("reviewKey") or "")
        )
        if latest is None:
            return None
        path, cached_payload = latest
        payload = copy.deepcopy(cached_payload)
        status = str(payload.get("status") or "unreviewed")
        snapshot_hash = str(payload.get("snapshots", {}).get("projectedHash") or "")
        if status in {"awaiting_codex", "approved"} and snapshot_hash != question.get("stateHash"):
            status = "post_fix_review"
            payload["status"] = status
            payload["updatedAt"] = iso_now()
            atomic_write(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            self._invalidate(qualification, list_group_id)
        return {
            **payload,
            "status": status,
            "reviewPath": str(path),
            "promptPath": str(path.parent.parent / "prompts" / f"{path.stem}.md"),
        }

    def update_status(
        self,
        review_id: str,
        status: str,
        *,
        current_state_hash: str | None = None,
    ) -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"unsupported review status: {status}")
        matches = list(self.root.glob(f"*/*/reviews/{_safe_segment(review_id)}.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"review not found: {review_id}")
        path = matches[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = status
        if status in {"approved", "awaiting_codex"}:
            if not current_state_hash:
                raise ValueError("現在のprojected hashが必要です。")
            payload.setdefault("snapshots", {})["projectedHash"] = current_state_hash
        payload["updatedAt"] = iso_now()
        atomic_write(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self._invalidate(str(payload["qualification"]), str(payload["listGroupId"]))
        return payload

    def get(self, review_id: str) -> dict[str, Any]:
        matches = list(self.root.glob(f"*/*/reviews/{_safe_segment(review_id)}.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"review not found: {review_id}")
        return json.loads(matches[0].read_text(encoding="utf-8"))

    def _latest_by_review_key(
        self, qualification: str, list_group_id: str
    ) -> dict[str, tuple[Path, dict[str, Any]]]:
        directory = self.root / qualification / list_group_id / "reviews"
        files = sorted(directory.glob("*.json")) if directory.is_dir() else []
        fingerprint = hashlib.sha256(
            "\n".join(
                f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
                for path in files
            ).encode("utf-8")
        ).hexdigest()
        key = (qualification, list_group_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == fingerprint:
                return cached[1]
            latest: dict[str, tuple[int, Path, dict[str, Any]]] = {}
            for path in files:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                review_key = str(payload.get("reviewKey") or "")
                if not review_key:
                    continue
                candidate = (path.stat().st_mtime_ns, path, payload)
                existing = latest.get(review_key)
                if existing is None or (candidate[0], candidate[1].name) > (
                    existing[0],
                    existing[1].name,
                ):
                    latest[review_key] = candidate
            mapping = {
                review_key: (candidate[1], candidate[2])
                for review_key, candidate in latest.items()
            }
            self._cache[key] = (fingerprint, mapping)
            return mapping

    def _invalidate(self, qualification: str, list_group_id: str) -> None:
        with self._lock:
            self._cache.pop((qualification, list_group_id), None)


def _hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
