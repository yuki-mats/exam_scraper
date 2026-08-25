# 問題整備の下位クラウドモデル比較

## Objective

問題整備の初回作業モデルとして、Lunaより画面上で下位にあるGPT-5.4、GPT-5.4 Mini、GPT-5.3 Codex Sparkを同一条件で実際に利用し、Sol監査後も品質を満たす最もコスト効率の良いモデルを選定する。

## Original Request

Lunaより低いモデルについてもそれぞれ利用してみて、問題ないか確認して、コスパの良いモデルを見つけたい。

## Intake Summary

- Input shape: `specific`
- Audience: 問題整備システムの運用者と受験者
- Authority: `approved`
- Proof type: `metric`
- Completion proof: 同じ固定問題集合を各候補で1並列実行し、構造化応答、raw品質、Sol監査結果、所要時間、call・usage、holdを比較した再現可能な採否表
- Goal oracle: 重大誤り0を前提に、品質基準を満たした候補の中から実測コストと時間が最小のモデルを選ぶ
- Likely misfire: Codex画面の上下順を能力又は価格順とみなし、実測せず採用すること
- Blind spots considered: GPT-5.4はLunaよりAPI単価が高いこと、Sparkはcoding/UI向けで一般知識問題用model IDと利用条件が未確定であること、既存36問benchmarkには画像入力と監査scorerの既知欠陥があること
- Existing plan facts: 初回作業は下位model、監査はSol、audit batchは最大5問、全LLM呼び出し1並列、柔道整復師を主サンプルとする

## Goal Oracle

`候補ごとの実model ID、同一prompt・固定集合、raw正答、Sol監査後の正答・重大誤り、schema失敗、所要時間、call・usageが欠落なく対応し、品質を満たす最安候補又はLuna維持をfail-closedで決定できる`

## Goal Kind

`specific`

## Current Tranche

既存の柔道整復師・二級建築士固定集合を基礎に、既知のbenchmark欠陥を補正した隔離harnessを用意する。全候補を小さな共通集合で実利用し、品質早期不採用にならない候補だけを拡大評価する。production source、patch、Firestore及び公開状態は変更しない。

## Non-Negotiable Constraints

- `00_source`、既存ID、production patch、Firestore及び外部公開を変更しない。
- oracle、正答番号及び既存評価結果を生成model又はSol監査promptへ渡さない。
- 全model callを合わせて1並列にする。
- 同じ固定入力、prompt、reasoning effort、Sol監査契約で比較する。
- provider usageが得られない場合はtoken又は金額を推定せず、公式API単価とcall数を参考値として分離する。
- Sparkが一般問題整備に利用できない場合も、実model availabilityと失敗内容を採否証拠として残す。
- 重大な正答誤り、法令時点混同、医学的捏造又は画像誤認を採用候補に残さない。

## Stop Rule

最終Judge又はPM監査が、各候補を実際に呼んだ証拠と比較表を確認し、最もコスト効率の良い採用model又はLuna維持を`full_outcome_complete: true`で記録した場合だけ終了する。

## Canonical Board

`docs/goals/question-maintenance-lower-cloud-model-benchmark/state.yaml`
