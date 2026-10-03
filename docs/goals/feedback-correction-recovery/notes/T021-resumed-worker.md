# T021 実装の再開

- 前回の `/root/scoped_publication_worker` は live agent 一覧に存在しないことを確認した。観測 timeout による再起動ではない。
- 途中変更は `primary_law_evidence.py` とその test、`scoped_canonical_context.py`、`prepare_scoped_question_artifacts.py`、`scoped_artifacts.py` の5ファイル。完了 receipt として扱わず、保持した。
- 同じ T021 の完全な許可範囲・検証・停止条件で native `goal_worker` `/root/scoped_publication_resume` へ引き継いだ。他者変更の破棄、正式問題パッチの追加反映、Firestore write は許可していない。
- 実装再開時の HEAD は `d36e6e12d`、branch は `main`、ローカルに記録された `origin/main` との差は0。
- board renderer は現 YAML の配列を完全に表示できない。task 定義は PyYAML で全体を読み取る。board checker は T021 active・22 tasks・errors 0・warnings 0。
- PyYAML 利用済み interpreter は `/Users/yuki/.pyenv/versions/3.14.0/bin/python3`。製品の test・runtime は T021 の WORK `.venv/bin/python` 指定を維持する。

正答と `questionIntent=select_correct` の正式パッチは承認済み。追加4fieldの正式保存と Firestore 公開はそれぞれ別の承認境界を維持する。
