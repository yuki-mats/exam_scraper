# 問題整備システム

この文書は、ローカルGUI「問題整備システム」と`tools/question_review_console/`のUX、別セッション評価、Firestore操作の安全境界の正本です。全工程の順序は[問題整備ワークフロー](exam_pipeline_manual_and_automation.md)、保存先は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照します。

## 目的

大量の過去問を、整備と評価を分離した次の流れで扱います。

```text
複数問題を整備 -> 評価待ちへ蓄積 -> 後日、任意の問題を複数選択
                                  -> 問題ごとに別セッションで評価
                                  -> 合格は公開可能 / 不合格だけ再整備
```

- 資格全体について、どこまで終わり、次に何をするかを表示する。
- 問題文、全選択肢、正誤、解説、根拠を元問題単位で扱う。
- 整備した時期や作業sessionに関係なく、評価待ちから任意の問題を複数選択する。
- 選択した各問題を整備とは別の新しいセッションで評価する。
- 評価結果から公開可否を自動計算し、未評価又は不合格の問題を公開しない。
- 合格した問題は他の問題を待たず、UIの明示確認後にFirestoreへ反映する。
- 反映直後に同じdocumentをreadbackし、一致した場合だけ完了にする。

## 基本UX

3つの画面や複数の評価laneには分けません。既存の問題一覧で複数選択し、`選択した問題を評価`を1回実行します。問題詳細には評価状態、定量値、次の主操作を表示します。

評価対象は常に同じ資格の問題だけです。年度が`すべて`なら資格全体、特定年度ならその年度が一覧と選択の範囲になります。資格、年度又は絞り込みを変更したときは選択をクリアし、別範囲の問題を隠れたまま混在させません。

| 状態 | UI表示 | 主操作 |
| --- | --- | --- |
| 生成物不足又はblocking issueあり | `整備が必要` | `整備を続ける` |
| 現在内容の評価なし | `未評価` | `別セッションで評価` |
| 評価process実行中 | `評価中` | 進捗を表示する。 |
| 評価不合格 | `要再整備` | `整備を続ける` |
| 評価合格かつFirestore差分あり | `公開可能` | `この問題をFirestoreへ反映` |
| 評価合格かつreadback一致 | `Firestore反映済み` | 主操作なし。 |
| 合格後に問題内容が変更された | `再評価が必要` | `別セッションで再評価` |

定量表示は、意味の異なる数値を一つの総合点へ混ぜません。

- `正誤確認`: 根拠付きで確認できた選択肢数 / 全選択肢数。
- `解説品質`: 0から100点。合格基準は90点以上かつ重大指摘0件。
- `公開準備`: 機械gate、別セッション評価、Firestore一致の3段階。

一覧は例外を先に並べ、各問題に選択checkboxと短い状態badgeを一つ表示します。`評価待ち`、`要再整備`、`公開可能`、`反映済み`で絞り込み、表示中の対象をまとめて選択できます。詳細では評価理由、選択肢別判定、根拠、解説点数、再整備対象を確認できます。

## 安全境界

| 対象 | UIの責務 |
| --- | --- |
| `00_source` | 閲覧専用。変更しない。 |
| 01から04 | 対象pathと正本文書を含む依頼を作る。工程名と順序は`config/question_maintenance_workflow.toml`から読む。 |
| 別セッション評価 | upload-ready候補を読み取り、評価artifactだけを保存する。patchやFirestoreを変更しない。 |
| `publishReady` | 現在の問題内容、評価artifact、機械gateからserver側で計算する。手動変更を受け付けない。 |
| patch | 許可fieldだけ直接編集できる。その他はCodex依頼へ切り替える。 |
| Firestore readback | upload-readyに列挙された対象documentだけ読む。 |
| Firestore反映 | project、問題、件数、差分、hashを明示確認した場合だけ書く。 |

サーバーは`127.0.0.1`だけにbindし、任意pathやproject IDをrequestから受け取りません。

## 本人スマホからの接続

スマホ接続はTailscale Serveのprivate HTTPSだけを使います。LAN公開、routerのport開放、`0.0.0.0` bind、Tailscale Funnelは使いません。

| 経路 | GET・静的file | mutation API |
| --- | --- | --- |
| Macのlocalhost | local Host完全一致 | local Host、local Origin、起動session token |
| Tailscale Serve | `.ts.net` Host、Tailscale login、許可済みiPhoneのTailscale IP | 左記、external Origin、起動session token |
| その他 | 拒否 | 拒否 |

external経路は`Tailscale-User-Login`を本人loginと完全一致させ、Serveが上書きする`X-Forwarded-For`を許可済みiPhoneのnode IPと照合します。Funnel headerがあるrequestは拒否します。端末制限の第一境界はtailnet policyです。

