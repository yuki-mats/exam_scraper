# 問題整備artifact契約

この文書は、問題整備で作るディレクトリ、ファイル名、所有工程の正本です。fieldの中身は[question field契約](../reference/question_field_contract.md)、作業手順は[prompt一覧](../../prompt/README.md)を参照してください。

## ディレクトリ構成

通常運用の正本はローカルcheckoutに置きます。問題、画像、patch、作業版、runログはこのcheckoutのローカル実体を読み書きし、Google Drive上の保存先へのリンクを実行時の入力・出力にしません。Google Driveは検証済みデータのバックアップ先として使い、整備中の正本と同時に同期・更新する作業領域にはしません。復旧時はバックアップをローカルへコピーし、件数・ID・hashを照合してから通常運用へ戻します。

```text
output/<qualification>/
  question_images/<list_group_id>/
  questions_json/<list_group_id>/
    00_source/
    05_originalized/
    10_questionType_fixed/
    12_merged_questionType/
    15_correctChoiceText_fixed/
    18_law_context_prepared/
    20_merged_1/
    21_explanationText_added/
    22_questionSetId_linked/
    23_correctChoiceText_fixed/
    24_questionIssueCorrections/
    25_verified_publication/
    30_merged_2/
    40_convert/
    99_model_review_flags/
  questions_json/upload_to_firestore/
  category/category.json
  law_evidence/<list_group_id>/
  reports/
output/question_review_console/
  <qualification>/<listGroupId>/
    work_versions/<reviewKeyのsha256先頭24桁>.json
    evaluations/
    reviews/
  workflow_runs/<qualification>/<runId>/
  runtime_observations/<qualification>/<parentRunId>/
  direct_edit_transactions/<transactionId>/
  work_version_backfills/<timestamp>/manifest.json
  work_version_invalidations/<receipt_id>/manifest.json
  work_version_migrations/<timestamp>/manifest.json
  publish_runs/<qualification>/<runId>/
  publish_queue_runs/<qualification>/<queueRunId>/
```

`<qualification>`は人が読めるkebab-caseのローカル資格コードとします。本番Firestoreで既存`qualificationId`を維持する必要がある場合は、`config/scrape_presets.json`の`publication_qualification_id`へ分離します。

`<list_group_id>`は既存工程が扱う問題グループです。公式過去問では、年度内に試験区分が1つなら`YYYY`、複数ある資格では`YYYY01`を前期、`YYYY02`を後期として保存します。独自問題では、取得元の講座又は問題集を識別できる安定名を使います。例: `udemy-ok-aws-e`。取得元site内の一時的なgroup IDは`source_list_group_id`にだけ保持します。

## 所有工程

`<source_stem>`は`00_source`の拡張子を除いた名前です。日常運用では固定名を更新し、同じ工程のtimestamp付きファイルを増やしません。

