# 問題整備prompt

この文書は、人間・AIが行うpatch工程の**入口と実行境界**の正本です。工程順・表示名・GUIで組み合わせる文書は`config/question_maintenance_workflow.toml`、判定方法は各prompt、保存先は[artifact契約](../document/operations/artifact_contract.md)、fieldの型は[question field契約](../document/reference/question_field_contract.md)で管理します。

## 詳細prompt

- [01 questionType](01_prompt_fix_questionType.md)
- [02 questionIntent](02_prompt_fix_questionIntent.md)
- [02a correctChoiceText](02a_prompt_review_correctChoiceText.md)
- [02b law context](02b_prompt_prepare_law_context.md)
- [03 explanation](03_prompt_add_explanationText.md)
- [03b current law audit](03b_prompt_audit_current_law_and_patch.md)
- [03c category.json](03c_prompt_prepare_category_json.md)
- [04 questionSetId](04_prompt_link_questionSetId.md)

各ファイルは担当工程の判断方法だけを所有します。工程間の入力・出力と保存先は[artifact契約](../document/operations/artifact_contract.md)を参照します。

## 資格固有資料

[qualification_docs](qualification_docs/README.md)に、資格ごとの試験範囲、解説方針、カテゴリ境界、法令スコープを置きます。共通fieldの意味や共通工程を資格文書へ複製しません。

## 実行境界

- 判断本文は一問ずつ読み、scriptで量産しない。
- scriptはarchive、materialize、merge、convert、validation、upload dry-runに使う。
- 既存patchを洗い替える場合も、各promptに指定された一次情報から再判定する。
- 判断不能は`99_model_review_flags`又はreview sidecarへ残し、推測で完了させない。
- 機械検証は[question_bank CLI](../tools/question_bank/README.md)を使う。
