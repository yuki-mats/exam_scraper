# gas-shunin-kou 2021 問9 Lawzilla practical review

- reviewedAt: `2026-07-10T02:20:00+09:00`
- verdict: `usable_as_hint_not_sufficient_alone`
- sourceCandidates: `output/gas-shunin-all/review/primary_law_evidence/20260709_015500_gas_shunin_lawzilla_primary_evidence_links.jsonl:359-363`
- direct basis: `技術基準省令 第17条第1項 / 第20条第1項 / 第19条第1項 / 第33条第1項 / 第37条第1項`

## Findings

- 選択肢1: Lawzilla候補は施行規則第17条の熱量・圧力等測定方法に寄ったが、過圧時の安全弁設置の直接根拠は技術基準省令第17条第1項。
- 選択肢2: Lawzilla候補収集では技術基準省令第20条第1項が返っているが、primary evidence batchでは当該選択肢のsnapshotが欠落していたため、既存e-Gov XMLで直接確認した。
- 選択肢3: Lawzilla候補は技術基準省令第18条の計測装置等も含んだが、警報装置義務と移動式ガス発生設備除外の直接根拠は技術基準省令第19条第1項。
- 選択肢4: Lawzilla候補は施行規則第197条の消費機器周知に寄ったが、ガスホルダー配管の遮断装置の直接根拠は技術基準省令第33条第1項。
- 選択肢5: Lawzilla候補は施行規則第1条の高圧定義に寄ったが、高圧ガスホルダーの耐熱措置の直接根拠は技術基準省令第37条第1項。

## Feedback For Search Improvement

- 「高圧 ガス発生設備 過圧 安全弁」は技術基準省令第17条第1項を優先し、施行規則第17条の熱量等測定とは分ける。
- 「製造所 供給所 移動式ガス発生設備 遮断装置 誤操作 確実 操作」は技術基準省令第20条第1項を返す。
- 「移動式ガス発生設備 警報装置 損傷に至るおそれ」は技術基準省令第19条第1項の除外文言を確認対象にする。
- 「ガスホルダー 送り出し 受け入れる 配管 流出 流入 遮断」は技術基準省令第33条第1項を返す。
- 「高圧 ガスホルダー 熱 冷却装置 耐熱措置」は技術基準省令第37条第1項を返す。
