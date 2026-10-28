# [システムプロンプト] 02b 法令コンテキスト事前準備

03の解説作成前に、各設問が法令・制度論点かを`isLawRelated`で確定し、必要な現行法根拠候補を`lawReferences`（及び必要時の`officialGuidanceReferences`）へ整理する工程の正本です。解説本文（`explanationText`）や正答更新はこの工程では行いません。field仕様は[question field契約](../document/reference/question_field_contract.md)、監査接続は[現行法監査](../document/operations/current_law_question_maintenance_workflow.md)を参照します。

## 役割と境界

- 入力は`logicalProjection`（手動時は`20_merged_1/`）、対象資格の`prompt/qualification_docs/<qualification>/`（特に`*law_reference*.md`）、及びserverが`primaryLawEvidence`として取得したe-Gov法令API v2本文です。不足箇所に限り一次情報を探索し、検索要約だけで`verified`にしません。
- 02aで確定した`correctChoiceText`と公式解答は出題時正誤の正本であり、02bは現行条文から正しい記述数を再計算しません。出題当時法との不一致が疑われても、肢と対応する現行条文を特定できれば`current_basis`を保存し、`lawContextForExplanation`へ「出題当時法との差分確認が必要」と残して03bへ渡します。予測個数の不一致だけを理由に、正しく対応付けた`current_basis`を破棄したり`hold`にしたりしません。条文や対応自体を特定できない場合だけ`hold`にします。

## `isLawRelated`の判定

法令・政令・省令・告示・通達・条例・公式制度資料上の義務、定義、手続、数値基準が正誤判断又は主要理解に直結するかを判定します。

- `isLawRelated=true`: 法令・制度論点（原則`lawGroundedExplanationNotNeeded=false`）。
- `isLawRelated=false`: 工学・技術・一般知識のみで決まる論点（原則`lawGroundedExplanationNotNeeded=true`、`lawReferences`は入れない）。JISや技術基準が法令へ直接取り込まれていることを確認できない限り、用語の類似だけで法令問題にしない。
- 政府の戦略・政策文書に載った標語や基本原則の名称を知識として問うだけなら、文書が公式でも`isLawRelated=false`とする。制度資料が権利義務、手続、適用条件又は規範上の判定基準を定め、その内容を肢へ適用する必要がある場合に`true`とする。`officialGuidanceEvidence`の配送や資料の実取得だけから法令関連性を決めない。非法令でも公式資料を解説の出典として使える。

## `lawReferences`と公式制度資料の扱い

- **配列構造と再利用**: 選択肢別の`lawReferences`は外側配列を`choiceTextList`と同じ件数・順序にし、根拠がない肢も`[]`で位置を保持する（全体根拠は`scope="question"`、肢別は`scope="choice"`かつ0始まりの`choiceIndex`）。既存の`lawReferences`で全選択肢を十分説明できる場合は広域検索と再構築を省略し、不足、404又は内容不一致がある場合だけ該当肢を探索する。
- **条文対応の厳密性**: 語句の類似だけで選ばず、条文の見出し、規律対象、申請・登録の種類、義務主体が設問と一致するか確認する。許可の施設基準の肢に更新期限だけを定める項を根拠にしない。例外・代替・「代えて」の規定は、置き換わる義務となお残る交付・記載・手続の義務を分けて`lawContextForExplanation`へ短く残す。判例・ガイドラインには架空の`lawId`を振らず、実在条文locatorと分けてメモを残し03bへ渡す。
- **実取得と検証**: 探索turnでは`candidate` / `unverified`を返せるが、最終保存はserverが実取得した現行本文（`egov_xml`又は一致する`sourceUrl`）と照合済みの`verified`（`role="current_basis"`）だけを認める。条文本文自体はpatchへ保存しない。
- **公式制度資料（標準管理規約等）**: 直接適用するe-Gov法令がない制度問題は`lawReferences`を全肢空配列とし、server実取得の`officialGuidanceEvidence`を読んで`officialGuidanceReferences`（配送済み`request`・`evidenceRef`と全肢の`applicabilityByChoice`）を保持する。所定頁不足は`blocked`とし、歴史版比較と正誤確定は03bへ渡す。

## 出力と検証

出力先は`18_law_context_prepared/<source_stem>_merged_lawContext_prepared.json`です。最小JSON（`original_question_id`、`isLawRelated`、`lawGroundedExplanationNotNeeded`、任意の`lawReferences`・`lawContextForExplanation`）を作成した場合は`question_bank.py materialize-patch --task law_context`で補完し、単体検証を実行します（全体検査は公開準備で行う）。

```bash
.venv/bin/python tools/question_bank/question_bank.py check-law-context-patch \
  --source <logical_projection_or_20_merged_1.json> \
  --patch <list_group_id>/18_law_context_prepared/<source_stem>_merged_lawContext_prepared.json
```
