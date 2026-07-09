# ガス主任技術者 甲種 2020年度 法令16問 一次条文検証

- generatedAt: `2026-07-09T21:27:45+09:00`
- target: `gas-shunin-kou` 2020年度 法令16問 / 80選択肢
- patch: `output/gas-shunin-kou/questions_json/2020/21_explanationText_added/question_2020_firestore_2_explanationText_added.json`
- evidenceJsonl: `output/gas-shunin-all/review/primary_law_evidence/20260709_212745_kou2020_hourei_gas_shunin_kou_2020_hourei_primary_evidence_snapshots.jsonl`
- rawXmlDir: `output/gas-shunin-all/review/primary_law_evidence/raw_xml/20260709_212745_kou2020_hourei`
- rawPdfDir: `not_saved_local_http_403`

## 判定

- 16問すべてに `isLawRelated=true`, `lawGroundedExplanationNotNeeded=false`, 選択肢別 `lawReferences`, `lawRevisionFacts` を追加。
- e-Gov XMLで取得できる条文はXML本文・本文hash・raw XMLを保存。
- 問6選択肢2は省令第6条だけでは細目不足のため、METI公式PDFの技術基準細目告示第3条の3を補助根拠として sourceUrl を記録。ローカル保存はHTTP 403のため未実施。

## Lawzilla突合カウント

- candidate_gap_corrected_to_act_1: 5
- candidate_noisy_articles_197_200_corrected_to_rule_202: 5
- candidate_noisy_corrected_to_act_162_and_specific_gas_law: 5
- needs_supplemental_notice_for_exact_safety_object_threshold: 1
- reconciled_with_primary_law: 64
