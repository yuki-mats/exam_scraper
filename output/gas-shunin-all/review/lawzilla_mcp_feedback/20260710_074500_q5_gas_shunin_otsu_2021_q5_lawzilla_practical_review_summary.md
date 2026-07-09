# gas-shunin-otsu 2021 問5 Lawzilla practical review

- reviewedAt: `2026-07-10T07:45:00+09:00`
- verdict: `usable_as_entry_hint_needs_direct_rule_and_dismissal_articles`
- sourceCandidates: `output/gas-shunin-all/review/lawzilla_mcp_feedback/20260709_003000_gas_shunin_P0_remaining_229_lawzilla_candidates.jsonl:781-785`
- direct basis: `ガス事業法 第25条第1項 / 施行規則 第27条第1項・第2項 / ガス事業法 第31条第1項`
- groupChoiceHandling: `answer_result_text=正解は 5 です。` is a combination option; use group-choice correctness instead.

## Findings

- 選択肢1: Lawzilla候補はガス事業法第25条を返しており選任義務の入口として有用だが、甲・通算・1年・経済産業大臣は施行規則第27条、解任命令はガス事業法第31条まで確認する必要がある。
- 選択肢2: Lawzilla候補はガス事業法第25条を返したが、(ニ)の認定主体が経済産業大臣である直接根拠は施行規則第27条第2項。
- 選択肢3: Lawzilla候補はガス事業法第25条を返したが、甲・通算・経済産業大臣の直接根拠は施行規則第27条第1項・第2項。
- 選択肢4: Lawzilla候補はガス事業法第25条を返したが、通算・1年は施行規則第27条第1項、解任命令の相手方はガス事業法第31条第1項まで確認する必要がある。
- 選択肢5: Lawzilla候補はガス事業法第25条を返しており選任義務の入口として有用だが、正しい組合せの確定には施行規則第27条第1項・第2項とガス事業法第31条第1項の直接確認が必要。

## Feedback For Search Improvement

- 「ガス主任技術者 実務経験 甲 通算 一年 経済産業大臣」は施行規則第27条第1項・第2項、「解任を命ずる」はガス事業法第31条を併せて返す。
- 「同等以上の実務経験 認定 経済産業大臣」は施行規則第27条第2項を返す。
- 「甲又は乙 連続 登録ガス工作物検査機関」を含む候補は、施行規則第27条第1項・第2項で甲・通算・経済産業大臣へ補正する。
- 「連続 6ヵ月 ガス主任技術者を解任する」は、施行規則第27条第1項とガス事業法第31条第1項で通算・1年・事業者への解任命令へ補正する。
- 正解組合せの検索では、ガス事業法第25条に加えて、施行規則第27条第1項・第2項とガス事業法第31条第1項を同時に候補化する。
