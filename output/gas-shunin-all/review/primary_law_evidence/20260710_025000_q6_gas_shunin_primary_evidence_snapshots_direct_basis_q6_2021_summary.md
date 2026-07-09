# gas-shunin-kou 2021 問6 primary evidence summary

- generatedAt: `2026-07-10T02:50:00+09:00`
- target: `gasushunin-koushu-hourei-2021-6` / 問6
- variant: `firestore_statement_set`
- sourceConflictHandling: `preserve_firestore_snapshot_after_review`
- direct basis: `ガス事業法 第101条第1項 / 第101条第3項 / 第102条第1項 / ガス事業法施行規則 第153条第1項 + 別表第一`
- Lawzilla reconciliation: candidates were useful for appended-table discovery but misdirected to definitions or surrounding enforcement-regulation articles for several choices
- recordCount: `5`
- rawXmlCount: `4`

| choice | locator | decision |
| --- | --- | --- |
| 1 | 第101条第1項 | Lawzilla候補は施行規則第5条・第39条など周辺条文に寄ったが、工事計画届出義務そのものの直接根拠はガス事業法第101条第1項。 |
| 2 | 第101条第3項 | Lawzilla候補はガス事業法第2条のガス製造事業定義に寄ったが、三十日起算点の直接根拠はガス事業法第101条第3項。 |
| 3 | 第102条第1項 | Lawzilla候補は施行規則第5条・第39条など工事計画周辺に寄り、primary evidence batchでも直接snapshotが欠落していたため、既存e-Gov XMLでガス事業法第102条第1項を直接確認した。 |
| 4 | 第153条第1項 + 別表第一 二（二）（1）3（1） | Lawzilla候補は施行規則第1条の高圧・中圧定義に寄ったが、届出対象性の直接根拠は施行規則第153条第1項と別表第一二（二）（1）3（1）。source conflict ledgerではarchive-siteが誤りとしていたが、別表第一によりFirestore側の正しい判定を採用する。 |
| 5 | 第153条第1項 + 別表第一 五3（1）2 | Lawzilla候補は別表第一を返しており有用。ただし届出対象性は施行規則第153条第1項が別表第一中欄に接続するため、別表第一五3（1）2と併せて確認する。 |
