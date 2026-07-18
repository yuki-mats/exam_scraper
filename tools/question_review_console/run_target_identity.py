from __future__ import annotations

from typing import Any, Mapping

from scripts.common.question_identity import (
    SourceIdentityBinding,
    source_identity_aliases,
    workflow_identity_aliases,
)


class RunTargetIdentityError(ValueError):
    pass


def target_identity_aliases(value: Mapping[str, Any]) -> set[str]:
    binding = SourceIdentityBinding.from_mapping(value)
    aliases = {
        str(alias).strip()
        for alias in (
            *(value.get("aliases") or []),
            value.get("id"),
            value.get("uiQuestionId"),
            value.get("questionKey"),
            value.get("reviewKey"),
            *binding.as_tuple(),
        )
        if str(alias or "").strip()
    }
    for key in ("source", "projected"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            aliases.update(source_identity_aliases(nested))
            aliases.update(workflow_identity_aliases(nested))
    return aliases


class RunTargetIdentityResolver:
    """Resolve one run target without alias overwrite or first-wins joins."""

    def __init__(self, targets: list[dict[str, Any]]):
        self.targets = tuple(targets)

    @classmethod
    def from_sources(
        cls,
        *sources: tuple[str, Any],
    ) -> "RunTargetIdentityResolver":
        merged: dict[str, dict[str, Any]] = {}
        for label, raw_values in sources:
            seen: set[str] = set()
            for raw in raw_values or []:
                if not isinstance(raw, Mapping):
                    continue
                target = dict(raw)
                target_id = cls.official_id(target)
                if not target_id:
                    raise RunTargetIdentityError(
                        f"{label}に正式ui IDがありません。"
                    )
                ui_question_id = str(
                    target.get("uiQuestionId") or target_id
                ).strip()
                explicit_id = str(target.get("id") or "").strip()
                if explicit_id and ui_question_id != explicit_id:
                    raise RunTargetIdentityError(
                        f"{label}のidとuiQuestionIdが一致しません: {target_id}"
                    )
                if target_id in seen:
                    raise RunTargetIdentityError(
                        f"{label}の正式ui IDが重複しています: {target_id}"
                    )
                seen.add(target_id)
                target["id"] = target_id
                target["uiQuestionId"] = ui_question_id
                current = merged.get(target_id)
                if current is None:
                    merged[target_id] = target
                    continue
                binding = _merge_binding(
                    target_id,
                    SourceIdentityBinding.from_mapping(current),
                    SourceIdentityBinding.from_mapping(target),
                )
                merged[target_id] = {
                    **current,
                    **{
                        key: value
                        for key, value in target.items()
                        if value not in (None, "", [], {})
                    },
                    "id": target_id,
                    "uiQuestionId": target_id,
                    **binding.as_mapping(),
                    "aliases": sorted(
                        target_identity_aliases(current)
                        | target_identity_aliases(target)
                    ),
                }
        return cls(list(merged.values()))

    @staticmethod
    def official_id(value: Mapping[str, Any]) -> str:
        return str(value.get("id") or value.get("uiQuestionId") or "").strip()

    def resolve(self, value: Any) -> Mapping[str, Any]:
        query = dict(value) if isinstance(value, Mapping) else None
        raw_value = str(value or "").strip() if query is None else ""
        official_values = (
            {
                str(query.get("id") or "").strip(),
                str(query.get("uiQuestionId") or "").strip(),
            }
            - {""}
            if query is not None
            else {raw_value} - {""}
        )
        official = [
            target
            for target in self.targets
            if official_values
            & {
                self.official_id(target),
                str(target.get("uiQuestionId") or "").strip(),
            }
        ]
        query_binding = (
            SourceIdentityBinding.from_mapping(query)
            if query is not None
            else SourceIdentityBinding.from_values("", "", "")
        )
        if len(official) == 1:
            target_binding = SourceIdentityBinding.from_mapping(official[0])
            if (
                query_binding.is_complete()
                and target_binding.is_complete()
                and query_binding != target_binding
            ):
                raise RunTargetIdentityError(
                    "正式ui IDとsource identity bindingが一致しません。"
                )
            return official[0]
        if len(official) > 1:
            raise RunTargetIdentityError(
                "正式ui IDが複数targetに一致します。"
            )

        exact = self._binding_matches(query_binding, raw_value)
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise RunTargetIdentityError(
                "source identity bindingが複数targetに一致します。"
            )

        query_aliases = (
            target_identity_aliases(query) if query is not None else {raw_value}
        )
        alias_matches = [
            target
            for target in self.targets
            if query_aliases & target_identity_aliases(target)
        ]
        if len(alias_matches) == 1:
            return alias_matches[0]
        if len(alias_matches) > 1:
            raise RunTargetIdentityError(
                "aliasが複数targetに一致するため"
                "一意に解決できません。"
            )
        raise RunTargetIdentityError("policy targetを解決できません。")

    def _binding_matches(
        self,
        binding: SourceIdentityBinding,
        raw_value: str,
    ) -> list[dict[str, Any]]:
        if binding.is_complete():
            return [
                target
                for target in self.targets
                if SourceIdentityBinding.from_mapping(target) == binding
            ]
        if not raw_value:
            return []
        return [
            target
            for target in self.targets
            if SourceIdentityBinding.from_mapping(target).is_complete()
            and SourceIdentityBinding.from_mapping(target).source_record_ref
            == raw_value
        ]


def resolve_policy_target_ids(
    targets: list[Mapping[str, Any]],
    raw_values: Any,
) -> tuple[set[str], int]:
    """Accept only current run target IDs in the policy contract."""

    resolved: set[str] = set()
    if not isinstance(raw_values, list):
        return resolved, 1
    try:
        resolver = RunTargetIdentityResolver.from_sources(
            ("progressTargets", targets)
        )
    except RunTargetIdentityError:
        return resolved, max(1, len(raw_values))
    official_ids = {
        resolver.official_id(target)
        for target in resolver.targets
    }
    invalid_count = 0
    for raw_value in raw_values:
        target_id = str(raw_value).strip() if isinstance(raw_value, str) else ""
        if target_id not in official_ids:
            invalid_count += 1
            continue
        resolved.add(target_id)
    return resolved, invalid_count


def _merge_binding(
    target_id: str,
    current: SourceIdentityBinding,
    incoming: SourceIdentityBinding,
) -> SourceIdentityBinding:
    values: list[str] = []
    for current_value, incoming_value in zip(
        current.as_tuple(), incoming.as_tuple()
    ):
        if (
            current_value
            and incoming_value
            and current_value != incoming_value
        ):
            raise RunTargetIdentityError(
                "同じui IDのsource identity bindingが一致しません: "
                f"{target_id}"
            )
        values.append(current_value or incoming_value)
    return SourceIdentityBinding.from_values(*values)
