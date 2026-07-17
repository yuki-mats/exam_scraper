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

問題整備システムでは、03bを通常整備とは別の新しいsessionで自動実行します。法令監査警告が残る問題はトップ整備の対象へ戻し、警告がなくなるまで完了記録を更新しません。技術知識又は計算だけで判断できる問題は、根拠のある`not_law_related`として03bを完了できます。法令根拠がないという理由だけで`hold`にしません。通常の再実行はトップから行い、詳細画面で監査対象を組み直しません。

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
- 必要な`lawReferences`と`lawRevisionFacts`がある。
- v2 sidecarのID、分類、必須metadataがpatchと一致する。
- トップレベル正答、`lawRevisionFacts.current.correctChoiceText`、解説先頭が一致する。
- `hold`と未完了review stateがない。
- evidenceから公的一次情報を追跡できる。
- [delivery workflow](delivery_workflow.md)のquality-gateとupload dry-runが通る。
