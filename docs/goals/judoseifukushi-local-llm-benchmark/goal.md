# 柔道整復師によるローカルLLM品質ベンチマーク

## Objective

柔道整復師を主サンプル、二級建築士を補助サンプルとして、ローカル生成・Codex監査が重大誤りなく問題整備へ使え、Codexのみよりクラウド呼出しを実際に削減できるかを盲検比較で確定する。

## Original Request

柔道整復師をサンプルとしてローカルLLM組み込みを評価する。

## Intake Summary

- Input shape: `specific`
- Audience: 問題整備システムの運用者と受験者
- Authority: `approved`
- Proof type: `metric`
- Completion proof: 固定問題集合について、ローカル候補、Codex監査、Codexのみbaseline、blind oracle、LLM呼出し数、token・fallback・holdを問題別に対応付け、採用又は不採用を再現可能に判定する。
- Goal oracle: 重大誤り0を守り、ローカル候補の監査合格率とCodex呼出し削減を同じ固定集合で測る。
- Likely misfire: 簡単な通常問題だけを選ぶ、oracleをモデルへ渡す、速度だけで不採用にする、又はCodex fallbackをローカル成功へ数える。
- Blind spots considered: 柔道整復師には画像問題がなく全問4択true_falseであること、旧年度法令差、正答表欠損、全7,600問レビューpending、医学解説のもっともらしい捏造、クラウド監査費用。
- Existing plan facts: 柔道整復師30問を主セット、二級建築士5〜7問を補助セットとし、速度上限は設けず、全LLM呼出しと問題処理は初期1並列、Codex監査は問題別micro-batchを使う。

## Goal Oracle

`固定集合の全問題で重大誤り0、各問題のlocal-only成否・Codex監査結果・Codex-only結果・blind oracle・cloud call/tokenが欠落なく対応し、採用基準を満たすかfail-closedで不採用にできる`

## Goal Kind

`specific`

## Current Tranche

正本データを変更しない隔離環境で、恣意性を抑えた固定評価集合を確定し、現在利用可能なローカルモデルとCodexのみを同一契約で実行する。品質未達が統計的に確定したモデルは残りを無駄に実行せず不採用証拠へ閉じる。

## Non-Negotiable Constraints

- `00_source`、既存ID、production patch、Firestore、外部公開を変更しない。
- oracle、正答番号、既存評価結果をモデルpromptへ渡さない。
- 重大な正答誤り、法令時点混同、医学的捏造、画像誤認を0件とする。
- 柔道整復師だけでは画像・形式多様性を評価できないため、二級建築士を補助する。
- 速度は合否条件にしないが、壁時計・queue・token・call数は記録する。
- Codex fallback、repair又はholdをlocal-only成功へ数えない。
- 全LLM呼出しと問題処理は1並列で実行する。

## Stop Rule

最終Judge又はPM監査が、固定集合の品質・安全・クラウド削減を現在証拠へ対応付け、採用又はfail-closed不採用を`full_outcome_complete: true`で記録した場合だけ終了する。

## Canonical Board

`docs/goals/judoseifukushi-local-llm-benchmark/state.yaml`

