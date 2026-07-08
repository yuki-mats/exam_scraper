# ガス主任技術者 過去問整備ワークフロー 残対象洗い出し

- generatedAt: 2026-07-09T21:00:00+09:00
- source secondary queue: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_023000_gas_shunin_secondary_law_review_queue.jsonl`
- source maintenance queue: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_000305_gas_shunin_latest_workflow_queue.jsonl`
- machine-readable remaining targets: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_210000_gas_shunin_current_remaining_workflow_targets.jsonl`

## 前提

- `00_source` は変更しない。
- 既存 Firestore document ID は保持する。
- Lawzilla MCP は並列 evidence として使い、最終的な正答・解説・条文紐付けは一次法令と手動二次確認で確定する。
- 実行単位は `queueSequence` の行単位。同一 queue item key は残るが、Firestore分割や別本文の可能性があるため自動統合しない。

## 集計

- 初期対象レコード: 387 件
- 初期対象の一意 queue item key: 330 件
- 二次検証済みレコード: 53 件
- 残対象レコード: 334 件
- 残対象の一意 queue item key: 289 件
- 残対象の重複 queue item key: 35 種類 / 追加行 45 件

### 残対象 Priority

| priority | count |
| --- | ---: |
| P0 | 208 |
| P1 | 40 |
| P2 | 86 |

### 残対象 Readiness

| readiness | count |
| --- | ---: |
| `answer_recheck_without_lawzilla_evidence` | 6 |
| `manual_review_required_all_choices_have_primary_evidence` | 206 |
| `manual_review_required_locator_detail` | 1 |
| `manual_review_required_partial_candidates` | 41 |
| `non_lawzilla_workflow_item` | 80 |

### 残対象 Track

| track | count |
| --- | ---: |
| `answer_recheck` | 28 |
| `lawzilla_law_evidence` | 248 |
| `source_conflict_review` | 122 |

### 年度・優先度別

| qualification | year | P0 | P1 | P2 | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gas-shunin-kou` | 2019 | 16 | 2 | 0 | 18 |
| `gas-shunin-kou` | 2020 | 16 | 1 | 30 | 47 |
| `gas-shunin-kou` | 2021 | 18 | 4 | 15 | 37 |
| `gas-shunin-kou` | 2022 | 14 | 5 | 14 | 33 |
| `gas-shunin-kou` | 2023 | 0 | 2 | 17 | 19 |
| `gas-shunin-kou` | 2024 | 0 | 2 | 1 | 3 |
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
| `gas-shunin-kou:2019:問4:` | 2 | 114, 278 |
| `gas-shunin-kou:2019:問7:` | 2 | 111, 277 |
| `gas-shunin-kou:2020:問11:` | 3 | 91, 372, 373 |
| `gas-shunin-kou:2020:問12:` | 3 | 90, 370, 371 |
| `gas-shunin-kou:2020:問13:` | 2 | 89, 276 |
| `gas-shunin-kou:2020:問14:` | 4 | 88, 367, 368, 369 |
| `gas-shunin-kou:2020:問15:` | 2 | 87, 366 |
| `gas-shunin-kou:2020:問16:` | 2 | 86, 365 |
| `gas-shunin-kou:2020:問1:` | 2 | 101, 383 |
| `gas-shunin-kou:2020:問2:` | 2 | 100, 382 |
| `gas-shunin-kou:2020:問3:` | 3 | 99, 380, 381 |
| `gas-shunin-kou:2020:問4:` | 2 | 98, 379 |
| `gas-shunin-kou:2020:問5:` | 2 | 97, 378 |
| `gas-shunin-kou:2020:問6:` | 2 | 96, 377 |
| `gas-shunin-kou:2020:問7:` | 2 | 95, 376 |
| `gas-shunin-kou:2020:問8:` | 2 | 94, 375 |
| `gas-shunin-kou:2020:問9:` | 2 | 93, 374 |
| `gas-shunin-kou:2021:問11:` | 2 | 73, 348 |
| `gas-shunin-kou:2021:問12:` | 2 | 72, 347 |
| `gas-shunin-kou:2021:問14:` | 3 | 70, 345, 346 |
| `gas-shunin-kou:2021:問15:` | 2 | 69, 344 |
| `gas-shunin-kou:2021:問1:` | 3 | 84, 85, 353 |
| `gas-shunin-kou:2021:問2:` | 3 | 82, 83, 352 |
| `gas-shunin-kou:2021:問4:` | 3 | 80, 275, 351 |
| `gas-shunin-kou:2021:問5:` | 2 | 79, 350 |
| `gas-shunin-kou:2021:問7:` | 2 | 77, 274 |
| `gas-shunin-kou:2021:問8:` | 2 | 76, 349 |
| `gas-shunin-kou:2022:問1:` | 2 | 67, 338 |
| `gas-shunin-kou:2022:問2:` | 3 | 65, 66, 337 |
| `gas-shunin-kou:2022:問3:` | 2 | 64, 336 |
| `gas-shunin-kou:2022:問4:` | 2 | 63, 271 |
| `gas-shunin-kou:2022:問6:` | 2 | 61, 270 |
| `gas-shunin-kou:2022:問7:` | 2 | 60, 269 |
| `gas-shunin-kou:2022:問8:` | 2 | 59, 335 |
| `gas-shunin-kou:2023:問15:` | 2 | 265, 318 |

