# ガス主任技術者 Lawzilla MCP 過去問整備ワークフロー対象洗い出し

- generatedAt: 2026-07-08T23:56:25+09:00
- source review ledgers: `output/gas-shunin-kou/review/01_04_manual_review/gas-shunin-kou_01_04_manual_review.jsonl`, `output/gas-shunin-otsu/review/01_04_manual_review/gas-shunin-otsu_01_04_manual_review.jsonl`
- source execution plan: `docs/goals/gas-shunin-01-04-full-pass/notes/question-plan/all_questions_plan.jsonl`
- machine-readable targets: `output/gas-shunin-all/review/lawzilla_mcp_target_discovery/20260708_235625_gas_shunin_lawzilla_workflow_targets.jsonl`

## 前提

- 01〜04 review ledger は 934 行すべて `ok`。
- execution plan は 936 行すべて `done`。
- execution plan 側には重複 reviewId が 2 件あるため、実作業対象は review ledger の一意行を正本にする。
- `00_source` と既存 Firestore document ID は変更しない。

## 対象サマリ

| 区分 | 件数 | 内容 |
| --- | ---: | --- |
| P0 | 261 | `sourceCategory=法令`。Lawzilla MCP + 既存 evidence の並列検証を最優先。 |
| P1 | 40 | 法令カテゴリ以外だが法令語彙を含む。実際に法令根拠が必要か確認。 |
| P2 | 86 | source conflict / count-answer / combo-answer など、先に正答・source確認が必要。 |
| 合計 | 387 | 何らかの追加ワークフロー対象。 |

## Track別件数

- `answer_recheck`: 28
- `lawzilla_law_evidence`: 301
- `source_conflict_review`: 132

## P0 年度別

| qualification | examYear | count |
| --- | ---: | ---: |
| `gas-shunin-kou` | 2019 | 16 |
| `gas-shunin-kou` | 2020 | 16 |
| `gas-shunin-kou` | 2021 | 18 |
| `gas-shunin-kou` | 2022 | 17 |
| `gas-shunin-kou` | 2023 | 18 |
| `gas-shunin-kou` | 2024 | 16 |
| `gas-shunin-kou` | 2025 | 16 |
| `gas-shunin-otsu` | 2017 | 16 |
| `gas-shunin-otsu` | 2018 | 16 |
| `gas-shunin-otsu` | 2019 | 16 |
| `gas-shunin-otsu` | 2020 | 16 |
| `gas-shunin-otsu` | 2021 | 16 |
| `gas-shunin-otsu` | 2022 | 16 |
| `gas-shunin-otsu` | 2023 | 16 |
| `gas-shunin-otsu` | 2024 | 16 |
| `gas-shunin-otsu` | 2025 | 16 |

## 優先対象の使い方

1. P0 の法令問題から、年度単位で 02b law context と Lawzilla MCP 並列 evidence check を実施する。
2. P0 で一致した根拠は 03 explanationText の根拠精度改善へ使う。
3. P0 で不一致または不足がある場合は、推測更新せず `hold` / `needs_secondary_review` に回す。
4. P1 は法令語彙が正誤判断に効くかを先に判定し、必要なものだけ P0 相当に昇格する。
5. P2 は Lawzilla より前に、source conflict と correctChoiceText の根拠を確認する。

## Reason別件数

- `combo_answer_recheck`: 13
- `count_answer_recheck`: 15
- `non_law_sourceCategory_but_legal_keywords`: 40
- `sourceCategory=法令`: 261
- `source_conflict_needs_review`: 129
- `source_content_conflict`: 132

## 重複 plan reviewId

- `2024:question_2024_gassyunin_site_1:c8af1e8b0c970ab1`: 2 rows in execution plan
- `2024:question_2024_gassyunin_site_1:ffc0cd209ba5b141`: 2 rows in execution plan

## P0 サンプル

