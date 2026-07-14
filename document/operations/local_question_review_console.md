# 問題整備システム

この文書は、ローカルGUI「問題整備システム」と`tools/question_review_console/`のUX、API、別セッション確認、書き込み安全性の正本です。問題整備工程は[幹](exam_pipeline_manual_and_automation.md)、patchの責務は[artifact契約](artifact_contract.md)、公開処理は[merge・検証・公開](delivery_workflow.md)を参照します。

## 目的

JSONを直接読み回らず、資格全体の進捗と例外を一つのローカル画面で扱います。

- 資格配下の全listGroupIdについて、次工程と残件を確認する。
- 資格選択と資格見出しはローカルコードではなく、scrape presetの資格和名で表示する。
- 選択工程の正本をGUI内で直接読み、更新内容を再起動なしで反映する。
- 元問題単位で問題文、全選択肢、正誤、解説、法令根拠を確認する。
- 整備を行ったセッションとは別のセッションで、全問題・全選択肢を根拠付きで確認する。
- 正誤確認と解説評価の結果から公開可否を自動計算し、未確認の問題を公開しない。
- patch projection、merge、convert、upload-ready、Firestoreを比較する。
- 指摘sidecarと、正本文書を参照するCodex依頼を生成する。
- 許可fieldだけをpatchへ直接保存する。
- 明示確認後だけ対象groupをFirestoreへ反映し、readbackする。

## 最終UX構成

画面は資格・対象年度の選択を共通headerに固定し、次の3 viewをtabで切り替えます。01から04の工程順、名称、正本文書は引き続き`config/question_maintenance_workflow.toml`だけを正本とし、view名と工程名を混同しません。

| view | 目的 | 主操作 |
| --- | --- | --- |
| `整備` | 01から04、merge・convertまでの不足を解消する。 | `整備を続ける`、`要確認のみ整備` |
| `品質確認` | 問題ごとの別セッション確認、解説評価、不一致の再整備を管理する。 | `未確認を別セッションで確認`、`基準未達を再整備` |
| `公開` | 公開条件、Firestore差分、反映結果を確認する。 | `公開前確認`、`Firestoreへ公開` |

資格一覧は装飾的なcardではなく比較できるtableとし、`整備完了`、`正誤確認済み`、`解説合格`、`再整備`、`保留`、`公開可能`、`Firestore一致`を元問題数で表示します。問題一覧も例外を先に並べ、通常時に必要な主操作は一つだけ表示します。

状態はicon、短い日本語、件数を併記し、色だけで区別しません。確認済みはcheck、作業中は進行表示、不一致・根拠不足は警告、`hold`は停止として統一し、手動toggleに見える表現を使いません。

`次にすること`は次の優先順でシステムが決めます。

1. 01から04又は生成物が不足していれば`整備を続ける`。
2. 別セッション確認が未実施又は古ければ`別セッションで確認`。
3. 不一致又は解説基準未達があれば`基準未達を再整備`。
4. 全公開条件を満たせば`公開前確認`。
5. 公開後のreadbackが不一致なら`Firestoreを再確認`。

スマホでは3 viewを横幅内に収まるtabとして固定し、集計、例外一覧、主操作の順に縦配置します。dialogは全画面とし、対象範囲と公開条件を本文で確認しながら、閉じる操作と主操作をsafe area内に固定します。

## 安全境界

| 対象 | UIの責務 |
| --- | --- |
| `00_source` | 閲覧専用。変更しない。 |
| 人間判断工程 | 対象pathと正本文書を含む依頼を生成する。UI自身は妥当性を推測しない。 |
| 別セッション確認 | frozen inputを読み、根拠と判定をreview artifactへ保存する。patchやFirestoreを直接変更しない。 |
| 公開可否flag | review artifact、内容hash、機械gateから決定論的に計算する。手動でONにできない。 |
| patch | 許可fieldだけ直接編集できる。その他はCodex依頼へ切り替える。 |
| merge・convert | 対象qualification / listGroupIdだけ既存pipelineを実行する。 |
| Firestore readback | 選択資格のupload-readyに列挙されたdocumentだけ読む。 |
| Firestore publish | project、件数、artifact SHA、差分を明示確認した場合だけ書く。 |

