# eco検定（環境社会検定試験）

- ローカル資格コード: `eco-kentei`
- 取得元の扱い・画像取得・文字起こし: [Knoun取得契約](../../../document/sources/knoun/source_contract.md)
- 取得済み入力の構造と05の参照資料: [試験・入力概要](01_exam_profile.md)
- 試験範囲の参照先: [東京商工会議所の試験要項](https://kentei.tokyo-cci.or.jp/eco/exam-info/)

ユーザーの指定により全問を05独自問題化へ送る。原画像の取得、転記照合、`00_source`登録を済ませてから、[共通の独自問題ワークフロー](../../../document/operations/original_question_authoring_workflow.md)へ進める。

問題文・正答・解説の事実確認は、文字の転記照合とは別に行う。取得元解説を確認済みの法令・政策・統計として扱わず、後続工程で現行の一次根拠に照らす。

## 取得元の表記差異と作問時の確認

UNESCOの名称は「国際連合教育科学文化機関」であり、「国連教育科学文化機関」とも表記する。[文部科学省「ユネスコとは」](https://www.mext.go.jp/unesco/003/001.htm)と[外務省の概要](https://www.mofa.go.jp/mofaj/gaiko/culture/kyoryoku/unesco/gaiyo.html)で名称、本部所在地、活動目的を確認できる。

模擬問題4の`mondai4_26`は、選択肢と解説本文では「国連教育科学文化機関」、正答見出しでは「国際教育科学文化機関」と印字されている。原画像を対で照合した差異としてsourceに双方の原文を残す。05以降は上の一次資料で確認した名称を使い、取得元の見出しの表記差を独自問題へ引き継がない。
