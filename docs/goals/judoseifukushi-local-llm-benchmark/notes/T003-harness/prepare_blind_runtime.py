#!/usr/bin/env python3
"""Create three identical, oracle-free runtime snapshots in OS temp space."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from benchmark_contract import JUDO, source_index

ROUTES = ("codex_only", "qwen3:14b", "qwen3.5:27b")
ALLOWED = {"id", "year", "question", "choices", "questionIntent", "image", "targets", "routing"}
FORBIDDEN_TEXT = ("correctChoice", "answerTable", "answer_result", "explanation", "review", "evaluation", "priorModel", "patch")


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare(repo: Path, manifest_path: Path, temp_root: Path, artifacts: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    found = source_index(repo)
    items = []
    for item in manifest["items"]:
        question = found[item["id"]][2]
        clean = {**item, "questionIntent": question.get("questionIntent"),
                 "routing": {"qualification": "2nd-class-kenchikushi" if item["id"] not in {v for ids in JUDO.values() for v in ids} else "judoseifukushi",
                             "holdWithoutModel": item["id"] in set(JUDO["source-answer-missing"])}}
        if set(clean) != ALLOWED:
            raise RuntimeError("sanitized allowlist mismatch")
        serialized = json.dumps(clean, ensure_ascii=False)
        if "data:" in serialized or "base64" in serialized:
            raise RuntimeError(f"forbidden sanitized content: {item['id']}")
        items.append(clean)
    snapshot = {"schemaVersion": "blind-runtime/v1", "items": items}
    fingerprint = canonical_hash(snapshot)
    route_rows = []
    for route in ROUTES:
        route_dir = temp_root / route.replace(":", "-")
        if route_dir.exists():
            shutil.rmtree(route_dir)
        route_dir.mkdir(parents=True)
        (route_dir / "input-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
        route_rows.append({"route": route, "path": str(route_dir), "fingerprint": fingerprint})
    if len({row["fingerprint"] for row in route_rows}) != 1:
        raise RuntimeError("route runtime fingerprints differ")
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "input-snapshot.json").write_text(json.dumps({"fingerprint": fingerprint, **snapshot}, ensure_ascii=False, indent=2) + "\n")
    return {"snapshotFingerprint": fingerprint, "routes": route_rows, "itemCount": len(items)}
