# 過去問フィールド契約

この文書は、過去問データを整備するときに人間が最初に見る統合仕様書です。
資格ごとの出題形式や法令範囲は変わってよい一方で、共通フィールドの意味、型、必須性、更新主体は資格ごとに揺らさないでください。

## この文書の位置づけ

この文書は人間向けの判断基準です。機械的な最終判定は次の実装で行います。

| 役割 | 正本 |
| --- | --- |
| アプリ/Firestore の DB 制約 | `/Users/yuki/StudioProjects/repaso/firestore.rules` |
| Flutter の読み取り型・enum | `/Users/yuki/StudioProjects/repaso/lib/firestore/models/question_doc.dart` |
| `exam_scraper` の Firestore 投入前検証 | `scripts/common/repaso_firestore_schema.py` |
| 工程別の必須項目 | `config/requirements/required_fields.toml` |
| 日常整備の統一CLI | `tools/question_bank/question_bank.py` |
| アップロード直前の補正・差分判定 | `scripts/upload/upload_questions_to_firestore.py` |
| Firestore 変換ロジック | `scripts/convert/convert_merged_to_firestore.py` |

上記の実装を変えた場合は、この文書も同じ commit で更新します。

## 基本原則

1. Firestore の `questions` に未知のキーを増やさない。
   - 資格固有の検討メモ、判断履歴、スクレイピング由来の一時情報は `output/*` の中間 JSON、review sidecar、または `prompt/qualification_docs/<qualification>/` に置く。
   - Firestore へ新しいキーを入れる場合は、`repaso` 側の rules / typed model / schema sync test と `exam_scraper` 側の schema を同時に更新する。
2. `questionId` は Firestore document ID であり、`questions/{questionId}` のフィールドとしては保存しない。
   - `40_convert/*_firestore_*.json` では upload 用の識別子として `questionId` を持つ。
   - upload 時に document ID として使い、ドキュメント本体には入れない。
3. `DB必須` と `整備必須` を分ける。
   - `DB必須`: Firestore rules / typed model 上、`questions` document として必須。
   - `整備必須`: 過去問品質・出典管理・公式データ更新のため、`exam_scraper` の工程では必須。
   - 例: `examYear` は DB 上は optional だが、過去問整備では必須。
4. `null` と未定義は同じ意味ではない。
   - 可能なら optional field は未定義にする。
   - 明示的に `null` を許すのは、既存互換または rules / schema が許容している場合に限る。
   - 空文字を「未確認」の代わりに使う場合は、upload script が既定値として作る field に限る。人間の判断結果としては未確認 reason を sidecar に残す。
5. `correctChoiceText` は出題当時のソース、`questionIntent`、現行法監査結果の関係を崩さない。
   - AI が目視だけで `correctChoiceText` を推測してはいけない。
   - 法令問題で現行法ベースへ更新する場合は、出題当時の正答・現行法根拠・更新済み注記を `lawRevisionFacts` / `explanationText` / `suggestedQuestionDetails` / `lawReferences` / review sidecar に残す。
6. 資格固有ルールは field の意味を変えない。
   - 資格ごとに変えてよいのは、法令スコープ、カテゴリ粒度、出題形式の傾向、解説方針。
   - `questionType` や `lawReferences` などの共通 field の意味を資格ごとに変えてはいけない。
7. 複数正解を単一正解へ矯正しない。
   - `answer_result_text`が複数番号を示す問題は、資格・問題形式により正規の仕様として存在する。
   - 単一正解を要求する検証は、対象資格と`questionType`を明示的に限定する。

## 工程別の必須項目

