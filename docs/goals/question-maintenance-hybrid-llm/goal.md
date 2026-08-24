# 問題整備システムのハイブリッドLLM化

## Objective

問題整備システムの一問単位の安全境界を維持したまま、整備と評価で使うLLM実行先を設定で切り替えられるようにする。全工程をCodex App Serverで実行する構成を標準互換設定として残し、ローカルLLMで一問ずつ整備してCodex App Serverで複数問を独立監査する構成を追加する。実装後は両構成を同じ代表問題集合で実動検証し、ハイブリッド構成が公開可能な品質へ収束することをreceiptで証明する。

## Original Request

「ハイブリッド方式の中で、Codex App Serverのみを利用する組み合わせも選べるようにしたい。複雑化を避けた計画書を作り、その計画を元に実装し、最後にハイブリッド方式で十分な問題整備ができるかテストしてほしい。100並列は使わず1並列とし、ローカル・従来方式・別のローカル又はクラウドLLMを切り替えられるようにしたい。クラウド評価は複数問をまとめて監査したい。」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備システムの運用者と、整備結果を利用する受験者
- Authority: `approved`
- Proof type: `test`, `artifact`, `metric`, `demo`
- Completion proof: Codex App Serverのみの構成と、ローカル整備・Codex App Server監査の構成が、同じ一問pipeline・同じ保存契約で実行できる。代表問題の実動テストで安全条件、品質基準、再開、設定切替、複数問監査をすべて満たし、Firestoreへ書き込まずに結果receiptを残す。
- Goal oracle: 固定した代表問題集合に対する比較runで、重大な正答・法令・根拠・日本語品質の誤りが0件、誤公開可能状態が0件、`00_source`変更が0件となり、ハイブリッド構成が規定回数内に全問を`passed`又は根拠付き`hold`へ収束させること。
- Likely misfire: providerごとにworkflowを複製する、従来方式を別pipelineとして残す、複数問監査を一括合否にする、評価へ現在の正答ラベルを渡す、ローカルLLMの低品質を機械検査の緩和で隠す、又はモックだけで完了とすること。
- Blind spots considered: 未コミット変更との競合、旧runのreadbackとresume互換、認証・課金境界、API key秘匿、ローカルendpoint停止、model差替え時の再現性、batchの欠落・重複・部分失敗、大きいprompt、法令・画像・計算問題、評価者と生成者の独立性、実機速度、fallbackが常用されてローカル化の効果が失われること。
- Existing plan facts:
  - LLM呼出しは全体で同時実行数1とする。
  - ハイブリッド方式は一つのpipelineであり、役割ごとの実行先を設定する。
  - `codex_only`は整備・再試行・再整備・評価をCodex App Serverで実行する。
  - `local_generate_codex_audit`は整備をローカルLLM、独立評価をCodex App Serverで実行する。
  - ローカル又はクラウドのOpenAI互換endpointとmodel名を設定で交換できるようにする。
  - クラウド監査は複数問を一回のmodel turnへまとめても、一問ごとの入力、判定、`stateHash`、保存、再試行、公開可否を分離する。
  - OpenAI API Batchは初期実装へ含めない。まず同期的な一回の監査turnへ複数問を入れるmicro-batchで重複を減らす。
  - 実装前の現状調査では、現行コードがCodex App ServerとOpenAI providerへ強く結合し、現在の複数選択評価もmodel呼出し自体は一問ずつであることが確認されている。実装開始時に現行差分を再確認する。

## Goal Oracle

The oracle for this goal is:

`固定代表問題集合で codex_only と local_generate_codex_audit を実動させ、同じ決定的server検査と一問receiptを通して、重大品質事故0、誤公開可能状態0、00_source変更0、Firestore write 0、欠落又は重複した監査結果0、resume後の二重確定0を確認し、ハイブリッド構成が規定した再試行・fallback内で全問をpassed又は根拠付きholdへ収束させる。`

計画書、型だけのadapter、モックテストだけ、単一の簡単な問題だけ、又はクラウド監査の`passed`表示だけでは完了としない。最終Judge又はPMは、実runのmanifest、問題別receipt、比較表、品質監査、Git差分、`00_source`不変検査をoracleへ対応付け、`full_outcome_complete: true`を記録する。

## Goal Kind

`existing_plan`

## Current Tranche

