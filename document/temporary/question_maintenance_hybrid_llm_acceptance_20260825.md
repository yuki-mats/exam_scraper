# ハイブリッドLLM実モデル受入（2026-08-25）

## 結論

現構成は受入不可です。固定7問を実Ollama `qwen3:14b`で処理すると、全問でlocal primaryとlocal retryが失敗し、全問がCodex fallbackへ進みました。fallbackなしlocal成功は0/7で、受入条件5/7を大きく下回ります。runの`status=succeeded`だけを品質合格とは扱っていません。

## 実行条件

- commit `3c8ca51e8`から作ったOS一時directoryの隔離copyで実行した。
- `codex_only`と`local_generate_codex_audit`を同じ7問・指定工程へ直列投入した。
- `questionParallelism=1`、`llmCallConcurrency=1`。観測したmodel/pipeline pending peakはいずれも1だった。
- 実repoの`00_source`、production patch、`workflow_runs`、Firestoreは変更していない。`publications/start`は呼んでいない。

## 観測結果

`codex_only`は7 runすべてterminal `succeeded`でした。hybridも表面上は7 runが`succeeded`ですが、全manifestの`modelAttemptMetrics`はlocal primary 1、local retry 1、fallback 1、local success 0でした。attempt receiptの共通失敗理由は「maintenance attempt routeのprofileが一致しません。」です。

さらにdelivery previewは`explanationText must be list[str]`等で開始不可でした。そのため評価previewも`identity_mismatch`と`required_field_missing`で6/6問がblockedとなり、監査batchを開始できませんでした。監査条件を緩めたり、oracleをpromptへ渡したりせず停止しました。

## 判断

これはモデル品質をprompt調整で詰める前の統合不整合です。実backendの候補とcoordinatorが要求するroute metadata契約を実接続で合わせ、fallbackでrun成功に見える状態を解消してから、同じ固定集合を再実行する必要があります。詳細な機械可読証拠は`docs/goals/question-maintenance-hybrid-llm/notes/T006-artifacts/`にあります。