| 工程 | 保存先 | ファイル名 | 責務 |
| --- | --- | --- | --- |
| scrape | `00_source/` | `question_<source又はexam occurrence ID>_<n>.json` | 取得元の現在スナップショット。手作業では不変。同じ安定IDの取得元更新だけ標準scraperが同じ名前へ反映する。 |
| scrape | `question_images/<list_group_id>/` | source由来名 | 取得元の現在スナップショットに属するローカル画像。 |
| 05 image | `question_images/<list_group_id>/05_originalized/` | `originalized_<public_question_id>_<用途>_<連番>.<拡張子>` | 独自問題用に新規生成した公開画像。取得元画像を上書きしない。 |
| 05 | `05_originalized/` | `<source_stem>_originalized.json` | 独自問題化した文章と正答を先に確定し、画像生成後に必要な公開画像URLを同じrecordへ追記するpatch。公式過去問では作らない。 |
| 01 | `10_questionType_fixed/` | `<source_stem>_questionType_fixed.json` | 問題形式。 |
| merge | `12_merged_questionType/` | `<source_stem>_merged.json` | 01反映確認用の生成view。 |
| 02 | `15_correctChoiceText_fixed/` | `<source_stem>_merged_correctChoiceText_fixed.json` | 互換名を維持した`questionIntent` patch。 |
| 02a | `23_correctChoiceText_fixed/` | `<source_stem>_merged_correctChoiceText_fixed.json` | 03前に確定する厳密正答patch。 |
| 02b | `18_law_context_prepared/` | `<source_stem>_merged_lawContext_prepared.json` | 法令関連性と根拠候補。 |
| merge | `20_merged_1/` | `<source_stem>_merged.json` | 03・04の主入力。02aと02bも反映する。 |
| 03 | `21_explanationText_added/` | `<source_stem>_merged_explanationText_added.json` | 資格横断の問題学習分類、解説、想定質問、法令監査facts。 |
| 03c | `output/<qualification>/category/` | `category.json` | 資格全体の分類正本。問題単位patchではない。 |
| 04 | `22_questionSetId_linked/` | `<source_stem>_merged_questionSetId_linked.json` | `category.json`に基づく問題集対応。 |
| 問題報告 | `24_questionIssueCorrections/` | `<batch>_<work>_<originalQuestionId>.json` | blind review済みcorrection overlay。 |
| 検証済み公開正本 | `25_verified_publication/` | `<list_group_id>_verified_publication.json` | ガス主任技術者の移行後正本。公式問題・正答PDFでIDと内容を検証済みの表示対象recordを保持し、通常再生成では旧mergedやFirestore readbackを参照しない。 |
| merge | `30_merged_2/` | `<source_stem>_merged_<timestamp>.json` | upload前の全patch統合結果。 |
| convert | `40_convert/` | `<list_group_id>_firestore_<timestamp>.json` | Firestore schemaへの変換結果。 |
| delivery | `upload_to_firestore/` | `<list_group_id>_firestore_<timestamp>.json` | upload対象の正規artifact。 |
| uncertainty | `99_model_review_flags/` | `<source_stem>_<stage>_needs_5_5_high_review.jsonl` | patchへ混ぜない未確認事項。 |

## 補助artifact

| 種類 | 保存先 | 性質 |
| --- | --- | --- |
| category | `output/<qualification>/category/category.json` | `questionSetId`の分類正本。 |
| law snapshot | `output/<qualification>/law_evidence/<list_group_id>/` | 条文本文・hashなどの監査用evidence。 |
| law audit | `output/<qualification>/review/law_revision_audit/` | queue、sidecar、監査結果。 |
| generated reports | `output/<qualification>/reports/` | checkerやmigrationの再生成可能なreport。 |
| review | `output/question_review_console/<qualification>/<listGroupId>/reviews/` | 人間の指摘とCodex依頼。 |
| work version | `output/question_review_console/<qualification>/<listGroupId>/work_versions/<reviewKeyのsha256先頭24桁>.json` | 一問だけの検証済み工程版履歴。patch又はFirestore fieldではない。 |
| session run | `output/question_review_console/workflow_runs/<qualification>/<runId>/` | 不変`plan.json`、小さい親`manifest.json`、`questions/<questionIdのsha256>.json`、派生`question_summary.json`、`attempts/<token>/`のresult・progress・baseline、技術ログ、問題別projection、終端時の`improvement_report.json`。serverはmodel候補を一問state内のattemptへwrite-onceで保存するが、model自身はここへ書き込まない。通常の一問turnは子manifestを作らない。 |
| runtime observation | `output/question_review_console/runtime_observations/<qualification>/<parentRunId>/` | `monitor-event/v1`の`events.jsonl`と観測cursor・欠落件数を持つ`snapshot.json`。表示用のbest-effort projectionであり、工程・成果物・公開状態の正本ではない。 |
| direct edit transaction | `output/question_review_console/direct_edit_transactions/<transactionId>/` | 直接修正のbaseline（開始前bytes）とcommit・rollback結果。 |
| evaluation projection | `output/question_review_console/<qualification>/<listGroupId>/evaluations/` | 元問題単位の`question-evaluation-projection/v2`。有効な現行評価、最新attempt pointer、単調sequenceを一つのatomic fileに保持する。全attempt履歴は`workflow_runs/`だけを正本とする。promptは同階層の`evaluation_prompts/`。 |
| work version backfill | `output/question_review_console/work_version_backfills/<timestamp>/manifest.json` | 公開済み問題をlegacy `v0.0`へ初期化した対象、照合結果、件数のreceipt。 |
| work version invalidation | `output/question_review_console/work_version_invalidations/<receipt_id>/manifest.json` | 誤って成功扱いにしたrun・工程を再整備対象へ戻した履歴。 |
| work version migration | `output/question_review_console/work_version_migrations/<timestamp>/manifest.json` | 旧年度単位台帳を検証し、一問単位へ分割して旧fileを除去した件数と保存先のreceipt。 |
| publish run | `output/question_review_console/publish_runs/<qualification>/<runId>/` | preflight、対象artifact、result、readback。 |
| publish queue run | `output/question_review_console/publish_queue_runs/<qualification>/<queueRunId>/` | 明示選択した順序付き問題IDを固定する親`manifest.json`、全問題の確認時preflightを持つ`preflight.json`、問題別の成否・publish run ID・readback要約を持つ終端`result.json`。問題内容、対象artifact、詳細readbackは問題単位のpublish runだけに保存し、親へ複製しない。 |