現在の一問pipelineを複製せず、LLM呼出し部分だけを小さな共通契約へ切り出す。最初に現行の未コミット変更と実行経路を再監査し、設定契約を確定する。次にCodex App Serverをその契約へ載せ替えて互換性を証明し、その後にOpenAI互換HTTP backend、run profile、複数問監査を順番に追加する。最後に実ローカルmodelを一つ選んで代表問題集合を走らせ、Codex App Serverのみの構成と比較する。

### 採用する最小構成

LLMの役割は二つだけとする。

1. `maintenance`: 候補生成、工程内再試行、評価指摘後の再整備を担当する。
2. `audit`: 整備とは独立したread-only評価を担当する。

backendの種類も初期実装では二つだけとする。

1. `codex_app_server`: 現行のChatGPT認証・Standard境界・structured outputを使う。
2. `openai_compatible_http`: loopback上のローカルserverを主対象とし、同じ契約を提供するクラウドendpointも設定できる。認証情報は環境変数からだけ読む。

provider固有のworkflow、model固有の条件分岐、`local`と`cloud`を表す別backend型は作らない。新しい非互換providerが必要になった時だけ、共通契約へadapterを一つ追加する。

### 設定の形

`config/question_maintenance_workflow.toml`は工程順・promptの正本として維持し、LLM実行設定は責務を分けた新しい設定ファイル一つへ置く。設定は名前付きprofileを持ち、UIはrun開始時にprofileだけを選ぶ。run開始後は解決済み設定とfingerprintをmanifestへ固定し、途中でprovider又はmodelを変更しない。

必須profileは次の二つとする。

| profile | maintenance | audit | 目的 |
| --- | --- | --- | --- |
| `codex_only` | Codex App Server | Codex App Server | 現行方式の互換構成。新旧を別pipelineにしない。 |
| `local_generate_codex_audit` | OpenAI互換ローカルendpoint | Codex App Server | ローカルで一問ずつ整備し、クラウドの高品質modelで独立監査する。 |

各役割は`backend`、`model`、`reasoning`、`timeout`を持つ。`maintenance`だけは同じbackend上の`retry_model`と、規定回数で収束しない場合の任意`fallback`を持てる。モデル名、endpoint、量子化、weights fingerprintなど再現性情報はreceiptへ残すが、secretは残さない。

### 実行と監査の境界

- modelは現在と同じ意味判断JSONだけを返す。ID、対象path、`stateHash`、patch適用、atomic保存、version、receipt、公開可否はserverが所有する。
- 全backendを合わせた実model呼出しの同時実行数は1とする。一問の中でも工程は直列に進める。
- `audit`は同じ資格、同じ評価policy fingerprint、同じ法令・画像モードの問題だけをまとめる。
- 初期値は最大5問かつ入力120,000 bytes以下とし、先に達した方で区切る。上限は設定可能にする。一問だけで上限を超える場合はその問を単独監査する。
- batch出力は問題ごとの配列とし、serverは要求した`questionId`と`stateHash`の完全一致、欠落0、重複0、全選択肢評価、根拠、statusとscoreの整合を検査する。
- batchの一部だけが正しい場合は、正しい問だけを問題別receiptへ確定し、欠落・不正な問だけを単独又は小さいbatchで再試行する。batch全体の一括合否は保存しない。
- 評価promptへ現在の`correctChoiceText`の正誤対応を渡さない。各選択肢を問題文と結合した完全な命題として独立判定し、serverが現在値と比較する。
- OpenAI APIの非同期Batch APIは、同期micro-batchの効果と必要性を計測した後の別目標とする。

### 旧runと失敗時の扱い

- 既存manifestとreceiptは書き換えない。新しいprovider metadataがない旧runもreadbackできる。
- 旧runを再開する時は、完了済み問題を保持し、新しいattemptに`codex_only`互換設定とfingerprintを記録してから未完了だけを続行する。暗黙に別modelへ切り替えない。
- ローカルendpoint停止、timeout、schema不一致、context超過は問題又はbatch単位の明示失敗とする。検査を緩めず、設定された再試行又はfallbackだけを使う。
- fallbackを使った問は、ローカル生成成功として数えない。品質だけでなく、ローカル候補がそのまま採用された率、再試行率、fallback率、所要時間も比較する。

## Implementation Plan

