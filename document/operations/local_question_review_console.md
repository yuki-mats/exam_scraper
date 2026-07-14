# 問題整備システム

この文書は、ローカルGUI「問題整備システム」における整備・評価・再整備の実行境界を定める正本です。工程順は[問題整備ワークフロー](exam_pipeline_manual_and_automation.md)、工程名とpromptは[`config/question_maintenance_workflow.toml`](../../config/question_maintenance_workflow.toml)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照します。

## 一つの流れ

画面は分けず、問題一覧と問題詳細から次の操作だけを行います。

```text
整備を開始（新規session）
  -> 機械gate
  -> 評価を開始（問題ごとの新規session）
  -> 合格: 公開可能
  -> 不合格: 再整備を開始（新規session）
  -> 機械gate
  -> 再評価（さらに新規session）
```

同じapp-server processを共有しても、sessionを共有したとはみなしません。整備、評価、再整備、再評価はそれぞれ独立したCodex threadで実行します。

## 絶対条件

### 現在のサブスクリプションだけを使う

- app-server接続時と各thread開始前に`account/read`を実行し、`account.type = "chatgpt"`の場合だけ開始する。
- 各thread開始前に`account/rateLimits/read`を確認し、利用上限又はspend controlに達した場合は開始しない。
- `account/updated`とrate limit更新を監視し、ChatGPT認証でなくなった場合又は上限到達後は新しいturnを開始しない。
- `apiKey`、未ログイン、account又はcredit状態を判定できない場合は開始しない。
- usage-based plan、`credits.hasCredits = true`、又は追加支出を許すspend controlがあるaccountでは開始しない。
- OpenAI Platform API、`OPENAI_API_KEY`、API key認証、外部model providerへfallbackしない。
- Standard速度を使い、Fast mode、rate limit reset、credit追加又は追加credit消費をsystem側から有効化しない。
- サブスクリプションの利用上限に達した場合は`待機`又は`失敗`にし、従量課金経路へ切り替えない。
- 追加購入済みcredit又はoverageを使わないことまで保証する場合は、account又はworkspace側のspend controlを追加支出なしに設定し、その状態を確認できなければ開始しない。

