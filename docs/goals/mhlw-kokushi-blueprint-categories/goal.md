# MHLW Kokushi Blueprint Categories

## Objective

医師国家試験の令和6年版出題基準・ブループリントを根拠に、`mecnet-kokushi` の folder、questionSet、category 構成を整理する。命名はブループリントと出題基準 PDF の表記を原則そのまま使い、違和感のある独自命名を避ける。

## Original Request

医師国家試験の出題基準のドキュメントより、folder、questionsetの構成を整理したい。命名はブループリントの命名を原則、そのまま利用する。MHLW の令和6年版医師国家試験出題基準ページから、医師国家試験用の `category.json` やその他整備すべきことを整備する。PDF は都度スクショを取るなどして取得精度を高くする。

## Intake Summary

- Input shape: `specific`
- Audience: repository owner and downstream repaso / Firestore upload workflow.
- Authority: `requested`
- Proof type: `source_backed_answer + artifact + test`
- Completion proof: MHLW PDF の該当ページをスクリーンショット等で確認し、ブループリント表記に沿った `output/mecnet-kokushi/category/category.json` と関連する根拠/生成スクリプト/検証が repo に残る。
- Likely misfire: 問題データ側の既存 list group 名や独自翻訳で category を作り、MHLW ブループリントの自然な命名から逸れる。

## Non-Negotiable Constraints

- 常に日本語で会話し、変更内容と保存先を明示する。
- MHLW 令和6年版ページを一次情報として扱う。
- PDF 内容はテキスト抽出だけで決めず、該当ページを画像化して階層と見出しを確認する。
- `mecnet-kokushi` の既存 `questions_json/<list_group_id>/00_source` 互換を壊さない。
- 既存の未コミット変更は戻さない。今回必要なファイルだけを触る。
- 変更後は検証し、可能なら GitHub へコミット・プッシュする。

## Current Tranche

1. MHLW ページと PDF を取得し、ブループリント/出題基準の階層を確認する。
2. 既存 repo の category schema と upload/convert 期待値を確認する。
3. `mecnet-kokushi` の category/questionset 構成と根拠ファイルを追加・更新する。
4. 生成物と検証コマンドを通し、差分を確認する。
5. 最終監査後にコミット・プッシュする。

## Canonical Board

Machine truth lives at:

`docs/goals/mhlw-kokushi-blueprint-categories/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/mhlw-kokushi-blueprint-categories/goal.md.
```
