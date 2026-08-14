# AWS 2資格の学習公開

## Objective

CLF-C02とSAA-C03を本番の資格一覧から検索でき、実際に問題を開始・回答できる状態へそろえる。CLF-C02は既存公開を保ち、SAA-C03は整備・独立評価に合格した問題だけを公開する。

## Original Request

AWSの資格2種ともにアップして、学習できるようにしてほしい。SAA-C03はアップしている気がする。

## Intake Summary

- Input shape: `recovery`
- Audience: AWS資格を学習する受験者
- Authority: `approved`
- Proof type: `demo`
- Completion proof: 本番Firestoreの件数readback、資格一覧とカタカナ検索、Tailscale経由の問題開始・表示・回答がすべて成功する。
- Goal oracle: CLF-C02とSAA-C03の両方について、本番アプリで検索から学習回答まで通る。
- Likely misfire: upload-readyを本番公開と取り違える、未評価問題を公開する、または資格一覧だけ追加して学習動作を確認しない。
- Blind spots considered: SAA-C03の未整備Q23/Q29、既存CLF-C02の保全、重複アップロード、00_source不変、既存Firestore ID、資格名とカタカナ別名、他者のdirty差分。
- Existing plan facts: CLF-C02は既に公開済み。SAA-C03は395問中393問が整備済みで、Q23/Q29は公開対象外とする。

## Goal Oracle

The oracle for this goal is:

`本番Firestoreの資格別readbackと、Tailscale経由の検索・開始・問題表示・回答の実地成功`

## Goal Kind

`recovery`

## Current Tranche

SAA-C03の公開可能な整備済み問題を最新基準で評価し、合格分だけを本番へ公開する。資格一覧とカタカナ検索を追加し、CLF-C02を含む2資格の学習動作を確認する。

## Non-Negotiable Constraints

- `00_source`は内容・ファイル名とも変更しない。
- Q23とQ29は追加照合せず、今回の公開対象から除外する。
- 独立評価に合格していない問題を公開しない。
- 既存Firestore IDを維持し、資格別のdry-runとreadbackを行う。
- repasoの既存dirty差分を変更・破棄しない。
- エージェントを追加せず、PMが一資格ずつ直列実行する。

## Stop Rule

両資格について、本番の資格一覧・検索・問題開始・表示・回答が確認でき、GitHub反映と監査記録がそろうまで継続する。

## Canonical Board

Machine truth lives at:

`docs/goals/aws-two-qualification-live-publication/state.yaml`

## Run Command

```text
Codex: /goal Follow docs/goals/aws-two-qualification-live-publication/goal.md.
Claude Code: /goalbuddy Follow docs/goals/aws-two-qualification-live-publication/goal.md.
```
