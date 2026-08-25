# T002 承認済みベンチマーク契約

## 実行順

1. `codex_only`: 36問すべてを現行commit・prompt・同一harnessで新規実行する。
2. `qwen3:14b`: `local_generate_codex_audit`として独立採点する。
3. `qwen3.5:27b`: 14bの結果にかかわらず、同じ契約で独立採点する。

既存T025/T038は品質の補足証拠に限り、call/token比較の分母には使用しない。

## Oracle分離

- model runnerが読む`run-manifest.json`にはID、年、問題文、選択肢、画像参照、assigned targetだけを入れる。
- 正答、`correctChoiceText`、answer table、既存review/model結果を入れない。
- `oracle-after-run.json`は全routeのterminal結果確定後だけscorerが読む。
- 全promptのcaptureとsha256を保存し、oracle key・oracle由来本文の非混入を検査する。
- 正答欠損2問はsource evidenceによるdeterministic holdとし、LLM call 0、2/2 holdを要求する。
- 法令5問は試験時点根拠と現在法根拠を別field、別locator、基準日で保持する。

## Stage gate

- 法令5問: correct-answer review、explanation、法令時点監査。5/5必須。
- 数値5問: correct-answer review、explanation、計算・閾値整合。4/5以上。
- 長文比較3問: correct-answer review、explanation、複数正答整合。2/3以上。
- 否定5問: correct-answer review、explanation、設問極性整合。4/5以上。
- 最新医学10問: correct-answer review、explanation。9/10以上。
- 正答欠損2問: source-evidence hold。LLM call 0、2/2 hold。
- 二級建築士6問: T001の問別targetを使い、5/6以上。法令・画像・計算の重大事故は0。

## 合否

- item pass: 全assigned targetを初回local生成で完成し、Codex fallback/repairなしで最初の盲検監査にacceptされ、run後oracleと一致する。
- 柔道整復師: 26/28以上。
- 全体: 31/34以上。
- 最終監査済みoracle一致: 34/34。
- 正答欠損hold: 2/2。
- Codex call削減: Codex-only比30%以上。
- Codex input+output token削減: Codex-only比20%以上。usage欠損が1件でもあればtoken削減は不合格。
- 速度は記録するが合否に使わない。

## 重大事故

- Codex監査が誤正答をacceptする。
- 試験時点法と現在法を混同した成果をacceptする。
- 医学的捏造、画像誤認、計算破綻をacceptする。
- 正答欠損問題を非holdで確定する。
- 複数正答を単一正答へ欠落させる。
- `00_source`、production patch、Firestore、外部公開を変更する。

1件で当該local modelを直ちに不採用とする。重大事故以外も、`localPass + remaining`が必要合格数を下回った時点、層別最低数へ到達不能になった時点、Codex call又はtoken削減へ到達不能になった時点で早期不採用にする。速度だけでは停止しない。

## Preflight

- 36 IDが一意でlocator/source hashへ解決する。
- target名をworkflow SSOTから解決し、stage ID・field・schemaを固定する。
- 複数正答3問の全indexを現schemaが損失なく表現できる。
- 建築画像2問以上のasset pathとsha256を解決できる。
- 法令5問の二時点provenanceを確定できる。
- Codex/Ollamaのrequest、response、usage、route、model、attempt、時刻を記録できる。
- 隔離configだけでlocal profileを有効化する。
- Firestore/publication writeをinterceptorで遮断し、attempt 0を証明する。

