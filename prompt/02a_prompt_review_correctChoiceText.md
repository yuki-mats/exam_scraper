# 02a `correctChoiceText`厳密判定

このpromptは、一問ごとの`logicalProjection`を読み、各選択肢そのものの正誤を根拠から独立に確定する工程の正本です。03の解説は、この工程で確定した正誤を前提に作ります。

fieldの型と工程間の不変条件は[問題field契約](../document/reference/question_field_contract.md)、保存先と固定名は[artifact契約](../document/operations/artifact_contract.md)を優先します。

## 入力と責務

- 入力は`00_source`と01・02までの確定patchを重ねた`logicalProjection`です。02a自身の結果を含む物理的なmerged artifactを入力にしません。
- `questionBodyText`と各`choiceTextList`を結合した完全な命題を一肢ずつ読み、出題時点の公式解答、元解説、資格別方針で認めた根拠と照合します。
- 問題整備システムでは、入力された`currentRecord.questionBodyText`と`currentRecord.choiceTextList`が、serverが投影した判定対象本文です。元の問題文・選択肢の文言はこの2fieldだけで確認し、repository内の物理ファイルを開いて別の本文を探しません。`explanation_common_prefix`、`explanation_choice_snippets`、現在の`explanationText`などの解説fieldは、誤りを直した文章又は後続工程の草案を含む参照資料であり、`00_source`の選択肢本文でも02aの正誤正本でもありません。特に、`choiceTextList`が「弱い」で解説断片が「強い」のように反対語を含んでも、解説断片を原文として両者を入れ替えません。解説候補同士又は現在の解説と正答が食い違う場合は、問題文・公式解答・確認済み専門資料から正誤を確定し、解説の誤りは03で直します。解説の食い違いだけで02aを`hold`にしません。
- 02aが所有するのは通常`correctChoiceText`だけです。`questionType`、`isCalculationQuestion`、`questionIntent`、問題文、選択肢、解説、IDを変更しません。
- 現在の`correctChoiceText`、正答数、`questionType`又は`questionIntent`から各肢の正誤を逆算しません。各選択肢を同じ根拠基準で独立に判定します。
- `sourceAnswerEvidence`は、`00_source`から分離した更新不能な正答証拠です。`evidenceType=trusted_gassyunin_judge_statement_verdicts`かつ`verdictSemantics=final_correct_choice_text_for_source_text`では、取得元のjudge欄がsourceの問題文と各選択肢を組み合わせた最終命題へ`correctChoiceText`を対応付け、件数・順序・出所を機械検証済みです。gassyunin URLとjudge対応を保持したFirestore snapshotも同じ検証を通った場合だけ含みます。`appliesToCurrentText=true`なら現在の本文・選択肢と完全一致するため、この配列を最終正誤として扱い、否定語又は`questionIntent`で再反転しません。モデル内部の記憶や出典を示さない一般知識だけを、この証拠との明白な衝突とは扱いません。公式解答又は確認済みの公式・一次資料と衝突する場合は、配列を推測で変更せず、確認した資料を要約して`hold`にします。`appliesToCurrentText=false`ならsource配列を現在値へ転記せず、現在の完全な命題を独立に判定します。公式解答番号は`answerResultSemantics`に従って数、元の組合せ肢番号又は選択肢番号として解釈します。問題文に「選択肢(N)はすべて正しい」と明示され、`answerResultSemantics=count_choice_index_with_all_correct_sentinel`である場合、公式解答Nは該当肢がN件という意味ではありません。`select_incorrect`では誤った記述が0件、`select_correct`では全記述が正しいことと照合します。
- 根拠不足、公式解答との衝突、画像欠落又は命題を一意に読めない問題は、問題単位の`hold`（構造化候補では`status=blocked`）にします。一部の肢だけを確定したり、現在値で残りを埋めたりしません。

## 判定基準

`correctChoiceText`は、問題文と選択肢から作る完全な命題の正誤です。`select_incorrect`の問題でも、完結した記述肢は誤っていれば`間違い`、正しければ`正しい`と記録します。

