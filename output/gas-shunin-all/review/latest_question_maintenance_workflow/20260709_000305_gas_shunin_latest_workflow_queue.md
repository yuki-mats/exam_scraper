# ガス主任技術者 最新過去問整備ワークフロー実行キュー

- generatedAt: 2026-07-09T00:03:05+09:00
- source target report: `output/gas-shunin-all/review/lawzilla_mcp_target_discovery/20260708_235625_gas_shunin_lawzilla_workflow_targets.jsonl`
- queue: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_000305_gas_shunin_latest_workflow_queue.jsonl`
- summary: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_000305_gas_shunin_latest_workflow_queue_summary.json`
- first batch: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_000305_gas_shunin_latest_workflow_batch_P0_first_32.json`

## 集計

| priority | count |
| --- | ---: |
| P0 | 261 |
| P1 | 40 |
| P2 | 86 |

## P0 年度別

| qualification:year | count |
| --- | ---: |
| `gas-shunin-kou:2019` | 16 |
| `gas-shunin-kou:2020` | 16 |
| `gas-shunin-kou:2021` | 18 |
| `gas-shunin-kou:2022` | 17 |
| `gas-shunin-kou:2023` | 18 |
| `gas-shunin-kou:2024` | 16 |
| `gas-shunin-kou:2025` | 16 |
| `gas-shunin-otsu:2017` | 16 |
| `gas-shunin-otsu:2018` | 16 |
| `gas-shunin-otsu:2019` | 16 |
| `gas-shunin-otsu:2020` | 16 |
| `gas-shunin-otsu:2021` | 16 |
| `gas-shunin-otsu:2022` | 16 |
| `gas-shunin-otsu:2023` | 16 |
| `gas-shunin-otsu:2024` | 16 |
| `gas-shunin-otsu:2025` | 16 |

## 実行境界

- `00_source` は変更しない。
- 既存 Firestore document ID は変更しない。
- Lawzilla MCP は並列 evidence であり、単独で正答・現行法更新を確定しない。
- `updated_to_current_law` は三次確定後のみ公開確定する。
- source conflict / answer recheck がある項目は、Lawzilla より先に source と正答 mapping を確認する。

## First batch

| seq | priority | qualification | year | label | id | tracks |
| ---: | --- | --- | ---: | --- | --- | --- |
| 1 | P0 | `gas-shunin-kou` | 2025 | 問16 | `bd05d02105026f15` | lawzilla_law_evidence |
| 2 | P0 | `gas-shunin-kou` | 2025 | 問15 | `7bd905381415c9b4` | lawzilla_law_evidence |
| 3 | P0 | `gas-shunin-kou` | 2025 | 問14 | `f73181ec7b58db9a` | lawzilla_law_evidence |
| 4 | P0 | `gas-shunin-kou` | 2025 | 問13 | `b366a2fcd28be935` | lawzilla_law_evidence |
| 5 | P0 | `gas-shunin-kou` | 2025 | 問12 | `02995d8bb53f6d42` | lawzilla_law_evidence |
| 6 | P0 | `gas-shunin-kou` | 2025 | 問11 | `e9fa6e51658d6e1f` | lawzilla_law_evidence |
| 7 | P0 | `gas-shunin-kou` | 2025 | 問10 | `cd086d1aeb2029f7` | lawzilla_law_evidence |
| 8 | P0 | `gas-shunin-kou` | 2025 | 問9 | `c451b45f04b5d97f` | lawzilla_law_evidence |
| 9 | P0 | `gas-shunin-kou` | 2025 | 問8 | `ca6ffbbb1cc30192` | lawzilla_law_evidence |
| 10 | P0 | `gas-shunin-kou` | 2025 | 問7 | `277819d0f657d22a` | lawzilla_law_evidence |
| 11 | P0 | `gas-shunin-kou` | 2025 | 問6 | `4a1f8f37df1b07b4` | lawzilla_law_evidence |
| 12 | P0 | `gas-shunin-kou` | 2025 | 問5 | `270520054cd311ea` | lawzilla_law_evidence |
| 13 | P0 | `gas-shunin-kou` | 2025 | 問4 | `bf1f4414f56731a4` | lawzilla_law_evidence |
| 14 | P0 | `gas-shunin-kou` | 2025 | 問3 | `813f596b05bf2267` | lawzilla_law_evidence |
| 15 | P0 | `gas-shunin-kou` | 2025 | 問2 | `b85f33d43566b1fe` | lawzilla_law_evidence |
| 16 | P0 | `gas-shunin-kou` | 2025 | 問1 | `675ce6f3db722a24` | lawzilla_law_evidence |
| 17 | P0 | `gas-shunin-kou` | 2024 | 問16 | `8254b1976c93db27` | lawzilla_law_evidence |
| 18 | P0 | `gas-shunin-kou` | 2024 | 問15 | `9629e0f0e30a9ba6` | lawzilla_law_evidence |
| 19 | P0 | `gas-shunin-kou` | 2024 | 問14 | `a42e5bc2a9c012a5` | lawzilla_law_evidence |
| 20 | P0 | `gas-shunin-kou` | 2024 | 問13 | `5d3de50bde7929c2` | lawzilla_law_evidence |
| 21 | P0 | `gas-shunin-kou` | 2024 | 問12 | `5ab400b643bac920` | lawzilla_law_evidence |
| 22 | P0 | `gas-shunin-kou` | 2024 | 問11 | `2401c6d4fa7111a5` | lawzilla_law_evidence |
| 23 | P0 | `gas-shunin-kou` | 2024 | 問10 | `db637cee87824d57` | lawzilla_law_evidence |
| 24 | P0 | `gas-shunin-kou` | 2024 | 問9 | `fd9364f45fe29acc` | lawzilla_law_evidence |
| 25 | P0 | `gas-shunin-kou` | 2024 | 問8 | `d7367850b0588b72` | lawzilla_law_evidence |
| 26 | P0 | `gas-shunin-kou` | 2024 | 問7 | `ffc0cd209ba5b141` | lawzilla_law_evidence |
| 27 | P0 | `gas-shunin-kou` | 2024 | 問6 | `cdb9cad7080f3d37` | lawzilla_law_evidence |
| 28 | P0 | `gas-shunin-kou` | 2024 | 問5 | `7b372e50d597c363` | lawzilla_law_evidence |
| 29 | P0 | `gas-shunin-kou` | 2024 | 問4 | `b312de5198cdf4bf` | lawzilla_law_evidence |
| 30 | P0 | `gas-shunin-kou` | 2024 | 問3 | `c8af1e8b0c970ab1` | lawzilla_law_evidence |
| 31 | P0 | `gas-shunin-kou` | 2024 | 問2 | `677edcd7d7120dcd` | lawzilla_law_evidence |
| 32 | P0 | `gas-shunin-kou` | 2024 | 問1 | `f72eb811cf645592` | lawzilla_law_evidence |
