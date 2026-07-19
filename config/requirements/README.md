# 必須項目チェック（requirements）

このディレクトリは、JSONの各段階（`00_source` / `merged` / `firestore` など）に対して
「必須項目」「空文字禁止」「条件付き必須」などのルールを定義し、スクリプト側で参照して
異常を早期に検知するための設定を置く場所です。

## 目的
- 作業途中での異変（`examYear` が `null`、`answer_result_text` 欠損、など）を早期に検知する
- ルールをコードにハードコードせず、設定ファイルとして人間が整備できるようにする

## 設定ファイル
- `required_fields.toml`
  - デフォルトルール（全資格共通）と、資格ごとの上書き（任意）を定義します。

## 条件付き必須

`when = { field = "value" }`は指定値と一致するレコードだけに、`when_not = { field = "value" }`は指定値と一致しないレコードにルールを適用します。現在は公式過去問の`examYear`を必須に保ちながら、`examSource = "独自問題"`ではfield自体の省略を許可するために使います。

独自問題のFirestore変換結果では、画像要否をMergeで確認済みであることを示す内部field`_independentImageRequired`も必須です。このfieldはUploaderの公開停止判定に使った後、Firestore documentへ保存しません。

`00_source`の全資格共通ルールは取得元の種類を推測しません。公式過去問の年度は、各scraperと資格別検証で確定します。
