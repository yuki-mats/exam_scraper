# gas-shunin-kou 2021 問7 Lawzilla practical review

- reviewedAt: `2026-07-10T02:40:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:369-373`
- sourceConflictHandling: `preserve_firestore_snapshot_after_review`
- direct basis: `技術基準省令 第4条第2項 / 第5条第1項 / 第8条第1項 / 第9条第1項 / 第13条第1項`

## Findings

- 選択肢1: Lawzilla候補は施行規則第17条・第48条・第78条の整圧器語に寄ったが、整圧器の公衆操作防止措置の直接根拠は技術基準省令第4条第2項。
- 選択肢2: Lawzilla候補の上位は施行規則第26条のガス主任技術者選任に寄ったが、緊急時通信設備の直接根拠は技術基準省令第5条。候補収集側には同条も含まれていたため、技術基準省令を優先する。
- 選択肢3: Lawzilla候補は施行規則第1条の導管・製造所等の定義に寄ったが、防消火設備設置の直接根拠は技術基準省令第8条。
- 選択肢4: Lawzilla候補の上位は施行規則第26条に寄ったが、室のガス滞留防止構造の直接根拠は技術基準省令第9条第1項。候補収集の技術基準省令第4条・第5条候補も本肢の直接根拠ではない。
- 選択肢5: primary evidence batchでは当該選択肢のsnapshotが欠落していたが、Lawzilla候補収集には技術基準省令第13条が含まれていたため、既存e-Gov XMLで第13条第1項を直接確認した。
- source conflict status is `none`; preserve reviewed Firestore statement set and existing statement-level IDs.

## Feedback For Search Improvement

- 「整圧器 公衆 みだり 操作」は技術基準省令第4条第2項を優先する。
- 「緊急時 迅速な通信 通信設備 製造所 供給所 導管」は技術基準省令第5条を返す。
- 「防消火設備 製造所 供給所 ガス工作物」は技術基準省令第8条を返す。
- 「室 漏えい 滞留しない構造」は技術基準省令第9条第1項を返す。
- 「ガス発生設備 安全に置換」は技術基準省令第13条第1項を返し、「安全に放出」と混同しない。
