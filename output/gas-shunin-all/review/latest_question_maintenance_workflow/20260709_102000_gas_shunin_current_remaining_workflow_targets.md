# ガス主任技術者 過去問整備ワークフロー 残対象洗い出し

- generatedAt: 2026-07-09T10:20:00+09:00
- source secondary queue: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_023000_gas_shunin_secondary_law_review_queue.jsonl`
- source maintenance queue: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_000305_gas_shunin_latest_workflow_queue.jsonl`
- machine-readable remaining targets: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_102000_gas_shunin_current_remaining_workflow_targets.jsonl`

## 前提

- `00_source` は変更しない。
- 既存 Firestore document ID は保持する。
- Lawzilla MCP は並列 evidence として使い、最終的な正答・解説・条文紐付けは一次法令と手動二次確認で確定する。
- 実行単位は `queueSequence` の行単位。同一 queue item key は5種類残るが、Firestore分割や別本文の可能性があるため自動統合しない。

## 集計

- 初期対象レコード: 387 件
- 初期対象の一意 queue item key: 1 件
- 二次検証済みレコード: 22 件
- 残対象レコード: 365 件
- 残対象の一意 queue item key: 1 件
- 残対象の重複 queue item key: 1 種類 / 追加行 364 件

### 残対象 Priority

| priority | count |
| --- | ---: |
| P0 | 239 |
| P1 | 40 |
| P2 | 86 |

### 残対象 Readiness

| readiness | count |
| --- | ---: |
| `manual_review_required_all_choices_have_primary_evidence` | 235 |
| `non_lawzilla_workflow_item` | 80 |
| `manual_review_required_partial_candidates` | 43 |
| `answer_recheck_without_lawzilla_evidence` | 6 |
| `manual_review_required_locator_detail` | 1 |

### 残対象 Track

| track | count |
| --- | ---: |
| `lawzilla_law_evidence` | 279 |
| `source_conflict_review` | 132 |
| `answer_recheck` | 28 |

### 年度・優先度別

| qualification | year | P0 | P1 | P2 | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gas-shunin-kou` | 2019 | 16 | 2 | 0 | 18 |
| `gas-shunin-kou` | 2020 | 16 | 1 | 30 | 47 |
| `gas-shunin-kou` | 2021 | 18 | 4 | 15 | 37 |
| `gas-shunin-kou` | 2022 | 17 | 5 | 14 | 36 |
| `gas-shunin-kou` | 2023 | 18 | 2 | 17 | 37 |
| `gas-shunin-kou` | 2024 | 10 | 2 | 1 | 13 |
| `gas-shunin-kou` | 2025 | 0 | 1 | 5 | 6 |
| `gas-shunin-otsu` | 2017 | 16 | 1 | 0 | 17 |
| `gas-shunin-otsu` | 2018 | 16 | 1 | 0 | 17 |
| `gas-shunin-otsu` | 2019 | 16 | 0 | 0 | 16 |
| `gas-shunin-otsu` | 2020 | 16 | 5 | 2 | 23 |
| `gas-shunin-otsu` | 2021 | 16 | 4 | 0 | 20 |
| `gas-shunin-otsu` | 2022 | 16 | 3 | 2 | 21 |
| `gas-shunin-otsu` | 2023 | 16 | 3 | 0 | 19 |
| `gas-shunin-otsu` | 2024 | 16 | 4 | 0 | 20 |
| `gas-shunin-otsu` | 2025 | 16 | 2 | 0 | 18 |

### 残る重複 queue item key

| queueItemKey | count | queueSequence |
| --- | ---: | --- |
| `None` | 365 | 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250, 251, 252, 253, 254, 255, 256, 257, 258, 259, 260, 261, 262, 263, 264, 265, 266, 267, 268, 269, 270, 271, 272, 273, 274, 275, 276, 277, 278, 279, 280, 281, 282, 283, 284, 285, 286, 287, 288, 289, 290, 291, 292, 293, 294, 295, 296, 297, 298, 299, 300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 338, 339, 340, 341, 342, 343, 344, 345, 346, 347, 348, 349, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 361, 362, 363, 364, 365, 366, 367, 368, 369, 370, 371, 372, 373, 374, 375, 376, 377, 378, 379, 380, 381, 382, 383, 384, 385, 386, 387 |

## 次の着手順 Top 40