サーバーは`127.0.0.1`だけにbindし、任意pathやproject IDをrequestから受け取りません。

## 本人スマホからの接続

スマホ接続はTailscale Serveのprivate HTTPSだけを使います。サーバーは引き続き`127.0.0.1`の固定portにだけbindし、LAN公開、routerのport開放、`0.0.0.0` bind、Tailscale Funnelは使いません。

許可経路:

| 経路 | GET・静的file | mutation API |
| --- | --- | --- |
| Macのlocalhost | local Host完全一致 | local Host、local Origin、起動session token |
| Tailscale Serve | `.ts.net` Host、Tailscale login、許可済みiPhoneのTailscale IP | 左記、external Origin、起動session token |
| その他 | 拒否 | 拒否 |

external経路は`Tailscale-User-Login`を本人loginと完全一致させ、Serveが上書きする`X-Forwarded-For`を許可済みiPhoneのnode IPと照合します。Funnel headerがあるrequestは拒否します。端末制限の第一境界はtailnet policyとし、iPhone node IPからMacの`tcp:443`だけをgrantします。既存の広いgrantは狭いgrantで上書きできないため、同時に削除または非該当化します。

スマホ幅では工程一覧と長い説明を省略せず折り返し、dialogはブラウザの可視領域に追従する全画面表示にします。headerと操作buttonを固定したまま本文だけを縦スクロールでき、safe areaの上下へ操作要素を隠しません。

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

IPv4とIPv6の両方を許可する場合は`--tailscale-source-ip`を繰り返します。Tailscaleの端末再登録でIPが変わった場合は起動設定とtailnet policyの両方を更新します。完了確認は、Serveの行き先、Funnel無効、tailnet policy test、未許可Host・login・source IPの403、iPhone実機からのGETとmutation previewで閉じます。

## 工程カタログと作業ガイド

工程の順序、表示名、目的、patch層、工程ごとの正本文書は`config/question_maintenance_workflow.toml`だけで定義します。PythonとJavaScriptへ同じ一覧を複製しません。判定手順の本文は各Markdownが所有し、設定ファイルには本文を書きません。

GUIは選択資格の実データと工程カタログを結合し、現在地、不足理由、次工程を表示します。工程を選ぶと、その工程の詳細prompt、資格固有方針、[幹](exam_pipeline_manual_and_automation.md)を作業ガイド内で直接表示します。文書本文はAPIが都度読み、ガイド表示中は変更を自動再読込します。

patch更新のCodex依頼は、工程に固定された正本文書とGUIで選んだ対象pathを組み合わせて生成します。問題工程は複数選択でき、Codexは一問について選択工程を順に完了してから次の問題へ進みます。正本文書は安全要件なので対象範囲から外せません。

問題工程と公開準備では、資格配下の年度又はフォルダを複数選択して整備範囲を固定します。現在表示中の単一年度を初期選択とし、一覧が`すべて`なら全年度を初期選択します。`全件洗い替え`、`未作業のみ`、`要確認のみ`はいずれも選択範囲内だけへ適用し、全年度選択が従来の資格全体に相当します。資格方針と03cカテゴリ設計は資格単位のままです。

複数工程の全件洗い替えでは、対象問題数を重複しない元問題数、作業量を`対象問題数 × 選択工程数`の延べ工程判定数として併記します。選択した各工程へ全対象問題を一問ずつ通し、現行法監査も既存の`isLawRelated`だけで先に除外せず、各問題で法令該当性を再確認します。

`03c カテゴリ設計`は問題単位工程から分離した資格単位工程です。`category.json`が未作成又は不正な間は04を開始できず、GUIは03cを次作業として示します。

状態:

| 状態 | 判定 |
| --- | --- |
| `完了` | 対象patch又はartifactが全件揃い、未解決issueがない。 |
| `未着手` | 対象patchがない。 |
| `作業中` | 一部だけ存在する。 |
| `要確認` | 前工程欠落又は当該fieldのissueがある。 |
| `前工程待ち` | 前提工程が完了していない。 |