ChatGPTログインはサブスクリプション利用、API keyログインは従量課金です。実装時は[Codex authentication](https://learn.chatgpt.com/docs/auth)と[Codex pricing](https://learn.chatgpt.com/docs/pricing)の現行仕様を確認します。

### 作業ごとに新しいsessionを使う

| 作業 | session単位 | 権限 | 引き継ぐ情報 |
| --- | --- | --- | --- |
| 整備 | 利用者が開始したrunごとに新規thread | patch層だけ書込可 | 現在の問題、正本文書、対象工程 |
| 評価 | 元問題1問ごとに新規thread | read-only | 現在の問題と根拠候補だけ |
| 再整備 | 不合格問題ごとに新規thread | patch層だけ書込可 | 現在の問題と構造化された評価結果 |
| 再評価 | 元問題1問ごとに再び新規thread | read-only | 再整備後の現在内容だけ |

- 異なる作業種別で`thread/resume`又は`thread/fork`を使わない。
- 中断した同一runの復旧に限り、そのrun自身のthreadをresumeできる。
- chat transcriptや内部思考は次の作業へ渡さない。必要な事実だけをartifactとして渡す。
- session識別子、作業種別、対象、時刻、結果は[artifact契約](artifact_contract.md)に従って保存する。

## Codex app-serverとの接続

```text
browser -> 問題整備システムのPython server -> codex app-server（stdio）
```

- Python serverがapp-serverを一つ管理し、`initialize`後と各thread開始前にaccountと利用上限を確認する。
- browserからapp-serverへ直接接続しない。experimentalなWebSocket transportへ依存しない。
- 開始操作は`thread/start`と`turn/start`へ変換する。
- 進捗と完了通知を既存job表示へ流し、停止は`turn/interrupt`で行う。
- command又はfile変更の承認要求はUIへ表示し、自動承認しない。
- app-serverが使えない、schemaが合わない、ChatGPT認証でない場合は処理を開始しない。

Codex app-serverは独自clientへ認証、会話履歴、承認、stream eventを組み込むためのinterfaceです。protocolの正本は[Codex App Server](https://learn.chatgpt.com/docs/app-server)と、使用中のCodex binaryから生成したschemaです。

## 評価の客観性

評価sessionは整備又は再整備のsessionを知らない状態で始めます。

- 問題文と全選択肢を一体で読み、一問ずつ評価する。
- 現在の正誤と解説は比較対象であり、正しいことの根拠にしない。
- 各選択肢を一次資料、公式資料、法令本文又は独立計算で確認する。
- 公式解答表だけ、類似文言、confidenceだけで合格にしない。
- 法令問題は出題時と現行法を分け、計算問題は式、単位、丸めを確認する。
- 根拠不足は推測せず`insufficient_evidence`として不合格にする。
- 結果は指定JSON Schemaに限定し、評価artifact以外のfileを変更しない。

serverはAIが返した`passed`だけを信用せず、現在の`stateHash`、全選択肢の根拠、正答対応、解説点数、重大指摘、機械gateを再検証して`publishReady`を計算します。

## 再整備

- 不合格理由、対象選択肢、根拠、推奨工程だけを新しい再整備sessionへ渡す。
- 再整備は該当patchだけを変更し、`00_source`、生成物、評価artifactを直接編集しない。
- 再整備後に問題の`stateHash`が変わると以前の評価は自動で古くなる。
- 再評価も以前の評価sessionを再利用せず、新しい評価sessionで行う。
- 合格済みの他問題は再整備又は再評価しない。

## UIと状態

評価対象は同じ資格内から選びます。年度が`すべて`なら資格全体、特定年度ならその年度を範囲とし、資格・年度・絞り込みを変えたら選択をクリアします。

| 状態 | 主操作 |
| --- | --- |
| 整備が必要 | `整備を開始` |
| 評価待ち又は評価が古い | `評価を開始` |
| 評価中 | 進捗表示又は停止 |
| 評価不合格 | `再整備を開始` |
| 評価合格 | `この問題をFirestoreへ反映` |
| Firestore readback一致 | 操作なし |

複数問題を一度に選択しても、評価threadは問題ごとに分けます。一問の失敗で残りを止めず、サブスクリプションの利用上限を尊重してqueueと同時実行数を制御します。

## 安全境界

- `00_source`は常に閲覧専用とする。
- 整備と再整備の書込先は責務に合うpatch層だけとする。
- 評価はread-onlyとし、patch、生成物、Firestoreを変更しない。
- Firestore反映はapp-server sessionへ任せず、既存の公開preflightとUIの明示確認を使う。
- 未評価、不合格、古い評価、根拠不足、機械gate未完了の問題を公開しない。
- 反映直後のreadback一致だけを公開完了とする。
- serverは`127.0.0.1`へbindする。本人スマホから使う場合だけTailscale Serveのprivate HTTPSを使い、LAN公開、`0.0.0.0`、Funnelを使わない。

## 実装状態

現行実装は、整備runではCodex promptを保存・コピーし、評価runでは`codex exec`を別processとして起動します。app-server adapterへの移行が完了するまでは、この文書のsession分離とサブスクリプション認証を完全には強制できません。UIは実装済みと未実装を区別して表示します。

## 受入条件

1. ChatGPT認証以外では整備、評価、再整備を開始できない。
2. API key又は従量課金経路へfallbackしない。
3. usage-based plan、追加credit又は追加支出を許すspend controlがあるaccountでは開始できない。
4. 整備、評価、再整備、再評価のthread IDがすべて分かれている。
5. 評価threadは元問題1問だけをread-onlyで評価し、過去sessionの会話を受け取らない。
6. 再整備後は以前の評価が古くなり、新規評価threadに合格するまで公開できない。
7. `00_source`不変、patch責務、機械gate、明示的なFirestore確認とreadbackを維持する。
8. 利用上限、認証不一致、app-server停止を安全な未完了として記録し、再開できる。

標準起動:

```bash
python3 tools/question_bank/question_bank.py review-ui
```
