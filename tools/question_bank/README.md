# question_bank tools

過去問整備で迷ったら、まずこのディレクトリを見ます。

## 読む順番

1. ルール: `document/reference/question_field_contract.md`
2. 日々の目視作業: `prompt/README.md` と `prompt/01_prompt_*.md` から `prompt/04_prompt_*.md`
   - 法改正・現行法差分が疑われる場合、または年1回の法令関係問題の全問監査では `prompt/03b_prompt_audit_current_law_and_patch.md` で03bの監査パッチ/sidecarを作成・更新し、既存成果物へマージする
3. 機械チェック: このディレクトリの `question_bank.py`
4. 補助実装: 必要な場合だけ `scripts/` 配下を見る

## 標準コマンド

日々の整備後は、個別 script を探す前にこのコマンドを実行します。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

既存互換のため、次の旧コマンドも同じ処理へ委譲します。

```bash
python scripts/check/run_question_quality_gate.py \
  --qualification <qualification> \
  --list-group-id <list_group_id>
```

## 何を確認するか

- `00_source`、merged、`40_convert` の必須フィールド
- `questionType`、`questionIntent`、`explanationText`、`questionSetId` patch の全問 coverage
- `suggestedQuestions` / `suggestedQuestionDetails` の整合
- `isLawRelated` の有無と `lawGroundedExplanationNotNeeded` との逆関係
- `lawReferences` の基本構造
- Firestore upload dry-run による schema validation

部分実行したい場合:

```bash
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode required
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode patches
python tools/question_bank/question_bank.py quality-gate --qualification <qualification> --list-group-id <list_group_id> --mode firestore
```

03工程後は、全解説 patch に厳密な `isLawRelated` と、条文解説ボタン制御用の `lawGroundedExplanationNotNeeded` を必ず残すため、次を追加します。

```bash
python tools/question_bank/question_bank.py quality-gate \
  --qualification <qualification> \
  --list-group-id <list_group_id> \
  --require-is-law-related \
  --require-law-grounded-flag
```

root 直下に出てしまった資格別レポートは、資格フォルダ配下へ寄せます。

```bash
python tools/question_bank/question_bank.py organize-reports --qualification gas-shunin
```

## 作業中の単体確認

全体ゲート前に、raw JSON の正式 patch 化や作成中の patch だけ確認したい場合も、このCLIから実行します。

```bash
python tools/question_bank/question_bank.py materialize-patch \
  --task question_type \
  --source /path/to/question_*.json \
  --raw /path/to/raw.json \
  --output /path/to/10_questionType_fixed/question_*_questionType_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-question-type-patch \
  --source /path/to/question_*.json \
  --patch /path/to/10_questionType_fixed/question_*_questionType_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-question-intent-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/15_correctChoiceText_fixed/question_*_correctChoiceText_fixed.json
```

```bash
python tools/question_bank/question_bank.py check-explanation-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/21_explanationText_added/question_*_explanationText_added.json \
  --require-is-law-related \
  --require-law-grounded-flag
```

```bash
python tools/question_bank/question_bank.py check-question-set-patch \
  --source /path/to/question_*_merged.json \
  --patch /path/to/22_questionSetId_linked/question_*_questionSetId_linked.json \
  --category /path/to/category.json \
  --questionset-only
```

## フォルダ責務

| 場所 | 役割 |
| --- | --- |
| `document/reference/question_field_contract.md` | 共通フィールドの人間向け正本。 |
| `prompt/` | 01から04の目視 patch 作成プロンプト。品質判断の主役。 |
| `tools/question_bank/` | 日常運用で直接叩く統一CLI。 |
| `scripts/` | CLIから呼ばれる実装、互換入口、個別補助。通常は直接探さない。 |
| `output/` | 資格ごとの生成物・作業中データ。Git管理の正本にしない。root直下に単発レポートを増やさず、`output/<qualification>/reports/` へ置く。 |

## Codex が改修するときのルール

- 日常運用の入口を増やす場合は、まず `question_bank.py` のサブコマンドとして追加する。
- 個別 checker / fixer を追加する場合も、ユーザー向けREADMEでは `tools/question_bank` からたどれるようにする。
- 新しい監査・修復レポートの既定出力先は `output/<qualification>/reports/` にする。既存のroot直下レポートは `organize-reports` で移す。
- `prompt/`、field contract、merge/convert/upload の仕様を変えたら、このREADMEと `quality-gate` の対象も同じ commit で見直す。
- `scripts/` に新しい単発スクリプトを置く場合は、日常運用の正本にするのか、内部補助に留めるのかを `scripts/README.md` に明記する。
