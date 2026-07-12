# [システムプロンプト] category.json 整備用

この工程は、04で問題を問題集へ紐付ける前に、資格全体の分類正本`output/<qualification>/category/category.json`を新規作成又は洗い替える工程です。

## 入力

- 対象資格の全`00_source`
- 公式出題範囲、専門家資料、参考書・過去問集の目次
- 既存の`category.json`と`prompt/qualification_docs/<qualification>/`
- [category taxonomy policy](qualification_docs/category_taxonomy_policy.md)

## 作業

1. 対象資格の全年度を俯瞰し、上記policyに従って資格単位のtaxonomyを設計する。
2. 既存IDがある場合は互換性を優先し、ID変更が必要なら通常整備と分けてmigrationとして扱う。
3. 資格固有の境界根拠が必要な場合だけ`03_category_preparation.md`へ記録し、共通ルールを複製しない。
4. `category.json`を固定pathへ保存し、policy記載のschema dry-runを実行する。

問題ごとの`questionSetId`はこの工程で付与しません。04で一問ずつ判断します。`00_source`、既存問題ID、Firestoreは変更しません。

## 完了条件

- `folders`と`questionSets`が空でなく、IDが一意で参照関係が有効である。
- 分類が公式・専門家taxonomyを根拠に資格全体を扱い、単年度だけへ過適合していない。
- schema dry-runが成功し、実アップロードは行っていない。
