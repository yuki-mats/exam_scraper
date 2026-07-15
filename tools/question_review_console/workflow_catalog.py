from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "question_maintenance_workflow.toml"
)
STAGE_KINDS = {"source", "human", "machine"}


def _document_path(value: Any) -> str:
    path = str(value or "").strip()
    parsed = PurePosixPath(path)
    if (
        not path
        or parsed.is_absolute()
        or ".." in parsed.parts
        or parsed.suffix.lower() != ".md"
    ):
        raise ValueError(f"workflow document pathが不正です: {path}")
    return path


def _artifact_path(value: Any) -> str:
    path = str(value or "").strip()
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or parsed.suffix == "":
        raise ValueError(f"workflow artifact pathが不正です: {path}")
    return path


def _policy_version(value: Any, field: str, *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field}は1以上の整数で指定してください。")
    return value


def _document_patterns(value: Any, field: str) -> list[str]:
    patterns = _string_list(value, field)
    if any(
        not pattern
        or "/" in pattern
        or "\\" in pattern
        or ".." in pattern
        or not pattern.endswith(".md")
        for pattern in patterns
    ):
        raise ValueError(f"{field}は資格文書の安全なMarkdown basenameで指定してください。")
    return patterns


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field}は文字列配列で指定してください。")
    return list(value)


class WorkflowCatalog:
    """Loads the GUI workflow structure from its machine-readable SSOT."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    @property
    def path(self) -> Path:
        local = self.repo_root / "config" / "question_maintenance_workflow.toml"
        return local if local.is_file() else DEFAULT_CATALOG_PATH

    def load(self) -> dict[str, Any]:
        raw = self.path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
        system = self._system(parsed.get("system"))
        stages = self._stages(parsed.get("stages"))
        return {
            "system": system,
            "stages": stages,
            "evaluation": self._evaluation(parsed.get("evaluation")),
            "catalogHash": hashlib.sha256(raw).hexdigest(),
            "catalogPath": str(self.path),
        }

    @staticmethod
    def _system(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("workflow catalogに[system]がありません。")
        required = ("id", "name", "description", "trunk_document")
        missing = [field for field in required if not str(value.get(field) or "").strip()]
        if missing:
            raise ValueError(f"workflow system fieldが不足しています: {', '.join(missing)}")
        trunk = _document_path(value["trunk_document"])
        defaults = [
            _document_path(path)
            for path in _string_list(
                value.get("default_documents"), "system.default_documents"
            )
        ]
        human_documents = [
            _document_path(path)
            for path in _string_list(
                value.get("human_documents"), "system.human_documents"
            )
        ]
        return {
            "id": str(value["id"]),
            "name": str(value["name"]),
            "description": str(value["description"]),
            "trunkDocument": trunk,
            "defaultDocuments": defaults,
            "humanDocuments": human_documents,
        }

    @staticmethod
    def _stages(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            raise ValueError("workflow catalogに[[stages]]がありません。")
        stages: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ValueError("workflow stageはtableで指定してください。")
            stage_id = str(raw.get("id") or "")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", stage_id) or stage_id in seen:
                raise ValueError(f"workflow stage idが不正又は重複しています: {stage_id}")
            seen.add(stage_id)
            kind = str(raw.get("kind") or "")
            if kind not in STAGE_KINDS:
                raise ValueError(f"workflow stage kindが不正です: {stage_id}={kind}")
            required = ("code", "label", "purpose")
            missing = [field for field in required if not str(raw.get(field) or "").strip()]
            if missing:
                raise ValueError(
                    f"workflow stage fieldが不足しています: {stage_id}: {', '.join(missing)}"
                )
            stage: dict[str, Any] = {
                "id": stage_id,
                "code": str(raw["code"]),
                "label": str(raw["label"]),
                "purpose": str(raw["purpose"]),
                "kind": kind,
                "batchSelectable": bool(raw.get("batch_selectable", False)),
                "policyVersion": _policy_version(
                    raw.get("policy_version"),
                    f"{stage_id}.policy_version",
                    required=bool(raw.get("batch_selectable", False)),
                ),
                "qualificationDocumentPatterns": _document_patterns(
                    raw.get("qualification_document_patterns"),
                    f"{stage_id}.qualification_document_patterns",
                ),
                "documents": [
                    _document_path(path)
                    for path in _string_list(raw.get("documents"), f"{stage_id}.documents")
                ],
                "issueFields": _string_list(
                    raw.get("issue_fields"), f"{stage_id}.issue_fields"
                ),
            }
            patch_dir = str(raw.get("patch_dir") or "")
            patch_suffix = str(raw.get("patch_suffix") or "")
            if bool(patch_dir) != bool(patch_suffix):
                raise ValueError(
                    f"patch_dirとpatch_suffixは組で指定してください: {stage_id}"
                )
            if patch_dir:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", patch_dir):
                    raise ValueError(f"patch_dirが不正です: {stage_id}")
                stage["patchDir"] = patch_dir
                stage["patchSuffix"] = patch_suffix
            stages.append(stage)
        stage_by_id = {stage["id"]: stage for stage in stages}
        required_kinds = {
            "source": "source",
            "setup": "human",
            "law_context": "human",
            "law_audit": "human",
            "category_setup": "human",
            "delivery": "machine",
        }
        for stage_id, kind in required_kinds.items():
            if stage_by_id.get(stage_id, {}).get("kind") != kind:
                raise ValueError(f"必須workflow stageがありません: {stage_id} ({kind})")
        special = {"source", "setup", "law_audit", "category_setup", "delivery"}
        missing_patch = [
            stage["id"]
            for stage in stages
            if stage["id"] not in special and not stage.get("patchDir")
        ]
        if missing_patch:
            raise ValueError(
                "通常工程にはpatch_dirが必要です: " + ", ".join(missing_patch)
            )
        unsupported_kinds = [
            stage["id"]
            for stage in stages
            if (stage["kind"] == "source" and stage["id"] != "source")
            or (stage["kind"] == "machine" and stage["id"] != "delivery")
        ]
        if unsupported_kinds:
            raise ValueError(
                "source又はmachine工程を追加するには実行実装が必要です: "
                + ", ".join(unsupported_kinds)
            )
        invalid_batch_stages = [
            stage["id"]
            for stage in stages
            if stage.get("batchSelectable")
            and (
                stage["kind"] != "human"
                or stage["id"] in {"setup", "category_setup"}
            )
        ]
        if invalid_batch_stages:
            raise ValueError(
                "複数工程へ含められないstageです: " + ", ".join(invalid_batch_stages)
            )
        return stages

    @staticmethod
    def _evaluation(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("workflow catalogの[evaluation]が不正です。")
        return {
            "id": "evaluation",
            "code": "評価",
            "label": "別セッション評価",
            "policyVersion": _policy_version(
                value.get("policy_version"),
                "evaluation.policy_version",
                required=True,
            ),
            "documents": [
                _document_path(path)
                for path in _string_list(
                    value.get("documents"), "evaluation.documents"
                )
            ],
            "inputs": [
                _artifact_path(path)
                for path in _string_list(value.get("inputs"), "evaluation.inputs")
            ],
        }
