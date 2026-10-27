# 02 `questionIntent`判定

このpromptは、一問ごとの`logicalProjection`から、設問が成立する側（`select_correct`）と成立しない側（`select_incorrect`）のどちらを選ばせるかを確定する工程の正本です。fieldの型は[問題field契約](../document/reference/question_field_contract.md)、保存先は[artifact契約](../document/operations/artifact_contract.md)を参照します。

## 入力と責務

- 入力は、`00_source`と先行patchを重ねた`logicalProjection`です。
- 02が所有するfieldは`questionIntent`だけです。`correctChoiceText`、`answer_result_text`、`questionType`、問題文、選択肢、解説、IDを変更しません。
- 現在の`questionIntent`、公式解答番号、`correctChoiceText`から逆算せず、問題文と選択肢の構造だけから独立に確定します。
- `aggregateAnswerDecomposition`で記述を分解済みの問題は、元の個数・組合せ候補が`originalAggregateAnswerEvidence`へ分離されています。抽出記述と元の設問指示（例：「適切なものはいくつあるか」→`select_correct`）から判定し、分解hash・範囲が不整合なら保留します。
- 解答指示の欠落、OCR崩れ、画像不足、補う述語の曖昧さにより方向を一意に読めない問題は`hold`（構造化候補では`status=blocked`）にします。

## 判定基準

| 値 | 設問が求めるもの |
| --- | --- |
| `select_correct` | 完全命題として読んだとき、条件に合う・正しい・適切・成立する選択肢を選ぶ。 |
| `select_incorrect` | 完全命題として読んだとき、条件に合わない・誤り・不適切・成立しない選択肢を選ぶ。 |

最初に選択肢の構造を確認し、次の基準で判定します。どちらの値にも既定値や優先順位はありません。

1. **完結記述肢**（選択肢だけで真偽判定できる文）:
   - 本文の選択指示に従う。「正しいものはどれか」は`select_correct`、「誤っているものはどれか」「誤っている記述はいくつあるか」は`select_incorrect`。
   - 行為や観察結果を並べて「監査の指摘事項に該当する行為はどれか」のように条件適合を問う設問は、該当する側を選ぶため`select_correct`とする（行為自体の善悪で反転しない）。
2. **断片肢**（設備名・用語・数値・時期などの名詞句）:
   - 本文の述語を否定語も含めて一度だけ補い、完全命題を作る。例えば「規定に該当しない設備はどれか」「必要性の低い時期はどれか」は、「設備Xは規定に該当しない」「時期Xは必要性が低い」が成立する肢を選ぶため`select_correct`とする。本文の否定語だけで`select_incorrect`へ反転しない。
   - 例外として、「気体と材料の組合せとして最も不適切なものはどれか」のように対応の適否自体を問う組合せ肢で不適切側を選ぶ設問は`select_incorrect`とする。
3. **本文下線部への番号ポインタ肢**:
   - 選択肢が下線部番号だけの場合は本文中の語句・記述へのポインタとして扱う。「誤っている語句はどれか」は誤った参照先を選ぶため`select_incorrect`とし、「下線部Nは誤っている」を補って反転しない。

## 出力と検証

システム実行ではJSON Schemaに従い`questionIntent`だけを返します（`hold`時は更新候補を返さない）。手動・batch最小JSONは`original_question_id`（なければ`public_question_id`）と`questionIntent`の2fieldとし、`question_bank.py materialize-patch --task question_intent`で`15_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json`へ保存し、`check-question-intent-patch`で検証します。02a判定後に`questionIntent`・`correctChoiceText`・公式解答が矛盾した場合は自動補正せず停止します。