| 工程 | 対象 | 必須/準必須 | 目的 |
| --- | --- | --- | --- |
| `00_source` | `question_bodies[]` | `question_url`, `answer_result_text`, `examYear`, `examLabel`, `public_question_id` または `original_question_id` | 元サイトからの出典、年度、正答根拠を保持する。 |
| `10_questionType_fixed` | patch | `questionType` | 回答体験を確定する。ここで `true_false` / `flash_card` / `group_choice` などを決める。 |
| `15_correctChoiceText_fixed` | patch | `questionIntent`、必要時のみanswer result補正 | 設問が正しいもの・誤っているもののどちらを選ばせるか確定する。 |
| `23_correctChoiceText_fixed` | 厳密正答patch | `original_question_id`, `correctChoiceText` | 02aで問題文・全選択肢・公式解答を一問ずつ照合し、03の前提となる正誤を確定する。 |
| `20_merged_1` / `30_merged_2` | `question_bodies[]` | `questionType`, `answer_result_text`, `correctChoiceText`, `examYear`, `examLabel` | Firestore 変換前の最低限の品質を担保する。 |
| `20_merged_1` / `30_merged_2` | `question_bodies[]`, `questionType=true_false` | `questionIntent` | 正しいものを選ぶ問題か、誤っているものを選ぶ問題かを明示する。 |
| `18_law_context_prepared` | 法令コンテキスト patch | `isLawRelated`, `lawGroundedExplanationNotNeeded`, 条件付きで `lawReferences` | 03の解説文作成前に、法令・制度論点かどうかと現行法根拠候補を固定する。 |
| `21_explanationText_added` | patch | `explanationText`, `suggestedQuestions`, `suggestedQuestionDetails`, `original_question_id`, `question_url` | 解説と想定質問を事前データとして持ち、画面表示時に AI を自動起動しない。 |
| `21_explanationText_added` | 法令判定の最終反映 | `isLawRelated`, `lawGroundedExplanationNotNeeded` | 02bの判定を引き継ぎ、解説文作成中に矛盾を見つけた場合だけ修正する。 |
| `22_questionSetId_linked` | patch | `questionSetId` | アプリ内カテゴリ/問題集へ紐付ける。 |
| `40_convert` | `questions[]` | `questionId`, `questionSetId`, `questionText`, `questionType`, `qualificationId`, `questionTags`, `isOfficial`, `isDeleted`, `isChoiceOnly`, `isGroupable`, `originalQuestionBodyText`, `correctChoiceText`, `examYear`, `examSource` | upload 直前の Firestore 相当データ。 |
| upload | Firestore doc | `createdById`, `updatedById`, `createdAt`, `updatedAt` | upload script が付与する監査フィールド。人手で中間 JSON に書かない。 |

## `questions` フィールド契約

凡例:

- `DB必須`: Firestore 上の `questions/{questionId}` document として必須。
- `整備必須`: 公式過去問データとして `exam_scraper` で必須または実質必須。
- `nullable`: `null` を明示してよい。`原則omit` は、値がないなら field 自体を作らない。
- `作成主体`: 主にどの工程/実装が作るか。