`00_source`に値があっても人間確認済みとはみなしません。02aは`23_correctChoiceText_fixed`の全問coverage、03bは法令関連問題の`lawRevisionFacts`と未解決issue、03cは`category.json`の構造、出力はgroup単位のmerge・convert・upload-ready一致で判定します。Firestore一致はローカル出力完了と分離します。

開始前に正本文書数、対象source数、更新先数、対象件数と選択listGroupId配列をpreviewし、runを次へ保存します。

```text
output/question_review_console/workflow_runs/<qualification>/<runId>/
  manifest.json
  prompt.md
  result.json
```

`result.json`は固定pathの完了receiptです。`succeeded`には非空summaryと、1件以上すべて`pass`の検証commandが必要です。出力jobは完了groupをmanifestへ記録し、中断後は残りだけ再開します。

## 別セッションによる品質確認

品質確認は01から04を作成・修正する工程ではなく、upload-ready候補を読み取り専用で評価する独立laneです。整備に使った会話や結論を暗黙に引き継がず、元問題ごとに新しい別セッションを開始します。

実行単位は`元問題1問`です。システムは問題文、全選択肢、出題時点、資格方針、公式正答、現在の正誤、解説を比較用のfull frozen inputとして固定します。このうち正誤調査と独立確認へ渡すblind inputからは、公式正答、現在の`questionIntent`・正誤・解説、先行セッションの結果を除きます。両セッションは問題文から設問意図と一肢ごとの判定を独立導出し、完了後にだけシステムが非公開の比較対象と照合します。選択肢だけを問題文から切り離して一括評価しません。

別セッションは次の役割に分けます。

1. `正誤調査`: 公式資料・一次資料を調査し、設問意図と全選択肢の命題を`true`、`false`、`insufficient_evidence`で判定する。
2. `独立確認`: 正誤調査の結論を受け取らず、同じfrozen inputを別セッションで再調査する。
3. `解説評価`: 正誤が確認済みになった後、確定した選択肢別判定と根拠を基準に現在の解説を採点する。
4. `追加確認`: 正誤調査と独立確認の不一致、根拠競合、高リスク判定だけを新しい別セッション又は人間確認へ送る。

正誤調査、独立確認、解説評価はそれぞれ異なる`sessionId`を必須とします。独立確認のinputに正誤調査の判定、score、confidenceを含めず、解説評価にも整備セッションの会話を渡しません。同じ結論でも根拠が不足していれば確認済みにせず、件数、confidence、AI同士の多数決だけを公開根拠にしません。

公式解答表は出題時正答との比較対象であり、各選択肢の実質的な正誤を裏付けるevidenceとして単独では認めません。各別セッションは選択肢の内容自体を一次資料又は独立計算で確認します。

法令問題は[現行法監査](lawzilla_mcp_question_maintenance_workflow.md)のevidence bundleをlocatorとして利用し、その本文とhashを別セッションで確認します。出題当時と現行法の判定を分け、必要な条・項・号、revision、基準日が不足する場合は推測せず`insufficient_evidence`とします。計算問題は式、代入値、単位、丸めを独立に再計算します。それ以外も資格方針が定める公式資料・一次資料を優先し、取得元、locator、取得日時、内容hashを残します。

### 実行状態

| 内部状態 | UI表示 | 次の処理 |
| --- | --- | --- |
| `not_started` | 別セッション未実施 | 正誤調査を開始する。 |
| `queued`, `running` | 確認待ち、確認中 | 完了を待つ。中断後は同じrunを再開できる。 |
| `answer_verified` | 正誤確認済み | 解説評価へ進む。 |
| `conflict` | 判定不一致 | 追加確認へ送る。 |
| `insufficient_evidence` | 根拠不足 | 追加調査又は`hold`へ送る。 |
| `explanation_failed` | 解説基準未達 | 03の再整備へ送る。 |
| `ready` | 公開可能 | 公開前確認へ進む。 |
| `stale` | 再確認待ち | 変更された問題だけ別セッションで再確認する。 |

