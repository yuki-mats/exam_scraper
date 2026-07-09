# gas-shunin-otsu 2021 問15 Lawzilla practical review

- reviewedAt: `2026-07-09T20:35:00+09:00`
- verdict: choice 4 `usable_as_entry_hint_needs_interpretation_article_51`; choice 5 `misdirected_candidates_need_road_traffic_act_77_and_road_act_32`; choices 1-3 are `not_law_related_technical_choice`.
- direct basis: `ガス工作物技術基準省令 第15条第3項 / 解釈例 第51条第2項第2号 / 道路交通法 第77条第1項第1号 / 道路法 第32条第1項第2号`
- singleChoiceHandling: `answer_result_text=正解は 5 です。` is preserved in 00_source; patch uses choice-level correctness and leaves `answer_result_text` blank.

## Findings

- 選択肢1: 導管の機械的接合の施工技術であり、法令根拠を無理に付与しない。
- 選択肢2: PE管融着方式の施工技術であり、法令根拠を無理に付与しない。
- 選択肢3: 架管のスリーブ・シール材施工の技術知識であり、法令根拠を無理に付与しない。
- 選択肢4: Lawzilla候補の省令第15条は入口として有用だが、0.2パーセントの直接根拠はMETI解釈例第51条第2項第2号で確認する。
- 選択肢5: Lawzilla候補は施行規則第1条に寄ったため直接根拠ではない。道路使用許可は道路交通法第77条、道路占用許可は道路法第32条で確認する。

## Feedback For Search Improvement

- 「気密試験 ガス検知器 0.2パーセント 作動しない」は、省令第15条第3項と解釈例第51条第2項第2号を併せて返す。
- 「道路使用許可 道路管理者 警察署長 道路占用許可 ガス管」は、道路交通法第77条第1項第1号と道路法第32条第1項第2号を併せて返す。
