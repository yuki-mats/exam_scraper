from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check/build_gas_shunin_official_pdf_index.py"
SPEC = importlib.util.spec_from_file_location("gas_pdf_index", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_parse_pdf_identity() -> None:
    assert MODULE.parse_pdf_identity(Path("2023/q_otsu_r5.pdf")) == (2023, "otsu")


def test_parse_answer_pdf_identity() -> None:
    assert MODULE.parse_answer_pdf_identity(Path("2025/a_kou_R7.pdf")) == (2025, "kou")


def test_parse_header_candidates_handles_explicit_and_generic() -> None:
    assert MODULE.parse_header_candidates("（法）問 １ 本文\n問2 次の記述\n問番号") == [
        ("law", 1),
        (None, 2),
    ]


def test_build_header_index_advances_sections_after_expected_counts() -> None:
    pages = []
    page = 3
    for section, count in MODULE.EXPECTED_BY_SECTION.items():
        for number in range(1, count + 1):
            prefix = {"law": "（法）", "basic": "（基）", "gas": "（ガ）"}[section]
            pages.append({"pdfPage": page, "text": f"{prefix}問{number} 本文"})
            page += 1
    index = MODULE.build_header_index({"pages": pages})
    assert len(index) == 58
    assert index[("law", 1)] == 3
    assert index[("basic", 1)] == 19
    assert index[("gas", 27)] == 60


def test_coverage_score_prefers_matching_page() -> None:
    needle = MODULE.normalize_text("家庭用ガス機器に関する記述。セミ・ブンゼンバーナー")
    matching = MODULE.normalize_text("問21 家庭用ガス機器に関する記述。セミ・ブンゼンバーナー")
    other = MODULE.normalize_text("問5 都市ガスの付臭と活性炭に関する記述")
    assert MODULE.coverage_score(needle, matching) > MODULE.coverage_score(needle, other)


def test_canonical_identity_uses_question_id_and_exam_source() -> None:
    audit = {
        "questionId": "gas-shunin-kou-2024-kyokyu-q10-s02",
        "grade": "甲種",
        "examYear": 2024,
        "sourceMatch": {"status": "unmapped"},
    }
    decoded = {"examSource": "ガス主任技術者（甲種）, 2024年, 問10"}
    assert MODULE.canonical_identity(audit, decoded) == ("kou", 2024, "gas", 10)


def test_extract_answer_rows_handles_spaced_and_joined_digits() -> None:
    text = "\n".join(
        [
            "正 解 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1",
            "正  解 123451234512345",
            "正 解 1 2 3 4 5 1 2 3 4",
            "正 解 5 4 3 2 1 5 4 3 2",
            "正 解 1 1 2 2 3 3 4 4 5",
        ]
    )
    rows = MODULE.extract_answer_rows(text)
    assert tuple(len(row) for row in rows) == MODULE.ANSWER_ROW_LENGTHS
    assert rows[1] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2, 3, 4, 5]


def test_scanned_answer_rows_are_complete_and_in_range() -> None:
    assert set(MODULE.SCANNED_ANSWER_ROWS) == {
        (2020, "kou"),
        (2020, "otsu"),
        (2022, "kou"),
        (2022, "otsu"),
    }
    for rows in MODULE.SCANNED_ANSWER_ROWS.values():
        assert tuple(len(row) for row in rows) == MODULE.ANSWER_ROW_LENGTHS
        assert all(1 <= answer <= 5 for row in rows for answer in row)


def test_parse_official_question_header_ignores_range_and_handles_ocr_variants() -> None:
    seen = {("law", number) for number in range(1, 17)}
    assert MODULE.parse_official_question_header("（ガ）問1～（ガ）問9", "gas", seen) is None
    assert MODULE.parse_official_question_header("（問1 本文", "gas", seen) == ("gas", 1)
    assert MODULE.parse_official_question_header("（分）問16 本文", "gas", seen) == ("gas", 16)


def test_document_identity_overrides_have_complete_identity_and_page() -> None:
    assert len(MODULE.DOCUMENT_IDENTITY_OVERRIDES) == 25
    for override in MODULE.DOCUMENT_IDENTITY_OVERRIDES.values():
        assert len(override["identity"]) == 4
        assert override["identity"][2] in MODULE.EXPECTED_BY_SECTION
        assert override["pdfPage"] >= 3


def test_display_question_evidence_prefers_rendered_body_and_quote() -> None:
    live = {
        "questionText": "2022年の表示本文[quote]表示選択肢[/quote]",
        "originalQuestionBodyText": "誤って残った2021年本文",
        "originalQuestionChoiceText": "古い選択肢",
    }
    body, choice = MODULE.display_question_evidence(live)
    assert body == MODULE.normalize_text("2022年の表示本文")
    assert choice == MODULE.normalize_text("表示選択肢")


def test_ranked_block_scores_can_compare_other_years_in_same_grade() -> None:
    evidence = MODULE.ngrams(MODULE.normalize_text("2022年だけの問題文と選択肢"))
    candidates = [
        (("kou", 2021, "law", 1), MODULE.normalize_text("2021年だけの別問題")),
        (("kou", 2022, "law", 1), MODULE.normalize_text("2022年だけの問題文と選択肢")),
    ]
    assert MODULE.ranked_block_scores(evidence, candidates)[-1][1] == (
        "kou",
        2022,
        "law",
        1,
    )
