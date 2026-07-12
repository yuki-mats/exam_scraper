from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ALLOWED_DIRECTORIES = (
    PurePosixPath("document/operations"),
    PurePosixPath("document/reference"),
    PurePosixPath("document/sources"),
    PurePosixPath("prompt"),
)
ALLOWED_FILES = {
    PurePosixPath("document/temporary/README.md"),
    PurePosixPath("tools/question_bank/README.md"),
}


class CanonicalDocumentStore:
    """Read-only access to durable workflow documents shown by the local GUI."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def read(self, relative_path: str) -> dict[str, Any]:
        normalized = self._normalize(relative_path)
        candidate = (self.repo_root / normalized).resolve()
        if not candidate.is_relative_to(self.repo_root) or not candidate.is_file():
            raise FileNotFoundError(f"正本文書が見つかりません: {normalized.as_posix()}")
        content = candidate.read_text(encoding="utf-8")
        stat = candidate.stat()
        return {
            "path": normalized.as_posix(),
            "title": self._title(content, candidate.stem),
            "content": content,
            "contentHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "modifiedAt": (
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds")
            ),
        }

    @staticmethod
    def _normalize(value: str) -> PurePosixPath:
        raw = str(value or "").strip()
        path = PurePosixPath(raw)
        if (
            not raw
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix.lower() != ".md"
        ):
            raise ValueError("表示できる正本文書pathではありません。")
        allowed = path in ALLOWED_FILES or any(
            path == prefix or path.is_relative_to(prefix)
            for prefix in ALLOWED_DIRECTORIES
        )
        if not allowed or path.is_relative_to(PurePosixPath("document/temporary")):
            if path not in ALLOWED_FILES:
                raise ValueError("継続的な問題整備の正本文書ではありません。")
        return path

    @staticmethod
    def _title(content: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
        return match.group(1) if match else fallback