| seq | priority | readiness | qualification | year | label | displayQuestionId | tracks |
| ---: | --- | --- | --- | ---: | --- | --- | --- |
| 23 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問10 | `db637cee87824d57` | lawzilla_law_evidence |
| 24 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2024 | 問9 | `fd9364f45fe29acc` | lawzilla_law_evidence |
| 25 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問8 | `d7367850b0588b72` | lawzilla_law_evidence |
| 26 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問7 | `ffc0cd209ba5b141` | lawzilla_law_evidence |
| 27 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問6 | `cdb9cad7080f3d37` | lawzilla_law_evidence |
| 28 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問5 | `7b372e50d597c363` | lawzilla_law_evidence |
| 29 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問4 | `b312de5198cdf4bf` | lawzilla_law_evidence |
| 30 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問3 | `c8af1e8b0c970ab1` | lawzilla_law_evidence |
| 31 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問2 | `677edcd7d7120dcd` | lawzilla_law_evidence |
| 32 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2024 | 問1 | `f72eb811cf645592` | lawzilla_law_evidence |
| 33 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問16 | `gasushunin-koushu-hourei-2023-16` | lawzilla_law_evidence |
| 34 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問15 | `gasushunin-koushu-hourei-2023-15` | lawzilla_law_evidence |
| 35 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問14 | `gasushunin-koushu-hourei-2023-14` | lawzilla_law_evidence |
| 36 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問13 | `gasushunin-koushu-hourei-2023-13` | lawzilla_law_evidence |
| 37 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問12 | `gasushunin-koushu-hourei-2023-12` | lawzilla_law_evidence<br>source_conflict_review |
| 38 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問11 | `gasushunin-koushu-hourei-2023-11` | lawzilla_law_evidence<br>source_conflict_review |
| 39 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問10 | `gasushunin-koushu-hourei-2023-10` | lawzilla_law_evidence<br>source_conflict_review |
| 40 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問9 | `gasushunin-koushu-hourei-2023-9` | lawzilla_law_evidence |
| 41 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問8 | `gasushunin-koushu-hourei-2023-8` | lawzilla_law_evidence<br>source_conflict_review |
| 42 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問7 | `gasushunin-koushu-hourei-2023-7` | lawzilla_law_evidence<br>source_conflict_review |
| 43 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問7 | `gasushunin-koushu-hourei-2023-7` | lawzilla_law_evidence<br>source_conflict_review |
| 44 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問6 | `gasushunin-koushu-hourei-2023-6` | lawzilla_law_evidence |
| 45 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問5 | `gasushunin-koushu-hourei-2023-5` | lawzilla_law_evidence |
| 46 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問4 | `gasushunin-koushu-hourei-2023-4` | lawzilla_law_evidence<br>source_conflict_review |
| 47 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問3 | `gasushunin-koushu-hourei-2023-3` | lawzilla_law_evidence |
| 48 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問2 | `gasushunin-koushu-hourei-2023-2` | lawzilla_law_evidence<br>source_conflict_review |
| 49 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2023 | 問2 | `a2493f72a866d905` | lawzilla_law_evidence<br>source_conflict_review |
| 50 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2023 | 問1 | `bc75f5a14a1a330c` | lawzilla_law_evidence |
| 51 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問16 | `gasushunin-koushu-hourei-2022-16` | lawzilla_law_evidence<br>source_conflict_review |
| 52 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問15 | `gasushunin-koushu-hourei-2022-15` | lawzilla_law_evidence |
| 53 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問14 | `gasushunin-koushu-hourei-2022-14` | lawzilla_law_evidence |
| 54 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問13 | `gasushunin-koushu-hourei-2022-13` | lawzilla_law_evidence<br>source_conflict_review |
| 55 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問12 | `gasushunin-koushu-hourei-2022-12` | lawzilla_law_evidence |
| 56 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問11 | `gasushunin-koushu-hourei-2022-11` | lawzilla_law_evidence<br>source_conflict_review |
| 57 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問10 | `gasushunin-koushu-hourei-2022-10` | lawzilla_law_evidence |
| 58 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問9 | `gasushunin-koushu-hourei-2022-9` | lawzilla_law_evidence |
| 59 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問8 | `gasushunin-koushu-hourei-2022-8` | lawzilla_law_evidence<br>source_conflict_review |
| 60 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問7 | `gasushunin-koushu-hourei-2022-7` | lawzilla_law_evidence<br>source_conflict_review |
| 61 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問6 | `gasushunin-koushu-hourei-2022-6` | lawzilla_law_evidence<br>source_conflict_review |
| 62 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問5 | `gasushunin-koushu-hourei-2022-5` | lawzilla_law_evidence |

## 実行単位

1. P0: 法令問題。Lawzilla候補、既存条文検索、e-Gov一次条文を突合し、`lawReferences` と `lawRevisionFacts` を確定する。
2. P1: 法令カテゴリ外だが法令語彙あり。法令根拠が正誤に効くものだけP0相当に昇格する。
3. P2: source conflict / count-answer / combo-answer。条文より先に正答・source整合を確認する。
