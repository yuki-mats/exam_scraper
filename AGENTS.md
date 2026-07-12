# Agent Instructions

- 常に日本語で会話すること。
- ユーザーのプロンプトを丁寧に読み、指示に従うこと。
- 変更時には作業内容と保存先を明示すること。
- 変更内容はGitHubへコミット・プッシュすること。

## Git運用

- このリポジトリは `main` だけを使う単一ブランチ運用とする。
- `codex/*`、feature branch、作業用branch、専用branchを新規作成しない。worktree用branchも作成しない。
- 作業開始時に `git status --short`、現在branch、`origin/main`との差分を確認する。
- 未コミット変更がある場合は、内容と所有範囲を確認し、関連する変更ごとに検証・コミットして作業ツリーをクリーンにする。未コミット変更を放置したまま新しい作業を始めない。
- 作業ツリーがクリーンになったら `main` を `origin/main` と同期し、以後のコミットは `main` に直接積む。
- push先は `origin/main` だけとする。別branchへのpush、force push、履歴の上書きを行わない。
- 複数の作業が混在している場合は、一括コミットせず、内容別の小さなコミットに整理する。
- 他者が作成した未コミット変更を、確認なく破棄・巻き戻ししない。

## `00_source`

- スクレイピングでの新規作成だけ許可し、既存ファイルは変更・削除・改名しない。
- 修正は責務に応じて `10` / `15` / `18` / `21` / `22` / `23` / `24` のpatch層へ入れる。
- 新規scrape後のみ `python scripts/check/check_00_source_immutability.py --record-new` を実行する。

## ドキュメント

- 最初に`document/operations/exam_pipeline_manual_and_automation.md`を読み、そこから関心事ごとの正本へ進む。
- 幹には全体順序とリンク先の要旨だけを書き、field、コマンド、UI、法令監査などの詳細を複製しない。
- 継続更新する仕様は`document/operations/`、`document/reference/`、`document/sources/`、`prompt/`の責務に合う1ファイルをSSOTとする。
- 問題整備GUIの工程順・名称・正本文書の組合せは`config/question_maintenance_workflow.toml`だけで定義し、Python・JavaScript・Markdownへ一覧を複製しない。
- 日付付き監査、単発レビュー、移行記録は`document/temporary/`へ置く。`docs/goals/`も実行記録であり仕様正本にしない。
- 新しい文書を追加する前に既存正本へ統合できないか確認し、重複文書と重複記述を残さない。
