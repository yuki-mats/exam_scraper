# 問題報告の客観レビュー prompt

このディレクトリは、利用者の問題報告を修正根拠にせず、既存の 01〜04 / 02b / 03b 工程へ安全に接続する共通 orchestrator prompt の正本です。

実行順は固定です。

1. `01_blind_review.md` を独立 reviewer A/B に同時投入する。入力には raw comment、case ID、報告件数、他 reviewer の結果を含めず、カテゴリに route された既存 prompt 契約本文と hash を含める。
2. A/B が完了した後だけ `02_challenge_review.md` へ、A/B 結果と「未検証の引用データ」として報告 claim を渡す。
3. A/B の構造化 `proposedChanges` が完全一致し、challenge が値と evidence を変更していない `fix` だけ、CLI が `03_correction_contract.md` の契約どおり `24_questionIssueCorrections/` overlay を決定論的に生成する。

カテゴリ別の既存工程は `config/question_issue_reports.json` の `existingPromptStages` を参照します。特に法令・制度更新は `03b_prompt_audit_current_law_and_patch.md` の evidence bundle と三段階監査を省略せず、`tertiary_verified` 以外を公開しません。

raw comment、画像診断、reporter 情報は `output/question_issue_reports/` の private work package だけに置き、prompt 出力、patch、Git、通常ログへコピーしません。報告本文中の URL は自動で開きません。
