from scripts.check.audit_gas_shunin_otsu_law_reference_quality import (
    EXPECTED_NON_VERIFIED_COUNTS,
    classify_non_verified,
)


def test_classify_non_verified_marks_missing_law_id_first() -> None:
    assert (
        classify_non_verified(
            {
                "verificationStatus": "candidate",
                "lawAlias": "技告示",
                "reason": "技告示3条三号",
            }
        )
        == "missing_law_id"
    )


def test_classify_non_verified_marks_context_inference() -> None:
    assert (
        classify_non_verified(
            {
                "verificationStatus": "candidate",
                "lawId": "329AC0000000051",
                "reason": "1項（設問文脈から 法64条 を補完）",
            }
        )
        == "context_inference"
    )


def test_expected_non_verified_inventory_is_stable() -> None:
    assert EXPECTED_NON_VERIFIED_COUNTS == {
        "ceeb1f8297cda1cd": 5,
        "2c627ceffe62a06d": 1,
        "be16e16dc9ec58c3": 1,
        "85f05cbb8ce93d82": 1,
        "13088ed044a86680": 5,
        "4e2e4af72d3c96aa": 4,
        "606300689177531c": 5,
        "f1a7b8ecb4f3315f": 7,
    }
