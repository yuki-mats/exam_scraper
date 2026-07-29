# ガス主任技術者乙種の公開版互換性障害

## 事象

2026年7月29日20時21分頃、`gas-shunin-otsu`の問題2,601件をFirestoreへ一括反映した後、App Store公開版で乙種の問題を表示できなくなった。

## 原因

App Store公開版2.17.6の`QuestionDoc`は未知fieldを拒否する。7月23日に問題整備へ追加した`explanationReferences`は、最新ソースでは読取対応済みだったが、公開版には未搭載だった。

一括反映により`explanationReferences`が760件、55問題集へ入り、公開版の問題変換が失敗した。

このfieldを除去した後、問題本文は表示されるが選択肢が表示されない事象も確認した。`group_choice`へ変換した1問の5documentへ肢別の`choiceQuestionSetIds`を適用したため、同じ問題のdocumentが複数の問題集へ分断されていた。アプリは表示中の1問題集だけを取得するため、正答documentしかない画面では選択肢グループを構築できなかった。

## 復旧

- 対象資格: `gas-shunin-otsu`
- 削除field: `explanationReferences`
- 対象document: 760件
- `questionId`、本文、選択肢、正答、解説は変更していない。
- 端末の再同期を促すため、対象documentの`updatedAt`だけを更新した。
- 復旧後の本番readbackで同fieldの残存0件を確認した。
- App Store公開版2.17.6の許可field集合と、本番乙種2,601件のfield集合を照合し、未知field 0件を確認した。
- 複数の問題集へ分断された15問・75documentを、直前の表示可能な問題形式へ一度復元した。
- 恒久修正では、`group_choice`と`flash_card`の全documentへ問題全体の`questionSetId`を使う。肢別の`choiceQuestionSetIds`は、独立表示する`true_false`だけへ適用する。
- 恒久修正と同じ投影で15問・75documentを再反映し、複数の`questionSetId`へ分断されたグループが0件であることを本番readbackした。

実行receipt:

`output/gas-shunin-otsu/release_compatibility_restore/20260729T131257Z/receipt.json`

`output/gas-shunin-otsu/release_compatibility_restore/20260729T131650Z/receipt.json`

`output/gas-shunin-otsu/release_compatibility_restore/20260729T131918Z/receipt.json`

## 再発防止

整備patchには`explanationReferences`を保持する。Firestore uploaderは、配信中のアプリが同fieldを読めることを確認できるまで書込み対象から除外し、既存documentに残っていれば削除する。

新しいFirestore fieldを公開するときは、最新のapp sourceだけでなく、App Storeで実際に配信中の版の読取契約を先に確認する。