| priority | qualification | year | label | id | question |
| --- | --- | ---: | --- | --- | --- |
| P0 | `gas-shunin-kou` | 2019 | 問1 | `gasushunin-koushu-hourei-2019-1` | 法令では、ガス小売事業者が小売供給を受けようとする者と小売供給契約の締結をしようとするときは、経済産業省令で定めるところ |
| P0 | `gas-shunin-kou` | 2019 | 問10 | `gasushunin-koushu-hourei-2019-10` | 技術基準で規定されているガス工作物に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問11 | `gasushunin-koushu-hourei-2019-11` | 技術基準で規定されているガス工作物に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問12 | `gasushunin-koushu-hourei-2019-12` | 技術基準で規定されている整圧器及び昇圧供給装置に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問13 | `gasushunin-koushu-hourei-2019-13` | 法令で規定されているガス用品（特定ガス用品を除く。）に関する次の記述について、[  ]の中の（イ）〜（ホ）の語句のうち、 |
| P0 | `gas-shunin-kou` | 2019 | 問14 | `gasushunin-koushu-hourei-2019-14` | 法令で規定されている消費機器に関する周知及び調査に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問15 | `gasushunin-koushu-hourei-2019-15` | 消費機器の技術上の基準で規定されている次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問16 | `gasushunin-koushu-hourei-2019-16` | 「特定ガス消費機器の設置工事の監督に関する法律」等に関する次の記述のうち、正しいものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問2 | `gasushunin-koushu-hourei-2019-2` | ガス小売事業者が、その事業の用に供するガス工作物及びその供給するガスに係る消費機器の事故のうち、事故が発生した時から又は |
| P0 | `gas-shunin-kou` | 2019 | 問3 | `gasushunin-koushu-hourei-2019-3` | 法令で規定されている保安規程及びガス主任技術者に関する次の記述のうち、正しいものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問4 | `gasushunin-koushu-hourei-2019-4` | 法令で規定されている託送供給及びガス工作物の工事に関する次の記述のうち、正しいものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問5 | `gasushunin-koushu-hourei-2019-5` | 技術基準で規定されているガス工作物及び保安物件に関する次の記述のうち、正しいものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問6 | `gasushunin-koushu-hourei-2019-6` | 技術基準で「ガス工作物の構造は、供用中の荷重並びに最高使用温度及び最低使用温度における最高使用圧力に対し、設備の種類、規 |
| P0 | `gas-shunin-kou` | 2019 | 問7 | `gasushunin-koushu-hourei-2019-7` | 技術基準で規定されているガス工作物及び付臭措置に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問8 | `gasushunin-koushu-hourei-2019-8` | 技術基準で規定されているガス工作物に関する次の記述のうち、誤っているものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2019 | 問9 | `gasushunin-koushu-hourei-2019-9` | 技術基準で規定されているガスホルダー及び液化ガス用貯槽（不活性の液化ガス用のものを除く。）に関する次の記述のうち、正しい |
| P0 | `gas-shunin-kou` | 2020 | 問1 | `gasushunin-koushu-hourei-2020-1` | 法令で規定されているガス事業法の目的に関する次の記述について、[  ]の中の（イ）〜（ホ）の語句のうち、正しいものはいく |
| P0 | `gas-shunin-kou` | 2020 | 問10 | `gasushunin-koushu-hourei-2020-10` | 技術基準で規定されているガス遮断装置に関する次の記述のうち、正しいものはいくつあるか。 |
| P0 | `gas-shunin-kou` | 2020 | 問11 | `gasushunin-koushu-hourei-2020-11` | 技術基準で規定されている導管の漏えい検査に関する次の記述のうち、漏えい検査の対象から除外されているものはいくつあるか。た |
| P0 | `gas-shunin-kou` | 2020 | 問12 | `gasushunin-koushu-hourei-2020-12` | 技術基準に規定されているガス事業者の掘削により周囲が露出することとなった導管の防護の基準、整圧器及び昇圧供給装置に関する |

