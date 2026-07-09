# gas-shunin-kou 2021 問8 Lawzilla practical review

- reviewedAt: `2026-07-10T02:30:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:364-368`
- sourceConflictHandling: `preserve_firestore_snapshot_after_review`
- direct basis: `技術基準省令 第14条第1項第6号 / 第15条第1項第2号 / 第15条第2項第3号 / 第16条第1項 / 第16条第2項第3号ロ`

## Findings

- 選択肢1: Lawzilla候補は施行規則第13条・第74条・第197条のガス栓語に寄ったが、ガス栓の主要材料の直接根拠は技術基準省令第14条第1項第6号。
- 選択肢2: Lawzilla候補の技術基準省令第15条第1項は有用だが、正誤判断は第1項第2号のガスホルダーまで絞って確認する。
- 選択肢3: Lawzilla候補の技術基準省令第15条第2項は有用だが、正誤判断はただし書第3号の昇圧供給装置例外まで絞って確認する。
- 選択肢4: Lawzilla候補収集では技術基準省令第16条第1項が返っているが、primary evidence batchでは当該選択肢のsnapshotが欠落していたため、既存e-Gov XMLで直接確認した。
- 選択肢5: Lawzilla候補は施行規則第1条の圧力定義に寄ったが、中圧導管の溶接施工方法確認の直接根拠は技術基準省令第16条第2項第3号ロ。
- source conflict ledger lines 261-262 show archive-site per-statement correctness conflicts for choices 1 and 3; Firestore snapshot remains the reviewed source.

## Feedback For Search Improvement

- 「ガス栓 主要材料 最高使用温度 最低使用温度 機械的性質」は技術基準省令第14条第1項第6号を優先する。
- 「ガスホルダー 構造 供用中の荷重 最高使用温度 最低使用温度 最高使用圧力」は技術基準省令第15条第1項第2号を返す。
- 「昇圧供給装置 耐圧試験」は技術基準省令第15条第2項第3号の例外まで確認する。
- 「零Paを超える圧力 溶接 溶込み 有害な欠陥 設計上要求される強度」は技術基準省令第16条第1項を返す。
- 「中圧 導管 0.3MPa 内径150mm 溶接施工方法」は技術基準省令第16条第2項第3号ロを返す。
