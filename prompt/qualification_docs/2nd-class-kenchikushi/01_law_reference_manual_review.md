# 二級建築士 `lawReferences` 目視監査手順

## 目的

二級建築士の法規問題について、`lawReferences` が選択肢の正誤根拠として正しい法令・条項を指しているかを一問ずつ確認する。

この監査では、`lawId` が入っていることだけでは合格にしない。`lawTitle`、`lawId`、`article`、`paragraph`、`item` が、問題文・選択肢・解説根拠と一致しているかを確認する。

## `03_prompt_add_explanationText.md` との関係

この手順は、`prompt/03_prompt_add_explanationText.md` で作成した `explanationText` / `suggestedQuestions` / `suggestedQuestionDetails` / `lawReferences` の QA 工程である。

単独の別作業として扱わない。必ず次の流れで使う。

1. `prompt/03_prompt_add_explanationText.md` を正本として解説 patch を作る。
2. 二級建築士固有の対象法令範囲は `02_law_reference_scope.md` で確認する。
3. 二級建築士固有の法令短縮表記と文脈判断は、この手順書で確認する。
4. manual review sheet を生成し、1問ずつ `lawReferences` の妥当性を目視確認する。
5. `needs_fix` が出た場合は、問題文・設問・選択肢・解説文・法令本文のどこが一致していないかを記録し、patch を修正する。

Python のキーワード一致、正規表現、XML 自動突合によって `ok` / `needs_fix` / `verified` を決めてはいけない。Python スクリプトは、台帳生成、JSON 構造チェック、必須フィールドの有無確認などの作業補助に限る。

## 入力

監査者は、次の台帳を使う。

```bash
python3 scripts/check/export_2nd_class_kenchikushi_law_reference_review_sheet.py
```

このコマンドは、問題文・選択肢・解説・`lawReferences` を目視しやすい台帳に整形するだけである。法令紐付けの正誤判定は行わない。

出力先は次のディレクトリである。

```text
output/2nd-class-kenchikushi/review/law_reference_manual_review/
```

主に使うファイルは次の2種類。

- `*_review_<timestamp>.jsonl`
  - 監査結果を記録する正本。
- `*_review_<list_group_id>_<timestamp>.md`
  - 問題文、選択肢、解説、紐づいた `lawReferences` を読みやすく並べた作業用資料。

## 監査単位

- 1問を1単位として確認する。
- 選択肢ごとの `lawReferences` も確認する。
- 1問内で一部の選択肢だけ誤っている場合でも、その問題の `reviewDecision` は `needs_fix` にする。

## 判定

`reviewDecision` は次の3種類だけ使う。

| decision | 使う条件 |
| --- | --- |
| `ok` | 全ての `lawReferences` が、問題文・選択肢・解説根拠と一致し、正式な `lawId` と条項も確認できた |
| `needs_fix` | lawId、法令名、条、項、号、選択肢対応、余分な参照、漏れた参照のいずれかに修正が必要 |
| `hold` | 現行法だけでは判断できず、出題当時法令や改正経緯の確認が必要 |

`pending` は未確認の初期状態だけで使う。完了台帳に `pending` を残してはいけない。

## 目視手順

各問題について、必ず次の順番で確認する。

1. `02_law_reference_scope.md` の対象法令範囲を確認する。
   - スコープ内の法令から確認する。
   - スコープ外の法令を使う場合は、問題文・設問文・選択肢・解説候補に直接根拠があるか確認する。

2. 問題文を読む。
   - 何を問う問題かを把握する。
   - 「建築基準法上」「建築士法上」など、根拠法令の範囲を確認する。

3. 各選択肢と `correctChoiceText` を読む。
   - アプリでは各選択肢が `正しい` / `間違い` で保存される。
   - 問題文が「正しいものはどれか」「誤っているものはどれか」でも、`lawReferences` は選択肢単位で確認する。

4. `explanationText` と `explanationChoiceSnippets` を読む。
   - 解説がどの条文を根拠にしているか確認する。
   - `explanationChoiceSnippets` は候補であり、単独では `verified` の根拠にしない。

5. 法令文書本文を確認する。
   - e-Gov XML/API または官公庁一次情報で、対象条文の本文を確認する。
   - 条文本文の対象・要件・例外・数値が、問題文・選択肢・`explanationText` と一致しているか確認する。

6. 各 `lawReferences` を確認する。
   - `choiceIndex` が対象選択肢と一致しているか。
   - `lawTitle` と `lawId` が正式法令と一致しているか。
   - `article` / `paragraph` / `item` が根拠説明と一致しているか。
   - その参照が余分ではないか。
   - 必要な参照が漏れていないか。

7. 汎用表記を確認する。
   - `法` は原則 `建築基準法`。
   - `令` / `施行令` は原則 `建築基準法施行令`。
   - `規則` / `施行規則` は原則 `建築基準法施行規則`。
   - ただし、設問文脈が次の法令を指す場合は文脈を優先する。

