# 問題報告の正式訂正と公開工程を復旧する

対象は前回確認した2025・2018・2024年二級建築士学科II問3、2020年管理業務主任者問8肢3の4件。一次資料と現行Firestoreを照合し、source identity、独立Blind A/B・Challenge、正式24 patch、一問限定公開gate、台帳追跡をそろえる。

2025年の肢1/2正誤入替と原問正答表示2→1は承認済み。同一差分の再承認は求めない。他の正式patchは具体的差分承認後。本番公開は別の明示判断を要する。

Oracleは一次根拠、全カテゴリ判断、source identity、真正なreview receipts、機械gate、限定diff、patch commit/push、台帳状態と、公開対象の承認及びlive readbackの一致。根拠不足の対象は理由と再開条件を残す。隔離simulationだけで完了扱いしない。

00_sourceの手作業変更、ID変更、他作業の巻き込み、安全gateの迂回、review証跡の捏造を避ける。private利用者本文や識別情報をGitと通常ログに残さない。mainの自分の検証済み変更だけcommit/pushする。

state.yamlを正本としてGoalBuddy execution contractを実行する。receipt後に次の安全sliceへ進み、承認待ちtaskがあっても安全なlocal workを続ける。既存user-feedback-response-system goalは常設UIの別scopeとして維持する。