開始前previewには対象資格、年度、元問題数、予定別セッション数、現在の未確認・不一致・古い評価の件数を表示します。操作modeは`未確認のみ`、`変更後のみ`、`判定不一致のみ`、`全件を別セッションで再確認`とします。実行中は元問題単位で永続queueへ保存し、一時停止、再開、失敗した問題だけの再試行を可能にします。

### 成果物と鮮度

保存先は[artifact契約](artifact_contract.md)に従い、次の構造とします。

```text
output/<qualification>/review/question_quality/<runId>/
  manifest.json
  questions/<questionKeyHash>/
    answer_research.json
    answer_verification.json
    explanation_assessment.json
    verdict.json
  summary.json
```

`answer_research.json`、`answer_verification.json`、`explanation_assessment.json`は各別セッションの構造化出力です。`verdict.json`はAIが直接作る公開承認ではなく、システムが両判定とgateを比較して生成します。各成果物は少なくとも`reviewKey`、`questionInputHash`、`blindAnswerInputHash`、`policyHash`、`promptHash`、`profileHash`、`sessionId`、`sessionRole`、`model`、`modelVersion`、`startedAt`、`finishedAt`、`resultHash`を持ちます。

各選択肢の判定には`choiceIndex`、命題、判定、短い理由、evidence配列を保存します。法令問題は取得できる範囲で`examTimeVerdict`と`currentVerdict`を分けます。evidenceは`sourceType`、文書名又はURL、locator、基準日又はrevision、`retrievedAt`、`contentHash`を持ちます。内部思考過程は保存せず、第三者が根拠をたどれる短い理由だけを残します。

`questionInputHash`はfull frozen input全体、`blindAnswerInputHash`は正誤調査・独立確認へ実際に渡したblind inputを表します。問題内容、資格方針、確認prompt、評価profileのいずれかが変わった場合は既存結果を`stale`にし、公開可能状態を解除します。新基準で再確認しても内容変更が不要ならpatchを作り直しません。

### 解説評価

解説は100点で表示し、既定profileは次の配点とします。

| 観点 | 点数 |
| --- | ---: |
| 確定した正誤・結論との整合 | 40 |
| 根拠と理由の妥当性 | 25 |
| 全選択肢の説明充足 | 20 |
| 明瞭さと学習上の有用性 | 15 |

合格は`90点以上`かつ重大指摘0件です。正誤との矛盾、根拠のない断定、必要な選択肢の説明欠落、計算過程の破綻、法令基準日の混同又は出題当時と現行法の差分説明欠落は点数にかかわらず不合格とします。profileと閾値はhashで固定し、profile変更後は対象評価を`stale`にします。

### 公開flag

次のflagはUI操作で直接変更せず、成果物から毎回再計算します。

| flag | trueになる条件 |
| --- | --- |
| `allChoicesVerified` | 全選択肢で正誤調査と独立確認が一致し、根拠不足がない。法令問題は必要な出題時・現行の両判定を含む。 |
| `answerMappingMatched` | 導出した設問意図と学習上正答が現在fieldと整合する。法令差分がある場合は出題時公式正答を`examTime`に保持し、現行正答との差分が三次確定済みである。 |
| `explanationPassed` | 最新profileで解説が合格し、重大指摘がない。 |
| `evaluationCurrent` | input、方針、prompt、profileのhashが現在値と一致する。 |
| `lawAuditPassed` | 非法令問題、又は必要な法令監査が公開可能stateまで完了している。 |
| `machineGatePassed` | merge、convert、required field、ID、upload dry-runの対象gateが通っている。 |
| `publishReady` | 上記すべてがtrueで、`hold`と未解決issueが0件である。 |

集計の`100%`はscore平均ではなく、選択範囲の全元問題で`publishReady=true`になった割合です。一つでも未確認、古い評価、不一致、根拠不足、基準未達があれば選択範囲全体のFirestore公開を許可しません。

### 再整備ループ

品質確認成果物はpatchを直接変更しません。`answerMappingMatched=false`は02a、法令根拠不足は02b又は03b、解説不合格は03、形式・設問意図の矛盾は01又は02の作業itemへ戻します。UIは失敗理由、対象field、選択肢、evidenceを付けた再整備依頼を作ります。

