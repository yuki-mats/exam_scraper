from scripts.check.audit_2nd_class_kenchikushi_law_explanation_quality import (
    EXPECTED_CANDIDATE_ALIAS_COUNTS,
    EXPECTED_ENTRY_COUNT,
)


def test_expected_entry_count_is_stable() -> None:
    assert EXPECTED_ENTRY_COUNT == 256


def test_expected_candidate_alias_counts_are_stable() -> None:
    assert EXPECTED_CANDIDATE_ALIAS_COUNTS == {}