run directoryは再利用しません。通常整備runの対象、source identity、工程版、sandbox、`stateHash`、`policyVersions`、`policyFingerprints`、`policyTargets`は不変`plan.json`へ保存します。親`manifest.json`はrun全体の検証・同期状態と小さい集計だけを持ち、一問の`questionExecutions`、停止理由、fingerprint、`validationAttempts`、attempt、検証済み作業版receiptは`questions/<questionIdのsha256>.json`へ保存します。`question_summary.json`は一問stateから再生成できる表示用派生物です。

model turnはread-onlyで`question-maintenance-candidates/v3`候補だけを返し、serverが候補を問題ID、工程、入力fingerprint、projection hash、内容hashとともに一問state内のattemptへwrite-onceで保存します。汎用`question-tool` executor上のpatch tool queueはattempt IDだけを持ち、保存済み候補をreadback検証してから`patchApplyStartedAt`をwrite-onceで記録します。中断後に同じ候補を再利用できるのは、入力identityが完全一致し、patch反映を開始していない場合だけです。serverは`attempts/<token>/`へresult、progress、開始前baselineも保存します。patchは工程・年度ごとの既存形式を維持し、一問ごとには分割しません。実際に更新するpatch JSONと対象一問のwork version JSONだけを同じ実path transactionに含め、checkpoint保存まで成功した後に確定します。失敗時は実path lockを解放する前に両方を開始前へ戻します。再起動時はtransactionが開いた一問を開始前へ戻し、確定済みreceiptを持つ問を除外して未完了だけを再開します。詳細は[問題整備システム](local_question_review_console.md)、評価内容は[`evaluation_result.schema.json`](../../tools/question_review_console/evaluation_result.schema.json)を正本とします。

評価projectionの`currentValid`は従来v1の完全な評価結果又は`null`、`latestAttempt`はrun IDと結果要約だけです。`nextAttemptSequence`、`latestAttemptSequence`、`promotedAttemptSequence`は整数で順序を決め、日時、mtime、run ID文字列では並べません。従来v1は読取を維持し、次の評価attemptを予約する時だけ同じpathでv2へ昇格します。

