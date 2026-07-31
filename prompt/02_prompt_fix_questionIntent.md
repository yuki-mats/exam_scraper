# 02 `questionIntent`判定

このpromptは、一問ごとの`logicalProjection`から、設問が正しい側と誤っている側のどちらを選ばせるかを確定する工程の正本です。`correctChoiceText`を決める工程ではありません。

fieldの型と工程間の不変条件は[問題field契約](../document/reference/question_field_contract.md)、保存先と固定名は[artifact契約](../document/operations/artifact_contract.md)を優先します。

## 入力と責務

- 入力は`00_source`と確定済みpatchを重ねた`logicalProjection`です。物理的なmerged artifactを02の前提にしません。
- `questionBodyText`の解答指示を読み、受験者が選ぶべき正誤方向を文全体の意味から判断します。
- `questionBodyText`が「正しい」「適切」「誤っている」「不適切」「該当しない」などの選択方向を明示する場合は、その解答指示を最優先します。選択肢が名詞句、数値、対象名などの短い断片であることを理由に、明示された選択方向を反転しません。
- 02が所有するfieldは`questionIntent`だけです。`correctChoiceText`、`answer_result_text`、`questionType`、問題文、選択肢、解説、IDを変更しません。
- 現在の`questionIntent`、正答番号又は`correctChoiceText`から方向を逆算しません。設問の要求だけから独立に確定します。
- 解答指示が欠けている、OCR崩れや二重否定で方向を一意に読めない、又は画像なしでは判断できない問題は`hold`（構造化候補では`status=blocked`）にします。現在値や多数派を既定値にしません。

## 判定基準

| 値 | 設問が求めるもの |
| --- | --- |
| `select_correct` | 条件に合う、正しい、適切又は成立する選択肢を選ぶ。 |
| `select_incorrect` | 条件に合わない、誤り、不適切又は成立しない選択肢を選ぶ。 |

`questionIntent`は、選択肢の文法形式ではなく、設問がどちら側を答えとして選ばせるかを表します。

- 「次の記述のうち、正しいものはどれか」は`select_correct`です。
- 「次の記述のうち、誤っているものはどれか」は`select_incorrect`です。
- 「気体と材料の組合せとして、最も不適切なものはどれか」は、選択肢が名詞句の組合せでも`select_incorrect`です。
- 「次の設備のうち、この規定に該当しないものはどれか」は、選択肢が設備名だけでも`select_incorrect`です。
- 「誤っているものはいくつあるか」の各肢が「〜の場合」のように短くても`select_incorrect`です。

`choiceTextList`を本文と組み合わせて各肢の`correctChoiceText`を判定する作業と、本文の解答指示から`questionIntent`を判定する作業を混ぜません。

どちらの値にも意味上の優先順位や既定値はありません。

## 出力

問題整備システムでは、指定されたJSON Schemaに従い、各問題の候補に`questionIntent`だけを設定します。`hold`では更新候補を返しません。

手動又はbatch運用で全問を確定できた場合のAI生出力JSONは、元の順序と件数を保ち、各要素を次の2fieldだけにします。`original_question_id`がなければ`public_question_id`を使います。一問でも`hold`なら現在値で行を埋めず、そのbatchを正式patchへmaterializeしません。

```json
[
  {
    "original_question_id": "92e46de21bcb2232",
    "questionIntent": "select_incorrect"
  }
]
```

正式patchは同じ`list_group_id`の`15_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json`へ固定名で保存します。ディレクトリ名は互換上維持しますが、この工程の所有fieldは`questionIntent`だけです。serverは`sourceQuestionKey`、`reviewQuestionId`、`sourceRecordRef`と互換用の識別fieldを機械的に付けます。旧patchにある`questionIntent_changed`、`questionIntent_change_detail`、`questionIntent_change_reason`は現行工程の必須fieldではありません。

```bash
python3 tools/question_bank/question_bank.py materialize-patch \
  --task question_intent \
  --source <logical_projection.json> \
  --raw <minimal.json> \
  --output <list_group_id>/15_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json
```

## 安全境界と検証

- `00_source`は変更、削除、改名しません。既存IDを変更しません。
- merged、convert、upload-readyを直接編集しません。
- 不確実性は`99_model_review_flags/`又はreview sidecarへ`hold`理由と不足根拠を残します。
- server又は`check-question-intent-patch`はsource identity、件数、型、許可値及び問題文に明示された記述の正誤方向との整合を検証します。
- 02aの独立判定後に`questionIntent`、`correctChoiceText`、公式解答が矛盾した場合、機械検証は停止して再確認へ送ります。`questionIntent`又は`correctChoiceText`のどちらかを自動補正しません。

```bash
python3 tools/question_bank/question_bank.py check-question-intent-patch \
  --source <logical_projection.json> \
  --patch <list_group_id>/15_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json
```
