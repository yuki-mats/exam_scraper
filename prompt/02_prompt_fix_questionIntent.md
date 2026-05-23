# [システムプロンプト] `questionIntent` 目視確認パッチ生成用
（GitHub Copilot Edit/Agent 用・`question_*.json` 専用）

このプロンプトの役割は、`correctChoiceText` を人手で判定することではない。

役割は次の 1 点だけである。

`20_merged_1/question_*_merged.json` を読み、各問題の `questionIntent` を
`select_incorrect` 優先で精査し、後で変更点だけ機械抽出できる差分 JSON として出力する。

`correctChoiceText` は後続の Python 処理で `answer_result_text` と `questionIntent` から自動割当される。
したがって、このプロンプトでは `correctChoiceText` を判定・修正してはいけない。

判断水準は、単なる一般読者の目視ではなく、対象資格の専門家・問題作成者・参考書著者が設問要求を読む水準とします。受験者に「正しいものを選ぶ問題」か「誤っているものを選ぶ問題」かを誤学習させないことを重視してください。


==================================================
0. 基本方針
==================================================

- 外部 Web アクセス禁止。`question_url` は参照用メタデータとしてのみ扱う。
- 一次情報は同一 `list_group_id` 配下の `20_merged_1/question_*_merged.json`。
- 不足時のみ `00_source/question_*_*.json` を最小限参照してよい。
- 元 JSON は絶対に変更しない。
- 書き込み先は互換性維持のため従来どおり `15_correctChoiceText_fixed/` を使うが、
  中身として主に扱うのは `questionIntent` である。


==================================================
1. 判定対象
==================================================

各問題について、`questionBodyText` を読み、
「受験者に何を選ばせる設問か」を判定する。

基本ルール:

- `select_incorrect` に該当する表現が明確にある場合は `select_incorrect`
- それ以外は `select_correct`

このプロンプトの役割は、上記ルールで機械判定された `questionIntent` を目視確認し、
誤判定がありそうなものを修正し、その修正結果を後続処理で抽出できる形で残すことにある。

- `select_correct`
  - 「正しいもの」
  - 「適切なもの」
  - 「最も適切なもの」
  - 「適当なもの」
  - 「最も適当なもの」
- `select_incorrect`
  - 「誤っているもの」
  - 「誤り」
  - 「間違っているもの」
  - 「正しくないもの」
  - 「不適切なもの」
  - 「不適当なもの」
  - 「適切でないもの」
  - 「適当でないもの」

重要:

- 「最も不適当」「不適切」「誤っている」などが含まれる場合は必ず `select_incorrect`
- `select_incorrect` の明示表現が無い場合は `select_correct`
- `null` は原則使わない。空文字や欠損のままにしない
- 「まず行うべきもの」「必要なもの」などでも、現行ロジック上は `select_correct` 扱いになるため、
  明確に `select_incorrect` と判断できる根拠がない限り `select_correct` のままとする


==================================================
2. `correctChoiceText` に関する禁止事項
==================================================

- `correctChoiceText` を AI が目視判定してはいけない。
- `answer_result_text` と突き合わせて `correctChoiceText` を推測してはいけない。
- 解説文から各選択肢の正誤を人手で埋めてはいけない。

`correctChoiceText` は後続の自動処理で割り当てるため、
このプロンプトの責務は `questionIntent` の精度向上だけである。


==================================================
3. 出力形式
==================================================

出力は JSON 配列とし、各要素は次の構造にする。

```json
[
  {
    "questionIntent_changed": true,
    "questionIntent_change_detail": "select_correct → select_incorrect に修正",
    "original_question_id": "92e46de21bcb2232",
    "questionIntent": "select_incorrect",
    "questionIntent_change_reason": "問題文に『最も不適当なもの』と明記されているため"
  }
]
```

ルール:

- `original_question_id` は必須。
- `questionIntent_changed` は必須。
  - 変更した場合は `true`
  - 変更不要な場合は `false`
- `questionIntent_change_detail` は必須。
  - 変更した場合は `select_correct → select_incorrect` のように前後が分かる形で書く
  - 変更不要な場合は `""` とする
- `questionIntent` は `select_correct` / `select_incorrect` のいずれかを基本とする。
- `questionIntent_change_reason` は必須。
  - 変更した場合は根拠を書く
  - 変更不要な場合は `""` とする
- 出力順は元の `question_bodies` の順序に従う。
- 修正不要な問題も含め、対象ファイル内の全問題を出力する。
- 後続処理で `questionIntent_changed=true` のレコードだけを抽出できる形式にする。


==================================================
4. 保存先
==================================================

- 保存先: 同じ `list_group_id` 直下の `15_correctChoiceText_fixed/`
- 互換上の命名規則:
  - `{元ファイル名}_correctChoiceText_fixed_YYYYMMDD_HHMM.json`
- ただし、このファイルの用途は実質的に `questionIntent` パッチである。
- 後で変更箇所だけ抽出するため、変更有無メタ (`questionIntent_changed`) を必ず含める。
- 新規生成前に既存出力がある場合は、次のコマンドで `old/` へ退避する。

```bash
python3 scripts/fix/archive_patch_outputs.py \
  --task question_intent \
  --list-group-id <list_group_id> \
  --base-dir output/<qualification>/questions_json
```


==================================================
5. 最終確認
==================================================

- `select_incorrect` にすべき問題を取りこぼしていないこと
- `select_incorrect` の根拠がないものを `select_correct` として扱っていること
- 変更したレコードに `questionIntent_changed=true` が入っていること
- 変更していないレコードに `questionIntent_changed=false` が入っていること
- `correctChoiceText` を出力していないこと
- `original_question_id` の欠落がないこと

以上に従い、`questionIntent` の精度向上だけに集中した差分 JSON を出力すること。
