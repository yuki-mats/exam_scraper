# gas-shunin-otsu 2021 問8 Lawzilla practical review

- reviewedAt: `2026-07-10T06:45:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:926-930`
- direct basis: `技術基準省令 第14条第1項第6号 / 技術基準省令 第16条第1項 / 技術基準省令 第15条第4項 / 技術基準省令 第16条第2項第3号ロ / 技術基準省令 第15条第2項第3号`
- comboAnswerHandling: `answer_result_text=正解は 3 です。` is a combination option; use statement-level correctness instead.

## Findings

- 選択肢1: Lawzilla候補は施行規則第13条・第74条・第197条のガス栓語に寄ったが、ガス栓の主要材料の直接根拠は技術基準省令第14条第1項第6号。
- 選択肢2: Lawzilla候補は技術基準省令第16条を含むため有用だが、正誤判断は第16条第1項の溶接部品質要件まで絞って確認する。
- 選択肢3: Lawzilla候補バッチでは当該選択肢の直接根拠snapshotが欠落していたため、基礎構造規定と配管除外の直接根拠として技術基準省令第15条第4項をe-Gov XMLで確認した。
- 選択肢4: Lawzilla候補は施行規則第1条の圧力定義に寄ったが、中圧導管の溶接施工方法確認の直接根拠は技術基準省令第16条第2項第3号ロ。
- 選択肢5: Lawzilla候補の技術基準省令第15条第2項は有用だが、正誤判断は第2項第3号の液化ガス用ポンプ例外まで絞って確認する。

## Feedback For Search Improvement

- 「ガス栓 主要材料 最高使用温度 最低使用温度 機械的性質」は技術基準省令第14条第1項第6号を優先する。
- 「零Paを超える圧力 溶接 溶込み 有害な欠陥 設計上要求される強度」は技術基準省令第16条第1項を返す。
- 「配管 基礎 不等沈下 有害なひずみ」は技術基準省令第15条第4項の配管除外まで確認する。
- 「中圧 導管 0.3MPa 内径150mm 溶接施工方法」は技術基準省令第16条第2項第3号ロを返す。
- 「液化ガス用ポンプ 耐圧試験」は技術基準省令第15条第2項第3号の例外まで確認する。
