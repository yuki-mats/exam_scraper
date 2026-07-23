# 問題フィールド契約

この文書は、公式過去問と暗記プラス独自問題を整備するときに人間が最初に見る統合仕様書です。
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
   - `整備必須`: 公開品質・出典管理・運営データ更新のため、`exam_scraper` の工程では必須。
   - 例: `examYear` は DB 上は optional であり、公式過去問では必須、独自問題ではomitとする。
4. `null` と未定義は同じ意味ではない。
   - 可能なら optional field は未定義にする。
   - 明示的に `null` を許すのは、既存互換または rules / schema が許容している場合に限る。
   - 空文字を「未確認」の代わりに使う場合は、upload script が既定値として作る field に限る。人間の判断結果としては未確認 reason を sidecar に残す。
5. `correctChoiceText` は出題当時のソース、`questionIntent`、現行法監査結果の関係を崩さない。
   - AI が目視だけで `correctChoiceText` を推測してはいけない。
   - 法令問題で現行法ベースへ更新する場合は、出題当時の正答・現行法根拠・更新済み注記を `lawRevisionFacts` / `explanationText` / `lawReferences` / review sidecar に残す。補足質問は後述の追加価値がある場合だけ保存する。
6. 資格固有ルールは field の意味を変えない。
   - 資格ごとに変えてよいのは、法令スコープ、カテゴリ粒度、出題形式の傾向、解説方針。
   - `questionType` や `lawReferences` などの共通 field の意味を資格ごとに変えてはいけない。
7. 複数正解を単一正解へ矯正しない。
   - `answer_result_text`が複数番号を示す問題は、資格・問題形式により正規の仕様として存在する。
   - 単一正解を要求する検証は、対象資格と`questionType`を明示的に限定する。
