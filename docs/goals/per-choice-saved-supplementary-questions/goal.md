# 選択肢別・保存済み補足質問

## Objective

`exam_scraper`で、基本解説を読んだユーザーが次に抱きそうな疑問をAIが予測し、`isChoiceOnly=false`となる選択肢ごとに最大3件の質問と回答を更新用patchへ事前保存する。`repaso`では、その保存済み回答をチップ押下時に表示し、チップ経由では生成AI APIを呼ばない。

## Original Request

「AIが、基本解説を読んだユーザーが次に抱きそうな疑問を予測し、選択肢ごとに最大3件、回答付きで更新用のパッチを事前保存する、という仕組みをexam_scraperとrepasoディレクトリ配下で整備したい。」

## Intake Summary

- Input shape: `specific`
- Audience: 暗記プラスの受験者と問題整備の運用者
- Authority: `requested`
- Proof type: `test`
- Completion proof: 選択肢を分割する代表問題で、patchの補足質問が選択肢別・最大3件・回答付きになり、Firestore変換後は`isChoiceOnly=false`の該当documentだけがその選択肢用データを持つ。repasoのチップ押下テストでは生成AI API呼出しが0件で、自由入力では従来どおり呼び出される。
- Goal oracle: 両repoの契約テストと代表的な変換fixtureによるcross-repo readback
- Likely misfire: 既存の平坦な質問配列を先頭3件へ切り詰めるだけで、他選択肢の質問複製、`isChoiceOnly=true`への保存、回答欠落時のAPI fallbackを残すこと
- Blind spots considered: 既存patchとの互換、質問文の二重正本、true_falseとflash_card/group_choiceの公開単位差、法令問題の追加質問、dirty worktreeの保護、両repo別commit/push
- Existing plan facts: 基本解説で完結し、その内容を踏まえた追加疑問だけを用意する。質問と回答は事前保存する。チップは保存回答だけを表示し、生成AI APIは自由入力だけに使う。

## Goal Oracle

The oracle for this goal is:

`代表的な5選択肢問題を03 patchからFirestore相当データまで変換すると、各isChoiceOnly=false documentにその選択肢専用の0〜3件のquestion/answerだけが保存され、isChoiceOnly=true documentには保存されない。repasoで全チップをタップしてもVertex AI requestは0件で、自由入力だけは1件送信される。`

PMは各receiptをこのoracleへ対応付ける。promptだけの修正、UIだけの制限、先頭3件への切詰め、又は片方のrepoだけのテスト成功では完了としない。最終監査で`full_outcome_complete: true`を記録する。

## Goal Kind

`specific`

## Current Tranche

両repoの現行契約を一つに揃え、patch authoringの選択肢別正本、機械検査、Firestore projection、repasoのtyped read/UI動作、回帰テスト、必要最小限の正本文書を一つの縦断機能として完成させる。既存データの一括再生成や本番Firestore更新は別の明示作業とし、このtrancheでは安全に再生成できる仕組みと移行判定を完成させる。

## Non-Negotiable Constraints

- 基本解説だけで正誤理由と学習上の核心が完結し、補足回答へ核心を退避しない。
- AIは「基本解説を読んだユーザーが次に抱きそうな疑問」を選択肢ごとに0〜3件作る。件数を満たすための水増しをしない。
- 更新用patchでは質問と回答を一体の正本として保存し、質問文だけの独立編集による二重管理を避ける。
- Firestore互換上`suggestedQuestions`が必要なら、保存済みquestion/answerから機械的に導出し、独立した正本にしない。
- `isChoiceOnly=true`のdocumentには補足質問・回答を公開しない。
- repasoは保存済み回答が有効なチップだけを表示し、チップ押下では生成AI APIを呼ばない。自由入力の生成AI導線は維持する。
- `true_false`、`flash_card`、`group_choice`の公開document差を同じ変換契約で明示的に扱う。
- `00_source`、既存questionId、本番Firestoreを変更しない。
- exam_scraperの既存2020〜2025年等の未コミットpatchと、repasoの既存未コミット差分を破棄・上書き・混在commitしない。
- 両repoの既定branch運用に従い、exam_scraperは`main`、repasoは`master`へ、今回変更だけを別々にcommit/pushする。
- 重複文書を増やさず、exam_scraperとrepasoそれぞれの既存正本へ契約を統合する。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

片方のrepoだけの変更、旧flat schemaの黙示的受入れ、回答欠落時のAPI fallback、又は最大3件を保証しない経路が残る限り完了しない。既存dirty差分との重なりで安全なcommitができない場合は、そのfileだけを止め、重ならない実装・テスト・文書作業を継続する。

## Slice Sizing

最初のScout/Judgeは、patch schema、公開document判定、repaso表示までの境界を一度だけ確定する。Workerは小さなhelperだけで止まらず、生成契約・検査・変換・typed read・チップ動作・cross-repo testを一つの最大安全な縦断sliceとして完成させる。

## Canonical Board

Machine truth lives at:

`docs/goals/per-choice-saved-supplementary-questions/state.yaml`

## Run Command

```text
/goal Follow docs/goals/per-choice-saved-supplementary-questions/goal.md.
```

## PM Loop

各継続時にこのcharter、GoalBuddy実行契約、`state.yaml`を読み、active taskだけを進める。各完了又は停止はreceiptへ残し、oracleが満たされるまで次の安全なtaskへ進む。