promotion時は`result.json`のhashと問題identity、state、App Server session/thread/turn、run種別、評価版とfingerprintをmanifestと照合します。合格又は要再整備では、さらにmanifestの作業版receiptと対象一問のwork version JSONにあるcurrent又はhistoryを確認します。作業版の親recordは`reviewKey`、`questionId`、`originalQuestionId`、`publicationQualificationId`が現在問題と完全一致し、stageは同じrun ID、`source=validated_evaluation`、同じ版を示す必要があります。result、manifest、stageのfingerprintは非空かつ三者一致の場合だけ受理します。通常の状態取得は、最初に検証したprojection snapshotから現行payloadと回復判定を導出し、同じ取得内でprojectionを読み直しません。同じ取得内でresult、manifest、App Server session/thread/turnまで完全照合したlogical resultは、その照合済み事実を後段へ渡し、同じreceiptを再読しません。この事実は別の状態取得へ持ち越しません。`currentValid`があり、最新attemptが既に`inconclusive`なら、表示に使わない最新run成果物を再読せず`currentValid`を返します。`currentValid`がない場合と予約状態の回復は、引き続き最新runの証拠を照合します。実際にrepair又はpromotionを書く場合だけ、atomic更新側が現行projectionを再確認します。projectionが破損している場合は未評価へ戻さず`stale`として停止します。

## 編集境界

- 手作業で編集するのはpatchと承認対象の設定だけとする。`25_verified_publication`は専用の検証済み移行・再生成処理だけが更新する。
- `00_source`の親ディレクトリは、資格コード又は年度・試験区分を整理する移行に限り、file hashと`00_source/`以下の相対名を保持して移動できる。移行後はimmutability manifestへ明示登録する。
- `12`、`20`、`30`、`40`、`upload_to_firestore`は生成物であり、直接修正しない。
- 不確実性と監査履歴をFirestore用recordへ未知fieldとして混ぜない。
- 品質確認artifactと公開flagはreview専用であり、patch、merged、Firestore question documentへ混ぜない。
- 一問work version JSONはserverだけが検証済みreceipt又は明示的なbackfillから更新し、patch、merged、upload-ready、Firestore question documentへ複製しない。
- 新fieldを公開artifactへ入れる前に、field契約、repaso schema、convert、upload、quality-gateを同時に更新する。
- 正誤が02a又は03bで変わった場合は`23`を更新し、`20`と03以降を再生成する。

### フィードバックによる検証済みガス解説の保存

`python -m tools.question_bank.feedback_verified_save prepare --repo-root <physical checkout> --result <調査result> --canonical <25正本> --output <新規private実行directory> --document-index <公式index> --pdf-verification <公式検証>`は、一task・一年度・一変更documentの固定候補を作る。公式PDF現物、全siblingのmasked snapshot、元bytes、対象外不変、task要件を再検証する。正本とFirestoreは変更しない。

