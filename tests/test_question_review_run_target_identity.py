from __future__ import annotations

import unittest

from tools.question_review_console.run_target_identity import (
    RunTargetIdentityError,
    RunTargetIdentityResolver,
    resolve_policy_target_ids,
    target_identity_aliases,
)


def _target(number: int, *aliases: str) -> dict[str, object]:
    return {
        "id": f"ui-q{number}",
        "uiQuestionId": f"ui-q{number}",
        "reviewQuestionId": "shared-review-id",
        "sourceQuestionKey": "sample:2026:shared",
        "sourceRecordRef": f"question_2026_{number}.json#0",
        "aliases": list(aliases),
    }


class RunTargetIdentityResolverTests(unittest.TestCase):
    def test_resolves_by_official_id_then_exact_binding(self) -> None:
        resolver = RunTargetIdentityResolver.from_sources(
            ("targets", [_target(1), _target(2)])
        )

        self.assertEqual(resolver.resolve("ui-q2")["id"], "ui-q2")
        self.assertEqual(
            resolver.resolve(
                {
                    "reviewQuestionId": "shared-review-id",
                    "sourceQuestionKey": "sample:2026:shared",
                    "sourceRecordRef": "question_2026_2.json#0",
                }
            )["id"],
            "ui-q2",
        )

    def test_record_alias_must_be_unique(self) -> None:
        unique = RunTargetIdentityResolver.from_sources(
            ("targets", [_target(1, "source-q1")])
        )
        ambiguous = RunTargetIdentityResolver.from_sources(
            (
                "targets",
                [_target(1, "shared-alias"), _target(2, "shared-alias")],
            )
        )

        self.assertEqual(unique.resolve("source-q1")["id"], "ui-q1")
        for value in ("shared-alias", "missing"):
            with self.subTest(value=value):
                with self.assertRaises(RunTargetIdentityError):
                    ambiguous.resolve(value)

    def test_rejects_duplicate_or_conflicting_official_id(self) -> None:
        first = _target(1)
        conflicting = {
            **first,
            "sourceRecordRef": "another.json#0",
        }
        cases = (
            (("targets", [first, first]),),
            (("progress", [first]), ("bindings", [conflicting])),
        )

        for sources in cases:
            with self.subTest(sources=sources):
                with self.assertRaises(RunTargetIdentityError):
                    RunTargetIdentityResolver.from_sources(*sources)

    def test_official_id_cannot_override_conflicting_complete_binding(self) -> None:
        resolver = RunTargetIdentityResolver.from_sources(
            ("targets", [_target(1)])
        )

        with self.assertRaises(RunTargetIdentityError):
            resolver.resolve(
                {
                    **_target(1),
                    "sourceRecordRef": "another.json#0",
                }
            )

    def test_policy_contract_counts_noncurrent_ids(self) -> None:
        targets = [_target(1, "shared"), _target(2, "shared")]

        resolved, invalid = resolve_policy_target_ids(
            targets,
            ["ui-q1", "shared", "missing"],
        )

        self.assertEqual(resolved, {"ui-q1"})
        self.assertEqual(invalid, 2)
        self.assertEqual(resolve_policy_target_ids(targets, "ui-q1"), (set(), 1))

    def test_aliases_include_nested_source_identity(self) -> None:
        aliases = target_identity_aliases(
            {
                "id": "ui-q1",
                "source": {
                    "originalQuestionId": "source-q1",
                    "sourceQuestionKey": "sample:2026:q1",
                },
            }
        )

        self.assertTrue({"ui-q1", "source-q1", "sample:2026:q1"} <= aliases)


if __name__ == "__main__":
    unittest.main()
