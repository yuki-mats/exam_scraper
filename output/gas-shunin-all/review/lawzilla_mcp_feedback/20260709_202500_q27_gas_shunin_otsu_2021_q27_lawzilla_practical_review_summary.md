# gas-shunin-otsu 2021 問27 Lawzilla practical review

- reviewedAt: `2026-07-09T20:25:00+09:00`
- verdict: `misdirected_candidates_need_notice_pdf_and_rule_202` for choice 3; other choices are `not_law_related_technical_choice`.
- direct basis: `ガス事業法施行規則 第202条第1項第10号 / ガス漏れ警報器告示 第2条第25号`
- singleChoiceHandling: `answer_result_text=正解は 3 です。` is preserved in 00_source; patch uses choice-level correctness and leaves `answer_result_text` blank.

## Findings

- 選択肢1: 接触燃焼式ガス警報器の技術特性であり、法令根拠を無理に付与しない。
- 選択肢2: 熱線型半導体式ガス警報器の技術特性であり、法令根拠を無理に付与しない。
- 選択肢3: Lawzilla候補は高圧ガス保安法に寄ったため直接根拠ではない。作動濃度はMETI告示第2条第25号で確認する。
- 選択肢4: CO警報器のセンサー保守に関する技術特性であり、法令根拠を無理に付与しない。
- 選択肢5: 業務用換気警報器のセンサー特性であり、法令根拠を無理に付与しない。

## Feedback For Search Improvement

- 「ガス漏れ警報器 爆発下限界 四分の一 二百分の一 施行規則 第二百二条 第十号 告示」で、METI告示第五百七十八号第2条第25号と施行規則第202条第1項第10号を返す。
- 技術肢はLawzilla候補対象から除外し、法令・告示を明示する肢だけを条文照合対象にする。