### Phase 1: 現状固定と設定契約

1. 実装開始時点のdirty diff、現行run、lock、正本文書、候補生成、評価、resume、receipt経路を再確認する。
2. 既存の全体最適化goalに残る100並列・model batch不採用の前提と今回の合意の関係を整理し、今回のgoalを新しい実装判断として扱う。
3. profile schema、backend request/result、secret境界、fingerprint、旧run互換、停止条件をJudgeが確定する。

### Phase 2: Codex App Serverの共通化

1. providerに依存しない最小request/result契約を追加する。
2. 現行Codex App Server呼出しをadapter化し、候補生成と一問評価の出力、model照合、Standard・追加credits無効・tool制限を維持する。
3. `codex_only`を既定profileとして接続し、同時実行数1で候補生成、再試行、評価、再整備、再評価、resumeが現行契約どおり動くことを先に証明する。

### Phase 3: 交換可能なHTTP backendとprofile選択

1. OpenAI互換HTTP backendを追加し、endpoint、model、timeout、structured JSONの送受信を共通化する。
2. loopbackは無認証又は環境変数認証、外部endpointは環境変数認証を必須とし、URL中credential、設定へのsecret直書き、receiptへのsecret保存を拒否する。
3. UIで名前付きprofileを選び、利用する整備model・監査model、1並列、監査batch上限、fallback有無を確認してからrunを開始する。
4. planとmanifestへ解決済みprofileとfingerprintを固定し、resume時の差異を検出する。

### Phase 4: 複数問の独立クラウド監査

1. 現行の一問評価promptとschemaを、問題ごとの意味契約を変えずに複数問入出力へ拡張する。
2. server側でgrouping、byte上限、ID・hash照合、部分成功、欠落・重複検出、対象問だけの再試行を実装する。
3. 保存と公開gateは一問単位のまま維持し、batch request IDを各問題receiptへ関連付ける。
4. 一問評価と複数問評価を同じfixtureで比較し、判定差分、入力削減、所要時間、失敗率を記録する。

### Phase 5: 実modelでの品質・運用受入テスト

1. Macのメモリに収まり、OpenAI互換endpointと構造化JSONを提供できる日本語対応modelを一つ基準modelとして選ぶ。model名をコードへ固定せずprofileに設定する。
2. 固定代表問題集合を作る。少なくとも通常問題、`true_false`、`flash_card`、`group_choice`、計算問題、法令関連、画像あり、独自問題化対象を含め、正答と必須根拠を事前に確定する。
3. 小規模smokeの後、同じ入力で`codex_only`と`local_generate_codex_audit`を実行する。順序による評価偏りを避け、最終比較では出力元を伏せた独立クラウド監査を行う。
4. endpoint停止、model名誤り、timeout、壊れたJSON、監査結果の欠落・重複、途中停止・resume、profile変更resume拒否を故障注入で確認する。
5. Firestoreへは書き込まず、upload dry-runまでとする。全結果、使用model、fallback、時間、batch、receipt、Git状態をreadbackする。

## Acceptance Criteria

### 機能

- UIで`codex_only`と`local_generate_codex_audit`を選択でき、選択内容を開始前に確認できる。
- backend又はmodel名の変更は設定だけで行え、prompt、workflow、保存処理のコード変更を必要としない。
- 全model呼出しの実同時実行数が1を超えない。
- Codex App Serverだけの構成は、別実装へ分岐せず同じprofile機構で動く。
- 監査は複数問を一回で処理できるが、結果、retry、保存、公開可否は一問単位である。

### 安全性

- `00_source`、既存ID、対象外patch、既存run artifactを変更しない。
- `stateHash`不一致、問題ID欠落・重複、選択肢評価不足、model不一致、profile fingerprint不一致をfail-closedで停止する。
- secretがGit差分、manifest、receipt、画面、技術ログへ出ない。
- テスト中のFirestore writeは0件で、公開は既存の明示確認とreadback境界を維持する。

### 品質

