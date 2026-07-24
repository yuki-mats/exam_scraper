# [システムプロンプト] questionSetId 紐付け用

この工程の目的は、各問題が受験者に復習を求める論点を一問ずつ判断し、資格の分類正本に存在する`questionSetId`へ紐付けることです。通常の04では問題全体の主な復習先を再判定します。分類は、問題文だけでなく全選択肢を含む問題全体から確定します。

## 入力と正本

次の入力をすべて確認します。

1. 問題整備システムが渡す現在問題の`logicalProjection`
   - 直接ファイルを扱う場合は、同じ`list_group_id`配下の`20_merged_1/question_*_merged.json`を使います。
   - `questionBodyText`と`choiceTextList`の全選択肢を必ず読みます。
2. `output/<qualification>/category/category.json`
   - 利用できるIDは`questionSets[].questionSetId`だけです。
   - `name`、`description`、`matchingHints`、`folderId`との関係を合わせて読みます。
3. `config/question_maintenance_workflow.toml`から選ばれた対象資格の正本文書
   - 特に`prompt/qualification_docs/<qualification>/03_category_preparation.md`がある場合は、近接分類の境界を確認します。
   - 共通方針は[category taxonomy policy](qualification_docs/category_taxonomy_policy.md)に従います。

`category.json`は利用可能な分類とIDの正本、資格別文書はその分類根拠と境界の正本です。両者が矛盾する場合は04で片方へ決め打ちせず、03cの再作業対象とします。

## 3 fieldの責務

- `questionSetId`は、問題全体の主な復習先を表す一つのIDです。通常の04が再判定するのはこのfieldです。選択したscopeの各問題について、現在値に依存せず問題全体と分類正本から確定します。
- `questionSetIdList`は、Firestore由来の複数の設問を一問へ束ねた際に、取得時点で各設問が持っていた`questionSetId`を重複なく記録した出典情報です。問題全体の分類候補や04の判定結果ではありません。
- `choiceQuestionSetIds`は、`choiceTextList`と同じ順序・件数で、Firestore上の設問へ分割される各選択肢の復習先を保持するfieldです。問題全体の主な復習先とは役割が異なります。

通常の04では`questionSetId`だけを再判定し、`questionSetIdList`と`choiceQuestionSetIds`を新規生成又は同期しません。既存の肢別分類を見直す場合は、各選択肢と分類正本を照合する肢別の再分類として明示的に扱います。3 fieldを互いに自動変換せず、それぞれの意味に合う入力から確定した後、対象fieldの型、件数、`category.json`への所属を機械検証します。

## 守る境界

- 外部Web、取得元ページ、既存の`22_questionSetId_linked`、`30_merged_2`、`40_convert`を分類根拠にしません。
- 現在の`questionSetId`、`questionSetIdList`、`choiceQuestionSetIds`を正解として引き継ぎません。通常の04では、問題全体と分類正本から`questionSetId`を独立に確定し、最後に現在値と比較します。
- `folders[].folderId`を問題へ付与しません。
- `category.json`にないIDを捏造しません。
- 04では`category.json`や資格別正本文書を編集しません。分類の不足や矛盾は03cへ戻します。
- `00_source`、入力ファイル、問題IDは変更しません。
- 出力は`22_questionSetId_linked/`の正式パッチだけです。既存の固定名ファイルは同じ名前で更新し、タイムスタンプ付きの別版を増やしません。

資格固有のIDや境界ルールをこの共通promptへ追記してはいけません。必要な内容は対象資格の`category.json`と`prompt/qualification_docs/<qualification>/`で管理します。

## 一問ごとの判断