| 論理名 | DBキー | 型 | DB必須 | 整備必須 | nullable | validation / enum | 作成主体 | 備考 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Firestore document ID | なし。document ID | string | - | 必須 | 不可 | 空文字不可。既存 Firestore 由来は既存 ID 維持。 | `40_convert` | 中間 JSON では `questionId`。Firestore document 本体には保存しない。 |
| 問題集ID | `questionSetId` | string | 必須 | 必須 | 不可 | 空文字不可。 | `22_questionSetId_linked`, `40_convert` | 未分類のまま upload しない。 |
| 問題集参照 | `questionSetRef` | reference/path | 任意 | 任意 | 原則omit | Firestore rules では path/reference 系。 | app / migration | `exam_scraper` の通常 upload では作らない。 |
| フォルダID | `folderId` | string | 任意 | 任意 | 原則omit | string。 | app / migration | 通常は `questionSetId` からたどる。 |
| 年度/回グループID | `listGroupId` | string | 任意 | 推奨 | 原則omit | string。 | upload | 年度や回単位の追跡に使う。upload script は空文字既定値を入れることがある。 |
| 元問題ID | `originalQuestionId` | string | 任意 | 必須相当 | 原則omit | `00_source` では `public_question_id` または `original_question_id` が必須。 | scraper / convert | grouped choice、既存 ID 維持、source conflict 監査の軸。 |
| 元設問本文 | `originalQuestionBodyText` | string | 任意 | 必須 | 原則omit | upload 前に空白不可。 | scraper / convert | 公式過去問では必須。現行法更新後も元設問本文は保持する。 |
| 元選択肢本文 | `originalQuestionChoiceText` | string | 任意 | 条件付き必須 | 原則omit | `true_false` の表示対象では本文または画像が必須。 | convert | DB 契約は string。中間・legacy では配列由来が残ることがあるため、最終 upload 形を確認する。 |
| 画面用設問本文 | `questionBodyText` | string | 任意 | 推奨 | 原則omit | string。 | convert | `questionText` 生成の元。改行除去されることがある。 |
| 問題文 | `questionText` | string | 必須 | 必須 | 不可 | 空文字不可。 | convert | アプリで実際に表示・検索する主文。 |
| 問題タイプ | `questionType` | string enum | 必須 | 必須 | 不可 | `single_choice`, `true_false`, `flash_card`, `fill_in_blank`, `group_choice` | `10_questionType_fixed` | 回答体験の分類。資格ごとに別の意味を持たせない。 |
| 資格ID | `qualificationId` | string | 必須 | 必須 | 不可 | 空文字不可。 | convert / upload | 資格横断集計・カテゴリ管理の軸。 |
| 試験日 | `examDate` | timestamp | 任意 | 任意 | 可 | app rules では timestamp/null。 | future / app | 出題当時法令を厳密に解くための候補。現行 upload では通常使わない。string で入れない。 |
| 問題画像URL | `questionImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | Storage upload / convert | Storage URL 変換後の値。 |
| 問題画像パス | `questionImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | URL ではなく storage path を使う場合。 |
| 元選択肢画像URL | `originalQuestionChoiceImageUrls` | array<string> | 任意 | 条件付き | 可 | list[str]。中間では choice index 単位の nested array も扱う。 | Storage upload / convert | 選択肢本文がない画像問題では実質必須。 |
| 正答ラベル | `correctChoiceText` | string | 任意 | 必須 | 原則不可 | 最終 upload では原則 `正しい` / `間違い`。表記ゆれ `正解` / `不正解` / `誤り` は正規化。 | `23_correctChoiceText_fixed`, convert | 中間では選択肢数分の配列になり得る。Firestore 1 document では string として扱う。 |
| 正答画像URL | `correctChoiceImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 現行の公式過去問 upload では主経路ではない。 |
| 正答画像パス | `correctChoiceImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 同上。 |
| 誤答1 | `incorrectChoice1Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 公式 split 運用ではあまり使わない。 |
| 誤答2 | `incorrectChoice2Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 誤答3 | `incorrectChoice3Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 誤答4 | `incorrectChoice4Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 知識メモ | `knowledgeText` | string | 任意 | 任意 | 原則omit | string。 | explanation / manual | 解説本文と分ける補足知識。 |
| 基本解説 | `explanationText` | string | 任意 | 必須相当 | 原則omit | string。 | `21_explanationText_added` | AI自動起動を避けるため事前データとして持つ。法令差分注記もここに含める。 |
| 想定質問 | `suggestedQuestions` | array<string> | 任意 | 推奨 | 可 | list[str]。 | `21_explanationText_added` | 解説画面に即時表示する質問候補。 |
| 想定質問回答 | `suggestedQuestionDetails` | array<object> | 任意 | 推奨 | 可 | 各要素は `{question, answer}` のみ。どちらも空文字不可。 | `21_explanationText_added` | `question` は `suggestedQuestions` と一致させる。 |
| 条文参照 | `lawReferences` | array<object> | 任意 | 法令問題では推奨/条件付き必須 | 可 | 後述の `lawReferences` 契約に従う。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 条文本文は question doc に持たない。参照と監査状態を残す。 |
| 法令問題フラグ | `isLawRelated` | boolean | 任意 | 02b以降は必須 | 可 | bool/null。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 法令・政令・省令・告示・通達・制度上の義務/定義/手続/基準が、正誤判断または学習上の主要理解に関係する場合に true。年次03b監査の抽出軸。 |
| 法令根拠不要フラグ | `lawGroundedExplanationNotNeeded` | boolean | 任意 | 02b以降は必須 | 可 | bool/null。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 旧「条文に基づき解説」導線との互換フラグ。原則 `!isLawRelated` にする。AI解説・条文確認の正本ではなく、app 側では import/read 互換フィールド扱い。 |
| 法令根拠監査 | `lawRevisionFacts` | object | 任意 | 法令問題では03b以降に推奨/年次監査では必須 | 可 | 後述の `lawRevisionFacts` 契約に従う。 | `03b`, `21_explanationText_added`, convert | 法令関連問題の監査済み根拠・出題当時/現行法差分・AI prompt 用根拠要約。基本解説と自由質問 AI の正本。 |
| 解説画像URL | `explanationImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | Storage upload / app | 解説画像がある場合だけ。 |
| 解説画像パス | `explanationImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 同上。 |
| ヒント本文 | `hintText` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 公式過去問では基本解説優先。 |
| ヒント画像URL | `hintImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / user content | 同上。 |
| ヒント画像パス | `hintImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / user content | 同上。 |
| 試験年 | `examYear` | number/int | 任意 | 必須 | 可だが整備では不可 | 1900-2100 の整数。和暦/ラベルから推定できなければ停止。 | scraper / convert | DB optional でも過去問整備では必須。 |
| 出典表示 | `examSource` | string | 任意 | 必須 | 可だが整備では不可 | 空文字不可。 | convert | 例: `資格名, 2024年, 問1, 設問2`。 |
| タグ | `questionTags` | array<string> | 必須 | 必須 | 不可 | list[str]。空配列可。 | convert / upload | required field。カテゴリそのものではない。 |
| 公式データ | `isOfficial` | boolean | 必須 | 必須 | 不可 | bool。 | convert / upload | 公式過去問 upload は true。ユーザー作成問題と混同しない。 |
| 論理削除 | `isDeleted` | boolean | 必須 | 必須 | 不可 | bool。 | convert / upload | 削除・差し替え時も物理削除を避ける。 |
| 選択肢専用doc | `isChoiceOnly` | boolean | 必須 | 必須 | 不可 | bool。 | convert | `group_choice` / `flash_card` の誤答選択肢など、統計本体ではない表示用 doc。 |
| グループ化可能 | `isGroupable` | boolean | 必須 | 必須 | 不可 | bool。 | convert / upload | 同一 `originalQuestionId` に複数選択肢がある true_false 等で true。 |
| import 元キー | `importKey` | string | 任意 | 任意 | 原則omit | string。 | import / migration | ファイル由来の元キーを残す場合だけ。 |
| 穴埋め定義 | `fillInBlanks` | array<object> | 任意 | `fill_in_blank` では必須相当 | 原則omit | `blankIndex`, `correctChoiceText`, optional incorrect choices。 | app / import | `questionType=fill_in_blank` のみ使う。 |
| 作成者ID | `createdById` | string | 必須 | upload時必須 | 不可 | 空文字不可。 | upload | 中間 JSON では人手入力しない。 |
| 更新者ID | `updatedById` | string | 必須 | upload時必須 | 不可 | 空文字不可。 | upload | 同上。 |
| 作成日時 | `createdAt` | timestamp/datetime | 必須 | upload時必須 | 不可 | timestamp/datetime。 | upload | 既存 doc 更新時は既存値を維持。 |
| 更新日時 | `updatedAt` | timestamp/datetime | 必須 | upload時必須 | 不可 | timestamp/datetime。 | upload | 差分がある時だけ更新する。 |
| 削除日時 | `deletedAt` | timestamp/datetime | 任意 | 任意 | 可 | timestamp/datetime/null。 | app / migration | `isDeleted=true` と整合させる。 |
| 共有元問題集ID | `sourceSharedQuestionSetId` | string | 任意 | 任意 | 原則omit | app では admin only / immutable。 | app / migration | `exam_scraper` が通常更新しない。 |
| 共有元問題ID | `sourceSharedQuestionId` | string | 任意 | 任意 | 原則omit | app では admin only / immutable。 | app / migration | 同上。 |
| メモ件数 | `memoCount` | number/int | 任意 | 任意 | 原則omit | non-negative int。app では admin only / immutable。 | app | `exam_scraper` が更新しない。 |