独立内容監査に結び付いたmaintenance登録がある場合だけ、`--maintenance-task-id <登録ID>`で同じ年度・原問の複数documentを扱う専用planを作る。保存receiptも`feedback-verified-maintenance-save-receipt/v1`に分け、従来の単一document契約へ読み替えない。登録・変更field・未対応工程は[フィードバック運用正本](user_feedback_response_system.md#無人実行経路の既知の未対応)に従う。

`scope --plan <formal-save-plan.json> --stage primary|secondary|tertiary|final`は検証済みscopeのnative markerを出す。法令対象は三段階の異なる監査者によるnative証跡を固定し、finalの別subagent承認を専用schemaで検証する。`save --plan <plan> --approval <native-independent-approval/v1 JSON>`だけが正式25を保存する。fieldと判断の条件は[フィードバック運用正本](user_feedback_response_system.md#無人実行経路の既知の未対応)に従う。

`.feedback-save-<planHash>/`は25の親directory内に作るowner-onlyの復旧領域であり、`intent.json`・`original.json`・`replacement.json`・必要時の復元状態を保持する。元正本をexclusive退避して実bytesのhashを照合し、候補もexclusive確定する。置換途中の正本不存在を読取側は失敗として扱い、生成物やsnapshotで補わない。通常rebuildは25を読取専用とし、初回bootstrapは既存正本又は復旧領域があれば拒否する。

private planと同じdirectoryの`save-receipt.json`は`feedback-verified-explanation-save-receipt/v1`で、native承認・元bytes・候補bytesを再照合する。保存中断は同じplan/intentの再検証後に再開し、第三値は保持して停止する。`restore`は現candidateと退避元が一致する場合だけexclusiveに元へ戻し、復元中断と同一再送を追跡する。復元済みplanは再保存しない。receiptは公開結果を示さず、merge/convert・checker・quality gate・独立評価・限定upload dry-run・本番readbackは別途必須とする。

### 検証済み25の正式revisionと公開

`tools.question_bank.feedback_verified_delivery`は、真正な保存receiptと全5肢の公式対応を持つ25に限り、現在の工程に必要なauthoring情報と解説改善を別の正式revisionへ保存する。元の25と保存証跡は変更しない。独立native承認後に`25_verified_publication/revisions/<deliveryPlanHash>.json`へ`gas-shunin-verified-authoring-revision/v1`をexclusive保存し、同一bytesだけ再送できる。base hash・task要件・IDごとの差分・effective hash・承認bindingを保持する。通常patchや通常work versionへ読み替えない。

専用生成処理はbaseと承認revisionから全年度recordを再投影する。対象外record、安定ID、正誤とsourceを維持し、公開しないauthoring fieldは既存DTOで除外する。現行の適用工程、独立評価、3checkerの実検査件数、quality gate、upload dry-run、Gitのcommit/push、限定公開のnative承認を別々に検証する。CLIによるchecker実行は専用のnative command receiptで実呼出し・結果と結合する。

公開には全siblingのfresh snapshot・値のfingerprint・exact更新時刻とdocument別maskを固定し、承認されたtransport profileの既存uploaderを使う。永続intentとattemptを保存し、応答不明時は再書込みせずreadbackする。全対象field・更新時刻・対象外field・sibling集合が一致した後だけ専用公開証跡を作る。改善完了は既存台帳のCAS/history/backup経路で記録し、同じtransaction内でも固定入力・native承認・公開contractを再確認する。工程receipt、正式保存receipt、upload応答だけで公開済み又は完了と扱わない。

`question-maintenance-read-set-upload/v1`は、資格・整数試験年・原問を固定した全active siblingの`readDocumentIds`と、実変更だけの`questions/writeFieldsByDocument`を分離する。許可maskは`explanationText`単独、又は`explanationText`・`suggestedQuestions`・`suggestedQuestionDetails`の3field完全一致だけ。全読取IDの`expectedReadUpdateTimes`と値・presenceのfingerprintを必須とし、同じFirestore transaction内で全件の存在・公開flag・所属と完全なactive集合を検証した後、実変更documentだけをupdateする。明確な別資格・別年度のunitは書込範囲へ加えない。不明unit・同unitの不足余剰・no-op混入・第三値・競合は拒否する。`max_attempts=1`を使い、期待値を最新値へ置換して再送しない。

`maintenance_display`は、公式原題幹を保持したまま、既存の全true_false siblingの表示を整える専用revisionである。全IDについてauthoringの3field、maintenanceの4fieldと`questionBodyText`・`questionText`の2fieldを、理由・根拠付きで明示する。原題幹・原記述・変更前表示のpresenceとhashを差分へ固定し、正誤・ID・分類・法令metadataを変更しない。表示本文は全sibling共通の非空文字列とし、`questionText`は改行を除去した表示本文と、そのID自身の原記述を`[quote]`で結合したものに限る。両表示fieldの実変更を全siblingに要求する。表示の中立性と日本語は独立内容監査で判断する。評価入力では新表示と公式原文を別fieldで明示し、旧表示を評価本文へ残さない。

このrevisionの公開は`question-maintenance-read-set-upload/v2`と対応するresult/v2だけを使う。許可maskは表示2fieldに、解説1fieldと補足2fieldの各組を必要に応じて加える4種類の完全一致で、各fieldは実差分でなければならない。v1とatomicの許可maskは拡張しない。transaction内の実baselineと適用後の全siblingでも表示構造と対象外不変を検証する。profileはcandidate・intent・attempt・commit・readback・completionまで結合し、未知profile、v1/v2証跡の混同、未確定commitの再書込みを拒否する。

`question-maintenance-read-set-upload/v3`は、既存TFの表示と個別正誤を同時に訂正する専用transportである。必須maskは`questionBodyText`・`questionText`・`correctChoiceText`で、解説1field、補足2fieldの組を任意に加える4種類だけを認める。全active siblingの所属・値・presence・exact更新時刻を同じtransactionで検証し、既存documentだけを限定更新する。原文・ID・分類等のmask外差分は拒否する。正誤は`正しい`又は`間違い`に限定し、適用後の全siblingの表示構造と解説の正誤を検査する。結合maskの一部が現値と同じでもよいが、各write documentには実差分を必要とする。v1/v2のmaskとno-op条件は変更しない。

v3のresultも`publicationConfirmed=false`であり、transportの検証・commit応答は内容承認や公開完了を表さない。呼出し元は専用profile・差分・全read/write集合を真正な独立承認と永続intentへ結び、全対象field・対象外不変・読取専用siblingの時刻をreadbackしてから公開を確定する。通常sourceの正式variant・全工程・独立評価との接続が未完なら、このtransportを直接呼んで公開しない。

専用の公開candidate・intent・attempt・commit・resultはprofileとread/write集合を結合する。transactionの実`write_results`から書込集合のexact更新時刻を確認し、読取専用siblingは値・presence・更新時刻すべて不変、書込documentは対象field一致と対象外不変をreadbackする。不明commitは永続証跡を残して再観測し、再書込み又は完了へ自動的に進めない。従来の全対象変更atomic契約は維持し、旧receiptをこの専用証跡へ読み替えない。コード監査やtransport応答は、固定候補の内容監査・正式保存・独立評価・本番公開確認を代替しない。

### 通常sourceのTF公開unit

`python -m tools.question_bank.feedback_source_tf_delivery --repo-root <repo> --db <既存台帳>`は、通常sourceから整備した一問と既存TF全siblingを扱う明示経路である。`prepare --request <source-bound-public-tf-request/v1> --output <新規private directory>`は、完全なsource binding、保全manifest、公式資料とページ、公式選択答、各記述全文とlive IDの一意な対応、通常工程の真正な保存証跡、現在の台帳要件・状態・live所属集合を固定する。公式選択答と各記述の真偽は別に確認する。生成物を手で編集したり、原問の否定指示から真偽を反転したりしない。

`scope --plan <plan> --action save`のnative出力に対する別subagent承認を得てから、`save --plan <plan> --approval <承認>`を実行する。正式保存先は`output/<qualification>/publication_corrections/<unitHash>/`。`source.json`・`public.json`・`manifest.json`と`receipt.json`をexclusive保存し、同じ成功再送だけを受理する。schemaは`source-bound-public-tf-unit/v1`、保存receiptは`source-bound-public-tf-save-receipt/v1`である。通常の24探索へ自動で混ぜず、このplanを明示するselectorだけで読み込む。途中中断や第三値を新規成功として埋め合わせない。

正常工程の証跡は、固定された候補・実保存bytes・native承認・実child・work version・checkpointを照合する。後続工程が同じpatchを正当に更新する場合は、真正な保存履歴から各工程時点の入力を通常projectorで再構築し、現在の投影と最後の真正な保存内容を確認する。03bは通常のcandidateに結び付いた法令監査metadataと実child内のレビューを再検証し、必要人数と一次根拠を通常のvalidatorに従って確認する。工程名や自己申告の成功flagだけで代替しない。

`check --plan <plan>`は真正な工程履歴を通常projector・converterで投影した一問artifactに対し、実3checker・quality gate・v3 upload dry-runを実行し、検査対象0件を成功にしない。`evaluate --plan <plan>`は公式原文・公式選択答・公開表示を分けた入力で通常の別session評価を実行する。公開scopeは現在の正確な評価policyと永続評価結果を結び、候補作者の作成した検査JSONや旧評価を流用しない。

`scope --action publish`と別subagentの新たな承認を経て、`publish`はpush済みmainの実入力、fresh snapshot、全active集合、値・presence・exact時刻を照合し、既存uploaderのv3だけで限定更新する。`reconcile`は永続intent・attempt・実commit結果を再検証し、全対象field・対象外不変・読取専用siblingの時刻を読み戻す。確認結果は`source-bound-public-tf-publication-result/v1`として保存する。応答不明時は新規書込みせず、実SDKのcommit結果がなければ`commit_unconfirmed`で保留する。実commit証跡がある場合だけ現在値と同じ証跡を照合して再開し、現在値からcommit成功を推定しない。通常unitとガス25の専用receiptは相互に読み替えない。

公式報告の現投影を認証する場合は`tools.question_bank.feedback_source_tf_report_review`を使う。`execution_call()`へ`--repo-root`・`--db`・`execute --request <既存v1 request> --output <新規private directory>`の実引数を渡し、返された正規native wrapperを改変せず実行する。途中yieldは同じcellへの継続だけを許す。実AppServerによるBlind A/B・Challengeと全検証が完了した実行だけがnative採用scopeを発行し、別subagentの真正な承認を得てから、`source-bound-public-tf-report-request/v2`の`reportReview`にplanとapprovalのref/hashを結ぶ。旧v1 requestと既存24は保持し、後付けscopeで任意のreview JSONを認証しない。

report審査は実task/case、caseが保持するcanonical snapshotとその内容hash、現policy、現在の正常投影、全sibling対応・readSet・実writeSetを固定する。報告claimsはprivateなChallengeだけへ渡し、Blindと正式unitへ生の利用者本文・識別情報を載せない。公式問題PDFと正答PDFの両役割を別の実画像として送り、実turnの送信bytes/hash、独立session、受領・検証結果を照合する。認証対象は既に訂正した現在の投影に対するA/Bの`no_problem`とChallengeの`no_change`であり、新たな訂正や保留は通常writerで再整備する。ローカル投影の`no_change`を、未反映のreport caseの処理済みへ転記しない。scope発行後の入力・採用承認の変化や撤回は、保存・公開直前、readback後、reconcileでも再検証する。

中断した審査directoryは完成receiptやscopeを持たないまま保持し、同じdirectoryへ再実行して完成を装わない。旧report v1は審査reader未接続として公開保留を維持する。日次台帳の完了dispatchは[フィードバック運用正本](user_feedback_response_system.md#無人実行経路の既知の未対応)で追跡する。公開のreadback一致とtask完了を区別し、未接続dispatchを`completed`記録で迂回しない。

### 読取専用canonical contextによる限定生成

`scripts/pipeline/prepare_scoped_question_artifacts.py --context <JSON> --output <private-directory>` は、明示したcanonical rootの全source・選択patchとproduct rootの正式24を固定し、一問bindingの全公開IDを生成します。入力JSONは `canonicalRoot`、`overlayRoot`、`qualification`、`listGroupId`、完全な `binding`、`publicationIds`、`referencePaths` を持ちます。保存先は `output/user_feedback_response_system/staging/recovery-scoped-2025/` 配下に限り、manifestには入力・実装・projection・各artifactのhash、候補別対象外証明、全group診断を保存します。通常の全group gateは継続して適用されます。

`--private-candidate` は未承認の4field候補を18/21の責務で投影する検証専用です。正式patchには保存せず、評価・公開を停止します。候補を使わず正式入力から生成した成果物も、自動的に公開可能にはなりません。Inventoryの `scoped_question(manifest_path)`、評価、Publisherは同じmanifestを再検証し、現行工程版・真正評価・failed delta・live fingerprintなど既存条件を適用します。manifestの手編集による昇格は認めません。

限定contextは、canonical rootの `output/<qualification>/category/category.json` とカテゴリ依存集合を固定し、変換documentの `questionSetId` が正本に存在することを確認します。readerのPython依存、資格ID解決config、field要件及び評価schemaも入力fingerprintへ含めます。private JSONは保存前にdirectoryを700、temporary fileを600に設定してatomicに確定し、失敗時も広いpermissionで残しません。再実行は新しい `runs/<run-id>/` へ保存し、既存manifest・証跡の内容を保持します。
# Snapshot correction preview

`snapshot-corrections/v1` は既存 canonical reader と固定 native chain に結合する private manifest です。
年度公式 source の原問・選択肢・原選択答と、既存公開IDの TF 表示 variant を別々に保持します。
converter の全面成果を正式保存・upload候補にしません。2020 の対象は `questionText` 一文字のみです。

正式保存候補には `expectedBeforeHash`、公開 delta には field ごとの `beforePresent` / `before` / `after` と
snapshot `updateTime` を保存します。missing と null は別値です。mask 読取りに無mask fallback はありません。
証跡は event の LF 有無、output、receipt file、compact inner object、manifest file/object/files-map の hash を区別します。
`same_as_current` は両時点の真偽一致であり、表示・解説の修復不要を意味しません。


## Publication correction unit

`prepare_scoped_question_artifacts.py --formal-save-plan <固定manifest> --output <private run> --dry-run` は、正式予定9fileと同一bytesをprivate `planned-files/`へ生成する。実正式保存は行わない。2018/2024は原問本文・肢順・原選択答4/2を保持するsource no-op envelopeと公開TF variantを分離する。2020はproduction snapshot専用型でsource group/ref=null、questionText一文字deltaだけを保持する。local評価namespaceを正式sourceに代用しない。

`formal-save-plan.json`は全path/file SHA256・固定候補・native証跡・現入力hashを結合する。object hashはcompact sorted UTF-8 JSON（LFなし）、file hashは実pretty JSON bytes（末尾LFあり）。歴史的コード依存との差は新planに記録し、旧検証を現行コード成功へ流用しない。

正式保存APIは、固定質問のexact plan/path/hash bindingに関連する真正native人間回答と、別の日時・URL・revision・XML/body hash付き現行法取得証跡を要求する。bool/tokenや人間の法令真偽宣言だけでは許可しない。UI回答はquestionItemIdのtool/call/index・固定質問を照合する。法令差異は自動補正しない。保存はunitごとのOS atomic no-replace確定で、同一bytesの再送だけを受理する。receipt未確定のdirectoryは有効なunitとして読まない。

`load_formal_correction_unit`、Inventory、ArtifactSynchronizerの明示入口だけでplan/承認receiptを読む。通常24/05 selector・group resolverは不変。保存receiptからcheckpoint/evaluation/machineReady/publicationReadyを作らない。正式保存・現在法再照合・対象checkpoint/model評価・本番承認・SDK更新・公開後readbackは別工程である。


### 結合済みreviewの凍結と限定保存の回復

planに結合したcase_review本文は、行政的な承認待ち表示・進捗同期では変更しない。PMはplan hash・review hash・状態・receipt参照を別のappend-only linkageへ記録し、そのlinkageを同planのinputsに加えない。判断・根拠・候補の実質変更では旧planを失効させfresh planを生成する。2025の既存回答待ちは再送しない。

承認Markdownと詳細refs/facts付録の実bytes hashを、循環参照なしでplanと固定質問に結合する。法令gateはdated law_data実metadata応答内revisionと、XML実bytes/MainProvision/body hashを照合する。保存中のapproved bytes・staged bytes・現inputsをexclusive finalize直前に再照合する。成功receiptの同一再送は元法令証跡を確認するread-only操作であり、日付変更後も元receiptを返す。receiptなし中断resumeは新write/receipt確定を伴うためfresh法令再照合が必要。
