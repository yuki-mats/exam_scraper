# T014 token and call verdict

Judge decision: `approve_next`。

- Codex App Serverはbaseline 7 attemptすべてでprovider token usageを返さなかった。推定値で補わず、token削減は `inconclusive_provider_usage_unavailable` に固定する。
- prompt UTF-8 bytesとcanonical response bytesは補助通信量として示すが、token削減20%の代替合格根拠にしない。
- baselineは34問を最大5問ずつ7 Codex callで処理した。全34問を最大5問ずつクラウド監査するhybridも最低7 call必要なため、品質完走時の最大call削減率は0%。call削減30%は現構成で数学的に達成不能である。
- それでも14B/27Bの品質能力、重大事故、安全性は判断価値があるため、sealed baselineを再利用して両local modelを独立評価する。
- 合格しても `operational=true` へ直接昇格しない。品質が通っても現構成では `quality_capable_but_not_cloud_call_reducing` となる。