## `questionType` 契約

| 値 | 意味 | Firestore 変換 |
| --- | --- | --- |
| `true_false` | 1つの肢・文に対して正誤を答える。選択肢は `正しい` / `間違い`。 | 選択肢ごとに `questions` doc へ分割する。 |
| `single_choice` | 複数選択肢から1つを選ぶ。主にユーザー作成や legacy 互換。 | 原則1 doc。公式過去問で使う場合は最終 `correctChoiceText` の型を確認する。 |
| `flash_card` | 問題文だけでも解答可能な想起型。 | 正解 doc と誤答の `isChoiceOnly=true` doc を作ることがある。 |
| `fill_in_blank` | 本文の空欄を埋める。 | `fillInBlanks` が必要。 |
| `group_choice` | 同一設問の選択肢群を並べ、比較して1つだけ選ぶグループ出題専用。 | 正解 doc と誤答の `isChoiceOnly=true` doc を作る。単体出題不可。 |

資格固有の都合で新しい値を作らないでください。新しい回答体験が必要な場合は、`repaso` の enum / rules / app UI / tests / `exam_scraper` schema を同時に更新します。

`正解は 1, 3 です。`のような複数番号は、sourceの公式表示として保持します。`true_false`、`group_choice`などの変換後表現は各型の契約に従いますが、一般則として単一番号へ書き換えません。

