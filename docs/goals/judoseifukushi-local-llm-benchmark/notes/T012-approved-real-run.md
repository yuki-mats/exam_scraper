# T012 approved real-run contract

Judge decision: `approve_next`。

T011 preflightは固定36問v2、画像7点、公式試験日3件、法令5問の二時点一次資料、oracle分離、安全遮断を `status=pass`、`failedChecks=[]`、`modelCalls=0` で閉じた。

実モデルrunの前にpreflightを現行HEADで再実行し、stage matrixを `preflight_pass` へ更新する。3 routeは同一HEAD・同一36問sanitized snapshot・同一workflow/prompt closureから別々のOS一時環境へ展開する。

実行順は次のとおり。

1. `codex_only` 36問をterminalへ閉じ、provider usageを全attemptで取得する。
2. `qwen3:14b` を独立評価する。
3. `qwen3.5:27b` を14Bの結果に関係なく独立評価する。

正答欠損2問は全routeでdeterministic hold、LLM call 0。全LLM呼出し・問題処理はpeak 1。Codex監査batchは最大5問かつ120000 bytes。速度は記録のみで合否条件にしない。

oracleは3 routeが各36 terminal rowを持ち、prompt captureをsealした後にだけ生成・読込む。品質閾値、重大事故、早期不採用、cloud call/token削減式はT002から変更しない。
