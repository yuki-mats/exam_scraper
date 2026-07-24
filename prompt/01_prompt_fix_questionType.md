# 01 `questionType` / `isCalculationQuestion` 判定

このpromptは、問題整備システムが渡す一問ごとの`logicalProjection`を読み、回答操作を表す`questionType`と、解答に計算が必要かを表す`isCalculationQuestion`を確定する工程の正本です。対象資格の専門家・問題作成者・参考書著者が、受験者の学習体験を設計する水準で判断します。

fieldの型と工程間の不変条件は[問題field契約](../document/reference/question_field_contract.md)、保存先と固定名は[artifact契約](../document/operations/artifact_contract.md)を優先します。

## 入力と責務

- 通常入力は`logicalProjection`です。これは`00_source`へ確定済みpatchを工程順に重ねた現在の問題内容であり、物理的なmerged artifactをこの工程の前提にしません。
- `questionBodyText`、`choiceTextList`、図、ローカルに保存済みの解説資料を読み、受験者が実際に行う回答操作を確認します。外部Webや`question_url`は参照しません。
- 通常の01が所有するfieldは`questionType`と`isCalculationQuestion`だけです。問題文、選択肢、正答、設問方向、解説、IDを編集しません。
- 現在の`questionType`、`isCalculationQuestion`、`questionIntent`、`correctChoiceText`又は正答数を判定の正本にしません。2fieldも互いを根拠にせず、それぞれ内容から独立に確定します。
- 根拠不足、画像欠落、OCR崩れ又は回答操作を一意に定められない問題は`hold`（構造化候補では`status=blocked`）にします。現在値を候補として出して完了扱いにしません。

## `questionType`の判定

`isOfficial=true`の公式過去問と暗記プラス独自問題では、次の3値だけを使います。`single_choice`と`fill_in_blank`はユーザー作成問題用であり、公式問題の新規整備又は洗い替えの候補にはしません。

| 値 | 回答操作 |
| --- | --- |
| `true_false` | 選択肢ごとの記述を単独で読み、その記述自体の正誤・適否を判定する。複数の独立した肢を選ぶ問題も、各肢の判定が学習単位ならこれに含む。 |
| `flash_card` | 問題文の条件、知識、図又は計算から答えを一意に導き、選択肢は導いた答えとの照合に使う。 |
| `group_choice` | 選択肢側の情報又は候補同士の比較が解答に不可欠で、選択肢群から正答を1つ選ぶ。 |

文字数、記号、数値、選択肢数ではなく回答操作で決めます。例えば、計算結果を問題文の条件から導ける問題は`flash_card`であり、数値候補であることだけを理由に`group_choice`にはしません。反対に、選択肢にしかない条件を比較しなければ答えられない問題は`group_choice`です。

## `isCalculationQuestion`の判定

正答へ至るために、与条件を式へ代入し、四則演算、比、割合、単位換算などを実行する問題は`true`です。数値の暗記、式・基準値の選択、数値選択肢の存在だけでは`false`です。

この判定は`questionType`から派生させません。`true_false`、`flash_card`、`group_choice`のいずれにも`true`又は`false`があり得ます。

## 集約回答型だけの原文span投影

通常の01は本文と選択肢を所有しません。例外は、複数の独立した記述の正誤を個数・組合せなど一つの回答へ集約しており、どの記述を誤ったか分からない問題を記述単位へ投影する場合だけです。

対象にできるのは、各候補が受験者に個別の正誤判断を求める命題そのものであり、全命題が`questionBodyText`にある問題です。設例条件、共通前提、並べ替え項目、穴埋め語句・数値、計算入力、又は元の`choiceTextList`に個別命題が既に並ぶ通常問題は対象にしません。