### 次の対象（先頭20件）

| seq | priority | readiness | qualification | year | label | id | correctChoiceText | tracks |
| ---: | --- | --- | --- | ---: | --- | --- | --- | --- |
| 54 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問13 | `gasushunin-koushu-hourei-2022-13` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 55 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問12 | `gasushunin-koushu-hourei-2022-12` | `None` | lawzilla_law_evidence |
| 56 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問11 | `gasushunin-koushu-hourei-2022-11` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 57 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問10 | `gasushunin-koushu-hourei-2022-10` | `None` | lawzilla_law_evidence |
| 58 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問9 | `gasushunin-koushu-hourei-2022-9` | `None` | lawzilla_law_evidence |
| 59 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問8 | `gasushunin-koushu-hourei-2022-8` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 60 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問7 | `gasushunin-koushu-hourei-2022-7` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 61 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問6 | `gasushunin-koushu-hourei-2022-6` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 62 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問5 | `gasushunin-koushu-hourei-2022-5` | `None` | lawzilla_law_evidence |
| 63 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2022 | 問4 | `gasushunin-koushu-hourei-2022-4` | `None` | lawzilla_law_evidence |
| 64 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問3 | `gasushunin-koushu-hourei-2022-3` | `None` | lawzilla_law_evidence |
| 65 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問2 | `gasushunin-koushu-hourei-2022-2` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 66 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問2 | `gasushunin-koushu-hourei-2022-2` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 67 | P0 | `manual_review_required_partial_candidates` | `gas-shunin-kou` | 2022 | 問1 | `gasushunin-koushu-hourei-2022-1` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 68 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問16 | `gasushunin-koushu-hourei-2021-16` | `None` | lawzilla_law_evidence |
| 69 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問15 | `gasushunin-koushu-hourei-2021-15` | `None` | lawzilla_law_evidence |
| 70 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問14 | `gasushunin-koushu-hourei-2021-14` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 71 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問13 | `gasushunin-koushu-hourei-2021-13` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 72 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問12 | `gasushunin-koushu-hourei-2021-12` | `None` | lawzilla_law_evidence<br>source_conflict_review |
| 73 | P0 | `manual_review_required_all_choices_have_primary_evidence` | `gas-shunin-kou` | 2021 | 問11 | `gasushunin-koushu-hourei-2021-11` | `None` | lawzilla_law_evidence<br>source_conflict_review |
