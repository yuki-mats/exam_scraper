# 問題整備prompt

この文書は、人間・AIが行うpatch工程の**順序と入口**の正本です。判定方法は各prompt、保存先は[artifact契約](../document/operations/artifact_contract.md)、fieldの型は[question field契約](../document/reference/question_field_contract.md)で管理します。

## 標準順序

| 工程 | 正本prompt | 入力 | 出力 | 目的 |
| --- | --- | --- | --- | --- |
| 01 | [01 questionType](01_prompt_fix_questionType.md) | `00_source` | `10_questionType_fixed` | 回答体験の形式を確定する。 |
| merge | script | source + 01 | `20_merged_1` | 02の入力を作る。 |
| 02 | [02 questionIntent](02_prompt_fix_questionIntent.md) | `20_merged_1` | `15_correctChoiceText_fixed` | 正しいもの・誤っているもののどちらを選ぶか確定する。 |
| merge | script | source + 01 + 02 | `20_merged_1` | `correctChoiceText`下書きを作る。 |
| 02a | [02a correctChoiceText](02a_prompt_review_correctChoiceText.md) | 下書き済み`20_merged_1` | `23_correctChoiceText_fixed` | 問題・全選択肢・公式解答を一問ずつ照合し、正誤を確定する。 |
| merge | script | source + 01 + 02 + 02a | `20_merged_1` | 厳密正答を03前の入力へ反映する。 |
| 02b | [02b law context](02b_prompt_prepare_law_context.md) | 02a反映済み`20_merged_1` | `18_law_context_prepared` | 法令関連性と現行法根拠候補を準備する。 |
| merge | script | source + 01 + 02 + 02a + 02b | `20_merged_1` | 法令コンテキストを03へ渡す。 |
| 03 | [03 explanation](03_prompt_add_explanationText.md) | 02a・02b反映済み`20_merged_1` | `21_explanationText_added` | 解説と想定質問を作る。 |
| 03b | [03b current law audit](03b_prompt_audit_current_law_and_patch.md) | 法令関連問題とevidence | audit sidecar + 必要なpatch | 現行法監査を一次・二次・三次で確定する。 |
| 04 | [04 questionSetId](04_prompt_link_questionSetId.md) | `20_merged_1` + `category.json` | `22_questionSetId_linked` | 問題集へ分類する。 |

03bで正誤が変わった場合は`23_correctChoiceText_fixed`を更新し、merge後に03を再生成します。

## 資格固有資料

[qualification_docs](qualification_docs/README.md)に、資格ごとの試験範囲、解説方針、カテゴリ境界、法令スコープを置きます。共通fieldの意味や共通工程を資格文書へ複製しません。

## 実行境界

- 判断本文は一問ずつ読み、scriptで量産しない。
- scriptはarchive、materialize、merge、convert、validation、upload dry-runに使う。
- 既存patchを洗い替える場合も、各promptに指定された一次情報から再判定する。
- 判断不能は`99_model_review_flags`又はreview sidecarへ残し、推測で完了させない。
- 機械検証は[question_bank CLI](../tools/question_bank/README.md)を使う。