8. 作業バージョンをFirestore fieldにしない。
   - 問題ごとの工程版履歴は`output/question_review_console/<qualification>/<listGroupId>/work_versions.json`で管理する。
   - 版の意味と公開条件は[問題整備システム](../operations/local_question_review_console.md#作業バージョン)を正本とする。

## 工程別の必須項目

| 工程 | 対象 | 必須/準必須 | 目的 |
| --- | --- | --- | --- |
| `00_source` | `question_bodies[]` | `answer_result_text`、`public_question_id` または `original_question_id`。Web取得では`question_url`、公式過去問では`examYear`, `examLabel`も必須 | 取得元に応じた出典、正答根拠、公式過去問の年度を保持する。 |
| `05_originalized` | patch | `original_question_id`, `questionBodyText`, `choiceTextList`, `correctChoiceText`, `questionIntent`, `answer_result_text` | 取得元の原文を変更せず、独自問題として公開する基礎内容を作る。公式過去問では使わない。 |
| `10_questionType_fixed` | patch | `questionType`, `isCalculationQuestion`。集約回答型レビュー時は`aggregateAnswerDecomposition`、対象確定時はツール生成の`choiceTextList`, `sourceUniqueKeys` | 回答体験と計算問題分類を別々に確定する。集約回答型は二者一致したcandidate IDをserverが原文spanへ解決して記述単位へ投影する。 |
| `15_correctChoiceText_fixed` | patch | `questionIntent` | 設問が正しいもの・誤っているもののどちらを選ばせるかだけを確定し、正答は変更しない。 |
| `23_correctChoiceText_fixed` | 厳密正答patch | `original_question_id`, `correctChoiceText`。必要時のみ`answer_result_text`補正 | 02aで問題文・全選択肢・公式解答を一問ずつ照合し、03の前提となる正誤を確定する。中間配列も新規更新時は`正しい` / `間違い`へ正規化する。 |
| `20_merged_1` / `30_merged_2` | `question_bodies[]` | `questionType`, `isCalculationQuestion`, `answer_result_text`, `correctChoiceText`。公式過去問では`examYear`, `examLabel`も必須 | Firestore 変換前の最低限の品質を担保する。未分類legacyは監査時だけheuristicで抽出できるが、新規整備の代用にはしない。 |
| `20_merged_1` / `30_merged_2` | `question_bodies[]`, `questionType=true_false` | `questionIntent` | 正しいものを選ぶ問題か、誤っているものを選ぶ問題かを明示する。 |
| `18_law_context_prepared` | 法令コンテキスト patch | `isLawRelated`, `lawGroundedExplanationNotNeeded`, 条件付きで `lawReferences` | 03の解説文作成前に、法令・制度論点かどうかと現行法根拠候補を固定する。 |
| `21_explanationText_added` | patch | `explanationText`, `suggestedQuestionDetailsByChoice`, `original_question_id`。元データにURLがある場合は`question_url`も必須 | 解説と選択肢別の想定質問・回答を事前データとして持ち、画面表示時に AI を自動起動しない。 |
| `21_explanationText_added` | 法令判定の最終反映 | `isLawRelated`, `lawGroundedExplanationNotNeeded` | 02bの判定を引き継ぎ、解説文作成中に矛盾を見つけた場合だけ修正する。 |
| `22_questionSetId_linked` | patch | `questionSetId` | アプリ内カテゴリ/問題集へ紐付ける。 |
| `40_convert` | `questions[]` | `questionId`, `questionSetId`, `questionText`, `questionType`, `qualificationId`, `questionTags`, `isOfficial`, `isDeleted`, `isChoiceOnly`, `isGroupable`, `originalQuestionBodyText`, `correctChoiceText`, `examSource`。`examYear`は公式過去問だけ必須 | upload 直前の Firestore 相当データ。 |
| upload | Firestore doc | `createdById`, `updatedById`, `createdAt`, `updatedAt` | upload script が付与する監査フィールド。人手で中間 JSON に書かない。 |

## `questions` フィールド契約

凡例:

- `DB必須`: Firestore 上の `questions/{questionId}` document として必須。
- `整備必須`: 暗記プラス運営が公開する問題として `exam_scraper` で必須または実質必須。
- `nullable`: `null` を明示してよい。`原則omit` は、値がないなら field 自体を作らない。
- `作成主体`: 主にどの工程/実装が作るか。

| 論理名 | DBキー | 型 | DB必須 | 整備必須 | nullable | validation / enum | 作成主体 | 備考 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Firestore document ID | なし。document ID | string | - | 必須 | 不可 | 空文字不可。既存 Firestore 由来は既存 ID 維持。 | `40_convert` | 中間 JSON では `questionId`。Firestore document 本体には保存しない。 |
| 問題集ID | `questionSetId` | string | 必須 | 必須 | 不可 | 空文字不可。 | `22_questionSetId_linked`, `40_convert` | 未分類のまま upload しない。 |
| 問題集参照 | `questionSetRef` | reference/path | 任意 | 任意 | 原則omit | Firestore rules では path/reference 系。 | app / migration | `exam_scraper` の通常 upload では作らない。 |
| フォルダID | `folderId` | string | 任意 | 任意 | 原則omit | string。 | app / migration | 通常は `questionSetId` からたどる。 |
| 問題グループID | `listGroupId` | string | 任意 | 推奨 | 原則omit | string。 | upload | 公式過去問は年度・回、独自問題は取得元の講座・問題集を表す安定名を使う。 |
| 元問題ID | `originalQuestionId` | string | 任意 | 必須相当 | 原則omit | `00_source` では `public_question_id` または `original_question_id` が必須。 | scraper / convert | grouped choice、既存 ID 維持、source conflict 監査の軸。 |
| 元設問本文 | `originalQuestionBodyText` | string | 任意 | 必須 | 原則omit | upload 前に空白不可。 | scraper / convert | Firestore上で分割する前の基礎問題。公式過去問は元設問を保持し、独自問題は`05_originalized`の本文を入れて取得元の原文を入れない。 |
| 元選択肢本文 | `originalQuestionChoiceText` | string | 任意 | 条件付き必須 | 原則omit | `true_false` の表示対象では本文または画像が必須。 | convert | 独自問題では`05_originalized`の選択肢を使う。DB契約はstring。中間・legacyでは配列由来が残るため、最終upload形を確認する。 |
| 画面用設問本文 | `questionBodyText` | string | 任意 | 推奨 | 原則omit | string。 | convert | `questionText` 生成の元。改行除去されることがある。 |
| 問題文 | `questionText` | string | 必須 | 必須 | 不可 | 空文字不可。 | convert | アプリで実際に表示・検索する主文。 |
| 問題タイプ | `questionType` | string enum | 必須 | 必須 | 不可 | DBは`single_choice`, `true_false`, `flash_card`, `fill_in_blank`, `group_choice`。公式問題の整備は`true_false`, `flash_card`, `group_choice`だけ。 | `10_questionType_fixed` | 回答体験の分類。資格ごとに別の意味を持たせない。 |
| 資格ID | `qualificationId` | string | 必須 | 必須 | 不可 | 空文字不可。 | convert / upload | 資格横断集計・カテゴリ管理の軸。 |
| 試験日 | `examDate` | timestamp | 任意 | 任意 | 可 | app rules では timestamp/null。 | future / app | 出題当時法令を厳密に解くための候補。現行 upload では通常使わない。string で入れない。 |
| 問題画像URL | `questionImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | Storage upload / convert | Storage URL 変換後の値。 |
| 問題画像パス | `questionImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | URL ではなく storage path を使う場合。 |
| 元選択肢画像URL | `originalQuestionChoiceImageUrls` | array<string> | 任意 | 条件付き | 可 | list[str]。中間では choice index 単位の nested array も扱う。 | Storage upload / convert | 選択肢本文がない画像問題では実質必須。 |
| 正答ラベル | `correctChoiceText` | string | 任意 | 必須 | 原則不可 | `正しい` / `間違い`。取得元の表記ゆれ `正解` / `不正解` / `誤り` は読取時に同じ判定として扱い、新規patchと最終uploadでは正規化する。 | `23_correctChoiceText_fixed`, convert | 中間では選択肢数分の配列になり得る。Firestore 1 document では string として扱う。 |
| 正答画像URL | `correctChoiceImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 現行の公式過去問 upload では主経路ではない。 |
| 正答画像パス | `correctChoiceImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 同上。 |
| 誤答1 | `incorrectChoice1Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 公式 split 運用ではあまり使わない。 |
| 誤答2 | `incorrectChoice2Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 誤答3 | `incorrectChoice3Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 誤答4 | `incorrectChoice4Text` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 同上。 |
| 知識メモ | `knowledgeText` | string | 任意 | 任意 | 原則omit | string。 | explanation / manual | 解説本文と分ける補足知識。 |
| 基本解説 | `explanationText` | string | 任意 | 必須相当 | 原則omit | string。`isChoiceOnly=true`ではfield自体を禁止する。 | `21_explanationText_added`, convert | AI自動起動を避けるため事前データとして持つ。`flash_card`と`group_choice`は問題単位の1本だけを正答documentへ投影する。法令差分注記もここに含める。 |
| 解説の公式資料 | `explanationReferences` | array<object> | 任意 | 公式一次資料をオンライン確認できる場合は必須相当 | 原則omit | 後述の`explanationReferences`契約に従う。`isChoiceOnly=true`ではfield自体を禁止する。 | `21_explanationText_added`, convert | 解説の根拠として実際に確認した公式ページの軽量メタデータ。取得元の`referenceUrls`とは分ける。 |
| 想定質問 | `suggestedQuestions` | array<string> | 任意 | 条件付き | 原則omit | Firestore公開時に`suggestedQuestionDetails[].question`から派生する。最大3件。`isChoiceOnly=true`ではfield自体を禁止する。 | convert | 解説画面に即時表示する質問候補。patchでは手書きしない。 |
| 想定質問回答 | `suggestedQuestionDetails` | array<object> | 任意 | 条件付き | 原則omit | 各要素は `{question, answer}` のみ。最大3件。`isChoiceOnly=true`ではfield自体を禁止する。 | convert | 対応する`isChoiceOnly=false` documentだけへ選択肢別正本から投影する。 |
| 条文参照 | `lawReferences` | array<object> | 任意 | 法令問題では推奨/条件付き必須 | 可 | 後述の `lawReferences` 契約に従う。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 条文本文は question doc に持たない。参照と監査状態を残す。 |
| 法令問題フラグ | `isLawRelated` | boolean | 任意 | 02b以降は必須 | 可 | bool/null。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 法令・政令・省令・告示・通達・制度上の義務/定義/手続/基準が、正誤判断または学習上の主要理解に関係する場合に true。年次03b監査の抽出軸。 |
| 法令根拠不要フラグ | `lawGroundedExplanationNotNeeded` | boolean | 任意 | 02b以降は必須 | 可 | bool/null。 | `18_law_context_prepared`, `21_explanationText_added`, convert | 旧「条文に基づき解説」導線との互換フラグ。原則 `!isLawRelated` にする。AI解説・条文確認の正本ではなく、app 側では import/read 互換フィールド扱い。 |
| 法令根拠監査 | `lawRevisionFacts` | object / array<object> | 任意 | 法令問題では03b以降に推奨/年次監査では必須 | 可 | patch・mergedでは選択肢単位の配列又はquestion-level object、Firestoreではobject。詳細は後述。 | `03b`, `21_explanationText_added`, convert | 法令関連問題の監査済み根拠・出題当時/現行法差分・AI prompt 用根拠要約。基本解説と自由質問 AI の正本。 |
| 解説画像URL | `explanationImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | Storage upload / app | 解説画像がある場合だけ。 |
| 解説画像パス | `explanationImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / migration | 同上。 |
| ヒント本文 | `hintText` | string | 任意 | 任意 | 原則omit | string。 | app / user content | 公式過去問では基本解説優先。 |
| ヒント画像URL | `hintImageUrls` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / user content | 同上。 |
| ヒント画像パス | `hintImagePaths` | array<string> | 任意 | 任意 | 可 | list[str]。 | app / user content | 同上。 |
| 試験年 | `examYear` | number/int | 任意 | 条件付き必須 | 原則omit | 公式過去問は1900-2100の整数。独自問題はfield自体を保存しない。 | scraper / convert | 独自問題で空文字や`null`を保存しない。 |
| 出典表示 | `examSource` | string | 任意 | 必須 | 可だが整備では不可 | 空文字不可。独自問題は`独自問題`。 | convert | 公式過去問は例: `資格名, 2024年, 問1, 設問2`。 |
| タグ | `questionTags` | array<string> | 必須 | 必須 | 不可 | list[str]。空配列可。 | convert / upload | required field。カテゴリそのものではない。 |
| 運営データ | `isOfficial` | boolean | 必須 | 必須 | 不可 | bool。 | convert / upload | 暗記プラス運営が公開する公式過去問と独自問題は`true`、ユーザー投稿は`false`。 |
| 論理削除 | `isDeleted` | boolean | 必須 | 必須 | 不可 | bool。 | convert / upload | 削除・差し替え時も物理削除を避ける。 |
| 選択肢専用doc | `isChoiceOnly` | boolean | 必須 | 必須 | 不可 | bool。 | convert | Firestore documentの役割field。`true`のdocは`explanationText`、`explanationReferences`、`suggestedQuestions`、`suggestedQuestionDetails`をfieldごと持たない。問題内容や計算問題の分類には使わない。 |
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

## 公開区分の組合せ

公開区分を表すfieldは`isOfficial`だけです。`contentOriginType`は追加しません。公式過去問と独自問題は、既存の`examYear`と`examSource`の組合せで扱います。

| 問題 | `isOfficial` | `examYear` | `examSource` |
| --- | --- | --- | --- |
| 公式過去問 | `true` | 必須 | 資格名・年度・問番号などの既存表示 |
| 暗記プラス独自問題 | `true` | omit | `独自問題` |
| ユーザー投稿 | `false` | app側の既存契約に従う | app側の既存契約に従う |

独自問題では、取得元の`question_url`、`source_question_id`、原文、解説原文、取得元画像をFirestoreへ入れません。取得元の原文は`00_source`だけに保持し、公開用の問題文・選択肢は`05_originalized`の内容を使います。選択肢単位の公開IDは`public_question_id`から再生成し、取得元site名やIDを含めません。詳細は[独自問題作成ワークフロー](../operations/original_question_authoring_workflow.md)を正本とします。

独自問題の画像要否は、Mergeが`00_source`の問題画像・選択肢画像から内部field`_independentImageRequired`へ記録します。問題文・選択肢・正答を先に確定した中間projectionにもこのfieldを持たせ、画像が揃うまで公開準備を停止します。UploaderはFirestore documentからこのfieldを除外します。公式過去問には適用しません。

## `questionType` 契約

| 値 | 意味 | Firestore 変換 |
| --- | --- | --- |
| `true_false` | 1つの肢・文に対して正誤を答える。選択肢は `正しい` / `間違い`。 | 選択肢ごとに `questions` doc へ分割する。 |
| `single_choice` | ユーザー作成問題の単一選択形式。公式問題ではlegacyデータの読取互換に限る。 | 原則1 doc。公式問題の新規整備又は洗い替えでは使わない。 |
| `flash_card` | 問題文だけでも解答可能な想起型。 | 正解 doc と誤答の `isChoiceOnly=true` doc を作ることがある。 |
| `fill_in_blank` | ユーザー作成問題で本文の空欄を埋める。公式問題ではlegacyデータの読取互換に限る。 | `fillInBlanks` が必要。公式問題の新規整備又は洗い替えでは使わない。 |
| `group_choice` | 同一設問の選択肢群を並べ、比較して1つだけ選ぶグループ出題専用。 | 正解 doc と誤答の `isChoiceOnly=true` doc を作る。単体出題不可。 |

公式問題には、公式過去問と暗記プラス運営が整備する独自問題を含みます。`isOfficial=true`である公式問題は`examYear`の有無にかかわらず、`true_false`、`flash_card`、`group_choice`の3形式だけを使います。各選択肢の記述ごとに正誤を学ぶ問題を`true_false`、問題文の条件や知識から答えを導いて選択肢で照合する問題を`flash_card`、選択肢側の情報又は候補比較が解答に不可欠な問題を`group_choice`とします。計算式へ与条件を代入して答えを一意に求められる問題は`flash_card`です。

`single_choice`と`fill_in_blank`を新たに利用できるのは、ユーザーがアプリで作成する`isOfficial=false`の問題だけです。`examYear`は出典年度であり、公式問題かユーザー作成問題かの判定には使いません。既存データの読取互換は保ちますが、公式問題の新規整備又は洗い替えでこの2形式を候補にしません。

資格固有の都合で新しい値を作らないでください。新しい回答体験が必要な場合は、`repaso` の enum / rules / app UI / tests / `exam_scraper` schema を同時に更新します。

### 集約回答型の記述単位変換

`aggregateAnswerDecomposition`はpatchとmergedだけに保持する内部fieldです。`schemaVersion`、`sourceHash`、`classification`、`spans`、`decision`、`issueCodes`以外を認めません。ここにある`spans`はserverが合意済みcandidate IDから解決した`questionBodyText`上の0始まり・end-exclusive位置であり、レビュー出力ではありません。

serverはsource hashを固定し、資格に依存しない列挙境界の規則から候補span、boundary ID、candidate IDを決定的に生成します。独立した2レビューは`classification`、`candidateId`、`decision`、`issueCodes`だけを返し、同じsource hashとcandidate IDで完全一致した場合だけ`target/approve`になります。レビューschemaは本文、要約、理由、正誤回答、`start`、`end`を受け付けません。

`target`にできるのは、元の回答が複数記述の正誤を一つの回答へ集約し、候補spanの各記述が受験者に個別の正誤判定を求める命題そのものである問題だけです。設例の条件や共通前提、並べ替え項目、穴埋め語句・数値、計算入力は対象にしません。元の`choiceTextList`に個別の命題が既に並ぶ通常問題も変換せず、命題と前提を区別できない問題は`hold`にします。

serverは合意したcandidate IDを元の候補spanへ解決し、順序、非重複、範囲、boundary IDを再検証してから`questionBodyText[start:end]`を切り出します。不一致、hash不一致、候補不足又は境界を確定できない問題は第三レビューやoffset fallbackを行わず`hold`とし、一部の記述だけを公開しません。review slotの予約、確定、consensus保存はbatchごとに同じlock内の1回のload、write、readbackで確定します。

対象確定時は、元問題全文を`questionBodyText`と`originalQuestionBodyText`に残したまま、抽出した各記述を`choiceTextList`へ置き、`true_false`として分割します。派生IDは元問題の安定識別子、記述順、抽出文字列hashから新しく作り、旧集約回答documentのIDを再利用しません。旧正答・解説・選択肢別fieldも引き継がず、後続の既存工程で全記述分が揃った場合だけ公開対象にします。この内部field自体はFirestoreへ公開しません。

### `isCalculationQuestion`（問題整備専用）

`isCalculationQuestion`は`10_questionType_fixed`で管理するbooleanで、正答へ至るために与条件を式へ代入し、演算、比、換算などの計算を行う問題を`true`とします。選択肢が数値であるだけの問題や、式・基準値を知識として選ぶだけの問題は`false`です。

- `questionType`は回答体験、`isCalculationQuestion`は解説作成方針を表す。相互に代用しない。
- `isChoiceOnly`はFirestore documentの役割を表すため、計算問題判定に使わない。
- `isCalculationQuestion=true`の基本解説には、式、代入、必要な単位換算、途中計算、最終値、正答選択肢との対応を含める。この方針は全資格共通とする。
- このfieldは問題整備patchとmerged/auditだけに保持し、`40_convert`、Firestore、Repasoへ公開しない。
- legacyでfieldがない問題は監査用heuristicで候補抽出できるが、その結果を保存済み分類とみなさない。新規又は更新するstage 01出力はbooleanを明示する。

`正解は 1, 3 です。`のような複数番号は、sourceの公式表示として保持します。`true_false`、`group_choice`などの変換後表現は各型の契約に従いますが、一般則として単一番号へ書き換えません。

## `explanationReferences` 契約

`explanationReferences`は、解説の事実確認に実際に使った公式一次資料を、アプリからすぐ開ける形で保存します。資格別の型は作らず、すべての資格で同じ4 fieldだけを使います。

```json
[
  {
    "title": "公式資料のページ名",
    "sourceUrl": "https://example.go.jp/official-document",
    "referenceDate": "2026-07-23"
  },
  {
    "title": "選択肢1の根拠資料",
    "sourceUrl": "https://example.go.jp/official-document-2",
    "referenceDate": "2026-07-23",
    "choiceIndex": 0
  }
]
```

| field | 型 | 必須性 | 説明 |
| --- | --- | --- | --- |
| `title` | string | 必須 | アプリで表示する公式ページ名。 |
| `sourceUrl` | string | 必須 | 直接確認できるHTTPS URL。 |
| `referenceDate` | string | 必須 | 内容を確認した日。`YYYY-MM-DD`。 |
| `choiceIndex` | number/int | 任意 | 特定の選択肢だけに対応する場合の0-based index。 |

- 正式patchへは確認済みの公式資料だけを保存し、候補・未確認URLは`99_model_review_flags`へ分けます。
- 非公式サイト、取得元サイトの`referenceUrls`、検索結果を流用しません。
- 本文、長い引用、出版社種別、検証状態、判断理由などを追加fieldとして持ちません。
- 法令固有のlocatorと監査状態は`lawReferences`に保存し、同じURLを重複保存しません。
- 公開変換では、問題共通参照と該当する`choiceIndex`の参照だけを対象question docへ投影します。

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

`referenceDate` は条文の基準日であり、監査判断を確定した日時ではありません。監査日時は `auditedAt`、その問題で使用した監査方式の識別子は `auditMethodVersion` に保存します。03bを再実行すべきかは別の作業バージョンで判定します。

最終形の主な field:

| field | 型 | 必須性 | 説明 |
| --- | --- | --- | --- |
| `auditStatus` | enum string | 必須相当 | `same_as_current`, `updated_to_current_law`, `hold`, `not_law_related`。 |
| `reviewState` | string | 推奨 | `primary_checked`, `secondary_verified`, `tertiary_verified`, `needs_secondary_review`, `needs_tertiary_review` など。 |
| `auditedAt` | string | 推奨 | ISO-8601 datetime。監査判断を確定した日時。 |
| `nextAuditDueAt` | string | 推奨 | ISO-8601 date。原則年1回の次回監査期限。 |
| `auditMethodVersion` | string | 推奨 | その問題で使用した監査方式、prompt、検索・照合手順の識別子。03bの作業版とは別の監査証跡。 |
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

監査の作業順とevidence sourceは[現行法監査](../operations/current_law_question_maintenance_workflow.md)が正本です。この文書ではfield間の不変条件だけを定義します。

- `lawReferences`が非空なら`isLawRelated=true`。
- `isLawRelated=false`なら`lawReferences`は空又は未定義。
- `lawGroundedExplanationNotNeeded`は互換fieldで、原則`!isLawRelated`。
- `isLawRelated=true`の公開対象には`lawRevisionFacts`を持たせる。
- `auditStatus=updated_to_current_law`の公開には`reviewState=tertiary_verified`が必要。
- `current.correctChoiceText`、トップレベル`correctChoiceText`、解説先頭は同じ結論を示す。
- 複数選択肢のpatchでは、`lawRevisionFacts`を選択肢順の`list<object>`にして各`current.correctChoiceText`をscalarで持つ。互換のquestion-level objectでは`current.correctChoiceText`を選択肢順の配列にする。Convert後のquestion documentでは対応肢のscalarに解決する。
- `examTime.correctChoiceText`は出題時の公式正答を保持し、現行法更新で上書きしない。
- `hold`と未完了review stateは公開可能状態として扱わない。

### アプリ表示への接続メモ

現行法ベースへ更新した問題では、アプリ上でもユーザーが「出題当時の正答」と「現行法ベースの学習上の扱い」を区別できる必要があります。正本は `lawRevisionFacts` とし、基本解説・想定質問・自由入力AI補足はいずれもこの監査済み事実を前提にします。

- `lawRevisionFacts.auditStatus` / `reviewState` に監査判断と二次/三次確認状態を残す。
- `lawRevisionFacts.auditedAt` / `auditMethodVersion` / `auditInputHash` / `lawCorpusSnapshotId` に、いつ・どの方式・どの固定入力・どの法令 corpus で監査したかを残す。
- `lawRevisionFacts.examTime.correctChoiceText` と `lawRevisionFacts.current.correctChoiceText` を分ける。
- `lawRevisionFacts.evidenceSummary` に AI prompt と条文確認UIへ渡す根拠要約、`displayRefIds`、`refs[]` を残す。
- `explanationText` に「現行法に合わせて更新済み」「出題当時の公式正答とは異なる場合がある」という趣旨の短い注記を入れる。
- 出題当時と現行法の違いは、まず`lawRevisionFacts`と`explanationText`だけで理解できるようにする。そこにない追加疑問が残る場合だけ、後述の契約に従って補足質問を作る。
- `lawReferences` は `role="current_basis"` と `role="exam_time_basis"` を分け、差分がある場合は `comparisonStatus="differs_from_current"` と `differenceNote` を残す。current-law-only 方針では `current_basis` だけでよく、未取得の `exam_time_basis` は作らない。
- 年次監査 sidecar では `userVisibleNoticeRequired=true` を残し、将来のUI実装・監査対象抽出に使えるようにする。

repaso 側では、基本解説で正誤・現行法根拠・必要な差分説明が完結することを優先します。条文本文を見たい場合の UI は `lawRevisionFacts.evidenceSummary.refs[]` の `lawId + lawRevisionId + elm`、または `lawRevisionId` 未取得時の `lawId + article + referenceDate + articleTextHash` から開き、一般ユーザー操作で新規検索・再判定を開始しません。

## 選択肢別の補足質問契約

AI解説を画面表示時に自動起動しない方針のため、想定質問は問題データ側に事前保存します。

patchとmergedの正本は`explanationText`と`suggestedQuestionDetailsByChoice`です。基本解説で正誤理由を完結させた上で、公開対象の選択肢にだけ0〜3件の補足を保存します。

補足質問は、基本解説にない追加情報を回答できる場合だけ作ります。候補の質問と回答を基本解説と比べ、同じ結論・理由・根拠を質問形式で言い換えただけなら保存しません。基本解説へ入れるべき重要事項は基本解説へ移し、追加情報が残らなければ0件を正しい状態とします。

`flash_card`と`group_choice`の`explanationText`は、選択肢数にかかわらず問題単位の1要素だけです。選択肢ごとの基本解説は作りません。`flash_card`は正答へ至る考え方を、`group_choice`は正答と比較・組合せ・対応関係の判断基準を、この1本で完結させます。

用語を選ぶ問題では、各用語の意味と見分け方も同じ基本解説に含めます。計算問題は詳細な計算過程をこの1本へ含め、補足質問は原則0件とします。`true_false`だけが選択肢indexと同数の解説を持ちます。

`flash_card`と`group_choice`では、問題全体の補足だけを正答documentへ保存し、誤答選択肢ごとの補足は作りません。`true_false`では各選択肢について同じ追加価値の基準を適用します。いずれも件数を満たすために作りません。

| field | 型 | ルール |
| --- | --- | --- |
| `suggestedQuestionDetailsByChoice` | array<object> | `choiceIndex`は0始まりで重複不可。0件の選択肢は要素を省略する。 |
| `suggestedQuestionDetailsByChoice[].items` | array<object> | 1〜3件。各要素は`question`と`answer`だけを持つ。 |
| `items[].question` | string | 基本解説後に残る短い疑問。`flash_card`と`group_choice`では問題全体の疑問、`true_false`では対象選択肢の疑問とする。選択肢内で重複不可。 |
| `items[].answer` | string | 基本解説にない追加情報を含み、タップ後にAPIを使わず表示する事前回答。 |

公開変換では、対応する`isChoiceOnly=false` documentだけに問題形式に合う`explanationText`と、既存互換の`suggestedQuestionDetails`を投影し、`suggestedQuestions`をその`question`から派生します。`isChoiceOnly=true`には基本解説と両補足fieldを保存しません。既存documentに残る場合はuploadで削除します。旧flat patchを切り詰めたり、質問文の類似で選択肢へ推測配分したりせず、新形式で再生成します。

追加キーを入れないでください。出典や監査メモは `lawReferences` または review sidecar に分けます。

## 資格固有フィールドの扱い

資格ごとの事情は、Firestore の field 追加ではなく、原則として次の場所に置きます。

| 情報 | 保存先 | Firestore へ入れるか |
| --- | --- | --- |
| 出題範囲、章立て、頻出論点 | `prompt/qualification_docs/<qualification>/01_exam_profile.md` | 入れない |
| 解説方針、ひっかけ、学習者への補足観点 | `prompt/qualification_docs/<qualification>/02_explanation_strategy.md` | `explanationText` に反映するが、独自 field は作らない |
| カテゴリ粒度、questionSetId の境界 | `prompt/qualification_docs/<qualification>/03_category_preparation.md`, `category.json` | `questionSetId` として反映 |
| 法令スコープ、短縮表記、現行法監査方針 | `prompt/qualification_docs/<qualification>/*law_reference*.md` | `lawReferences` / `explanationText` / `suggestedQuestionDetailsByChoice` に反映 |
| 03前の法令作業メモ | `18_law_context_prepared[].lawContextForExplanation` | Firestore には入れない。03の文章化補助だけに使う |
| 作業中の不確実性、AI再確認対象 | `99_model_review_flags/`, review sidecar, goal notes | 入れない |
| 問題ごとの整備・評価工程版 | `output/question_review_console/<qualification>/<listGroupId>/work_versions.json` | 入れない |
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
