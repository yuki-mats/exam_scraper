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

## Follow-up Tranche: flash_card共通解説と計算問題分類

2026-07-20の利用者合意に基づき、次の契約を問題整備とrepasoで一貫させる。

- `isChoiceOnly`はFirestore documentの役割だけを表し、計算問題判定へ流用しない。
- 問題整備patchへ`isCalculationQuestion: boolean`を追加し、計算問題か否かを明示する。
- すべての`flash_card`は問題単位の基本解説を1本だけ持つ。`isChoiceOnly=true` documentには基本解説、補足質問、補足回答を公開しない。
- 計算`flash_card`の基本解説は、使用する式、代入、単位換算、中間計算、最終値、正しい選択肢との対応まで本文内で完結させ、選択肢別の基本解説を作らない。
- 非計算`flash_card`も基本解説は1本とする。補足質問の詳細な作成基準は後日の別trancheで詰めるため、今回は0〜3件を扱える契約と将来の基準追加位置だけを用意する。
- `true_false`等の非`flash_card`型の既存解説契約は、この追補で不要に変更しない。
- 代表データ`gas-shunin-kou / 2019 / gas-shunin:kou:2019:kyokyu:q10`を計算`flash_card`のreadback対象とし、詳細な計算過程を持つ基本解説1本・補足質問0件へ修正する。

## Follow-up Tranche: 補足質問の学習価値と重複禁止

2026-07-20の利用者指摘に基づき、補足質問を「基本解説を別の問い方で繰り返す欄」ではなく、基本解説を理解した後に生じる追加疑問へ答える欄として統一する。

- 基本解説と同じ結論・理由・説明を、質問形式に言い換えただけの補足質問は作らない。該当する候補しかない場合は0件とする。
- 補足質問は、基本解説だけでは扱わないが、その選択肢の理解を深める追加情報がある場合だけ作る。件数確保を目的に作らない。
- 問題形式や資格ごとの個別禁止事項を増やさず、生成、評価、法令監査、問題整備UIに関係する文書を同じ簡潔な原則へ揃える。
- スクリーンショットのように、基本解説の内容をそのまま疑問文と回答へ置き換えたものは不適合例とする。
- 今回は恒久正本と関連文書の全面的な整合を対象とし、既存問題データの一括洗い替えとFirestore反映は別の明示作業とする。

## Follow-up Tranche: field単位の部分洗い替え

2026-07-20の利用者要望に基づき、問題整備システムから年度・対象範囲・更新fieldを選び、選択したfieldだけを再整備できる汎用機能を追加する。

- 補足質問だけを選んだ場合は、現在の基本解説を入力として参照するが、`explanationText`は変更せず、`suggestedQuestionDetailsByChoice`だけを置換する。
- UIで資格、年度又はlist group、問題範囲、更新fieldを選び、実行前に対象問題数と変更可能fieldを確認できるようにする。
- fieldの選択肢はworkflow設定から導出し、補足質問専用の個別経路を増やさない。
- 実行prompt、candidate検証、patch保存、変更差分、receipt、作業版は、選択したfield以外の変更をfail-closedで拒否する。
- 複数fieldの同時選択を許容する場合も、field間依存を明示し、参照fieldを暗黙に上書きしない。
- 既存の工程単位・一問単位・年度単位の通常整備は維持する。

## Follow-up Tranche: listGroupId一覧からの統一導線

2026-07-20の利用者合意に基づき、トップの`listGroupId`一覧を人間による問題整備と洗い替えの統一入口にする。

- 各`listGroupId`行は整備済みでも開けるようにし、同じ詳細dialogで更新項目、問題番号範囲、対象条件を指定する。
- タップした`listGroupId`を初期選択し、同じdialogで他の`listGroupId`も追加できるようにする。`examYear`は使わない。
- 利用者は工程を直接選ばず、workflow設定の`update_targets`を選ぶ。UIは選択targetから必要工程を導出し、workflow順で実行する。
- 更新項目は初期未選択とし、現在整備が必要な項目をまとめて選ぶ補助操作だけを用意する。
- 資格全体の大きな実行ボタンと管理機能内の人間工程開始を主導線にせず、一覧へ戻す。トップの独立した「生成内容を監査」導線は置かず、履歴、技術ログ、機械工程の管理機能は維持する。
- 実装後は問題整備システムを再起動し、`gas-shunin-kou`の2017年を補足質問と回答だけでUI実行し、進捗、出力、技術ログ、変更fieldを確認する。本番Firestoreへは反映しない。

## Non-Negotiable Constraints

- 基本解説だけで正誤理由と学習上の核心が完結し、補足回答へ核心を退避しない。
- AIは「基本解説を読んだユーザーが次に抱きそうな疑問」を選択肢ごとに0〜3件作る。件数を満たすための水増しをしない。
- 更新用patchでは質問と回答を一体の正本として保存し、質問文だけの独立編集による二重管理を避ける。
- Firestore互換上`suggestedQuestions`が必要なら、保存済みquestion/answerから機械的に導出し、独立した正本にしない。
- `isChoiceOnly=true`のdocumentには補足質問・回答を公開しない。
- `isChoiceOnly`を問題内容の分類に使わず、計算問題は問題整備側の`isCalculationQuestion`で判定する。
- すべての`flash_card`は基本解説1本とし、選択肢別の基本解説を作らない。
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