1. serverが同一source snapshotのhashを固定し、原文の連続範囲から候補span、boundary ID、candidate IDを決定的に生成します。
2. 同じsource snapshotと候補を使い、別々のread-only threadで専用レビューを2回実行します。各レビューは`questionId`、`schemaVersion`、`sourceHash`、`classification`、`candidateId`、`decision`、`issueCodes`だけを返します。本文、要約、理由、`start`、`end`は返しません。
3. serverが二者の結果を照合し、完全一致、source hash、candidate ID、boundary ID、順序、重複、範囲を検証します。一致した`target/approve`だけを採用します。
4. serverだけが合意済みcandidate IDを`questionBodyText[start:end]`へ解決し、原文spanから`choiceTextList`と新しい`sourceUniqueKeys`を生成します。エージェントが抽出後の文章を書く経路は設けません。
5. serverの照合と投影後に、通常の問題形式候補を別のturnで生成します。対象確定した問題は、原文spanを投影した`logicalProjection`を入力にします。

不一致、hash不一致、候補不足、命題と前提の区別不能又は境界不明は問題単位の`hold`です。第三レビュー、offset fallback又は一部記述だけの採用は行いません。

対象確定時は`questionType=true_false`とします。旧集約回答のFirestore ID、正答、解説、選択肢別メタデータは派生記述へ流用せず、後続工程で全記述分を独立に確定します。同じ元問題の全記述が公開条件を満たすまで、親問題全体を公開しません。`aggregateAnswerDecomposition`はpatchとmergedだけに保持し、Firestoreへ公開しません。

## 出力

問題整備システムでは、指定されたJSON Schemaに従い、AI判断fieldとして`questionType`と`isCalculationQuestion`だけを返します。`hold`では更新候補を返しません。

手動又はbatch運用で全問を確定できた場合のAI生出力JSONは、元の順序と件数を保ち、各要素を次の3fieldだけにします。`original_question_id`がなければ`public_question_id`を使います。一問でも`hold`なら現在値で行を埋めず、そのbatchを正式patchへmaterializeしません。

```json
[
  {
    "original_question_id": "e0b892ab33c1e80e",
    "questionType": "flash_card",
    "isCalculationQuestion": true
  }
]
```

正式patchは同じ`list_group_id`の`10_questionType_fixed/<source_stem>_questionType_fixed.json`へ固定名で保存します。通常問題の正式artifactは次の6fieldだけを持ちます。source binding用の4fieldがartifactに存在しても、01のAI判断対象又は編集責務にはなりません。

| field | 正式artifactへの設定方法 |
| --- | --- |
| `questionBodyText` | server又はmaterialize処理が`logicalProjection`から機械複写する。 |
| `choiceTextList` | server又はmaterialize処理が`logicalProjection`から機械複写する。 |
| `questionType` | 01のAI判断field。 |
| `isCalculationQuestion` | 01のAI判断field。 |
| `original_question_id` | serverがsource identityから機械付加する。 |
| `question_url` | server又はmaterialize処理がsource bindingから機械付加する。 |

集約回答型の合意済み問題だけは、serverが`aggregateAnswerDecomposition`、原文span由来の`choiceTextList`、派生`sourceUniqueKeys`を追加します。

CLIで正式patchを作る場合:

```bash
python3 tools/question_bank/question_bank.py materialize-patch \
  --task question_type \
  --source <current_projection_or_source.json> \
  --raw <minimal.json> \
  --output <list_group_id>/10_questionType_fixed/<source_stem>_questionType_fixed.json
```

## 安全境界と検証

- `00_source`は変更、削除、改名しません。既存の`questionId`、`originalQuestionId`、`original_question_id`、`public_question_id`、`sourceUniqueKeys`を理由なく変更しません。
- `12_merged_questionType`、merged、convert、upload-readyは生成物であり、直接編集しません。
- 既存patchを別名で複製せず、対象sourceごとの固定名を更新します。
- 不確実性は`99_model_review_flags/`又はreview sidecarへ`hold`理由と不足根拠を残します。
- serverは一問ごとにID、件数、型、許可値、boolean、source binding、集約回答型のhash/spanを検証します。手動運用では`check-question-type-patch`を実行します。
- 後続の`questionIntent`と`correctChoiceText`が確定した後の機械検証は、不整合を検出したら停止するだけです。どのfieldが正しいかを決めたり、一方へ自動補正したりしません。

```bash
python3 tools/question_bank/question_bank.py check-question-type-patch \
  --source <current_projection_or_source.json> \
  --patch <list_group_id>/10_questionType_fixed/<source_stem>_questionType_fixed.json
```