再整備後は対象groupをmerge・convertし、`questionInputHash`が変わった元問題だけを別セッションで再確認します。合格済みでhashが変わらない問題を再実行しません。この循環を`確認 -> 再整備 -> 再確認`として履歴に残します。

## 元問題単位の状態

安定キー:

```text
<qualification>:<listGroupId>:<sourceStem>:<originalQuestionId>
```

本文hashだけを主キーにしません。表示状態は次の順です。

| 状態 | 読み取り元 |
| --- | --- |
| `source` | `00_source` |
| `projected` | sourceと最新patchのインメモリ合成 |
| `merged` | 最新`30_merged_2` |
| `converted` | 最新`40_convert` |
| `uploadReady` | 正規upload artifact |
| `quality` | 最新の`question_quality/<runId>/questions/<questionKeyHash>/verdict.json` |
| `live` | 本番Firestore readback |

projectionの適用順はmerge実装と合わせます。UI固有のpatch優先順位を作りません。内容hashで古さを判定し、ローカル成果物更新後の保存済みFirestore比較を最新一致として表示しません。

## 一覧と詳細

一覧は例外優先で、資格、listGroupId、法令、issue、review状態、正誤確認、解説評価、公開可否、Firestore差分、自由検索で絞り込みます。50件単位でページングし、資格全体の集計値は全件を維持します。

詳細では次を元問題へ再構成します。

- 問題文と全選択肢。
- 選択肢ごとの正誤と解説。
- 想定質問と保存済み回答。
- 法令名、条・項・号、検証状態、監査facts。
- 別セッションごとの状態、選択肢別根拠、解説score、公開flagとblock理由。
- sourceからliveまでのfield単位差分。

Firestoreで選択肢ごとに分割されていても、レビュー単位は元問題です。配列差分は選択肢index、質問index、法令参照単位で表示します。

## issue

既存checkerとfield contractを正本にし、UI独自判定は表示用の軽量検査に限定します。

| code | 意味 |
| --- | --- |
| `live_mismatch` | upload-readyとFirestoreが不一致。 |
| `firestore_readback_stale` | 保存済み比較よりローカル成果物が新しい。 |
| `answer_explanation_mismatch` | 正誤と解説先頭が不一致。 |
| `merge_stale`, `convert_stale` | 後段artifactがpatch又はmergeと不一致。 |
| `required_field_missing` | 公開必須fieldがない。 |
| `law_audit_metadata_incomplete` | 採点正答はあるが現行法監査factsが不足。 |
| `law_audit_verdict_mismatch` | 採点正答と監査正答が不一致。 |
| `identity_mismatch` | ID対応が崩れている。 |
| `law_hold`, `law_basis_missing` | 法令監査未完了又は根拠不足。 |
| `explanation_missing` | 解説欠損又は配列長不一致。 |
| `post_fix_review`, `manual_flag` | 修正後確認又は人間指定。 |
| `quality_unverified`, `quality_stale` | 別セッション確認が未実施又は現在の内容・基準より古い。 |
| `answer_verification_conflict` | 正誤調査と独立確認が不一致。 |
| `answer_evidence_insufficient` | 一つ以上の選択肢で根拠不足。 |
| `answer_mapping_mismatch` | 選択肢別判定、公式正答、設問意図、正答fieldが不整合。 |
| `explanation_quality_failed` | 解説が基準点未満又は重大指摘あり。 |
| `quality_session_failed` | 別セッションが失敗又は不正な成果物を返した。 |
| `live_quality_unverified` | Firestoreに公開中だが、現行profileの別セッション確認が未完了。 |

## review sidecarとCodex依頼

review状態は`unreviewed`、`needs_review`、`awaiting_codex`、`post_fix_review`、`approved`、`hold`です。承認はprojected hashへ結び付け、patch変更後は無効にします。

保存先:

```text
output/question_review_console/<qualification>/<listGroupId>/
  reviews/<reviewId>.json
  prompts/<reviewId>.md
```

review JSONと品質確認成果物はmerge対象ではありません。指摘には対象field、選択肢、data path、説明、調査範囲、source/projected/upload-ready/live hashと関連pathを保存します。