## `lawReferences` 契約

`lawReferences` は、法令問題の根拠と監査状態を表します。条文本文そのものは `questions` document に保存しません。

### 中間 JSON の形

`21_explanationText_added` などの patch では、選択肢単位の nested array を使います。

```json
[
  [
    {
      "role": "current_basis",
      "scope": "choice",
      "choiceIndex": 0,
      "lawId": "329AC0000000051",
      "lawTitle": "ガス事業法",
      "referenceDate": "2026-07-04",
      "article": "2",
      "verificationStatus": "verified",
      "source": "egov_xml",
      "comparisonStatus": "differs_from_current"
    }
  ],
  []
]
```

### Firestore 最終形

`convert_merged_to_firestore.py` で、対象 question doc に対応する参照だけを flat な `array<object>` として保存します。

| field | 型 | 必須性 | 説明 |
| --- | --- | --- | --- |
| `role` | enum string | 必須 | `current_basis` または `exam_time_basis`。 |
| `scope` | enum string | 必須 | `question` または `choice`。 |
| `choiceIndex` | number/int | `scope=choice` では必須 | 0-based。中間 nested array の index と一致させる。 |
| `lawId` | string | verified では必須 | e-Gov 法令ID。未確認なら `candidate` / `unverified` にする。 |
| `lawRevisionId` | string | 任意 | 法令履歴・施行日を特定できる場合に残す。 |
| `lawTitle` | string | 必須 | 正式法令名。 |
| `lawAlias` | string | 任意 | 問題文内の短縮表記。例: `法`, `政令`, `規則`。 |
| `referenceDate` | string | 必須 | 現行法または出題当時法令を判定した基準日。 |
| `effectiveDate` | string | 任意 | 法令改正の施行日。 |
| `article` | string | verified では必須 | 条番号。 |
| `articleTitle` | string | 任意 | 条名がある場合。 |
| `paragraph` | string | 任意 | 項。 |
| `item` | string | 任意 | 号。 |
| `subitem` | string | 任意 | 細分。 |
| `verificationStatus` | enum string | 必須 | `verified`, `candidate`, `unverified`。 |
| `source` | enum-ish string | 必須相当 | `manual_review`, `egov_xml`, `scraper_reference_snippet`, `ai_candidate` など。 |
| `comparisonStatus` | enum string | 任意 | `same_as_current`, `differs_from_current`, `not_checked`。 |
| `differenceNote` | string | 任意 | 現行法と出題当時法令が異なる場合の短い注記。 |
| `reason` | string | 任意 | 監査上の補足理由。 |

## `lawRevisionFacts` 契約

`lawRevisionFacts` は、法令根拠監査の結果を question doc に載せるための read-only メタデータです。`lawReferences` は軽量 locator、`lawRevisionFacts` はAI解説・条文確認・年次監査・二次/三次監査で使う監査済み事実セットとして扱います。

`isLawRelated=true` の問題は、差分がある問題だけでなく全件が作成対象です。差分がない問題は `auditStatus="same_as_current"`、出題当時との差分が未確定なら `auditStatus="hold"` として残します。

e-Gov API v2 / 整備済み corpus に出題当時 revision が保持されておらず、資格別方針で認めた一次資料でも出題当時条文を固定できない場合は、出題当時条文を参照していないことを明示したうえで現行法ベースのみの監査としてよい。この場合は `examTime.correctChoiceText` に公式元正答を残し、`examTime.verificationStatus="not_referenced_current_law_only_policy"` または `"from_original_answer"`、`notes` / review sidecar の `remainingRisk` に未参照理由を保存する。出題当時の `lawRevisionId`、`articleTextHash`、`exam_time_basis` は推測で作らない。出題当時 revision が e-Gov API v2 にないこと自体は `hold` 理由にせず、現行法根拠・委任規定・別表等が不足する場合だけ `hold` に戻す。

法令根拠監査は次の三段階で扱います。

