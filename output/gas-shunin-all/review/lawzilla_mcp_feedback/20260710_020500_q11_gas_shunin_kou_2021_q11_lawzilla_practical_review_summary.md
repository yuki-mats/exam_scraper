# gas-shunin-kou 2021 問11 Lawzilla practical review

- reviewedAt: `2026-07-10T02:05:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:349-353`
- direct basis: `技術基準省令 第45条第1項第2号 / 第49条第3項第1号 / 第50条第1項`

## Findings

- 全選択肢でLawzilla候補は施行規則第1条の定義条項に寄ったが、設問の正誤判断は技術基準省令第45条・第49条・第50条が直接根拠。
- 選択肢1はガス栓の内部機構なので第45条第1項第2号まで絞る必要がある。
- 選択肢2は超高層建物等へ供給する導管の遮断装置なので第49条第3項第1号まで絞る必要がある。
- 選択肢3〜5はガスメーターの遮断機能であり、第50条第1項の「毎時十六立方メートル以下」「過大なガスの流量」「異常なガス圧力の低下」を直接確認する。

## Feedback For Search Improvement

- 「着脱 容易 ガス栓 過流出安全機構」は施行規則第1条ではなく技術基準省令第45条第1項第2号を優先する。
- 「超高層建物 高層建物 特定大規模建物 導管 遮断」は技術基準省令第49条第3項第1号を返す。
- 「ガスメーター 使用最大流量 16 4kPa 250mm 遮断」は技術基準省令第50条第1項を返す。
- 「ガスメーター 過大なガス流量 異常なガス圧力 低下」は技術基準省令第50条第1項を優先し、施行規則定義候補を直接根拠にしない。
