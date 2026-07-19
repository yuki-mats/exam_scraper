# 問題整備prompt

この文書は、人間・AIが行うpatch工程の**入口と実行境界**の正本です。工程順・表示名・GUIで組み合わせる文書は`config/question_maintenance_workflow.toml`、判定方法は各prompt、保存先は[artifact契約](../document/operations/artifact_contract.md)、fieldの型は[question field契約](../document/reference/question_field_contract.md)で管理します。

## 詳細prompt

工程一覧、表示名、順序、各promptへの対応は[`config/question_maintenance_workflow.toml`](../config/question_maintenance_workflow.toml)だけで定義します。この文書へ一覧を複製しません。各promptは担当工程の判断方法だけを所有し、工程間の保存先は[artifact契約](../document/operations/artifact_contract.md)を参照します。

## 資格固有資料

[qualification_docs](qualification_docs/README.md)に、資格ごとの試験範囲、解説方針、カテゴリ境界、法令スコープを置きます。共通fieldの意味や共通工程を資格文書へ複製しません。

## 実行境界

- 人間向け日本語の共通品質は[`AGENTS.md`](../AGENTS.md#日本語の品質)を正本とし、すべての人間工程へ渡す。解説固有の構成は`03_prompt_add_explanationText.md`だけで定義する。
- 工程版の改訂と洗い替え要否は[問題整備システム](../document/operations/local_question_review_console.md#作業バージョン)だけを正本とする。
- 問題整備システムが各問題へ渡す`logicalProjection`は、`00_source`と確定patchを物理Mergeと同じ順で重ねた現在入力である。serverがtoken量に応じて複数問をまとめても、model候補とserver検査・確定は一問ごとに分離する。各promptの`20_merged_1`又は`30_merged_2`より優先し、物理成果物はqueue終了時だけ再生成する。
- 判断本文は一問ずつ読み、scriptで量産しない。
- scriptはarchive、materialize、merge、convert、validation、upload dry-runに使う。
- 既存patchを洗い替える場合も、各promptに指定された一次情報から再判定する。
- 判断不能は`99_model_review_flags`又はreview sidecarへ残し、推測で完了させない。
- 機械検証は[question_bank CLI](../tools/question_bank/README.md)を使う。