1. 一次監査: 現行法・取得できる場合の出題当時法令の条文 snapshot を取得し、`lawId`、`lawRevisionId`、条・項・号、`articleTextHash`、raw XML hash、暫定判断を固定する。
2. 二次監査: 一次で取得した evidence bundle を使い、正答・解説・差分説明が条文本文と矛盾しないかを妥当性監査する。根拠不足なら `hold` として追加取得キューへ戻す。
3. 三次確定: `updated_to_current_law`、一次/二次不一致、高リスク判断を最終決裁し、`correctChoiceText` / `explanationText` の公開確定を承認する。

`referenceDate` は条文の基準日であり、監査判断を確定した日時ではありません。監査日時は `auditedAt`、監査方式の版は `auditMethodVersion` に保存します。

最終形の主な field:

| field | 型 | 必須性 | 説明 |
| --- | --- | --- | --- |
| `auditStatus` | enum string | 必須相当 | `same_as_current`, `updated_to_current_law`, `hold`, `not_law_related`。 |
| `reviewState` | string | 推奨 | `primary_checked`, `secondary_verified`, `tertiary_verified`, `needs_secondary_review`, `needs_tertiary_review` など。 |
| `auditedAt` | string | 推奨 | ISO-8601 datetime。監査判断を確定した日時。 |
| `nextAuditDueAt` | string | 推奨 | ISO-8601 date。原則年1回の次回監査期限。 |
| `auditMethodVersion` | string | 推奨 | 監査方式、prompt、検索・照合手順の版。方式更新時の再監査判定に使う。 |
| `auditInputHash` | string | 推奨 | 問題文、選択肢、元正答、現行条文 snapshot、取得できた場合の出題当時条文 snapshot、locator/hash をまとめた固定入力 hash。 |
| `auditRunId` | string | 推奨 | 現在の確定判断に対応する監査 run ID。 |
| `lawCorpusSnapshotId` | string | 推奨 | 監査時に使った e-Gov / supplemental corpus snapshot ID。 |
| `primaryAuditRunId` | string | 推奨 | 一次監査 run ID。 |
| `secondaryAuditRunId` | string | 推奨 | 二次監査 run ID。 |
| `tertiaryAuditRunId` | string | 条件付き必須 | `updated_to_current_law`、一次/二次不一致、高リスク判断では必須。 |
| `reconciliationStatus` | string | 推奨 | `matched`, `mismatched`, `approved`, `hold` など。一次/二次/三次の照合状態。 |
| `sourceEvidenceVersionId` | string | 推奨 | 元になった `lawEvidenceVersions` の document ID。 |
| `evidenceBindingHash` | string | 推奨 | `lawRevisionFacts.evidenceSummary` と evidence 側 locator set の一致確認用 hash。 |
| `examTime` | object | 必要時 | 出題当時の正答、取得できた場合の lawId、lawRevisionId、条・項・号、参照日、本文hash。current-law-only 方針では公式元正答と verificationStatus だけでもよい。 |
| `current` | object | 法令問題では推奨 | 現行法ベースの正答、lawId、取得できた場合の lawRevisionId、条・項・号、参照日、本文hash。 |
| `differenceFacts` | array<string> | 任意 | 出題当時条文と現行法条文の差分事実。推測や解釈を混ぜない。 |
| `answerImpactFacts` | array<string> | 任意 | その差分が正誤へ与える影響。 |
| `notes` | array<string> | 任意 | 監査注記。未確認点はここか sidecar に残す。 |
| `evidenceSummary` | object | 推奨 | AI prompt と条文確認UIに渡す、監査済み根拠のdenormalized summary。 |

`examTime` / `current` snapshot の主な field:

- `correctChoiceText`
- `lawId`
- `lawRevisionId`（e-Gov API v2 / corpus から特定できる場合。取得できない場合は omit し、`referenceDate`、`articleTextHash`、`sourceUrl`、sidecar の raw XML hash で固定する）
- `lawTitle`
- `article`
- `paragraph`
- `item`
- `subitem`
- `referenceDate`
- `effectiveDate`
- `verificationStatus`
- `articleTextHash`
- `sourceUrl`

`evidenceSummary` の主な field:

