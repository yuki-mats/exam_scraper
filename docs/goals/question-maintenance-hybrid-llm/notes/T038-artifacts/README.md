# T038 codex_only 180秒SLA実測

現行HEAD `437cb70497483c9bdc15fe71ba620b4fdecd95c4`をOS一時directoryへ展開し、固定集合の既知入力だけをコピーして、実Codex App Serverへ直列投入した。subscriptionは`allowed=true`、`codex_only`は`operational=true`、`questionParallelism=1`と`llmCallConcurrency=1`を確認した。oracleはmodelへ渡していない。

先頭4問はすべて`validated`で、admissionからterminalまでは67.595秒、7.796秒、23.868秒、86.463秒だった。観測した問題処理peakとLLM呼出しpeakはいずれも1である。正答精査問は「選択肢3のみ間違い」という結果がblind oracleと一致した。他3問は工程検査を通過したが、全7問終了後のblind比較が未完了なので品質合格へ数えていない。

5問目の隔離入力を補完した後もpreviewが`対象年度がありません: 85003`を二回返した。同一環境原因を二回是正しても解消しない停止条件に達したため、残り3問を実行していない。したがって、この証跡は4/7の部分計測であり、全7問の180秒SLAを合格又は不合格へ分類しない。

実行APIはsession、Codex status、questions、qualification run preview/start、job、question detailだけで、Firestoreとpublicationの呼出しは0。実repoの`output` statusは空、`00_source` 53,349ファイルの集約hashを保存した。production patchと`00_source`は変更していない。

ファイル:

- `result.json`: 問題別時刻、SLA、terminal、品質状態、並列peak、停止理由
- `turn_timings.jsonl`: 工程・turn別時刻、model、role相当backend、fallback/localSuccess、queue telemetry
- `environment.json`: subscription、profile snapshot、1/1 limit、API安全境界
- `no_write_hashes.json`: 実repoのno-write証拠
- `run_codex_only_sla.py`: T025固定集合を再利用した実測runner
- `summarize_evidence.py`: 隔離raw responseからのsanitizer/集計器
