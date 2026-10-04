# NW直接目視整備の記録（2026-10-04）

ユーザーの「直接パッチを目視整備により作成していい」という指示に基づく、rootによる手動整備。Luna生成又は工程版receiptとして記録していない。全880問の品質確認完了を意味しない。

## 今回保存した3問

- 201202午前II問14（2f9f3ffb24f9f06b0e736e82）：共有IPv6表を直視し、`::`の展開後の第4フィールド0000と/56の比較範囲を解説に明記。
- 201202午前II問15（03e5a47c25c2799c2530121e）：RFC2637・RFC2661からPPPトンネルとMPLS転送を区別。PPTP又はL2TPだけへ根拠なく限定しない説明に更新。
- 201002午前I問19（731e04537a8cc629adc64b8f）：実図のD下→上、ダミー0日を確認。31日から28日への3日短縮を全有向経路から導出し、flash_cardの解説1件に統合。

各問題の本文・選択肢・正誤・画像・source・公開IDは保持した。変更は各問題自身の21解説patch。工程図の旧補足質問は通常PatchEditorで解説形式と整合する形に整理した。

## 検証と成果物

通常ArtifactSynchronizerで201002・201202をMerge、Convert、upload dry-runした。両年度成功。201002の新しい30成果物は旧日付成果物を通常生成経路で置き換えた。Firestore書込みは行っていない。

AGENTSの3検査は選択肢正答一致48merged、必須field48merged、実正答patch48files/880entriesで成功。metadataのみのpatch entryは一時検査入力から除き、正本ファイルは変更していない。全tracked NW source bytes不変。全patch recordをIDで比較し、変更は今回の3 originalQuestionIdsだけだった。全体test suite未実行。

詳細根拠は `output/nw/scrape_reports/direct_manual_review3_20261004.json`、`direct_manual_review3_artifact_sync_20261004.json`、`direct_manual_review3_required_coverage_20261004.json`。これらはローカル実行記録。

独立Sol評価は未完。評価前には通常API投影のstateHashと新候補を照合する。既存独立合格は変更対象外のまま保持する。

### 特許問25の現行設問に対する直接解説修正

`24e39328a4bf842ad7d79d97`（200902午前Ⅱ問25）の問題文・全選択肢・正誤・解説を一問として直視した。IPA公式冊子PDF11頁の肯定設問と公式正答イを確認し、取得元の改題注記及び現行e-Gov特許法のMainProvision第2・29・30条を別に読んだ。取得元は現行法に合わせた否定設問であり、旧候補の2009年判断の混在を解消した。守秘義務の顧客説明を公知と決めつけず、公開後の1年と申出・証明書提出の条件を説明する。

変更は21解説patchのこの問題4肢だけ。source、公開ID、選択肢、正誤、法令監査状態を保持し、Luna又は法令監査のreceiptを作っていない。正常ArtifactSynchronizerによる200902のmerge/convert/upload dry-run成功。AGENTS3検査成功、全体suite未実行。根拠は`output/nw/scrape_reports/direct_manual_patent1_20261004.json`及び同artifact_sync。法令工程の保留解除と新Sol評価は未完了。
