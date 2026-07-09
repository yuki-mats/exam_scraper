# gas-shunin-kou 2021 問6 Lawzilla practical review

- reviewedAt: `2026-07-10T02:50:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:374-378`
- sourceConflictHandling: `preserve_firestore_snapshot_after_review`
- direct basis: `ガス事業法 第101条第1項 / 第101条第3項 / 第102条第1項 / 施行規則 第153条第1項 + 別表第一`

## Findings

- 選択肢1: Lawzilla候補は施行規則第5条・第39条など周辺条文に寄ったが、工事計画届出義務そのものの直接根拠はガス事業法第101条第1項。
- 選択肢2: Lawzilla候補はガス事業法第2条のガス製造事業定義に寄ったが、三十日起算点の直接根拠はガス事業法第101条第3項。
- 選択肢3: Lawzilla候補は施行規則第5条・第39条など工事計画周辺に寄り、primary evidence batchでも直接snapshotが欠落していたため、既存e-Gov XMLでガス事業法第102条第1項を直接確認した。
- 選択肢4: Lawzilla候補は施行規則第1条の高圧・中圧定義に寄ったが、届出対象性の直接根拠は施行規則第153条第1項と別表第一二（二）（1）3（1）。source conflict ledgerではarchive-siteが誤りとしていたが、別表第一によりFirestore側の正しい判定を採用する。
- 選択肢5: Lawzilla候補は別表第一を返しており有用。ただし届出対象性は施行規則第153条第1項が別表第一中欄に接続するため、別表第一五3（1）2と併せて確認する。
- source conflict ledger lines 259-260 are resolved by primary law review; preserve Firestore statement set and existing statement-level IDs.

## Feedback For Search Improvement

- 「ガス製造事業者 工事計画 届出 災害 非常 一時的工事」はガス事業法第101条第1項を優先する。
- 「工事計画 届出 30日 受理された日」はガス事業法第101条第3項を返し、ガス事業法第2条の定義条文に寄せない。
- 「使用前検査 自主検査 登録ガス工作物検査機関」はガス事業法第102条第1項を返し、「完成検査」と区別する。
- 「製造所 ガス発生器 改造 20% 能力 変更後 高圧」は施行規則第153条第1項と別表第一二（二）（1）3（1）を併せて返す。
- 「供給所 整圧器 最高使用圧力 変更 改造 高圧」は施行規則第153条第1項と別表第一五3（1）2を併せて返す。
