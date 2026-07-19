# KeepItUp取得契約

この文書は、`aws.keepitup.jp`のAWS認定資格問題を取得するときのsite固有契約です。共通のID、`00_source`、画像、再取得ルールは[スクレイピングworkflow](../../operations/scraping_workflow.md)を正本とします。

## 取得単位と列挙

- 資格ごとのcourse topを取得入口とする。AWS Certified Cloud Practitionerでは`/CL00/`を入口にし、`/CL99/`のランダム出題表示件数を全体件数として照合する。
- course topの`QSET_ID`と問題開始formから、単一選択、複数選択を含む問題系列を自動発見する。資格コードや系列一覧をscraperへ固定しない。
- 各系列の`000L`一覧から開始し、`ACTION_ID=List`のformで公開されているページだけをたどる。
- 一覧の問題ID、問題固有URL、タイトル、カテゴリを対応付ける。同じ問題IDの内容が一覧ページ間で競合した場合は停止する。
- ランダム出題の表示件数と、全系列から列挙した一意な問題ID数が一致しない場合は保存を完了扱いにしない。件数はsite更新で変わるため、実装や恒久文書へ固定しない。

## 詳細取得

- 問題IDを`<question_id>`として、問題固有URL`/<question_id>Q/`と解答・解説URL`/<question_id>A/`を対応付ける。
- 解答・解説URLは回答送信なしで、問題文、選択肢、正答、解説を一体で取得できる。この公開GETを使用し、ランダム演習sessionを作成しない。
- 単一選択と複数選択を区別し、`correct_answer`が付いた全選択肢を正答として取得する。
- 問題、選択肢、解説内の同一domain画像を取得する。広告、アフィリエイト画像、参考書画像は問題画像として保存しない。

## ID、保存、再取得

- `source_question_id`は資格コード、`aws-keepitup-jp`、siteの不変な問題IDを含む問題固有URLから生成する。ランダム出題順、一覧内の問番号、本文hashはIDに使わない。
- `output_list_group_id`は取得元と資格版を識別する安定名にし、AWS Certified Cloud Practitionerでは`keepitup-aws-clf-c02`を使う。
- 1問を`question_keepitup-<question_id>.json`へ保存する。新規IDは排他的に作成し、既存IDは同じファイル名とIDを維持する。
- 再実行時は一覧を再列挙し、全IDの問題文、選択肢、正答、解説、画像URLをliveから再取得する。全件の列挙・取得・検証が成功した後、同一内容は確認のみ、変更内容は同じ`00_source`ファイルへアトミックに反映する。
- 新しい問題IDだけを追加し、一覧から消えた既存IDは自動削除しない。

## 完了条件

1. ランダム出題の表示件数と、全系列から列挙した一意な問題ID数が一致する。
2. 一覧ID集合と保存ID集合が一致し、問題ID、`source_question_id`、問題URLの重複がない。
3. 全問に問題文、2件以上の選択肢、1件以上の正答、解説がある。文字のない画像選択肢は画像が必要である。
4. 参照した問題画像がすべてローカルに保存され、空ファイルがない。
5. 再実行で、新規、更新、同一の合計が全件と一致する。更新した場合は`updatedQuestionIds`と更新後hashがreportに残り、manifestも同じhashへ更新される。

資格ごとの取得件数と系列内訳は日付付き監査又はgenerated reportへ置き、この契約へ固定値として複製しません。