- 固定代表問題集合で重大な正答誤り、法令誤り、根拠の捏造、問題と解説の矛盾、公開可能な誤判定が0件である。
- ハイブリッド構成は、設定した再試行とfallbackの範囲内で全問を`passed`又は理由と不足根拠が明示された`hold`へ収束させる。`needs_rework`、処理失敗、評価待ちを完了扱いにしない。
- 少なくともローカル候補の70%がcloudによる全文再生成fallbackなしで最終合格する。70%未満なら安全でもローカル化の実益不足として完了せず、model、prompt入力、工程分担の見直しtaskを追加する。
- 複数問監査と一問監査の最終判定差分は0件とする。差が出た場合はbatch sizeを縮小し、原因を解消するまで既定化しない。

### 回帰と実証

- provider、profile、candidate、evaluation、qualification run、resume、UI、receipt、00_source不変の対象テストが通る。
- 問題整備システムの関連回帰テストと`git diff --check`が通る。
- 実Codex App Server、実ローカルendpoint、実modelを使うrunのmanifestと問題別receiptが残る。モックだけでは完了しない。
- 実測値として、問題数、初回合格率、再試行率、fallback率、監査batch数、平均batch問数、prompt bytes、所要時間、失敗理由を比較表へ残す。

## Non-Negotiable Constraints

- `00_source`、既存question ID、既存patch、既存run成果物を移行書換えしない。
- 一問の工程順、決定的なserver検査、atomic patch確定、問題別`stateHash`、receipt、独立評価、明示的Firestore公開を維持する。
- 全model呼出しの同時実行数を1に固定する。micro-batchは一つのmodel呼出しへ複数問を入れる機能であり、並列実行ではない。
- 従来方式を別pipelineとして温存せず、`codex_only` profileとして同じ実装へ統合する。
- 初期実装の役割は`maintenance`と`audit`、backendは`codex_app_server`と`openai_compatible_http`に限定する。
- model出力検査をproviderごとに緩めない。localの構造化出力が弱い場合も、再試行又はfallbackで処理する。
- 評価modelへ現在の正答判定を渡さず、生成sessionと評価sessionを分ける。
- 新しい非同期queue、外部database、OpenAI API Batch、複数vendor固有SDK、provider別UI、model自動ダウンロードは初期実装へ含めない。
- 本goalの実装・テストだけを理由に、本番Firestoreへ問題データを書き込まない。
- 作業開始時に現在の大きな未コミット変更の所有範囲を確認し、他者の変更を破棄又は一括commitしない。重なる変更を安全に分離できなければユーザーへ止めて報告する。
- 仕様変更は[問題整備システム](../../../document/operations/local_question_review_console.md)など既存の責務に合う正本へ反映し、このgoalを恒久仕様の正本にしない。
- 各実装sliceは対象テスト、関連回帰、正本文書、receipt、scoped commit、`origin/main`へのpushまで閉じる。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

計画作成、adapter追加、`codex_only`の成功、モックlocal endpointの成功、又は少数問smokeだけでは停止しない。安全な実装とテストが残る限り、実ローカルmodelを使う比較runと最終品質監査まで続行する。

実cloud HTTP providerのcredentialがない場合でも、`local_generate_codex_audit`と`codex_only`の実動、HTTP backendのfixture検証、外部provider差替え契約の検証は続ける。任意の第三者cloud providerへの実課金テストだけをblocking条件にしない。

## Slice Sizing

一つのWorker packageは、設定契約、Codex互換化、HTTP backendとprofile、複数問監査、実model受入のいずれかを縦断して、コード・テスト・正本文書・receiptまで完了させる。小さなhelperごとにtaskを分割せず、provider全体を一つの巨大diffにもまとめない。

## Board Health

```bash
node /Users/yuki/.codex/plugins/cache/goalbuddy/goalbuddy/0.4.3/skills/goal-prep/scripts/check-goal-state.mjs docs/goals/question-maintenance-hybrid-llm
```

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-hybrid-llm/state.yaml`

## Run Command

```text
Codex: /goal Follow docs/goals/question-maintenance-hybrid-llm/goal.md.
Claude Code: /goalbuddy Follow docs/goals/question-maintenance-hybrid-llm/goal.md.
```

## PM Loop

各継続時にこのcharter、GoalBuddy実行契約、`state.yaml`を読み、active taskだけを進める。実装開始前に現行差分と実行中runを再確認する。各Workerの変更はそのsliceだけを検証・commit・pushし、受入テストでは実provider/modelのmanifest、問題別receipt、比較表を保存する。最終Judgeは全receiptをGoal Oracleへ対応付け、未達なら次の最大安全sliceを作る。
