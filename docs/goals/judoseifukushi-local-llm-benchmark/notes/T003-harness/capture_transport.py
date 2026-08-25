"""Redacted, append-only transport evidence for the real benchmark."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORBIDDEN_KEYS = {"authorization", "cookie", "api_key", "token", "credential", "image_bytes", "data"}


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def canonical_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()
                if str(key).casefold() not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and (value.startswith("data:") or "base64," in value):
        return "[redacted-data-url]"
    return value


class CaptureTransport:
    def __init__(self, root: Path, filename: str = "transport-events.jsonl") -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = root / filename

    def request(self, *, route: str, provider: str, question_ids: list[str], stage: str,
                role: str, attempt: int, payload: dict[str, Any]) -> dict[str, Any]:
        safe = redact(payload)
        record = {"kind": "request", "route": route, "provider": provider,
                  "questionIds": question_ids, "stage": stage, "role": role,
                  "attempt": attempt, "startedAt": utc_now(),
                  "requestFields": sorted(safe), "payloadSha256": digest_json(safe),
                  "canonicalRequestBytes": canonical_bytes(safe),
                  "promptUtf8Bytes": len(str(safe.get("prompt", "")).encode())}
        self._append(record)
        return record

    def response(self, request: dict[str, Any], *, usage: dict[str, int] | None,
                 response: Any, error: str | None = None) -> dict[str, Any]:
        safe = redact(response)
        record = {"kind": "response", "route": request["route"],
                  "provider": request["provider"], "questionIds": request["questionIds"],
                  "stage": request["stage"], "role": request["role"],
                  "attempt": request["attempt"], "startedAt": request["startedAt"],
                  "endedAt": utc_now(), "usage": usage, "responseSha256": digest_json(safe),
                  "canonicalResponseBytes": canonical_bytes(safe),
                  "error": error}
        self._append(record)
        return record

    def _append(self, record: dict[str, Any]) -> None:
        with self.events.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
