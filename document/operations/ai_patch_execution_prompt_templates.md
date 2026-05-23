# AI patch 実施用プロンプトテンプレート（省トークン）

AI に patch 作成を依頼する際、開始時の無駄な探索や既存成果物の確認を減らすためのテンプレート集。

## 共通方針

- `再実施` と明記する
- 対象範囲（単一 `list_group_id` か、資格配下の全 `list_group_id` か）を明記する
- `既存出力物は参照NG` を明記する
- `一般的な目視ではなく、対象資格の専門家・問題作成者・参考書著者として判断する` を明記する
- `repo 全体探索・他 prompt・運用ドキュメント確認は不要` を明記する
- `最後に報告する内容` を限定する

## 確定した処理順

正答精度と解説品質を上げる場合は、次の順で進める。

1. `01_prompt_fix_questionType.md`
2. merge して `20_merged_1` を更新
3. `02_prompt_fix_questionIntent.md`
4. merge / correctChoiceText 下書き補完で、`questionIntent + answer_result_text` 由来の `correctChoiceText` を整える
5. 厳密レビューが必要な設問は `23_correctChoiceText_fixed/` で最終 `correctChoiceText` を一問ずつ見直す
6. `03_prompt_add_explanationText.md`
7. `04_prompt_link_questionSetId.md`
8. merge / prepare で `30_merged_2`、`40_convert`、`upload_to_firestore` を更新

`02_prompt_fix_questionIntent.md` は `correctChoiceText` を直接目視判定する prompt ではない。`questionIntent` を精査するための prompt であり、最終的な `correctChoiceText` は `questionIntent` と `answer_result_text` の整合として別途確認する。`99.99%` 水準を目指す場合は、補完結果を1問ずつ目視レビューする。

## 01用

```text
/prompt/01_prompt_fix_questionType.md を
output/<qualification_key>/questions_json
に対して再実施してください。

対象:
- <単一なら list_group_id / 資格一括なら qualification_key>
- 一次情報は 00_source のみ
- 全問題を一問一問目視で確認して questionType を判定すること
- 既存 10_questionType_fixed の出力物は参照禁止
- 既存 10_questionType_fixed は中身確認せず archive してから新規作成
- 対象資格の専門家・問題作成者・参考書著者として、出題意図と教材上の扱いやすさまで確認すること
- repo 全体探索、他 prompt、運用ドキュメント、無関係スクリプトの確認は不要
- 実行してよいのは archive / materialize / check / eval / prepare_firestore_upload のみ
- 作業単位の最後に merge を実行し、`20_merged_1` を更新
- 最後に「保存先 / 件数 / 検証OK」だけ報告（ログ貼り付け不要）
```

## 02用

```text
/prompt/02_prompt_fix_questionIntent.md を
output/<qualification_key>/questions_json
に対して再実施してください。

対象:
- <単一なら list_group_id / 資格一括なら qualification_key>
- 一次情報は 20_merged_1 のみ
- 不足時のみ 00_source を最小限参照すること
- 全問題を一問一問目視で確認して questionIntent を判定すること
- correctChoiceText を直接判定・出力しないこと
- 既存 15_correctChoiceText_fixed の出力物は参照禁止
- 既存 15_correctChoiceText_fixed は中身確認せず `archive_patch_outputs.py --task question_intent` で archive してから新規作成
- 対象資格の専門家・問題作成者・参考書著者として、設問が受験者に何を選ばせているかを厳密に確認すること
- repo 全体探索、他 prompt、運用ドキュメント、無関係スクリプトの確認は不要
- 実行してよいのは archive / materialize / check / eval / prepare_firestore_upload のみ
- 作業単位の最後に merge / correctChoiceText 下書き補完を実行して更新（`questionSetId` 未完なら `--skip-qset-check`）
- 最後に「保存先 / 件数 / 検証OK」だけ報告（ログ貼り付け不要）
```

## 03用

```text
/prompt/03_prompt_add_explanationText.md を
output/<qualification_key>/questions_json
に対して再実施してください。

対象:
- <単一なら list_group_id / 資格一括なら qualification_key>
- 一次情報は 20_merged_1
- 必要時のみ 23_correctChoiceText_fixed と 00_source を参照
- 法令・数値・定義の裏取りが必要な場合のみ、信頼できる外部Web一次情報を参照
- 全問題を一問一問目視で確認して explanationText を手作業で記述すること
- 既存 21_explanationText_added の出力物は参照禁止
- 既存 21_explanationText_added は中身確認せず archive してから新規作成
- explanationText 本文の生成は AI が直接行い、既存 patch の流用は禁止
- 対象資格の専門家・問題作成者・参考書著者として、正答根拠、誤り箇所、受験者が誤学習しない説明、教材としての分かりやすさまで確認すること
- repo 全体探索、他 prompt、運用ドキュメント、無関係スクリプトの確認は不要
- 実行してよいのは archive / materialize / check / eval / prepare_firestore_upload のみ
- 作業単位の最後に prepare_firestore_upload.py を実行して更新（`questionSetId` 未完なら `--skip-qset-check`）
- 最後に「保存先 / 件数 / 検証OK」だけ報告（ログ貼り付け不要）
```

## 04用

```text
/prompt/04_prompt_link_questionSetId.md を
output/<qualification_key>/questions_json
に対して再実施してください。

対象:
- <単一なら list_group_id / 資格一括なら qualification_key>
- 一次情報は 20_merged_1 と category.json のみ
- 全問題を一問一問目視で確認して questionSetId を判定すること
- 既存 22_questionSetId_linked の出力物は参照禁止
- 既存 22_questionSetId_linked は中身確認せず archive してから新規作成
- 対象資格の専門家・問題作成者・参考書著者として、出題論点が最も自然に復習できる分類へ割り当てること
- repo 全体探索、他 prompt、運用ドキュメント、無関係スクリプトの確認は不要
- 実行してよいのは archive / materialize / check / eval / prepare_firestore_upload のみ
- 作業単位の最後に prepare_firestore_upload.py を実行して更新（`--questionset-only`）
- 最後に「保存先 / 件数 / 検証OK」だけ報告（ログ貼り付け不要）
```

## 毎回先頭に足す共通文

```text
既存出力の妥当性確認や流用はせず、一次情報だけを読んで最初から新規判定してください。
```

## より強く縛る追加文

```text
開始前の状況確認は、対象ディレクトリの列挙と archive 対象確認だけに限定し、repo 横断の探索はしないでください。
```
