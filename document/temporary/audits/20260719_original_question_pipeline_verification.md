# 独自問題パイプライン接続監査

- 実施日: 2026-07-19
- 対象: `05_originalized` → Merge → logical projection → convert → upload projection → requirements
- 公開契約: `isOfficial=true`, `examSource="独自問題"`, `examYear` omit

## 接続結果

1. `config/question_maintenance_workflow.toml`に05工程を追加し、01より前の人間工程として問題整備システムへ接続した。
2. 物理Mergeとlogical projectionの両方が`05_originalized`を最初に適用する。
3. Mergeは独自問題化した問題文、選択肢、正誤、設問意図を反映し、取得元の画像と解説を引き継がない。
4. 問題文全体、選択肢一式又は03解説が`00_source`と完全一致するpatchはMerge前に拒否する。選択肢は並べ替えだけでゲートを回避できない。解説の比較は元のsource recordと03 patchの間だけで行い、比較用原文fieldをmergedやFirestoreへ追加しない。
5. 選択肢単位の公開IDを`public_question_id`から再生成し、取得元site名とsite IDをFirestore document IDへ混ぜない。
6. convertとupload projectionは独自問題の`examYear`を生成せず、空文字も書かない。公式過去問の`examYear`必須契約は維持した。
7. `contentOriginType`等の新しい公開fieldは追加していない。

## 回帰fixture

`tests/fixtures/original_question_pipeline/`に、特定の取得元から転載していない人工データを置いた。`tests/test_original_question_pipeline.py`はこのfixtureを次の順で処理する。

```text
00_source
  → 05_originalized
  → 10_questionType_fixed
  → 20_merged_1 / 30_merged_2
  → 21_explanationText_added / 22_questionSetId_linked
  → 40_convert
  → upload document projection
```

最終artifactについて、取得元の問題文、URL、site ID、解説原文、画像hostが含まれないことを検証する。

## 検証コマンド

```bash
python3 -m unittest \
  tests.test_original_question_pipeline \
  tests.test_scrape_pingt \
  tests.test_scrape_presets \
  tests.test_scrape_identity_keys \
  tests.test_check_00_source_immutability \
  tests.test_question_review_workflow_catalog \
  tests.test_question_review_projection \
  tests.test_merge_source_identity \
  tests.test_convert_merged_to_firestore \
  tests.test_upload_questions_to_firestore \
  tests.test_documentation_structure
```

- 結果: 126 tests, OK
- 既存公式過去問のMerge、convert、upload関連testを同時実行し、回帰なし。
