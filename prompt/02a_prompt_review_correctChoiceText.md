# 02a `correctChoiceText`厳密判定

このpromptは、一問ごとの`logicalProjection`を読み、各選択肢そのものの正誤を根拠から独立に確定する工程の正本です。fieldの型は[問題field契約](../document/reference/question_field_contract.md)、保存先は[artifact契約](../document/operations/artifact_contract.md)を参照します。

## 入力と責務

- 入力は、`00_source`と01・02までの確定patchを重ねた`logicalProjection`です。システム実行では`currentRecord.questionBodyText`と`currentRecord.choiceTextList`が判定対象本文であり、repository内の物理ファイルを開いて別の本文を探しません。
- `explanation_common_prefix`、`explanation_choice_snippets`、現在の`explanationText`は誤り訂正文や後続草案を含む参照資料であり、選択肢原文でも正誤正本でもありません。選択肢と反対語を含んでいても解説断片を原文として両者を入れ替えません。解説の食い違いだけで02aを`hold`にせず、解説の誤りは03で直します。
- 02aが所有するfieldは`correctChoiceText`だけです。現在値、正答数、`questionType`、`questionIntent`から各肢の正誤を逆算せず、全選択肢を同じ基準で独立に判定します。
- `sourceAnswerEvidence`（検証済みのjudge欄又は`isOfficial: true` snapshot）で`appliesToCurrentText=true`の場合、その配列は現在の本文・選択肢に対する最終正誤を表すため、否定語や`questionIntent`で再反転しません。出典を示さない一般知識又は「技術的には正しい」という断定だけで覆さず、確認済みの公式・一次資料と衝突する場合だけ資料を要約して`hold`にします。`appliesToCurrentText=false`ならsource配列を転記せず独立判定します。
- 根拠不足、公式解答との実質的衝突、画像欠落又は命題を一意に読めない問題は問題単位の`hold`（構造化候補では`status=blocked`）とし、一部確定や現在値埋めを行いません。

## 判定基準

`correctChoiceText`は、問題文と各選択肢から作る完全な命題の正誤です。`select_incorrect`の問題でも、記述が誤っていれば`間違い`、正しければ`正しい`と記録します。

1. **完全命題の構成**:
   - **完結した記述**: その記述自体を判定命題とする。「誤っているものを選べ」などの指示は選択方向にだけ使い、記述の真偽を反転しない。事実だけを述べる肢に問題文が「登録拒否事由に該当するか」等の共通関係を与える場合は、肯定形の共通述語と条項、列挙範囲、時点又は分類の限定を補う（「該当しないものを選べ」の否定は選択方向であり、各肢へ補って反転しない）。
   - **断片肢**（名詞句・設備名・数値等）: 問題文の述語を否定語も含めて一度だけ補い（例：「Xは検査項目に含まれない」）、成立すれば`正しい`とする。
   - **下線部番号肢**: 番号は本文中の語句又は記述へのポインタであり、参照先の内容自体の正誤を記録する（「下線部Nは誤っている」を補って逆転させない）。
   - **並列述語の末尾否定**（「Aし、又はBしていない」等）: 句読点・助詞・法令上の並列関係を確認し、否定がAとBの双方に掛かるのかBだけに掛かるのかを先に確定する。全体否定は`A又はBを満たさないもの`と正規化し、条文の肯定条件`A又はBを満たすもの`の補集合と照合する。元解説の肯定形言い換えは元の問題文を訂正した証拠ではありません。
2. **根拠との照合**:
   - 用途・回路・運転状態など問題文の対象系の条件を先に固定し、別用途の一般論で反転させない。
   - 元解説の訂正文（例：選択肢「大きい」に対し解説「小さい」）は資料矛盾ではなく誤り訂正として読む。公式解答と訂正文が整合する場合、資料名と具体的内容を示せないモデル内部の記憶だけで覆さない。
   - 原本に「順不同」が明示された名前付き空欄（`unorderedAnswerEvidence`）は、共通条件を満たす全候補を`正しい`とする。
   - `aggregateAnswerDecomposition`でa〜d等の記述へ分解した問題は、元の組合せ肢4件の判定を転記せず、現在の`choiceTextList`にある抽出記述順の真偽（例：a・c・dが正しければ`["正しい", "間違い", "正しい", "正しい"]`）を出力する。「適切なものはいくつあるか」等の個数問題では、公式解答の数字は正しい記述の**個数**です（誤り個数を問う設問は誤った記述の個数）。個数と照合し、特定肢へ機械的に自動割当しません。「選択肢(N)はすべて正しい」の明示規約（`count_choice_index_with_all_correct_sentinel`）ではNを件数ではなく全記述成立として照合する。
   - 法令問題では出題時点の正誤を確定する。出題時の公式解答と現行条文が異なる可能性だけを02aの衝突や`hold`にせず、現行法差分の確認は02b・03bへ送る（03bで三次確定した場合のみ`23_correctChoiceText_fixed`を更新する）。
3. **最終整合確認**:
   - 全肢の独立判定後にだけ、02の`questionIntent`及び`answer_result_text`（公式解答）との整合を確認する。不整合があれば自動補正せず`hold`にし、確認した資料名と食い違いを明記する。

## 出力

システム実行ではJSON Schemaに従い、`choiceTextList`と同数・同順の`正しい` / `間違い`配列を`correctChoiceText`に設定します（`hold`時は返さない）。手動・batch最小JSONは`original_question_id`（なければ`public_question_id`）と`correctChoiceText`の2fieldとし、`question_bank.py materialize-patch --task correct_choice`で`23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json`へ保存し、`scripts/check/check_correct_choice_patch_coverage.py --require-full`で検証します。`00_source`や生成物は直接編集しません。
