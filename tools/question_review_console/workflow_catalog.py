from __future__ import annotations

import copy
import hashlib
import re
import threading
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "question_maintenance_workflow.toml"
)
STAGE_KINDS = {"source", "human", "machine"}
POLICY_VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


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


def normalize_policy_version(
    value: Any,
    field: str = "policyVersion",
    *,
    minimum_major: int = 0,
) -> str:
    """Return a MAJOR.MINOR string; legacy integers become MAJOR.0."""

    if isinstance(value, bool):
        raise ValueError(f"{field}はMAJOR.MINOR形式で指定してください。")
    if isinstance(value, int):
        major, minor = value, 0
    elif isinstance(value, str):
        match = POLICY_VERSION_PATTERN.fullmatch(value.strip())
        if match is None:
            raise ValueError(f"{field}はMAJOR.MINOR形式で指定してください。")
        major, minor = int(match.group(1)), int(match.group(2))
    else:
        raise ValueError(f"{field}はMAJOR.MINOR形式で指定してください。")
    if major < minimum_major:
        raise ValueError(
            f"{field}のメジャーは{minimum_major}以上で指定してください。"
        )
    return f"{major}.{minor}"


def policy_version_major(value: Any, field: str = "policyVersion") -> int:
    return int(normalize_policy_version(value, field).split(".", 1)[0])


def same_policy_major(left: Any, right: Any) -> bool:
    try:
        return policy_version_major(left) == policy_version_major(right)
    except ValueError:
        return False


def _policy_version(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    return normalize_policy_version(value, field, minimum_major=1)


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


def _update_targets(value: Any, stage_id: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{stage_id}.update_targetsは配列で指定してください。")
    targets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    writable_fields: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{stage_id}.update_targetsはtableで指定してください。")
        target_id = str(raw.get("id") or "")
        label = str(raw.get("label") or "").strip()
        fields = _string_list(raw.get("fields"), f"{stage_id}.{target_id}.fields")
        read_fields = _string_list(
            raw.get("read_fields"), f"{stage_id}.{target_id}.read_fields"
        )
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]*", target_id)
            or target_id in seen_ids
            or not label
            or not fields
        ):
            raise ValueError(
                f"{stage_id}.update_targetが不正又は重複しています: {target_id}"
            )
        invalid_fields = [
            field
            for field in [*fields, *read_fields]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field)
        ]
        if invalid_fields:
            raise ValueError(
                f"{stage_id}.{target_id}のfield名が不正です: "
                + ", ".join(invalid_fields)
            )
        if len(fields) != len(set(fields)) or len(read_fields) != len(set(read_fields)):
            raise ValueError(f"{stage_id}.{target_id}のfieldが重複しています。")
        overlap = writable_fields & set(fields)
        if overlap:
            raise ValueError(
                f"{stage_id}のupdate target間でfieldが重複しています: "
                + ", ".join(sorted(overlap))
            )
        writable_fields.update(fields)
        seen_ids.add(target_id)
        targets.append(
            {
                "id": target_id,
                "selectionId": f"{stage_id}.{target_id}",
                "label": label,
                "fields": fields,
                "readFields": read_fields,
            }
        )
    return targets


class WorkflowCatalog:
    """Loads the GUI workflow structure from its machine-readable SSOT."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self._last_good: dict[str, Any] | None = None
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        local = self.repo_root / "config" / "question_maintenance_workflow.toml"
        return local if local.is_file() else DEFAULT_CATALOG_PATH

    def load(self) -> dict[str, Any]:
        path = self.path
        raw: bytes | None = None
        with self._lock:
            try:
                raw = path.read_bytes()
                parsed = tomllib.loads(raw.decode("utf-8"))
                system = self._system(parsed.get("system"))
                session_groups = self._session_groups(parsed.get("session_groups"))
                stages = self._stages(parsed.get("stages"), session_groups)
                loaded = {
                    "system": system,
                    "sessionGroups": list(session_groups.values()),
                    "stages": stages,
                    "evaluation": self._evaluation(parsed.get("evaluation")),
                    "catalogHash": hashlib.sha256(raw).hexdigest(),
                    "catalogPath": str(path),
                    "catalogWarning": "",
                    "restartRequired": False,
                }
            except (OSError, UnicodeError, ValueError, KeyError) as exc:
                if self._last_good is None:
                    raise
                fallback = copy.deepcopy(self._last_good)
                fallback["catalogWarning"] = (
                    "workflow設定の更新を現在のサーバーで検証できないため、"
                    f"直前の正常な設定を維持しています: {exc}"
                )
                fallback["restartRequired"] = True
                fallback["pendingCatalogHash"] = (
                    hashlib.sha256(raw).hexdigest() if raw is not None else ""
                )
                return fallback
            self._last_good = copy.deepcopy(loaded)
            return copy.deepcopy(loaded)

    @staticmethod
    def _session_groups(value: Any) -> dict[str, dict[str, str]]:
        if value is None:
            return {}
        if not isinstance(value, list):
            raise ValueError("workflow session_groupsは配列で指定してください。")
        groups: dict[str, dict[str, str]] = {}
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ValueError("workflow session_groupはtableで指定してください。")
            group_id = str(raw.get("id") or "")
            label = str(raw.get("label") or "").strip()
            if (
                not re.fullmatch(r"[a-z][a-z0-9_]*", group_id)
                or group_id in groups
                or not label
            ):
                raise ValueError(
                    f"workflow session_groupが不正又は重複しています: {group_id}"
                )
            groups[group_id] = {"id": group_id, "label": label}
        return groups

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
    def _stages(
        value: Any,
        session_groups: Mapping[str, Mapping[str, str]],
    ) -> list[dict[str, Any]]:
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
            batch_selectable = bool(raw.get("batch_selectable", False))
            scope = str(
                raw.get("scope")
                or ("group" if batch_selectable else "qualification")
            )
            stage: dict[str, Any] = {
                "id": stage_id,
                "code": str(raw["code"]),
                "label": str(raw["label"]),
                "purpose": str(raw["purpose"]),
                "kind": kind,
                "batchSelectable": batch_selectable,
                "automatic": bool(raw.get("automatic", True)),
                "sessionGroup": str(raw.get("session_group") or ""),
                "scope": scope,
                "policyVersion": _policy_version(
                    raw.get("policy_version"),
                    f"{stage_id}.policy_version",
                    required=batch_selectable and scope == "group",
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
                "updateTargets": _update_targets(
                    raw.get("update_targets"), stage_id
                ),
            }
            if stage["scope"] not in {"qualification", "group"}:
                raise ValueError(
                    f"workflow scopeが不正です: {stage_id}={stage['scope']}"
                )
            stage["supportsGroupScope"] = stage["scope"] == "group"
            if stage["batchSelectable"] and not re.fullmatch(
                r"[a-z][a-z0-9_]*", stage["sessionGroup"]
            ):
                raise ValueError(
                    f"batch工程にはsession_groupが必要です: {stage_id}"
                )
            if not stage["batchSelectable"] and stage["sessionGroup"]:
                raise ValueError(
                    f"session_groupはbatch工程だけに指定できます: {stage_id}"
                )
            if stage["sessionGroup"] and stage["sessionGroup"] not in session_groups:
                raise ValueError(
                    f"未定義のsession_groupです: {stage_id}={stage['sessionGroup']}"
                )
            stage["sessionLabel"] = str(
                (session_groups.get(stage["sessionGroup"]) or {}).get("label") or ""
            )
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
                or stage["id"] == "setup"
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