スマホ幅では一覧、評価結果、主操作の順に縦配置します。dialogは可視領域に追従する全画面表示とし、headerと操作buttonをsafe area内へ固定します。

起動例:

```bash
python3 tools/question_bank/question_bank.py review-ui \
  --host 127.0.0.1 \
  --port 8765 \
  --no-browser \
  --tailscale-origin https://<mac>.<tailnet>.ts.net \
  --tailscale-login <login> \
  --tailscale-source-ip <iphone-tailscale-ip>

tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
tailscale funnel status
```

完了確認は、Serveの行き先、Funnel無効、未許可Host・login・source IPの403、iPhone実機からのGETとmutation previewで閉じます。

## 工程カタログと作業ガイド

01から04の順序、名称、目的、patch層、正本文書は`config/question_maintenance_workflow.toml`だけで定義します。Python、JavaScript、Markdownへ工程一覧を複製しません。別セッション評価とFirestore反映は01から04の後にあるシステム状態であり、工程カタログへ追加しません。

GUIは選択資格の実データと工程カタログを結合し、現在地、不足理由、次工程を表示します。工程を選ぶと、工程prompt、資格固有方針、この幹を作業ガイド内で直接表示します。

`00_source`に値があっても整備済みとはみなしません。02aは`23_correctChoiceText_fixed`のcoverage、03bは法令関連問題の`lawRevisionFacts`と未解決issue、03cは`category.json`、出力はgroup単位のmerge、convert、upload-ready一致で判定します。

工程runは次へ保存します。

```text
output/question_review_console/workflow_runs/<qualification>/<runId>/
  manifest.json
  prompt.md
  result.json
```

## 別セッション評価

### 選択と実行単位

利用者が開始する評価runは、同じ資格に属する任意の`元問題1問以上`を対象にします。現在の資格・年度範囲で一覧に読み込んだ問題をまとめて選択でき、以前の作業で整備した問題も同じrunへ含められます。

品質結果と再試行を混ぜないため、workerの実行単位は`元問題1問`です。選択された各問題について、整備に使った会話を再利用しない新しいprocess又はsessionを一つずつ起動します。一問の失敗でrun全体を停止せず、残りを継続します。入力には次を固定します。

同一runでは独立性を保ったまま最大4問を並列評価します。端末負荷やprovider制約に合わせて変更する場合だけ`QUESTION_EVALUATION_CONCURRENCY`を設定し、値は1から8の範囲に制限します。

- `reviewKey`と現在の`stateHash`。
- 問題文、設問意図、全選択肢。
- 現在の正誤、公式正答、解説。
- 資格方針、出題時点、必要な法令監査facts。
- 参照できるsource、patch、upload-readyのpath。

現在値は比較対象であり根拠ではありません。評価sessionは問題文と全選択肢を一体で読み、各選択肢を一次資料、公式資料、法令本文又は独立計算で確認します。選択肢だけを切り離した一括判定、confidenceだけの合格、公式解答表だけを根拠にした合格は認めません。

法令問題は出題時と現行法を分け、条・項・号、基準日又はrevisionを根拠へ残します。計算問題は式、代入値、単位、丸めを確認します。判断できない選択肢は推測せず不合格にします。

### 出力

server-side adapterはpromptを標準入力で渡し、評価sessionから構造化JSONを受け取ります。最低限、次を検証して保存します。

- `sessionId`、`reviewKey`、`stateHash`、開始・終了日時。
- 全選択肢について`choiceIndex`、選択肢の記述自体の真偽、短い理由、1件以上の根拠。
- 現在の正答対応が正しいか。
- 解説点数、重大指摘、改善事項。
- 総合結果`passed`又は`needs_rework`。

保存先:

```text
output/question_review_console/<qualification>/<listGroupId>/
  evaluations/<questionKeyHash>.json
  evaluation_prompts/<questionKeyHash>.md
```

内部思考過程は保存せず、第三者が確認できる結論、短い理由、根拠locatorだけを残します。評価processが失敗又は不正なJSONを返した場合は合格にせず、同じ問題を再実行できるようにします。

### 合格条件

`publishReady=true`は次をすべて満たす場合だけserverが計算します。

1. 評価の`stateHash`が現在の問題内容と一致する。
2. 全選択肢に根拠付き判定があり、現在の正誤と一致する。
3. 公式正答、設問意図、`correctChoiceText`の対応が一致する。
4. 解説が90点以上で重大指摘がない。
5. 法令監査を要する問題に未解決の`hold`又は根拠不足がない。
6. merge、convert、upload-readyが現在内容と一致し、blocking issueがない。

AIが返した`passed`だけを信用せず、選択肢数、根拠、点数、hash、機械gateをserver側で再検証します。

### 再整備

不合格結果は、問題全体を曖昧に差し戻さず、対象選択肢、理由、根拠、推奨工程を表示します。正答不一致は02a、法令根拠不足は02b又は03b、解説不合格は03、形式・設問意図は01又は02へ戻します。

