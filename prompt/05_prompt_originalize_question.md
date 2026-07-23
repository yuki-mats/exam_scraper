# 05 独自問題化

このpromptは、確認済みの取得元から保存した1問を、暗記プラスで公開する独自問題1問へ整える工程の正本です。全体方針は[独自問題作成ワークフロー](../document/operations/original_question_authoring_workflow.md)、fieldは[問題field契約](../document/reference/question_field_contract.md)を優先します。

## 実行条件

- 取得元全体を独自問題化すると松田さんが確認し、スクレイピング設定に登録済みのときだけ実行する。
- 公式過去問にはこのpatchを作らない。
- 1回に1問を判断し、取得元1問から原則1問を作る。

## 作成手順

1. `00_source`の問題文、選択肢、正答、解説を読み、問われる知識、正誤を分ける条件、難易度、資格特有の出題パターンを整理する。
2. 技術的な事実と正答を、作成時点の公式試験ガイド、公式仕様、法令等で確認する。取得元の解説は論点を知るための参考であり、根拠の正本にしない。
3. 問題文を、その資格で自然な設問として書き直す。必要な専門用語は保ちつつ、取得元固有の場面、数値、条件、情報順序は自然な独自問題に組み直す。選択肢は、新しい問題文との整合、自然さ又は技術的な正確さのために必要な場合だけ書き直し、差を作ること自体を目的に変更しない。
4. `questionIntent`、`correctChoiceText`、`answer_result_text`を新しい問題文と選択肢に合わせ、文章と正答を確定する。画像生成前に、画像なしの05 patchとしてここまでを保存できる。
5. `00_source`に問題画像又は選択肢画像がある場合は、確定した問題文・選択肢・正答から、画像に必要な情報、ラベル、数値、位置関係を画像仕様として整理する。
6. 画像仕様を基に新しい画像を生成し、確定した問題との整合を確認する。取得元画像そのもの、取得元と同じURL、単なる切り抜き・色変更は使わない。
7. 画像をStorageへ保存し、公開用URLを同じ05 patchへ追記する。生成又は整合確認ができない場合は画像待ちのまま`hold`へ送り、公開工程へ進めない。

## 完了基準

- 問題文全体は、空白と全半角の差を除いて`00_source`と一致しない。
- 選択肢は、独自化した問題文と整合し、正答と誤答を適切に分けている。取得元と同じ選択肢を使うことが自然で正確な場合は、そのまま使用できる。
- 単語の置換だけではなく、一問として自然な情報順序と条件になっている。
- `correctChoiceText`は`choiceTextList`と同じ件数で、値は`正しい`または`間違い`である。
- 取得元の問題画像、選択肢画像、解説は引き継がない。取得元に問題画像又は選択肢画像がある場合、文章確定時は画像URLを省略できるが、05の完成と公開には対応する独自生成画像と公開用Storage URLが必須である。
- 独自生成画像は`question_images/<listGroupId>/05_originalized/`へ保存し、ファイル名を`originalized_<public_question_id>_<用途>_<連番>.<拡張子>`とする。
- 判断できないときはpatchを完了せず、既存のreview sidecarへ送る。

## patch形式

保存先は`05_originalized/<source_stem>_originalized.json`です。`question_bodies`配列に、対応用IDと公開の基礎内容だけを保存します。`examYear`、`examSource`、`isOfficial`、`contentOriginType`はこのpatchに追加しません。

```json
{
  "question_bodies": [
    {
      "original_question_id": "既存の対応ID",
      "questionBodyText": "独自問題化した問題文",
      "choiceTextList": ["選択肢1", "選択肢2"],
      "correctChoiceText": ["正しい", "間違い"],
      "questionIntent": "select_correct",
      "answer_result_text": "正解は1です。"
    }
  ]
}
```

問題文・選択肢・正答を先に確定するときは、この画像fieldなしの形式で保存します。画像生成後、同じrecordへ必要なfieldだけを追記します。

```json
{
  "questionImageStorageUrls": [
    "独自生成した問題画像のFirebase Storage URL"
  ],
  "originalQuestionChoiceImageUrls": [[], []]
}
```

画像が不要な問題では、2つの画像fieldを追加しません。`examSource="独自問題"`の設定、`examYear`の除去、取得元を表さない選択肢IDの再生成、取得元画像と解説の除外、画像要否の内部判定はMergeが一律に行います。解説は03工程で新しく作成します。
