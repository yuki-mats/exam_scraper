# 整備データのローカル実体への復帰記録

2026-10-03のユーザー指示により、Google Driveを通常運用の保存先として使う構成を終了した。現在の共通方針は[artifact契約](../../operations/artifact_contract.md)を参照する。

- `output/nw/question_images`の212ファイル（1,340,656 bytes）をローカル実体へコピーした。
- 残り13資格の`question_images`も移行した。2,018ファイル（571,747,835 bytes）をコピーし、もともと空の3ディレクトリは空のまま維持した。
- 各保存先の切替前後で全ファイルの相対名とSHA-256を比較し、完全一致を確認した。全14資格の画像保存先リンクは0件になった。
- Drive側の既存データは変更・削除せず、バックアップとして保持した。移行時のコピー後は通常処理から参照しない。
- NWの問題、カテゴリ、review、scrape report、作業版、runディレクトリはローカル実体であることを確認した。`00_source`、既存ID、画像内容は変更していない。
- 実行コードにDriveの固定pathはない。過去runの候補workspace内に残る旧リンクは履歴であり、新規runの正本として流用しない。

画像入力はrepository外へのリンクを拒否し、ローカルへ移行するよう案内する。個別ファイルのroot外リンク、同名画像の内容不一致、MIME、20MiB上限の検査は維持した。対応テストは84件と68 subtestsが通過し、NWの212画像をローカルから読み込めた。

詳細なhash receiptは`output/nw/scrape_reports/local_storage_migration_20261003.json`と`output/local_storage_migration_20261003/`に保存した。これらと画像実体は既存のignore方針に従い、Gitへ強制追加しない。
