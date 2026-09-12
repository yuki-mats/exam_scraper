# T001 Scout receipt

## 結論

サイトの問題一覧には、第48回（2018年度）から第55回（2025年度）までの8グループがあり、list group IDは`95001`〜`95008`である。標準scraperのdry-runでも同じ8グループを確認できた。

## 現在のsource

- `output/birukan/questions_json/`には23個の`00_source` JSONがある。
- 問題レコードは491件、`canonical_question_key`重複除外後は456問。
- 2019〜2025年度のsourceは部分取得で、2018年度（95001）は問題JSONがなく画像1点だけ残っている。
- 2024年度は50レコード中40問、2025年度は55レコード中30問がcanonical重複除外後の実数である。
- 取得済みレコードは5選択肢・正答候補・解説候補を持つが、複合形式として`flash_card` 4問、`group_choice` 1問がある。

## 設定と次の安全な作業

- `config/scrape_presets.json`に`birukan`がない。
- `config/qualification_rules.json`に専用設定はない（defaultの法令工程は有効）。
- したがって、まず`birukan`のpresetを登録し、95001〜95008を標準scraperで取得する。既存sourceは直接編集せず、取得成功・source品質検査・manifest更新を一体で確認する。
- 取得完了後、取得件数ではなく年度・問番号・canonical identityの欠落と重複を検査してから整備へ進む。

## 証拠

- `https://birukan.kakomonn.com/`の問題一覧に95001〜95008が掲載されている。
- `config/scrape_presets.json`
- `config/qualification_rules.json`
- `output/birukan/questions_json/*/00_source/*.json`
- `output/birukan/question_images/95001/`