再整備後に`stateHash`が変わると、以前の評価は`再評価が必要`になります。同じ問題を新しい別セッションで再評価し、合格するまでこのループを繰り返します。他の合格済み問題を再評価しません。

## 元問題単位の状態

安定キー:

```text
<qualification>:<listGroupId>:<sourceStem>:<originalQuestionId>
```

表示は`source`、`projected`、`merged`、`converted`、`uploadReady`、`evaluation`、`live`を同じ元問題へ再構成します。Firestoreで選択肢ごとに分割されていても、評価と公開の単位は元問題です。

一覧は資格、listGroupId、法令、issue、評価状態、Firestore差分、自由検索で絞り込みます。詳細では問題文、全選択肢、正誤、解説、法令根拠、評価結果、sourceからliveまでの差分を表示します。

## reviewと直接編集

人間の指摘sidecarは従来どおり次へ保存します。

```text
output/question_review_console/<qualification>/<listGroupId>/
  reviews/<reviewId>.json
  prompts/<reviewId>.md
```

直接編集を許可するfieldは`correctChoiceText`、`explanationText`、`suggestedQuestions`、`suggestedQuestionDetails`だけです。解説系は`21_explanationText_added`、正誤は`23_correctChoiceText_fixed`へ保存します。法令問題の正誤変更と、それ以外のfieldはCodex依頼へ切り替えます。

## Firestore反映

`パッチ変更を反映`は選択groupへ`prepare_firestore_upload.py --skip-update-category-counts --upload-dry-run`を実行し、前後の`00_source` hashを確認します。

問題単位の公開preflightは、upload-readyから選択した元問題に属する全documentを抽出します。project ID、問題ID、document数、追加・更新件数、元artifact SHA、評価hashを表示し、削除又は別問題のdocumentが含まれる場合は停止します。

確認後だけ対象documentの一時artifactを既存uploaderへ渡し、直後に同じdocumentをreadbackします。全対象fieldが一致した場合だけ`Firestore反映済み`にします。差分がなければ書き込まず、既存documentの`updatedAt`も変更しません。

```text
output/question_review_console/publish_runs/<qualification>/<runId>/
  manifest.json
  preflight.json
  artifact.json
  result.json
  readback.json
```

group単位の公開APIは無効です。評価合格した問題だけを問題詳細から反映します。

## API

複数選択評価と問題単位公開に追加するAPIは次の4つです。既存のinventory、workflow、review、sync、readback、job APIは維持します。

| method | path | 用途 |
| --- | --- | --- |
| `POST` | `/api/evaluations/preview` | 選択問題、評価可能件数、別セッション数を確認する。 |
| `POST` | `/api/evaluations/start` | 選択問題を問題ごとの別セッション評価へ送る。 |
| `POST` | `/api/questions/<questionId>/publish-preview` | 合格済み問題のFirestore差分を確認する。 |
| `POST` | `/api/questions/<questionId>/publish` | 明示確認後に対象問題だけ反映する。 |

`GET /api/questions`と`GET /api/questions/<questionId>`は`evaluation`、`publishReady`、`nextAction`を返します。非同期処理は既存の`GET /api/jobs/<jobId>`で確認します。

## 実装と受入条件

実装は`tools/question_review_console/`、テストは`tests/test_question_review_*.py`です。追加build工程やブラウザ側credentialを導入しません。

安全要件:

- mutation APIは起動session tokenと許可経路のexact Originを必須にする。
- repo外path、任意file path、任意project IDを受け付けない。
- JSON更新は一時file、fsync、atomic replaceを使う。
- credentialと環境変数をresponse又はlogへ出さない。
- 同じ元問題の評価と公開を競合実行しない。
- `publishReady`をrequest bodyから受け取らず、server側で再計算する。

受入条件:

1. 異なる作業回で整備した問題を含め、一覧から任意の複数問題を一度に評価開始できる。
2. 1回の評価runへ異なる資格を混在させず、年度変更時に以前の選択を残さない。
3. 選択した各問題は別の新規sessionで評価され、一問の失敗後も残りを継続する。
4. 未評価、不合格、古い評価の問題はFirestoreへ反映できない。
5. 全選択肢の根拠と解説基準をserver側で検証する。
6. 合格した問題は、同じrunの不合格問題に関係なく明示確認後に反映できる。
7. 同じ元問題に属する全Firestore documentをまとめ、一部だけ公開しない。
8. upload成功後もreadback不一致なら完了表示にしない。
9. 問題変更後は評価が自動で古くなり、再評価するまで公開できない。
10. iPhone幅で複数選択、状態、評価結果、主操作が重ならず、safe area外へ隠れない。

標準起動:

```bash
python3 tools/question_bank/question_bank.py review-ui
```
