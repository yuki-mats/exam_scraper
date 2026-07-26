# バッチ成果物モニター完了監査

- 実施日: 2026-07-27
- 対象commit: `291b9f2bb715c49d93e5504547662d540fd84b30`
- 対象server: 現行の問題整備Python serverが管理する単一Codex App Server
- UI: `/monitor`
- API: `/api/monitor/v1`
- 判定: `full_outcome_complete: true`

## Outcome

問題整備システムとは別のread-only画面として、並列Lane、保存済み成果物本文、Agent発言、App Serverが公開した推論サマリー、tool・状態イベント、観測gapとstaleを表示できるようにした。モニターは既存Python serverのobserverと正本artifactだけを読み、Agentへの指示、開始、再実行、停止、編集又は公開を行わない。

問題整備画面とモニターは、資格、run、list group、questionのstable IDを使うdeep linkで相互に移動する。workflow工程と成果物保存先は既存正本を参照し、モニター側で重複定義していない。

## ライブreadback

`origin/main`を稼働checkoutへfast-forwardし、既存launchd job `com.yuki.question-review-console`だけを再起動した。

- server PID: `85861` → `12392`
- port 8765 listener: 1 process
- App Server: available
- 再起動直前のturn、control plane、model turn: in-flight 0
- 全資格のrun: 実行中0件。`awaiting_changes` 1件はjob、heartbeat、execution phaseを持たない待機記録
- 既存未コミット6ファイル: 更新前後のcontent hashが全件一致

実run `20260727T034152556446-a7774500`をmonitor APIから読み返した。

| 項目 | 結果 |
|---|---|
| run状態 | `interrupted`をそのまま表示 |
| snapshot | 128 Lane、artifact fingerprintあり |
| artifact宣言 | 105件 |
| 1 page目 | 64件、次cursorあり |
| 2 page目 | 41件、終端cursor |
| 重複・欠落 | unique 105件、重複0件、欠落0件 |
| 旧run互換警告 | `v2_output_fingerprint_missing`を明示し、成果物105件は失わない |
| mutation | POST、PUT、PATCH、DELETEはすべて405 |
| security header | `no-store`、CSP、`nosniff`、`no-referrer` |
| Tailscale HTTPS | `/monitor`がHTTP/2 200 |
| redaction | secret、session token、絶対home pathの露出なし |
| monitor model request | 全応答で`monitorModelRequests=0` |

monitor GETの前後で、turn budget、control plane budget、model turnはいずれもin-flight 0、peak 0のまま変化しなかった。歴史runはmonitor observer導入前に終了しているためlive eventは0件だったが、下記の忠実fixtureと自動試験でApp Server通知の受付、永続化、replay、描画を確認した。

## ブラウザwalkthrough

同じPython application、monitor read model、event hub、static assetを使う忠実fixtureで次を実操作した。

- 70並列Laneを実行中優先で表示し、残りを工程別に集約
- 保存済み成果物68件を`1–64 / 68`と`65–68 / 68`に分け、次へ、本文表示、前へを確認
- Agent発言、公開推論サマリー、tool状態、turn状態を同時表示
- 観測gapとstaleをrun状態とは別に表示
- server切断時に再接続中となり、復帰後約3秒で自動的に接続中へ回復
- 390 pxでは稼働・成果物・実況tabを切替、1029 pxと1440 pxを含め水平overflowなし
- desktopでは3 paneを同時表示
- 問題整備deep linkを実際に開き、資格、list group、question IDを維持
- Agent操作button、secret、tool引数、絶対pathを表示しない

## 自動検証

- 最終rebase後の全test: 1,450 tests、OK、skip 7
- monitor関連test: 239 tests、OK
- Python compile、JavaScript syntax、`git diff --check`: OK
- 最終独立監査: 残P1/P2なし
- 64 producer burst、64並列prebind、control plane飽和、queue overflow、disk failure、rotation failure、external lock、partial write、close timeout、長poll再接続を自動試験
- monitor有効・無効でmodel RPCとusageが同一であることを自動試験
- observer例外、stdout EOF、queue overflow又はdisk failureがprotocol処理とrunへ伝播しないことを自動試験
- raw reasoning、tool引数、stdout、cookie、authorization、JWT、private key、secret、絶対pathをallowlist外として除外又はredactすることを自動試験
- manifest、plan、state、receipt、record identity、保存fileの不整合、symlink、hardlink、読取中差替えをfail-closedで検出することを自動試験
- 最大2,048宣言、64件page、4 MiB境界、cursor reset、不正cursor、単一巨大item、既存非page client互換を自動試験

## 公開状態

実装3commitを`origin/main`へpushし、稼働checkoutとserverへ反映した。モニターの障害境界、保存artifactの正本性、追加model requestゼロ、ブラウザ表示、再接続、既存変更保護までoriginal outcomeへ対応する証跡が揃っている。

`full_outcome_complete: true`