- `verdict`: 監査済み結論。例: `correct`, `incorrect`, `same_as_current`, `hold`。
- `explanationText`: 基本解説・自由質問に渡す短い監査済み説明。
- `differenceSummary`: 正誤判断や暗記に影響する差分の要約。
- `promptContext`: AI に渡す方針文。AI はこの範囲外を推測しない。
- `displayRefIds`: UIで表示する根拠 ref の順序。
- `refs[]`: `refId`, `lawTimeScope`, `relation`, `primaryBasis`, `lawId`, `lawRevisionId`（取得できる場合）, `lawTitle`, `elm`, `encodedElm`, `rootArticleElm`, `article`, `paragraph`, `item`, `highlightElms`, `articleTextHash`, `textHash`。

条文本文そのものは、原則として question doc に長文保存しません。本文確認は `lawId + lawRevisionId + elm` と hash から、または `lawRevisionId` が未取得の場合は `lawId + article + referenceDate + articleTextHash` から、e-Gov v2 由来 corpus または整備環境のキャッシュを開きます。例外的に短い本文を中間監査で使う場合も、最終 upload 前に `articleTextHash` / locator へ寄せます。

整備環境では、verified `lawReferences` から取得した現行条文本文を `output/<qualification>/law_evidence/<list_group_id>/current_article_snapshots/` に保存します。JSONL には `lawId`、条・項・号、`apiUrl`、`articleText`、`articleTextHash`、`rawXmlHash`、紐づく `questionIds` を保存し、raw XML は `raw_xml/<timestamp>/` に残します。この evidence は `lawRevisionFacts.current.articleTextHash` や `evidenceSummary.refs[].articleTextHash` の照合元であり、Firestore question doc へ長文本文を直接載せるためのものではありません。

`lawRevisionFacts` が未整備の法令関連問題は、`output/<qualification>/review/law_revision_audit/<list_group_id>_law_revision_audit_queue_<timestamp>.jsonl` に監査 queue として切り出します。queue は判断済み成果物ではなく、問題文・現行正誤・`lawReferences`・取得済み条文 snapshot の hash/API URL/raw XML path を束ねる監査準備物です。同一 `originalQuestionId` の派生レコードに `lawReferences` が空で、兄弟レコードに根拠がある場合は、queue 上で `lawReferencesSource="same_original_question_fallback"` として明示し、元データ側の locator 欠落も summary に残します。監査者は queue を基に、`same_as_current` / `updated_to_current_law` / `hold` / `not_law_related` のいずれかを sidecar と `lawRevisionFacts` へ確定します。

### 法令監査の記録ルール

監査の作業順とevidence sourceは[現行法監査とLawzilla MCP](../operations/lawzilla_mcp_question_maintenance_workflow.md)が正本です。この文書ではfield間の不変条件だけを定義します。

- `lawReferences`が非空なら`isLawRelated=true`。
- `isLawRelated=false`なら`lawReferences`は空又は未定義。
- `lawGroundedExplanationNotNeeded`は互換fieldで、原則`!isLawRelated`。
- `isLawRelated=true`の公開対象には`lawRevisionFacts`を持たせる。
- `auditStatus=updated_to_current_law`の公開には`reviewState=tertiary_verified`が必要。
- `current.correctChoiceText`、トップレベル`correctChoiceText`、解説先頭は同じ結論を示す。
- `examTime.correctChoiceText`は出題時の公式正答を保持し、現行法更新で上書きしない。
- `hold`と未完了review stateは公開可能状態として扱わない。

### アプリ表示への接続メモ

現行法ベースへ更新した問題では、アプリ上でもユーザーが「出題当時の正答」と「現行法ベースの学習上の扱い」を区別できる必要があります。正本は `lawRevisionFacts` とし、基本解説・想定質問・自由入力AI補足はいずれもこの監査済み事実を前提にします。

- `lawRevisionFacts.auditStatus` / `reviewState` に監査判断と二次/三次確認状態を残す。
- `lawRevisionFacts.auditedAt` / `auditMethodVersion` / `auditInputHash` / `lawCorpusSnapshotId` に、いつ・どの方式・どの固定入力・どの法令 corpus で監査したかを残す。
- `lawRevisionFacts.examTime.correctChoiceText` と `lawRevisionFacts.current.correctChoiceText` を分ける。
- `lawRevisionFacts.evidenceSummary` に AI prompt と条文確認UIへ渡す根拠要約、`displayRefIds`、`refs[]` を残す。
- `explanationText` に「現行法に合わせて更新済み」「出題当時の公式正答とは異なる場合がある」という趣旨の短い注記を入れる。
- `suggestedQuestions` / `suggestedQuestionDetails` に、出題当時と現行法の違いを確認できる質問と回答を入れる。
- `lawReferences` は `role="current_basis"` と `role="exam_time_basis"` を分け、差分がある場合は `comparisonStatus="differs_from_current"` と `differenceNote` を残す。current-law-only 方針では `current_basis` だけでよく、未取得の `exam_time_basis` は作らない。
- 年次監査 sidecar では `userVisibleNoticeRequired=true` を残し、将来のUI実装・監査対象抽出に使えるようにする。

