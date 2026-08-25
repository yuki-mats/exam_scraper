# 問題整備システムのLuna単一モデル化

## Objective

問題整備システムの運用経路をローカルLLMや上位モデルへ自動昇格させず、GPT-5.6 Lunaだけで整備・再試行・監査を完結させる。

## Original Request

ローカルLLMではなく、Luna以下で応答性能と品質を満たすコスト効率の良い構成にする。

## Goal Oracle

初回、再試行及び独立reviewの全モデル指定が`gpt-5.6-luna`に限定され、1並列、安全境界、機械検査及び既存profile切替契約が回帰していないことをテストで確認する。

## Non-Negotiable Constraints

- `00_source`、production patch、Firestore及び外部公開を変更しない。
- `llm_call_concurrency=1`を維持する。
- 同じLunaによる再確認を、異種モデルによる独立監査とは表示しない。
- ローカル実験profileは運用不可のまま維持し、通常画面へ出さない。
- 変更はモデル選択、対応テスト及び正本文書に限定する。

## Stop Rule

現在のdiffと関連テストを監査し、Luna以外への運用model callがなく、元の依頼を満たすとPM監査が記録した場合だけ終了する。

## Canonical Board

`docs/goals/question-maintenance-luna-only/state.yaml`