1. 各選択肢が完結した記述なら、その記述自体を判定命題にします。「誤っているものを選べ」などの解答指示は選択方向にだけ使い、記述の真偽を反転しません。選択肢が名詞句、設備名、数値などの断片なら、問題文の述語を否定語も含めて一度だけ補い、その完全な判定命題を直接判定します。例えば「検査項目に含まれないもの」を断片肢から選ぶ問題では、「振動レベルは検査項目に含まれない」が成立するなら、その肢は`正しい`です。「振動レベルは検査項目に含まれる」という別の命題へ置き換えてから、公式解答又は`questionIntent`に合わせて再反転しません。
   選択肢が本文中の下線部を指す番号だけなら、番号は本文中の語句又は記述へのポインタです。`correctChoiceText`には参照先の内容自体の正誤を入れます。例えば「誤っている語句はどれか」で下線部2だけが誤りなら、2番目を`間違い`、他を`正しい`とします。「下線部Nは誤っている」という選択条件を各番号肢へ補って真偽を逆転させません。02の`questionIntent=select_incorrect`と組み合わせることで、誤った参照先が公式解答として選ばれます。
   選択肢が文法上は完結していても、事実だけを述べ、問題文が「登録拒否事由に該当するか」などの共通関係を与える場合は、その肯定形の共通述語を各肢へ一度だけ補います。「該当しないものを選べ」の否定は選択方向であり、各肢へ「該当しない」を付けて正誤を反転しません。
   「Aし、又はBしていない」のように、列挙した述語の末尾へ否定が一度だけ置かれている文は、句読点、助詞、修飾範囲及び法令上の並列関係を確認し、否定がAとBの双方に掛かるのかBだけに掛かるのかを先に確定します。表面上もっとも近いBだけを機械的に否定しません。公式問題本文、公式解答及び確認済み条文が一つの読みで整合する場合は、その構文上の読みを採用し、存在しない本文衝突を作りません。
   特に、末尾の「していないもの」が直前の並列条件全体を受ける候補では、まず`A又はBを満たさないもの`と正規化し、条文の肯定条件`A又はBを満たすもの`の補集合になっているかを照合します。元解説が条文の肯定条件を「Aし、又はBしている場合」と書いていても、それは広い否定作用域を肯定形へ言い換えた説明であり、元の問題文を訂正した証拠ではありません。この論理同値、公式解答及び確認済み条文が一致するなら、`A又はBしていない`を`A又は、Bしていない`へ分割してsource conflictを作りません。
2. その命題を、確認できる専門的根拠に照らして`正しい`又は`間違い`と判定します。
   同じ用語又は方式でも、設備の用途、回路、流量条件及び運転状態が違えば効果は変わります。別用途の一般論をそのまま移さず、問題文が指定する対象系の条件を先に固定して根拠を照合します。資格別文書が対象系の技術上の区別を明示し、公式問題と公式解答も整合する場合は、より広い別用途の知識だけを理由に反転又は`hold`にしません。
   元解説を使うときは、選択肢の引用と、誤りを訂正した説明を区別します。選択肢が「大きい」、元解説が「小さい」と述べていても、元解説がその選択肢の誤りを訂正しているなら、文言の違い自体は資料の矛盾ではありません。どちらの内容が根拠に合うかを確認し、選択肢に書かれた命題を判定します。元解説へ自動追随したり、訂正文を選択肢へ置き換えたりしません。
   出題時の公式解答と、選択肢の誤りを直した元解説が同じ判定を示す場合、モデル内部の記憶、出典を示さない一般知識又は「技術的には正しい」という断定だけを、それらとの衝突根拠にしません。反対の判定へ変える又は`hold`にするには、実際に確認した公式資料、一次資料若しくは資格別方針で認めた根拠の名称と、矛盾する具体的内容を示します。そのような根拠がなければ、公式解答と訂正文を独立判定の証拠として採用し、選択肢本文の正誤を確定します。これは公式解答番号から配列を自動生成する規則ではなく、確認できない一般論を証拠扱いしないための基準です。
3. 全肢の判定後にだけ、02で確定した`questionIntent`と出題時の公式解答を使って、選ばれる肢との整合を確認します。
4. 不整合があれば`hold`にし、02又は02aのどちらが正しいかをこの照合だけで決めません。根拠との衝突を理由にする場合は、実際に確認した資料と該当内容を特定し、選択肢又は公式解答との食い違いを示します。資料名と具体的内容を示せないモデル内部の記憶、前回の保留理由又は出典不明の「確認資料上」という記述だけを、新しい判定や`hold`の根拠にはしません。