通常のCodex依頼はreview JSON、対象path、指摘、変更禁止事項、検証条件を含みます。工程一括依頼は、対象sourceと出力path、[prompt正本](../../prompt/README.md)、資格方針だけを示し、問題本文や手順を複製しません。

法令監査一括依頼はqualification全体の未完了`21_explanationText_added`を重複なく列挙します。プロンプトには絶対path一覧と[現行法監査正本](lawzilla_mcp_question_maintenance_workflow.md)への参照だけを含め、一問分の問題文を展開しません。CodexはLawzilla MCPとFirestore条文検索を使い、一問一肢ずつ本文を照合します。

## 直接編集

許可field:

- `correctChoiceText`
- `explanationText`
- `suggestedQuestions`
- `suggestedQuestionDetails`

解説系は`21_explanationText_added`、正誤は`23_correctChoiceText_fixed`へ保存します。問題文、選択肢、形式、設問意図、分類、法令根拠・監査factsはCodex依頼へ切り替えます。

法令問題の正誤変更は直接保存しません。正誤変更時は理由と解説先頭の整合を必須にし、対象entryだけをatomic updateします。識別field、配列長、enum、必須fieldを検証できない場合は書き込みません。

## sync・readback・publish

`パッチ変更を反映`は選択groupへ`prepare_firestore_upload.py --skip-update-category-counts --upload-dry-run`を実行し、前後の`00_source` hashを確認します。

資格一括readbackは、選択qualificationのupload-readyにあるdocument IDだけをfield mask付き`get_all`で取得します。400件ごとに分割し、結果を次へ保存します。

```text
output/question_review_console/firestore_readback/<qualification>/
  manifest.json
  ...
```

`公開` viewは選択資格・年度の全元問題が`publishReady=true`になるまで公開buttonをdisabledにし、block理由と件数を表示します。公開可能な問題だけを暗黙に抜き出す部分公開は行いません。Firestoreで選択肢が別documentでも、同じ`originalQuestionId`の全documentを一単位として扱います。

`公開前確認`ではqualification、対象年度、project ID、元問題数、Firestore document数、追加・更新・削除件数、artifact SHA、各公開flagの合格数を表示します。project IDや任意pathを画面入力させません。削除又は想定外の対象外差分がある場合は停止します。

確認dialogで対象範囲と`全問題の別セッション確認済み`を再表示し、明示操作`Firestoreへ公開`の後だけ既存uploaderを実行します。実行直前にFirestoreを再読込し、preflight token、artifact SHA、品質確認hashが変わっていないことを確認します。差分だけを書き、直後に同じdocumentを自動readbackします。

公開runは次へ保存します。

```text
output/question_review_console/publish_runs/<qualification>/<runId>/
  manifest.json
  preflight.json
  result.json
  readback.json
```

upload commandが成功してもreadback不一致なら`published`にせず、`readback_mismatch`として再確認対象にします。全document一致を確認した場合だけUIを`公開済み・Firestore一致`へ更新します。

導入時にすでにFirestoreへ存在し、品質確認artifactがない問題は自動削除しません。UIでは`公開中・再確認待ち`として全件確認queueへ入れ、そのscopeに対する次回の更新公開を`publishReady=true`になるまで止めます。既存公開の停止又は削除は、この品質確認runとは別の明示操作とします。

## API

以下を最終API契約とします。現行実装との差分は次の「実装と検証」で管理します。

