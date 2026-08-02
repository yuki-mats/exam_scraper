# AWS CLF解説・学び方分類の再整備

## Objective

承認済みのAWS向け8分類例を03解説工程が参照する正本へ簡潔に反映し、その基準でAWS Certified Cloud Practitionerの全対象問題の解説と`questionLearningPatternId`をUIから再整備して、Firestore反映前の成果物を品質検証済みにする。

## Original Request

「これも解説工程を得るときに参照するようにして、AWSの解説を整備してほしい。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: AWS資格を初めて学ぶ受験者
- Authority: `requested`
- Proof type: `artifact`
- Completion proof: 承認済み8分類例を03工程が実際に参照し、AWS CLF全対象の現行03成果物が分類・基本解説・補足・根拠の機械検証と代表問題の文章レビューを通過したことを、run終端状態と成果物で確認できる。
- Goal oracle: UIから開始したAWS CLFの03再整備runが`status=succeeded`、`verified=true`、`artifactSync=succeeded`となり、全対象問題に現行版の03成果物、妥当な単一`questionLearningPatternId`、正誤から始まる基本解説が存在し、品質検証が合格する。
- Likely misfire: 03プロンプトへ長いAWS例を直書きして共通promptを再び読みにくくする、分類名だけ付けて解説構成へ反映しない、Snowballの正答不整合を03解説で辻褄合わせする、又はUIを使わず成果物だけ直接量産して完了扱いする。
- Blind spots considered: AWS仕様の時点変化、既存runとrepository lock、KeepItUpとPing-tの両list group、`group_choice`と肢別分類の境界、計算式のFlutter数式契約、補足0件を標準とする方針、公式一次資料、Firestore書き込み境界。
- Existing plan facts: 一肢につき学び方分類は一つ、基本解説は正誤を先頭に置いて単独で完結、誤答は設問用語を自然に使い正しい全体像を直接示す、補足は0件を標準、計算は公式・全変数・代入・途中計算・最終値を示す、承認済みAWS8分類例を使う、更新は問題整備UIから行い進捗確認はログとJSONを直接参照してよい。

## Goal Oracle

The oracle for this goal is:

`AWS CLFの03再整備run終端readbackと、全対象03成果物の機械検証・代表8分類レビューが同じ内容を支持し、Firestore書き込みが0件であること。`

The PM must keep comparing task receipts to this oracle. Planning, discovery, a passing tiny slice, or a clean-looking board is not enough. The goal finishes only when a final Judge/PM audit maps receipts and verification back to this oracle and records `full_outcome_complete: true`.

## Goal Kind

`existing_plan`

## Current Tranche

承認済み例の参照設計を現行実装と正本文書から確定し、必要最小限のprompt・資格別資料・検証を更新する。その変更をGitの`main`へコミット・pushした後、既存の問題整備UIからAWS CLFの03工程を全対象へ実行し、保留・失敗を同じ通常フローで再整備して、Firestore反映直前ではなく03成果物の品質収束点で停止する。

## Non-Negotiable Constraints

- `00_source`を変更・削除・改名しない。
- 共通03プロンプトを長いAWS固有例で肥大化させず、資格別資料を必要時に参照する単純な責務分離を優先する。
- 一肢に一つの`questionLearningPatternId`を独立に確定し、`questionSetId`から逆算しない。
- 基本解説は`正しい。`又は`間違い。`で始まり、基本解説だけで中心論点を理解できるようにする。
- 誤答解説は、正しい全体像、設問の用語、誤りとの差が自然に分かる文章にする。
- 補足解説は0件を標準とし、基本解説にない追加価値がある場合だけ作る。
- 計算問題は公式、全変数の意味、数値対応、単位、代入、途中計算、最終値をFlutter数式契約で示す。
- AWS仕様は公式一次資料で現行性を確認し、時点差で正答が崩れる問題は03で補正せず、当該問題だけ責務を持つ前工程へ戻す。
- 更新runは既存の問題整備システムUIから開始する。進捗・成果物の確認にはmanifest、progress、ログ、JSONを直接参照してよい。
- KeepItUpとPing-tの両list groupを対象とし、片方だけで全AWS完了としない。
- Firestoreへのアップロード・書き込みは行わない。
- サブエージェントを増やさず、PMがScout/Judge/Worker相当の作業を一つずつ直列に実施する。
- Gitは`main`のみを使い、関連変更だけを検証・コミットして`origin/main`へpushする。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if the user asked for working software or automation and a safe Worker task can be activated.

Do not stop after a single verified Worker package when the broader owner outcome still has safe local follow-up work. Advance the board to the next highest-leverage safe Worker package and continue unless a phase, risk, rejected-verification, ambiguity, or final-completion review is due.

Do not create one Worker/Judge pair per repeated file, table, route, or helper. Put repeated same-shape work into one Worker package and review the package as a whole.

## Slice Sizing

Safe means bounded, explicit, verified, and reversible. It does not mean tiny.

A good task is the largest safe useful slice.

Small is not the goal. Useful is the goal.

The prompt/reference integration is one coherent Worker package. The AWS CLF UI run, convergence, and artifact validation are a second coherent package. Do not split repeated questions into one task per file.

## Canonical Board

Machine truth lives at:

`docs/goals/aws-clf-explanation-learning-pattern-refresh/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins for task status, active task, receipts, verification freshness, and completion truth.

## Run Command

```text
/goal Follow docs/goals/aws-clf-explanation-learning-pattern-refresh/goal.md.
```

## PM Loop

On every `/goal` continuation:

1. Read this charter and the GoalBuddy execution contract.
2. Read `state.yaml` and work only on its active task.
3. Use PM fallback for Scout/Judge/Worker-shaped tasks; do not spawn sub-agents.
4. Record a compact receipt and update the board after each task.
5. Compare each package with the goal oracle and continue while safe work remains.
6. Run the stop checker before claiming completion.
