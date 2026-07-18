# 05 独自問題化

このpromptは、確認済みの取得元から保存した1問を、暗記プラスで公開する独自問題1問へ整える工程の正本です。全体方針は[独自問題作成ワークフロー](../document/operations/original_question_authoring_workflow.md)、fieldは[問題field契約](../document/reference/question_field_contract.md)を優先します。

## 実行条件

- 取得元全体を独自問題化すると松田さんが確認し、スクレイピング設定に登録済みのときだけ実行する。
- 公式過去問にはこのpatchを作らない。
- 1回に1問を判断し、取得元1問から原則1問を作る。

## 作成手順

1. `00_source`の問題文、選択肢、正答、解説を読み、問われる知識、正誤を分ける条件、難易度、資格特有の出題パターンを整理する。
2. 技術的な事実と正答を、作成時点の公式試験ガイド、公式仕様、法令等で確認する。取得元の解説は論点を知るための参考であり、根拠の正本にしない。
3. 問題文と選択肢を、その資格で自然な設問として書き直す。必要な専門用語は保ちつつ、取得元固有の場面、数値、条件、情報順序は自然な独自問題に組み直す。
4. `questionIntent`、`correctChoiceText`、`answer_result_text`を新しい問題文と選択肢に合わせる。正答の根拠と難易度は変えない。

## 完了基準

- 問題文全体は、空白と全半角の差を除いて`00_source`と一致しない。
- 選択肢一式は、順番を除いて`00_source`と一致しない。
- 単語の置換だけではなく、一問として自然な情報順序と条件になっている。
- `correctChoiceText`は`choiceTextList`と同じ件数で、値は`正しい`または`間違い`である。
- 取得元の問題画像、選択肢画像、解説は引き継がない。新規に作った公開用画像を使う場合だけ、05 patchにURLを明示する。
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

`examSource="独自問題"`の設定、`examYear`の除去、取得元を表さない選択肢IDの再生成、取得元画像と解説の除外はMergeが一律に行います。解説は03工程で新しく作成します。
