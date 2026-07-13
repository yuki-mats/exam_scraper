# 問題整備システム

この文書は、ローカルGUI「問題整備システム」と`tools/question_review_console/`のAPI・書き込み安全性の正本です。問題整備工程は[幹](exam_pipeline_manual_and_automation.md)、patchの責務は[artifact契約](artifact_contract.md)を参照します。

## 目的

JSONを直接読み回らず、資格全体の進捗と例外を一つのローカル画面で扱います。

- 資格配下の全listGroupIdについて、次工程と残件を確認する。
- 資格選択と資格見出しはローカルコードではなく、scrape presetの資格和名で表示する。
- 選択工程の正本をGUI内で直接読み、更新内容を再起動なしで反映する。
- 元問題単位で問題文、全選択肢、正誤、解説、法令根拠を確認する。
- patch projection、merge、convert、upload-ready、Firestoreを比較する。
- 指摘sidecarと、正本文書を参照するCodex依頼を生成する。
- 許可fieldだけをpatchへ直接保存する。
- 明示確認後だけ対象groupをFirestoreへ反映し、readbackする。

## 安全境界

| 対象 | UIの責務 |
| --- | --- |
| `00_source` | 閲覧専用。変更しない。 |
| 人間判断工程 | 対象pathと正本文書を含む依頼を生成する。UI自身は妥当性を推測しない。 |
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
| `live` | 本番Firestore readback |

projectionの適用順はmerge実装と合わせます。UI固有のpatch優先順位を作りません。内容hashで古さを判定し、ローカル成果物更新後の保存済みFirestore比較を最新一致として表示しません。

## 一覧と詳細

一覧は例外優先で、資格、listGroupId、法令、issue、review状態、Firestore差分、自由検索で絞り込みます。50件単位でページングし、資格全体の集計値は全件を維持します。

詳細では次を元問題へ再構成します。

- 問題文と全選択肢。
- 選択肢ごとの正誤と解説。
- 想定質問と保存済み回答。
- 法令名、条・項・号、検証状態、監査facts。
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

## review sidecarとCodex依頼

review状態は`unreviewed`、`needs_review`、`awaiting_codex`、`post_fix_review`、`approved`、`hold`です。承認はprojected hashへ結び付け、patch変更後は無効にします。

保存先:

```text
output/question_review_console/<qualification>/<listGroupId>/
  reviews/<reviewId>.json
  prompts/<reviewId>.md
```

review JSONはmerge対象ではありません。指摘には対象field、選択肢、data path、説明、調査範囲、source/projected/upload-ready/live hashと関連pathを保存します。

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

publish前にqualification、listGroupId、project ID、document数、artifact SHA、必須field、ID重複を検証します。実行直前にFirestoreを再読込し、preflight tokenの状態が変わっていないことを確認します。既存uploaderで差分だけを書き、同じ対象のreadback一致を完了条件にします。

## API

| method | path | 用途 |
| --- | --- | --- |
| `GET` | `/api/inventory` | 資格・group・件数。 |
| `GET` | `/api/workflow-catalog` | GUI工程構造と正本文書の組合せ。 |
| `GET` | `/api/document` | 許可された継続正本Markdownの本文。 |
| `GET` | `/api/qualification-workflow` | 資格全体の工程状態。 |
| `GET` | `/api/qualification-runs` | run履歴と進捗。 |
| `GET` | `/api/questions` | 一覧・検索・filter。 |
| `GET` | `/api/questions/<reviewKey>` | 問題詳細。 |
| `GET` | `/api/questions/<reviewKey>/fingerprint` | patch変更検知。 |
| `POST` | `/api/reviews` | reviewとCodex依頼作成。 |
| `POST` | `/api/reviews/<reviewId>/status` | review状態更新。 |
| `POST` | `/api/qualification-workflow/prompt` | 工程依頼生成。 |
| `POST` | `/api/qualification-runs/preview` | run対象preview。 |
| `POST` | `/api/qualification-runs/start` | run開始。 |
| `POST` | `/api/qualification-runs/resume-prompt` | 保存prompt再取得。 |
| `POST` | `/api/direct-edits/preview`, `/apply` | 直接編集。 |
| `POST` | `/api/groups/<qualification>/<listGroupId>/sync-preview`, `/sync` | ローカル成果物同期。 |
| `POST` | `/api/groups/<qualification>/<listGroupId>/publish-preview`, `/publish` | Firestore preflight・反映。 |
| `POST` | `/api/firestore-readback/preview`, `/run` | 資格一括readback。 |
| `GET` | `/api/jobs/<jobId>` | 非同期job状態。 |

## 実装と検証

実装は`tools/question_review_console/`、テストは`tests/test_question_review_*.py`です。追加build工程やブラウザ側credentialを導入しません。

安全要件:

- mutation APIは起動session tokenと許可経路のexact Originを必須にする。
- Hostを経路のallowlistと照合し、Tailscale経路はGETと静的fileも含めてloginとsource IPを必須にする。
- Tailscale公開時も`127.0.0.1` bindを維持し、Funnel requestを拒否する。
- repo外path、任意file path、任意project IDを受け付けない。
- JSON更新は一時file、fsync、atomic replaceを使う。
- 書き込み前後のhashが変わった場合は停止する。
- credentialと環境変数をresponse・logへ出さない。
- 同じqualification / groupでsyncとpublishを同時実行しない。

標準起動:

```bash
python3 tools/question_bank/question_bank.py review-ui
```