| method | path | 用途 |
| --- | --- | --- |
| `GET` | `/api/inventory` | 資格・group・件数。 |
| `GET` | `/api/workflow-catalog` | GUI工程構造と正本文書の組合せ。 |
| `GET` | `/api/document` | 許可された継続正本Markdownの本文。 |
| `GET` | `/api/qualification-workflow` | 資格全体の工程状態。 |
| `GET` | `/api/qualification-runs` | run履歴と進捗。 |
| `GET` | `/api/quality-summary`, `/api/quality-runs` | 品質確認の集計とrun履歴。 |
| `GET` | `/api/quality-runs/<runId>` | 元問題別の別セッション状態と結果。 |
| `GET` | `/api/questions` | 一覧・検索・filter。 |
| `GET` | `/api/questions/<reviewKey>` | 問題詳細。 |
| `GET` | `/api/questions/<reviewKey>/fingerprint` | patch変更検知。 |
| `POST` | `/api/reviews` | reviewとCodex依頼作成。 |
| `POST` | `/api/reviews/<reviewId>/status` | review状態更新。 |
| `POST` | `/api/qualification-workflow/prompt` | 工程依頼生成。 |
| `POST` | `/api/qualification-runs/preview` | run対象preview。 |
| `POST` | `/api/qualification-runs/start` | run開始。 |
| `POST` | `/api/qualification-runs/resume-prompt` | 保存prompt再取得。 |
| `POST` | `/api/quality-runs/preview`, `/start` | 別セッション確認の対象preview・開始。 |
| `POST` | `/api/quality-runs/pause`, `/resume`, `/retry` | 永続queueの停止・再開・失敗分再試行。 |
| `POST` | `/api/direct-edits/preview`, `/apply` | 直接編集。 |
| `POST` | `/api/groups/<qualification>/<listGroupId>/sync-preview`, `/sync` | ローカル成果物同期。 |
| `POST` | `/api/groups/<qualification>/<listGroupId>/publish-preview`, `/publish` | Firestore preflight・反映。 |
| `GET`, `POST` | `/api/publish-runs`, `/api/publish-runs/preview`, `/api/publish-runs/start` | 複数年度を含む公開runの履歴・preflight・開始。 |
| `POST` | `/api/firestore-readback/preview`, `/run` | 資格一括readback。 |
| `GET` | `/api/jobs/<jobId>` | 非同期job状態。 |

## 実装と検証

実装は`tools/question_review_console/`、テストは`tests/test_question_review_*.py`です。追加build工程やブラウザ側credentialを導入しません。別セッションworkerはpromptを標準入力で受け取り、構造化JSONを標準出力するserver-side adapterから起動します。promptをshell commandへ展開せず、資格ごとの同時実行数を制限します。

現行実装は人間工程のpromptを保存してclipboardへ渡すところまでです。最終仕様への移行は、品質確認用API、永続queue、別セッションadapter、品質artifact reader、公開gate、公開run receiptを実装し、既存のgroup publish endpointにも同じ`publishReady`判定を適用した時点で完了とします。

安全要件:

- mutation APIは起動session tokenと許可経路のexact Originを必須にする。
- Hostを経路のallowlistと照合し、Tailscale経路はGETと静的fileも含めてloginとsource IPを必須にする。
- Tailscale公開時も`127.0.0.1` bindを維持し、Funnel requestを拒否する。
- repo外path、任意file path、任意project IDを受け付けない。
- JSON更新は一時file、fsync、atomic replaceを使う。
- 書き込み前後のhashが変わった場合は停止する。
- credentialと環境変数をresponse・logへ出さない。
- 同じqualification / groupでsyncとpublishを同時実行しない。
- 同じ元問題の正誤調査、再整備、公開を競合実行しない。
- 別セッションの成果物schema、sessionId分離、input hashをserver側で検証する。
- `publishReady`をrequest bodyから受け取らず、server側で再計算する。

受入条件:

1. 品質成果物がない問題を含むscopeでは公開buttonが有効にならない。
2. 一肢でも不一致、根拠不足、古い評価があれば公開できない。
3. 正誤調査と独立確認が異なるsessionで、独立確認inputに先行判定が含まれない。
4. patch又は評価profile変更後、該当問題だけ`stale`になり公開可能状態が解除される。
5. 全flagと機械gateが合格したscopeだけ、明示確認後にFirestoreへ反映できる。
6. upload成功後もreadback不一致なら公開完了として表示しない。
7. ブラウザ再読込又はserver再起動後もrunを再開でき、同じ問題を重複起動しない。
8. iPhone幅で対象、block理由、確認buttonが重ならず、safe area外へ隠れない。
9. 既存live問題は自動削除せず`公開中・再確認待ち`として識別でき、次回更新だけがgateされる。

標準起動:

```bash
python3 tools/question_bank/question_bank.py review-ui
```