| 文脈 | 正式法令 |
| --- | --- |
| 建築士、建築士事務所、定期講習 | 建築士法 / 建築士法施行規則 |
| 長期優良住宅 | 長期優良住宅の普及の促進に関する法律 / 同施行令 / 同施行規則 |
| 宅地造成、盛土、擁壁 | 宅地造成及び特定盛土等規制法 / 同施行令 / 同施行規則 |
| バリアフリー | 高齢者、障害者等の移動等の円滑化の促進に関する法律 / 同施行令 |
| 耐震改修 | 建築物の耐震改修の促進に関する法律 |
| 品確、住宅性能表示 | 住宅の品質確保の促進等に関する法律 |
| 低炭素建築物 | 都市の低炭素化の促進に関する法律 |

8. 判定を記録する。
   - 問題ない場合は `reviewDecision` を `ok` にする。
   - 修正が必要な場合は `needs_fix` にし、`reviewNotes` と `fixInstructions` に修正内容を書く。
   - 判断を保留する場合は `hold` にし、必要な追加確認を書く。

## JSONL 記入ルール

JSONL の各行で、次を更新する。

```json
{
  "reviewDecision": "ok",
  "reviewer": "codex",
  "reviewedAt": "2026-06-02T12:00:00+09:00",
  "reviewNotes": "建築基準法第2条第十四号として確認。選択肢1の根拠と一致。",
  "fixRequired": false,
  "fixInstructions": ""
}
```

修正が必要な場合は次のように書く。

```json
{
  "reviewDecision": "needs_fix",
  "reviewer": "codex",
  "reviewedAt": "2026-06-02T12:00:00+09:00",
  "reviewNotes": "choiceIndex 2 の規則第10条の3は建築基準法施行規則ではなく建築士法施行規則ではないか確認が必要。",
  "fixRequired": true,
  "fixInstructions": "generator の文脈判定を建築士法施行規則へ寄せ、再生成後に監査を再実施する。"
}
```

## 完了チェック

レビュー完了後は、次を実行する。

```bash
python3 scripts/check/check_2nd_class_kenchikushi_law_reference_review_sheet.py \
  output/2nd-class-kenchikushi/review/law_reference_manual_review/<review_jsonl>
```

途中段階の schema 確認だけなら、次を使う。

```bash
python3 scripts/check/check_2nd_class_kenchikushi_law_reference_review_sheet.py \
  output/2nd-class-kenchikushi/review/law_reference_manual_review/<review_jsonl> \
  --allow-pending
```

## 合格条件

全体の完了条件は次の通り。

- `reviewDecision="pending"` が 0 件。
- `reviewDecision="needs_fix"` がある場合、修正方針が `fixInstructions` に記録されている。
- `reviewDecision="hold"` がある場合、追加確認事項が `reviewNotes` に記録されている。
- 法令紐付けの `ok` / `needs_fix` / `verified` は、問題文・設問・選択肢・解説文・法令本文の目視照合で判断されている。
- 構造チェックで JSON 形式、必須フィールド、台帳記入漏れに問題がない。

構造チェックは次で実行できる。ただし、このコマンドは法令紐付けの正誤判定には使わない。

```bash
python3 scripts/check/audit_2nd_class_kenchikushi_law_explanation_quality.py --repo-root . --strict
```

## 低めのモデルに渡す作業指示

次の文章をそのまま作業者モデルへ渡す。

```text
あなたは二級建築士試験の法規問題について、lawReferences の目視監査だけを行う作業者です。

目的:
prompt/02b_prompt_prepare_law_context.md または prompt/03_prompt_add_explanationText.md で作成された各問題の lawReferences が、選択肢の正誤根拠として正しい法令・条・項・号を指しているか確認してください。

入力:
- prompt/03_prompt_add_explanationText.md
- prompt/qualification_docs/2nd-class-kenchikushi/01_law_reference_manual_review.md
- prompt/qualification_docs/2nd-class-kenchikushi/02_law_reference_scope.md
- Markdown の問題別レビュー資料
- JSONL のレビュー台帳

作業ルール:
1. まず prompt/03_prompt_add_explanationText.md の lawReferences ルールを確認してください。
2. 次に 02_law_reference_scope.md で対象法令範囲を確認してください。
3. 次にこの二級建築士の手順書を確認してください。
4. 1問ずつ確認してください。
5. 問題文、選択肢、correctChoiceText、explanationText、source snippets、lawReferences の順に読んでください。
6. 法令文書本文を確認し、条文本文の対象・要件・例外・数値が、問題文・選択肢・explanationText と一致するか確認してください。
7. lawId が入っているだけで OK にしないでください。
8. lawTitle / lawId / article / paragraph / item が、その選択肢の正誤根拠と一致する場合だけ OK にしてください。
9. 法 / 令 / 規則 の短縮表記は、まず建築基準法 / 建築基準法施行令 / 建築基準法施行規則として確認してください。
10. 建築士法、長期優良住宅法、宅地造成及び特定盛土等規制法、バリアフリー法などの文脈では、その文脈の法令に読み替えて確認してください。
11. 判断できない場合は推測で OK にせず hold にしてください。
12. 修正が必要なら needs_fix にし、どの choiceIndex のどの lawReference をどう直すべきか fixInstructions に書いてください。

出力:
JSONL の reviewDecision / reviewer / reviewedAt / reviewNotes / fixRequired / fixInstructions だけを更新してください。
```
