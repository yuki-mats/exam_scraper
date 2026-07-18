from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import tomllib


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_REQUIREMENTS_PATH = ROOT_DIR / "config" / "requirements" / "required_fields.toml"


class RequirementsError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class ConditionalRule:
    when: dict[str, Any]
    when_not: dict[str, Any]
    required_keys: list[str]
    required_non_empty_keys: list[str]


@dataclasses.dataclass(frozen=True)
class StageRules:
    required_keys: list[str]
    required_non_empty_keys: list[str]
    required_any_of: list[list[str]]
    conditional: list[ConditionalRule]


def _as_list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    return [str(value)]


def _as_list_of_list_of_str(value: Any) -> list[list[str]]:
    if not value:
        return []
    if isinstance(value, list):
        out: list[list[str]] = []
        for item in value:
            if isinstance(item, list):
                out.append([str(v) for v in item if str(v)])
        return [group for group in out if group]
    return []


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RequirementsError(f"requirements TOML parse failed: {path} ({e})") from e


def load_requirements(path: Path | None = None) -> dict[str, Any]:
    requirements_path = (path or DEFAULT_REQUIREMENTS_PATH).resolve()
    if not requirements_path.exists():
        raise RequirementsError(f"requirements file not found: {requirements_path}")
    return _load_toml(requirements_path)


def _deep_get(mapping: dict[str, Any], keys: list[str]) -> Any:
    cur: Any = mapping
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _parse_stage_rules(raw: dict[str, Any], *, array_name: str) -> StageRules:
    required_keys = _as_list_of_str(raw.get(f"{array_name}_required_keys"))
    required_non_empty_keys = _as_list_of_str(raw.get(f"{array_name}_required_non_empty_keys"))
    required_any_of = _as_list_of_list_of_str(raw.get(f"{array_name}_required_any_of"))

    conditional: list[ConditionalRule] = []
    cond_raw = raw.get(f"{array_name}_conditional")
    if isinstance(cond_raw, list):
        for item in cond_raw:
            if not isinstance(item, dict):
                continue
            when = item.get("when")
            when_not = item.get("when_not")
            when = dict(when) if isinstance(when, dict) else {}
            when_not = dict(when_not) if isinstance(when_not, dict) else {}
            if not when and not when_not:
                continue
            conditional.append(
                ConditionalRule(
                    when=when,
                    when_not=when_not,
                    required_keys=_as_list_of_str(item.get("required_keys")),
                    required_non_empty_keys=_as_list_of_str(item.get("required_non_empty_keys")),
                )
            )

    return StageRules(
        required_keys=required_keys,
        required_non_empty_keys=required_non_empty_keys,
        required_any_of=required_any_of,
        conditional=conditional,
    )


def get_stage_rules(
    requirements: dict[str, Any],
    *,
    stage: str,
    record_array: str,
    qualification: str | None = None,
) -> StageRules:
    """
    stage: "source" | "merged" | "firestore"
    record_array: "question_bodies" | "questions"
    """
    default_stage = _deep_get(requirements, ["default", "stages", stage])
    if not isinstance(default_stage, dict):
        default_stage = {}

    qual_stage: dict[str, Any] = {}
    if qualification:
        q = _deep_get(requirements, ["qualification", qualification, "stages", stage])
        if isinstance(q, dict):
            qual_stage = q

    # 「資格側で上書きがない項目は default を使う」程度の浅いマージ
    merged_stage = dict(default_stage)
    merged_stage.update(qual_stage)

    return _parse_stage_rules(merged_stage, array_name=record_array)


def _is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list) or isinstance(value, dict):
        return bool(value)
    return True


def validate_records(
    *,
    records: list[dict[str, Any]],
    rules: StageRules,
    source_path: Path,
    id_keys: tuple[str, ...] = ("public_question_id", "original_question_id", "questionId"),
) -> list[str]:
    errors: list[str] = []

    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"{source_path}: record[{idx}] is not an object")
            continue

        record_id = None
        for key in id_keys:
            v = record.get(key)
            if _is_non_empty(v):
                record_id = str(v)
                break
        record_id = record_id or f"index_{idx}"

        for key in rules.required_keys:
            if key not in record or record.get(key) is None:
                errors.append(f"{source_path}: id={record_id} missing_required_key={key}")

        for key in rules.required_non_empty_keys:
            if key not in record or not _is_non_empty(record.get(key)):
                errors.append(f"{source_path}: id={record_id} empty_required_key={key}")

        for group in rules.required_any_of:
            if not any(_is_non_empty(record.get(k)) for k in group):
                joined = "|".join(group)
                errors.append(f"{source_path}: id={record_id} required_any_of_missing=({joined})")

        for cond in rules.conditional:
            matches_when = not cond.when or all(
                record.get(k) == v for k, v in cond.when.items()
            )
            matches_when_not = not cond.when_not or not all(
                record.get(k) == v for k, v in cond.when_not.items()
            )
            if matches_when and matches_when_not:
                condition = {
                    **({"when": cond.when} if cond.when else {}),
                    **({"when_not": cond.when_not} if cond.when_not else {}),
                }
                for key in cond.required_keys:
                    if key not in record or record.get(key) is None:
                        errors.append(
                            f"{source_path}: id={record_id} condition={condition} missing_required_key={key}"
                        )
                for key in cond.required_non_empty_keys:
                    if key not in record or not _is_non_empty(record.get(key)):
                        errors.append(
                            f"{source_path}: id={record_id} condition={condition} empty_required_key={key}"
                        )

    return errors
