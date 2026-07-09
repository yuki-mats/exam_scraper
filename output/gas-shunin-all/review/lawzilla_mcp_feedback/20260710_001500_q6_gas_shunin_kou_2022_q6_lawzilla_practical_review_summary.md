# Lawzilla MCP practical review: gas-shunin-kou 2022 問6

- reviewedAt: `2026-07-10T00:15:00+09:00`
- sourceCandidates: `output/gas-shunin-all/review/lawzilla_mcp_feedback/20260709_011000_gas_shunin_P1_40_lawzilla_candidates.jsonl:41-45`
- verdict: `usable_as_hint_not_sufficient_alone`

## Findings

- 選択肢1はガス事業法第24条ではなく、ガス事業法第97条及びガス事業法施行規則第148条へ差し替える必要があった。
- 選択肢4はガス工作物技術基準省令第9条及びMETI解釈例第6条へ差し替える必要があった。
- 防災一般、保安管理組織運用、LNG貯槽の台風時圧力管理は、e-Gov候補だけで確定せず補助技術資料・既存レビュー済みコーパスで確認する領域として扱う。
- source conflict line 358は文言差であり、Firestore本文と既存IDを維持する。

## Feedback For Search Improvement

- 「保安規程 製造設備」はガス小売事業者のガス事業法第24条ではなく、ガス製造事業者のガス事業法第97条及びガス事業法施行規則第148条を優先候補にする。
- 「ガス 滞留しない構造 開口部」は高圧ガス保安法の設備語句一致ではなく、ガス工作物技術基準省令第9条及びMETI解釈例第6条を優先候補にする。
- 防災一般、保安管理組織の運用、LNG貯槽の台風時圧力管理は、e-Gov条文だけで正誤を確定しにくい場合があるため、補助技術資料・既存レビュー済みコーパス確認を必要とする候補として扱う。
- source conflict が表現差にとどまる場合は、Firestore snapshot/sourcePriority を維持し、正誤影響なしとして記録する。
