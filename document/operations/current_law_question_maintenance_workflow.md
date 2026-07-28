# 現行法監査

この文書は、法令関連問題の根拠取得、現行法監査、patch更新の正本です。fieldは[question field契約](../reference/question_field_contract.md)、patch作業は[02b](../../prompt/02b_prompt_prepare_law_context.md)と[03b](../../prompt/03b_prompt_audit_current_law_and_patch.md)に従います。

## 原則

- `00_source`と既存IDを変更しない。
- 問題文と各選択肢を結合した完全命題を、一問一肢ずつ確認する。
- 既存の正誤、解説、法令metadata、検索要約だけで正誤を確定しない。
- e-Gov法令検索又は所管官庁の一次情報で、法令名、条・項・号、施行日、本文を確認する。
- 根拠不足は`hold`又は`needs_secondary_review`にする。
- `updated_to_current_law`は`tertiary_verified`後だけ公開確定する。
- 03bの改訂では[共通の作業バージョン規則](local_question_review_console.md#作業バージョン)を使う。問題単位の`auditMethodVersion`は使用した監査方式の証跡であり、作業版の代わりにしない。

Codex App Serverでは、組み込みweb検索を一次情報の入口として使います。外部MCP、Lawzilla、Firestore条文検索は使いません。保存済みの`lawReferences`、`lawRevisionFacts`、evidence cacheは候補として読み、一次情報と一致した場合だけ根拠にします。

### 既存の法令紐付けを使う順序

1. 既存の`lawReferences`に`lawId`と条番号又は保存済みURLがあれば、その一次情報本文を先に開く。
2. 問題文と各選択肢を照合し、既存の紐付け先だけで十分に説明できるかを確認する。
3. 十分なら広域検索と再紐付けを省略し、有効な`lawReferences`を保持する。
4. 不足、404又は内容不一致がある場合だけ、その選択肢と不足箇所に限定して探索する。

既存の紐付けは探索の入口であり、正答根拠として無条件には信用しません。問題整備runでは、各問の確認方針を`lawReferenceDiscoveryPlan`としてattempt artifactへ残し、既存参照で開始した件数と追加探索が必要だった件数を後から集計できるようにします。

問題整備システムでは、03bを通常整備とは別の新しいsessionで自動実行します。法令監査警告が残る問題はトップ整備の対象へ戻し、警告がなくなるまで完了記録を更新しません。技術知識又は計算だけで判断できる問題は、根拠のある`not_law_related`として03bを完了できます。法令根拠がないという理由だけで`hold`にしません。通常の再実行はトップから行い、詳細画面で監査対象を組み直しません。

03bの入力projectionが`isLawRelated=false`で、同じsource identityに対応するv2監査sidecarが`not_law_related/secondary_verified`として整合する場合は、モデルを再実行せず`not_applicable`で完了し、現在の03b作業版と検証receiptを記録します。sidecarがない又は分類が一致しない場合は旧ルール由来の指摘として消さず、その一問だけを実質的な保留に戻します。

## 監査

1. qualification、対象listGroupId、基準日、資格別law policyを固定する。
2. 各選択肢の完全命題と、法令名、条・項・号、施行日、locator、本文hashをまとめる。
3. 一次監査でevidence bundleと暫定判定を作る。
4. 二次監査で同じbundleを使い、正答、解説、locator、差分説明を再確認する。
5. 正答変更、一次・二次不一致、高リスク判断は三次確定へ回す。

`auditInputHash`、`lawCorpusSnapshotId`、一次・二次・三次のrun IDを残し、別phaseで入力を変えません。

## 状態

| `auditStatus` | 意味 | 公開条件 |
| --- | --- | --- |
| `same_as_current` | 出題時正答と現行法判定が同じ | `secondary_verified`以上 |
| `updated_to_current_law` | 現行法に合わせ正誤又は説明を更新 | `tertiary_verified` |
| `not_law_related` | 法令監査対象ではない | 根拠付き`secondary_verified` |
| `hold` | evidence又は方針不足 | 公開不可 |

出題当時の公式正答は`lawRevisionFacts.examTime`へ保持し、現行法判定と混同しません。出題当時の条文を確認できない場合は、その事実を明記して推測を避けます。

法令肢と技術肢が混在する問題は、問題全体を`isLawRelated=true`とし、選択肢別の`lawRevisionFacts`で各肢を独立に確定します。技術肢は`not_law_related/secondary_verified`、対応する`lawReferences`は空配列とします。監査sidecarの問題全体の`auditStatus`は法令肢から決め、法令肢に一件でも`updated_to_current_law`があれば同じ値、それ以外は`same_as_current`とします。

### 監査sidecar

03bの判断履歴は`law-revision-audit/v2`のJSONLとして、対象年度に1問1行で保存します。各行は次の三つのsource identityを必須とします。

- `reviewQuestionId`: 対象のsource recordから共通のreview ID規則で導出した安定ID
- `sourceQuestionKey`: 同じsource recordに保存されたsource identity
- `sourceRecordRef`: `00_source/`からの相対file pathと0始まりのrecord index（`<path>#<index>`）

画面APIの問題ID、`reviewKey`、`progressTargets[].id`、UI表示用hashは監査IDではありません。sidecarとsourceは上の3要素をexact joinし、部分一致で推測しません。UIの`reviewKey`が衝突しても`sourceRecordRef`で問題を分離し、資格・年度・問題一覧を表示します。3要素を一意に確定できない場合は03bの開始だけをfail-closedでblockします。選択肢の判定は`examTimeDecision`と`currentLawDecision`へ選択肢順で保存し、patchの正答・`lawRevisionFacts`と一致させます。

## 保存先

| 内容 | 保存先 |
| --- | --- |
| 法令関連性・根拠候補 | `18_law_context_prepared/` |
| 現行法で確定した正誤 | `23_correctChoiceText_fixed/` |
| 解説・監査facts | `21_explanationText_added/` |
| 監査履歴・未確認事項 | `output/<qualification>/review/law_revision_audit/` |
| 既存の条文cache | `output/<qualification>/law_evidence/<list_group_id>/`（App Serverからは更新しない） |

Codex App Serverの整備sessionが更新するのは、対象問題の`18` / `21` / `23` patchと対象年度の監査sidecarだけです。`law_evidence`、`merge`、`convert`、`upload-ready`、Firestoreは変更せず、必要な後続処理は問題整備システムの別工程で実行します。

## 公開前条件

- 工程03と同じ解説文の形式・日本語品質検証に合格している。
- 法令肢には必要な`lawReferences`と`lawRevisionFacts`があり、技術肢は`not_law_related/secondary_verified`と空の`lawReferences`で確定している。
- v2 sidecarのID、分類、必須metadataがpatchと一致する。
- patchとmergedのトップレベル記述真偽、`lawRevisionFacts.current.correctChoiceText`、解説先頭が一致する。
- `group_choice`と`flash_card`を分割したFirestore documentでは、トップレベル正答は正解選択肢かどうか、監査factsは記述自体の真偽を表す。両者を直接比較せず、mergedで記述真偽を検証した後、変換後は選択肢対応と監査factsの形式を検証する。
- `hold`と未完了review stateがない。
- evidenceから公的一次情報を追跡できる。
- [delivery workflow](delivery_workflow.md)のquality-gateとupload dry-runが通る。
