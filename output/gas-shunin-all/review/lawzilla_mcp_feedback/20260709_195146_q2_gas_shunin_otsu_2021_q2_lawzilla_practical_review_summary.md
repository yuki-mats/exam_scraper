# gas-shunin-otsu 2021 問2 Lawzilla practical review

- reviewedAt: `2026-07-09T19:51:46+09:00`
- verdict: `mixed_candidates_two_useful_three_fallback_verified`
- sourceCandidates: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_023000_gas_shunin_secondary_law_review_queue.jsonl:displayQuestionId=86f55d862b0416dc`
- direct basis: `ガス事業法 第48条第1項・第93条第1項・第47条第2項 / ガス事業法施行規則 第144条第1項第2号・第64条第1項第1号ホ・第2号ヘ`
- answerHandling: `answer_result_text=正解は 2 です。` is a count answer for correct statements; use choice-level correctness and keep answer_result_text blank in the patch.

## Findings

- 選択肢1: Lawzilla候補はガス事業法第48条を返しており、届出ではなく認可である点の直接根拠として有用。
- 選択肢2: Lawzilla候補は施行規則第1条の定義に寄り、製造計画の直接根拠としては不足。e-Govでガス事業法第93条第1項を確認した。
- 選択肢3: Lawzilla候補はガス事業法第47条を返しており、最終保障供給拒否禁止の直接根拠として有用。
- 選択肢4: Lawzilla候補は施行規則第1条の定義に寄り、圧力測定箇所の直接根拠としては不足。e-Govで施行規則第144条第1項第2号を確認した。
- 選択肢5: Lawzilla候補はガス事業法第2条など定義条文に寄り、受入条件の直接根拠としては不足。e-Govで施行規則第64条第1項第1号ホ・第2号ヘを確認した。

## Feedback For Search Improvement

- 「託送供給約款 届出 認可」はガス事業法第48条第1項を優先して返す。
- 「ガス製造事業者 供給計画 製造計画」はガス事業法第93条第1項を優先して返す。
- 「最終保障供給 拒んではならない 一般ガス導管事業者」はガス事業法第47条第2項を優先して返す。
- 「ガス製造事業者 圧力 ガスホルダーの出口」はガス事業法施行規則第144条第1項第2号を優先して返す。
- 「託送供給約款 ガスの熱量等 範囲 組成 受入条件」はガス事業法施行規則第64条第1項第1号ホ・第2号ヘを優先して返す。
- 正しいものの個数を問う設問では、answer_result_textの数字を選択肢番号として扱わず、choice-level correctnessを優先する。