repaso 側では、基本解説で正誤・現行法根拠・必要な差分説明が完結することを優先します。条文本文を見たい場合の UI は `lawRevisionFacts.evidenceSummary.refs[]` の `lawId + lawRevisionId + elm`、または `lawRevisionId` 未取得時の `lawId + article + referenceDate + articleTextHash` から開き、一般ユーザー操作で新規検索・再判定を開始しません。

## `suggestedQuestions` / `suggestedQuestionDetails` 契約

AI解説を画面表示時に自動起動しない方針のため、想定質問は問題データ側に事前保存します。

| field | 型 | ルール |
| --- | --- | --- |
| `suggestedQuestions` | array<string> | 画面に出す短い質問文。最大件数はUI負荷を見て絞る。 |
| `suggestedQuestionDetails` | array<object> | `suggestedQuestions` と同じ順序・件数。 |
| `suggestedQuestionDetails[].question` | string | 対応する `suggestedQuestions[i]` と一致。 |
| `suggestedQuestionDetails[].answer` | string | タップ後に表示する回答。条文引用、現行法差分、考え方をここに入れる。 |

追加キーを入れないでください。出典や監査メモは `lawReferences` または review sidecar に分けます。

## 資格固有フィールドの扱い

資格ごとの事情は、Firestore の field 追加ではなく、原則として次の場所に置きます。

| 情報 | 保存先 | Firestore へ入れるか |
| --- | --- | --- |
| 出題範囲、章立て、頻出論点 | `prompt/qualification_docs/<qualification>/01_exam_profile.md` | 入れない |
| 解説方針、ひっかけ、学習者への補足観点 | `prompt/qualification_docs/<qualification>/02_explanation_strategy.md` | `explanationText` に反映するが、独自 field は作らない |
| カテゴリ粒度、questionSetId の境界 | `prompt/qualification_docs/<qualification>/03_category_preparation.md`, `category.json` | `questionSetId` として反映 |
| 法令スコープ、短縮表記、現行法監査方針 | `prompt/qualification_docs/<qualification>/*law_reference*.md` | `lawReferences` / `explanationText` / `suggestedQuestionDetails` に反映 |
| 03前の法令作業メモ | `18_law_context_prepared[].lawContextForExplanation` | Firestore には入れない。03の文章化補助だけに使う |
| 作業中の不確実性、AI再確認対象 | `99_model_review_flags/`, review sidecar, goal notes | 入れない |
| 元サイト固有の一時キー | `00_source` または intermediate JSON | 入れない |

Firestore に資格固有キーを入れたい場合は、この文書の更新だけでは足りません。`repaso` の DB schema と app UI まで含めた仕様変更として扱います。

## 変更時チェックリスト

共通 field の追加・削除・意味変更をするときは、次を同時に確認します。

1. `repaso/firestore.rules` の allowed / required / updateable / immutable。
2. `repaso/lib/firestore/models/question_doc.dart` の enum / allowed fields / readAllowed fields。
3. `repaso/test/firestore/question_schema_sync_test.dart`。
4. `exam_scraper/scripts/common/repaso_firestore_schema.py`。
5. `exam_scraper/config/requirements/required_fields.toml`。
6. `exam_scraper/scripts/convert/convert_merged_to_firestore.py`。
7. `exam_scraper/scripts/upload/upload_questions_to_firestore.py`。
8. この文書。
9. 影響する `prompt/qualification_docs/<qualification>/README.md` または law reference policy。

docs だけを更新した場合でも、最低限 `git diff --check` を実行します。

## 機械チェック

日常CLIとoptionは[question_bank CLI](../../tools/question_bank/README.md)が正本です。この文書のfield契約を変更した場合は、required fields、convert、upload schema、quality-gateの検査も同じcommitで更新します。
