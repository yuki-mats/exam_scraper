# gas-shunin-otsu 2021 問1 Lawzilla practical review

- reviewedAt: `2026-07-09T20:03:33+09:00`
- verdict: `mixed_candidates_three_useful_two_fallback_verified`
- sourceCandidates: `output/gas-shunin-all/review/latest_question_maintenance_workflow/20260709_023000_gas_shunin_secondary_law_review_queue.jsonl:displayQuestionId=ef408279da68602f`
- direct basis: `ガス事業法 第2条第11項・第2条第1項・第86条第1項第3号ロ / ガス事業法施行規則 第1条第2項第8号ハ / 高圧ガス保安法 第2条第1項第3号`
- answerHandling: `answer_result_text=正解は 2 です。` is a combination answer for incorrect statements; use choice-level correctness and keep answer_result_text blank in the patch.

## Findings

- 選択肢1: Lawzilla候補はガス事業法第2条を返しており条文単位では有用だが、段落は第11項へ補正が必要。
- 選択肢2: Lawzilla候補は施行規則第1条を返しており条文単位では有用だが、特定導管要件の直接根拠は第2項第8号ハ。
- 選択肢3: Lawzilla候補は高圧ガス保安法第2条第1項第3号を返しており、液化ガスの0.2MPa基準の直接根拠として有用。
- 選択肢4: Lawzilla候補はガス事業法第90条等に寄り、ガス製造事業を営もうとする者の届出事項としては不足。e-Govでガス事業法第86条第1項第3号ロを確認した。
- 選択肢5: Lawzilla候補はガス事業法第2条第1項を返しており、小売供給定義の直接根拠として有用。

## Feedback For Search Improvement

- 「ガス事業 託送供給事業 特定ガス導管事業」はガス事業法第2条第11項を優先して返す。
- 「特定導管 13A 内径200mm未満 0.5MPa以上5MPa未満 15km超」はガス事業法施行規則第1条第2項第8号ハを優先して返す。
- 「液化ガス 0.1MPa 0.2MPa 35℃」は高圧ガス保安法第2条第1項第3号を優先して返す。
- 「ガス製造事業 営もうとする者 ガス発生設備 ガスホルダー 届出」はガス事業法第86条第1項第3号ロを優先して返す。
- 「小売供給 特定ガス発生設備 供給地点 70」はガス事業法第2条第1項を優先して返す。
- 誤っているものの組合せを問う設問では、answer_result_textの数字を単一選択肢番号として扱わず、choice-level correctnessを優先する。
