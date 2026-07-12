# 一時ドキュメント

このディレクトリは、将来の仕様正本として継続更新しない資料専用です。

## 対象

- 日付付き監査結果と単発レビュー
- 移行・cleanupの実施記録
- 一時的な比較表、調査メモ、承認前資料
- 恒久正本へ反映済みの根拠スナップショット

## 対象外

- 現行workflow、field、保存先、CLI、UIの仕様
- 資格固有の継続利用する方針
- 実装が依存するschemaや設定

恒久的に使う結論は`document/operations/`、`document/reference/`、`document/sources/`、`prompt/qualification_docs/`のいずれか一つへ移し、この配下の資料を日常作業から参照しません。

`docs/goals/`はGoalBuddyが管理する実行記録のため場所を維持しますが、同じく仕様正本ではありません。goal完了後に残すべきルールはoperations又はreferenceへ反映します。

## 構成

```text
temporary/
  audits/       日付付き監査
  reviews/      単発レビュー・承認資料
  migrations/   一度だけの移行手順・実施記録
```

新規ファイルは日付又は対象runを名前に含めます。参照されなくなり、結論が恒久正本へ反映済みなら削除できます。