1. `questionBodyText`と`choiceTextList`の全選択肢を最初に通読し、設問全体が何を判断させる問題かを確定します。
2. 「この問題を誤った受験者は何を復習すべきか」を一つの学習論点として言語化します。
3. `category.json`の`name`だけでなく、`description`、`matchingHints`、親folderとの関係を比較します。資格別正本文書に境界ルールがあれば、それも照合します。
4. 問題全体を根拠に候補を絞り、分類正本が一つの候補を明確に支持する場合だけ、その`questionSetId`を確定します。
5. 確定後に、選んだIDが`category.json`の`questionSets[]`に存在することを機械検証します。

`questionBodyText`だけで仮分類してはいけません。選択肢の一語、`examLabel`、既存の`category`、キーワード一致だけでも確定しません。これらは問題全体を読むための補助情報に限ります。

「総合」「融合」「その他」のような受け皿も、その分類の`description`と問題全体が明確に一致する場合だけ選びます。複数候補が残ったという理由だけで使ってはいけません。

## 一意に決まらない場合

全選択肢と分類正本を照合しても複数候補が残る場合、最も近いIDへ強制しません。次のどちらかで止めます。

- 同じ境界で複数問題が迷う、必要な学習単元がない、又は`description`と資格別正本文書が矛盾する場合
  - 04を止め、03cで`category.json`と資格別正本を再作業します。
- 分類正本は十分だが、その問題だけ主題を一つに確定できない、又は入力の欠落・崩れがある場合
  - 問題単位の`hold`（構造化候補では`status=blocked`）とし、`22_questionSetId_linked`へ反映しません。

`hold`対象について、空文字、仮ID、候補の先頭、既存値で穴埋めしてはいけません。必要なら既存IDだけを候補としてreview sidecarへ残しますが、候補の記録は分類確定を意味しません。

## 生成AIが直接出す中間JSON

確定できた問題は、各要素が次の2フィールドだけを持つ最小形式で出力します。

```json
[
  {
    "original_question_id": "da6a8179822b27d9",
    "questionSetId": "existing_question_set_id"
  }
]
```

- `question_url`は出力しません。後段の`materialize-patch`が入力から補完します。
- 通常の04では`questionSetIdList`と`choiceQuestionSetIds`を中間JSONへ出力しません。
- `questionSetName`、`questionBodyText`、`update_reason`などを加えません。
- `hold`対象を含む処理単位は未完了です。空IDで形式だけを整えず、再作業又は再確認が終わるまで正式パッチの完了扱いにしません。

## 直接ファイルを扱う場合の作業順

1. `category.json`と対象資格の正本文書を読みます。
2. 対象`20_merged_1/*.json`の件数、順序、`original_question_id`を固定します。
3. 各問題の問題文と全選択肢を読み、一問ずつ分類します。
4. 一意に確定できない問題は、03cへの再作業又は問題単位の`hold`へ分けます。
5. 全問を確定できた処理単位だけ、最小raw JSONから正式パッチを作ります。
6. 件数、順序、問題ID、`category.json`へのID所属を検証します。

## 検証コマンド

### 正式パッチへの変換

```bash
.venv/bin/python tools/question_bank/question_bank.py materialize-patch \
  --task question_set \
  --source /absolute/path/to/question_*_merged.json \
  --raw /absolute/path/to/raw_questionSetId.json \
  --output /absolute/path/to/22_questionSetId_linked/question_*_questionSetId_linked.json
```

### category.json所属を含むパッチ検証

```bash
.venv/bin/python tools/question_bank/question_bank.py check-question-set-patch \
  --source /absolute/path/to/question_*_merged.json \
  --patch /absolute/path/to/22_questionSetId_linked/question_*_questionSetId_linked.json \
  --category /absolute/path/to/category.json \
  --questionset-only
```

### 最終検証

```bash
.venv/bin/python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

## 完了条件

- 全問題を問題文と全選択肢から一問ずつ分類し、未解決の`hold`がない。
- 出力の件数、順序、`original_question_id`が入力と一致する。
- すべての`questionSetId`が`category.json`の`questionSets[]`に存在し、捏造ID、空文字、`folderId`がない。
- `check-question-set-patch`と`quality-gate`がともに終了コード`0`である。
