# 01 `questionType` / `isCalculationQuestion` 判定

このpromptは、一問ごとの`logicalProjection`を読み、受験者の回答操作を表す`questionType`と、計算操作の要否を表す`isCalculationQuestion`を独立に確定する工程の正本です。fieldの型は[問題field契約](../document/reference/question_field_contract.md)、保存先は[artifact契約](../document/operations/artifact_contract.md)を参照します。

## 入力と責務

- 入力は、`00_source`に先行patchを工程順で重ねた`logicalProjection`です。`questionBodyText`、`choiceTextList`、図表、ローカル保存済みの解説資料から回答操作を確認し、外部Webや`question_url`は参照しません。
- 通常の01が所有するfieldは`questionType`と`isCalculationQuestion`だけです。本文、選択肢、正答、設問意図、解説、IDを変更しません。
- 現在値、正答数、解説の単位を判定根拠にせず、2fieldも互いから派生させずに内容から独立判定します。
- 画像欠落、OCR崩れ、根拠不足などで回答操作を一意に定められない問題は、問題単位の`hold`（構造化候補では`status=blocked`）にします。

## `questionType`の判定

`isOfficial=true`の公式過去問と独自問題では、次の3値だけを使います。`single_choice`と`fill_in_blank`はユーザー作成問題用であり、公式問題の新規整備・洗い替え候補にはしません。

| 値 | 回答操作 |
| --- | --- |
| `true_false` | 選択肢ごとの記述を単独で読み、その記述自体の正誤・適否を独立に判定する。複数の独立した肢を選ぶ問題も、各肢の判定が学習単位ならこれに含む。 |
| `flash_card` | 問題文の条件、知識、図又は計算から単一の答えを選択肢を見る前に導き、選択肢は導いた答えとの照合に使う。 |
| `group_choice` | 候補間の組合せ・対応・順序など、関係を一体として比較して正答を1つ選ぶ。 |

判定は文字数、選択肢数、記号、正答数ではなく**受験者が行う回答操作**で決めます。

- **独立判定と一体比較の境界**: 正答が1つであることは`group_choice`の十分条件ではありません。「正しいものはどれか」「誤っているものを1つ選べ」でも、各肢の記述自体を独立に真偽判定する学習単位なら`true_false`です。最終回答となる組合せ表や順序表そのものが`choiceTextList`に並び、候補間の関係を一体比較する場合だけ`group_choice`とします。
- **複数選択・複数対象**: 複数の候補を選ぶ問題では、問題文の共通述語を各候補に補って判定します。各候補に対して他の候補の判定と切り離して該当・非該当を確定する操作は`true_false`であり、複数選択の指示や該当候補の数だけでは決めません。一方、問題文の条件や知識から単一の答えを選択肢を見る前に導き、選択肢と照合する場合だけ`flash_card`です。複数の下線部・事例に対応する用語を同一解答群から選ぶ問題も、各用語の該当性を独立判定するなら`true_false`、対応の組合せ表を選ぶなら`group_choice`です。名前付き空欄の`unorderedAnswerEvidence`に原本の順不同範囲がある場合は、表示位置の一例に固定せず共通条件への該当性で判断します。
- **単一導出計算と肢別計算の境界**: 計算で一つの数値を求め、その値に最も近い数値候補を選ぶ問題も、導く答えは一つなので`flash_card`です。単一の計算結果に数値候補を順番に当てはめるだけの問題は肢別計算ではありません。一方、問題文だけでは答えが定まらず、`choiceTextList`の各肢が互いに異なる条件、物質、反応式などの計算対象を持ち、肢ごとに計算・比較して該当性を判定する問題は`true_false`です。例えば複数の反応式から必要量が最大・最小となる式を選ぶ問題で、比較対象の反応式そのものを持つ場合は、各反応式の該当・非該当を判定する`true_false`とします。

## `isCalculationQuestion`の判定

正答へ至るために、与条件を式へ代入して四則演算、比、割合、単位換算などを実行する問題は`true`です。数値の暗記、式・基準値の選択、数値選択肢の存在だけでは`false`です。法令や規格の基準値と、選択肢に既に記載された数値を大小・一致で照合する単純比較（例：`0.8mは1.2m未満`）は計算過程ではないため`false`とします。この判定は`questionType`と独立であり、3形式のいずれにも`true`と`false`があり得ます。

## 集約回答型だけの原文span投影

本文中に複数の独立命題があり、個数や組合せ1つへ回答を集約しているため誤答箇所を特定できない問題に限り、原文spanから記述単位へ分解します。設例条件、並べ替え、穴埋め語句、又は`choiceTextList`に個別命題が既に並ぶ通常問題は対象外です。

1. serverがsource snapshotのhashを固定し、原文の連続範囲から候補span・boundary ID・candidate IDを決定的に生成する。
2. 別々のread-only threadで専用レビューを2回実行し、分類と候補採否だけを返す。
3. serverが二者の結果を照合し、完全一致した`target/approve`だけを採用して原文spanから`choiceTextList`と派生`sourceUniqueKeys`を切り出す（AIが抽出文を生成する経路は設けない）。
4. 投影後の`logicalProjection`を入力として、別turnで通常の問題形式候補（対象確定時は`questionType=true_false`）を生成する。

通常実行では、採用spanを含む実配送prompt、投影hash、分類turnの識別子を保存する。保存・再送・中断復元時も同じ投影入力との一致を確認し、分類候補と保存予定の形式が異なる場合は停止する。writerが分類結果を投影側の形式へ上書きして整合させてはならない。

二者の不一致、hash・範囲不整合、命題と前提の境界不明は問題単位の`hold`とします。分解後の正答・解説は後続工程で全記述分を独立に確定し、元の回答候補は`originalAggregateAnswerEvidence`へ分離保持します。

## 出力と検証

システム実行ではJSON Schemaに従い`questionType`と`isCalculationQuestion`だけを返します（`hold`時は更新候補を返さない）。手動・batch最小JSONは`original_question_id`（なければ`public_question_id`）を含む3fieldとし、`question_bank.py materialize-patch --task question_type`で`10_questionType_fixed/<source_stem>_questionType_fixed.json`へ保存し、`check-question-type-patch`で検証します。`00_source`や既存ID、生成物（`12_merged_questionType`等）は直接編集しません。後続工程との不整合を機械検証が検出した場合は自動補正せず停止します。