`answer_result_text`は最後の整合確認に使い、そこから`correctChoiceText`を自動割当しません。`true_false`では各記述の事実上の正誤を判定します。`flash_card`と`group_choice`でも正答だけへ配列を縮めず、各候補が問題文の条件を満たすかを全件判定します。単一正答が期待される形式でも、先に「正しい」を1件へ固定してから他の肢を合わせません。

`aggregateAnswerDecomposition`で元の組合せ問題をa〜d等の記述へ分解した場合、出力は現在の抽出記述順に並べます。例えば元の組合せ肢が`ab / ac / bd / cd`、公式解答が3番の`bd`、設問が適切な記述の組合せを問うなら、a〜dの出力は`["間違い", "正しい", "間違い", "正しい"]`です。元の組合せ肢4件に対する`["間違い", "間違い", "正しい", "間違い"]`を転記してはいけません。

元の設問が「適切な記述の個数」を選ぶ形式なら、公式解答の数字は正しい記述の**個数**です。例えばa〜dのうちa・c・dが適切で、公式解答が`3`なら、抽出記述順の出力は`["正しい", "間違い", "正しい", "正しい"]`です。bが法令又は事実に反することは、資料との衝突ではなく、bを`間違い`と判定する根拠です。公式解答`3`を3番目の記述だけが正しいという意味へ読み替えず、各記述の独立判定後に`正しい`の件数が公式解答と一致するかを照合します。

法令問題では、この工程で出題時点の正誤を確定します。出題時の公式解答と現行条文が異なる可能性だけを02aの衝突又は`hold`にせず、公式解答に整合する出題時正誤を確定して02b・03bへ差分確認を送ります。出題時点でも成立しないことを同時点の一次資料で確認した場合だけ、公式解答との実質的衝突として`hold`にします。03bで現行法ベースの正誤が正式に変わった場合は、同じ`23_correctChoiceText_fixed`を更新し、後続artifactを再生成します。

## 出力

問題整備システムでは、指定されたJSON Schemaに従い、各問題の候補に`correctChoiceText`だけを設定します。配列の要素数は`choiceTextList`と同じにし、選択肢順に`正しい`又は`間違い`だけを入れます。`hold`では更新候補を返しません。

手動又はbatch運用で全問を確定できた場合のAI生出力JSONは、元の順序と件数を保ち、各要素を次の2fieldだけにします。`original_question_id`がなければ`public_question_id`を使います。一問でも`hold`なら現在値で残りの肢を埋めず、そのbatchを正式patchへmaterializeしません。

```json
[
  {
    "original_question_id": "92e46de21bcb2232",
    "correctChoiceText": ["正しい", "間違い", "正しい"]
  }
]
```

正式patchは同じ`list_group_id`の`23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json`へ固定名で保存します。materialize処理はsource identity、変更メタデータ、`question_url`などの非判断fieldを機械的に補います。

```bash
python3 tools/question_bank/question_bank.py materialize-patch \
  --task correct_choice \
  --source <logical_projection.json> \
  --raw <minimal.json> \
  --output <list_group_id>/23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json
```

## 安全境界と検証

- `00_source`は変更、削除、改名しません。既存IDを変更しません。
- merged、convert、upload-readyを直接編集しません。
- 不確実性は`99_model_review_flags/`又はreview sidecarへ`hold`理由、衝突した根拠、確認事項を残します。
- serverは一問ごとにID、件数、配列型、`choiceTextList`との同数、値の許可集合、source bindingを検証します。
- 全工程後の機械検証は、`questionType`、`questionIntent`、`correctChoiceText`、公式解答の不整合を検出したら停止するだけです。正答数を合わせるための値変更や、他fieldへの自動補正は行いません。
- patchへの反映後に作るmerged artifactは独立した生成工程の責務です。

```bash
python3 scripts/check/check_correct_choice_patch_coverage.py \
  --source <logical_projection.json> \
  --patch <list_group_id>/23_correctChoiceText_fixed/<source_stem>_merged_correctChoiceText_fixed.json \
  --require-full
```
