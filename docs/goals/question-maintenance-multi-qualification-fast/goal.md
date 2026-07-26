# 問題整備の複数資格・合計32turn・Fast対応

## Objective

問題整備システムを、3資格以上へ拡張可能な資格分離構造へ変更する。全資格合計のmodel turn上限は32とし、資格ごとのqueue・進捗・writerを分離する。各runはStandard又は公式Fastを選択でき、モデル、high推論、検査、品質ゲート、`00_source`と既存IDの不変条件を維持する。初回canaryは`gas-shunin-otsu`とCLF-C02の2資格で行う。

## Original Request

「現在runを中断して、合計32turnで実装して」

## Intake Summary

- Input shape: `existing_plan`
- Audience: 問題整備の運用者と各資格の受験者
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 1台のreview-uiでガス主任技術者・乙種とCLF-C02の2runが同時に進み、合計in-flight turnが32以下、各runの選択speed modeと実適用service tierが一致し、資格間のfile/state混線、`00_source`差分、既存ID drift、品質ゲート省略がないこと。
- Goal oracle: 2資格の同時active run、両方のheartbeatとvalidated work増加、global turn telemetry、Fast service tier readback、資格別writer/readback、全対象testと`00_source`不変。
- Likely misfire: 各資格32turnとして合計上限を超える、Fastを黙ってStandardへfallbackする、global lockを外しただけでstate又はwriterが混線する、並列化のために品質工程を省略する。
- Blind spots considered: 現在のChatGPT accountはcredits無効、provider rate limit、16GB Macのlocal resource、既存runの再開、既存active Goalとのwrite-scope競合、merge・upload・Gitの全体直列化。
- Existing plan facts: 同時資格数は2固定にせず3以上へ拡張可能にする。初回は`gas-shunin-otsu`とCLF-C02。全資格合計32turn。2資格なら公平配分の目標は16+16。障害時は並列数だけを自動縮小し、モデル、high推論、検査、品質ゲートは維持する。

## Goal Oracle

The oracle for this goal is:

`review-uiのlive APIでgas-shunin-otsuとCLF-C02が同時activeとなり、両runのheartbeatとvalidated workが増加し、global in-flight turns <= 32、資格別割当とFast適用結果がreadbackでき、対象test・00_source不変・資格間changed-file監査が全てpassする。`

## Goal Kind

`existing_plan`

## Current Tranche

現在の単一資格・単一turn・Standard限定実装を、資格分離された複数run、全体公平上限32turn、run単位のStandard/Fastへ置き換える。既存ガスrunの検証済み作業を保持して再開し、CLF-C02との2資格canaryを開始又は、creditsだけが不足する場合はFast開始直前までの全ローカル検証と明示的なblocked receiptを完成させる。

## Non-Negotiable Constraints

- `00_source`の内容・ファイル名と既存question IDを変更しない。
- モデルと推論強度を速度のために下げない。
- 検査、品質ゲート、receipt、live readbackを省略しない。
- 全資格合計の同時model turnは32を超えない。
- 資格ごとのpatch writerは1本とし、同じ資格への複数writerを許可しない。
- merge、upload、Git操作は全体で1本ずつ実行する。
- Fastを要求したrunでFastを利用できない場合、Standardへ黙ってfallbackしない。
- review-uiは1serverだけを使用する。
- `main`だけを使い、`origin/main`へscoped commitをpushする。

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

## Slice Sizing

最初に現行lock・state・App Server・scheduler・UI/API・CLF-C02 inventoryを一つのread-only mapへまとめる。次に、資格分離、global turn budget、Fast contractを一つのvertical Worker packageとして実装する。最後に再起動と2資格canaryを行い、credentials又はcreditsだけが不足する場合も、それ以外の安全な検証をすべて完了してから当該live taskだけをblockedにする。

## Canonical Board

Machine truth lives at:

`docs/goals/question-maintenance-multi-qualification-fast/state.yaml`

## Run Command

```text
/goal Follow docs/goals/question-maintenance-multi-qualification-fast/goal.md.
```
