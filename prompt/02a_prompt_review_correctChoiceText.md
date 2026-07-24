# 02a `correctChoiceText`厳密判定

このpromptは、一問ごとの`logicalProjection`を読み、各選択肢そのものの正誤を根拠から独立に確定する工程の正本です。03の解説は、この工程で確定した正誤を前提に作ります。

fieldの型と工程間の不変条件は[問題field契約](../document/reference/question_field_contract.md)、保存先と固定名は[artifact契約](../document/operations/artifact_contract.md)を優先します。

## 入力と責務

- 入力は`00_source`と01・02までの確定patchを重ねた`logicalProjection`です。02a自身の結果を含む物理的なmerged artifactを入力にしません。
- `questionBodyText`と各`choiceTextList`を結合した完全な命題を一肢ずつ読み、出題時点の公式解答、元解説、資格別方針で認めた根拠と照合します。
- 02aが所有するのは通常`correctChoiceText`だけです。`questionType`、`isCalculationQuestion`、`questionIntent`、問題文、選択肢、解説、IDを変更しません。
- 現在の`correctChoiceText`、正答数、`questionType`又は`questionIntent`から各肢の正誤を逆算しません。各選択肢を同じ根拠基準で独立に判定します。
- 根拠不足、公式解答との衝突、画像欠落又は命題を一意に読めない問題は、問題単位の`hold`（構造化候補では`status=blocked`）にします。一部の肢だけを確定したり、現在値で残りを埋めたりしません。

## 判定基準

`correctChoiceText`は選択肢そのものの正誤です。`select_incorrect`の問題でも、誤っている肢は`間違い`、正しい肢は`正しい`と記録します。

1. 問題文の条件を各選択肢へ適用し、完全な判定命題を作ります。
2. その命題を、確認できる専門的根拠に照らして`正しい`又は`間違い`と判定します。
3. 全肢の判定後にだけ、02で確定した`questionIntent`と出題時の公式解答を使って、選ばれる肢との整合を確認します。
4. 不整合があれば`hold`にし、02又は02aのどちらが正しいかをこの照合だけで決めません。

`answer_result_text`は最後の整合確認に使い、そこから`correctChoiceText`を自動割当しません。`true_false`では各記述の事実上の正誤を判定します。`flash_card`と`group_choice`でも正答だけへ配列を縮めず、各候補が問題文の条件を満たすかを全件判定します。単一正答が期待される形式でも、先に「正しい」を1件へ固定してから他の肢を合わせません。

法令問題では、この工程で出題時点の正誤を確定します。現行法との差が疑われる場合は推測で更新せず、02b・03bへ送ります。03bで現行法ベースの正誤が正式に変わった場合は、同じ`23_correctChoiceText_fixed`を更新し、後続artifactを再生成します。

## 出力

問題整備システムでは、指定されたJSON Schemaに従い、各問題の候補に`correctChoiceText`だけを設定します。配列の要素数は`choiceTextList`と同じにし、選択肢順に`正しい`又は`間違い`だけを入れます。`hold`では更新候補を返しません。

手動又はbatch運用で全問を確定できた場合のAI生出力JSONは、元の順序と件数を保ち、各要素を次の2fieldだけにします。`original_question_id`がなければ`public_question_id`を使います。一問でも`hold`なら現在値で残りの肢を埋めず、そのbatchを正式patchへmaterializeしません。

```json
[
  {
    "original_question_id": "92e46de21bcb2232",
    "correctChoiceText": ["正しい", "間違い", "正しい"]
  }
]
```

正式patchは同じ`list_group_id`の`23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json`へ固定名で保存します。materialize処理はsource identity、変更メタデータ、`question_url`などの非判断fieldを機械的に補います。

```bash
python3 tools/question_bank/question_bank.py materialize-patch \
  --task correct_choice \
  --source <logical_projection.json> \
  --raw <minimal.json> \
  --output <list_group_id>/23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json
```

## 安全境界と検証

- `00_source`は変更、削除、改名しません。既存IDを変更しません。
- merged、convert、upload-readyを直接編集しません。
- 不確実性は`99_model_review_flags/`又はreview sidecarへ`hold`理由、衝突した根拠、確認事項を残します。
- serverは一問ごとにID、件数、配列型、`choiceTextList`との同数、値の許可集合、source bindingを検証します。
- 全工程後の機械検証は、`questionType`、`questionIntent`、`correctChoiceText`、公式解答の不整合を検出したら停止するだけです。正答数を合わせるための値変更や、他fieldへの自動補正は行いません。
- patchへの反映後に作るmerged artifactは独立した生成工程の責務です。

```bash
python3 scripts/check/check_correct_choice_patch_coverage.py \
  --source <logical_projection.json> \
  --patch <list_group_id>/23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json \
  --require-full
```
