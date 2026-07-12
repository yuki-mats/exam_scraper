# 手作業patch品質向上goalテンプレート

一問ずつ専門家水準でpatchを見直すGoalBuddy用テンプレートです。このディレクトリは実行計画の雛形であり、workflow仕様の正本ではありません。

## 参照する正本

- 全体順序: [問題整備ワークフロー](../../../../document/operations/exam_pipeline_manual_and_automation.md)
- 人間判断工程: [prompt一覧](../../../../prompt/README.md)
- 保存先: [artifact契約](../../../../document/operations/artifact_contract.md)
- field: [question field契約](../../../../document/reference/question_field_contract.md)
- 検証: [question_bank CLI](../../../../tools/question_bank/README.md)

詳細手順をこのテンプレートへ転載せず、goalでは対象qualification、listGroupId、設問、patch family、検証commandだけを固定します。

## 使い方

1. このディレクトリを`docs/goals/<slug>/`へコピーする。
2. [goal.md](goal.md)と`state.yaml`のplaceholderを埋める。
3. 原則`1 Worker = 1問`として対象設問分のtaskを作る。
4. `/goal Follow docs/goals/<slug>/goal.md.`で開始する。

`questionType`、`questionIntent`、`correctChoiceText`、`explanationText`は同じWorkerで確認できますが、更新先は各正本promptの責務に従って分けます。分類の全面見直しやpublishは、必要なら別goalに分離します。
