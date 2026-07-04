# <資格名> `lawReferences` 方針

この文書は、`prompt/03_prompt_add_explanationText.md` で `lawReferences` を作る前に読む資格別の法令スコープである。

目的は、作業者が e-Gov の全法令から無差別に探さず、この試験で通常参照する法令範囲から正しい `lawId` と条項を確認できるようにすること。

## 対象法令スコープ

| 優先度 | 短縮表記・別名 | 正式法令名 | lawId 候補 | 使う場面 | 使わない場面 |
| --- | --- | --- | --- | --- | --- |
| primary | 法 | <正式法令名> | `<lawId>` | 定義、義務、手続、数値基準 | 技術知識だけで判断できる問題 |
| primary | 令 / 施行令 | <正式法令名施行令> | `<lawId>` | 法の委任を受けた対象範囲・数値 | 法律本文で完結する定義 |
| primary | 規則 / 施行規則 | <正式法令名施行規則> | `<lawId>` | 様式、細目、手続、検査 | 政令で完結する数値 |
| conditional | 告示 | <告示名> | `<lawIdまたは管理ID>` | 告示本文が正誤根拠になる場合 | 告示が背景説明に出るだけの場合 |

## スコープ外法令を追加する条件

対象法令スコープにない法令を `lawReferences` に使う場合は、次を全て満たすこと。

- 問題文・設問文・選択肢・解説候補のいずれかに、その法令を使う合理的根拠がある。
- 一次情報で正式法令名、`lawId`、条番号を確認している。
- この文書の対象法令スコープ表へ追加し、なぜこの試験で必要かを `使う場面` に書く。

満たせない場合は、`verificationStatus="verified"` にしない。

## 現行法と出題当時法令

- 法令問題は、出題当時の正誤と現行法の正誤を分けて確認する。
- 現行法で正誤が明らかに変わる場合は、現行法ベースへ `correctChoiceText` / `explanationText` を更新する。
- 更新した場合は、更新済みであること、出題当時の正答、現行法の根拠条項を `explanationText`、`suggestedQuestions`、`suggestedQuestionDetails`、`lawReferences`、review sidecar に残す。
- `current_basis` は現行法に基づく更新後の根拠として作る。
- `exam_time_basis` は、出題当時法令を確認でき、かつ現行法との差分が過去問の元正答理解に関係する場合に追加する。
- 現行法と出題当時法令が同じ判断になる場合、`exam_time_basis` を無理に増やさない。

## `explanation_choice_snippets` の扱い

`explanation_choice_snippets` は条文候補の抽出に使えるが、単独では `verified` の根拠にしない。

禁止事項:

- `explanation_choice_snippets` だけを根拠に `verified` とする
- 条文本文を確認せず、条・項・号を補完する
- 対象法令スコープ外の法令を推測で使う
- `lawId` または `article` を確認できない参照を `verified` にする

## 目視確認の定義

1問ずつの目視確認では、次を照合する。

- 問題文・設問文がどの法令範囲を問うているか
- 各選択肢の正誤理由がどの条文本文に基づくか
- `explanationText` が条文本文の対象・要件・例外・数値と矛盾しないか
- `lawReferences` の `lawTitle` / `lawId` / `article` / `paragraph` / `item` が、その選択肢の根拠条文と一致するか
- 余分な参照や漏れている参照がないか

Python のキーワード一致、正規表現、XML 自動突合によって `ok` / `needs_fix` / `verified` を決めてはいけない。Python スクリプトは、台帳生成、JSON 構造チェック、必須フィールドの有無確認などの作業補助に限る。
