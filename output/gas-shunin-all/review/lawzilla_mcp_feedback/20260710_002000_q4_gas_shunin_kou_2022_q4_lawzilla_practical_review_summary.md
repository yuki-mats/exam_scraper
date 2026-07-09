# Lawzilla MCP practical review: gas-shunin-kou 2022 問4

- reviewedAt: `2026-07-10T00:20:00+09:00`
- sourceCandidates: `output/gas-shunin-all/review/lawzilla_mcp_feedback/20260709_011000_gas_shunin_P1_40_lawzilla_candidates.jsonl:46-50`
- verdict: `usable_as_hint_not_sufficient_alone`

## Findings

- Lawzillaは熱量・燃焼性の語句一致で第18条・第52条・第78条を拾うが、甲種製造では第91条・施行規則第144条を優先する必要がある。
- WI/MCPの算出式や熱量調整方式の比較は、条文だけでは不足し、JOGMEC等の補助技術資料が必要。
- 空気希釈の酸素4%管理は、一般高圧ガス保安規則第6条の圧縮禁止条件とJOGMEC技術解説で確認する。
- source conflict line 357は表記差であり、Firestore本文と既存IDを維持する。

## Feedback For Search Improvement

- 「燃焼性 熱量」はガス事業法第18条・第52条・第78条だけでなく、ガス製造分野の第91条及びガス事業法施行規則第144条を優先候補にする。
- ウォッベ指数やMCPの算出式・用語定義は、e-Gov条文だけでは「告示で定める方法」までしか出ないため、告示・公式/準公式技術資料で補完する。
- 熱量調整方式のランニングコスト、増熱原料の露点、空気希釈時の酸素濃度は技術運用論点としてJOGMEC等の補助資料を必須扱いにする。
- source conflict が表記差にとどまる場合は、Firestore snapshot/sourcePriority を維持し、正誤影響なしとして記録する。
