# G検定

- 資格コード: `g-kentei`
- 取得元: Udemy講座 `g-tbyxpv`（6つの演習テスト、各100問）
- 試験範囲・分類の正本: [JDLA公式 G検定](https://www.jdla.org/certificate/general/)
- 問題形式と独自問題化: [試験概要](01_exam_profile.md)
- 分類: [カテゴリ準備](03_category_preparation.md)

取得元の問題は、共通の05工程で1問ずつ確認し、問題文又は選択肢を自然に微改変する。正答と各誤答の判断理由は維持する。

## 確認済みの取得元修正

- 演習問題①問6: 学習済みモデルを一律に特許対象外とは扱わない。[特許庁のAI関連技術に関する審査事例](https://www.jpo.go.jp/system/laws/rule/guideline/patent/ai_jirei.html)に従い、単なるパラメータセットの提示と、プログラムとして機能するモデルを区別する。05では選択肢2を前者に限定する。
- 演習問題①問42: オフライン強化学習は固定データから学び、学習中に環境で追加探索することを前提としない。[オフライン強化学習の研究レビュー](https://arxiv.org/abs/2005.01643)と[Conservative Q-Learningの論文](https://arxiv.org/abs/2006.04779)に基づき、取得元の「探索戦略が必要」という説明を採用せず、データにない行動の価値推定を問う選択肢へ修正する。
- 演習問題①問45: 取得元は一次関数 `y=ax+b` と、加法性・斉次性を満たす線形写像の性質を混同している。[UC Davisの線形代数教材](https://math.ucdavis.edu/~linear/linear-guest.pdf)で後者の定義を確認した。05では選択肢1を「入力と出力の変化量の関係」に修正し、選択肢3だけが誤答となるようにする。
- 演習問題①問70: 取得元は特許を受ける権利が常に発明者へ原始帰属すると説明するが、現行の特許法第35条第3項では、職務発明についてあらかじめ契約や勤務規則などで使用者に取得させると定めた場合、権利は発生時から使用者に帰属する。[特許庁の職務発明制度の概要](https://www.jpo.go.jp/system/patent/shutugan/shokumu/shokumu.html)に従い、05の正答肢にこの条件を加える。後続03の解説も同じ条件で作成する。
- 演習問題①問71: 取得元は「実際は陽性だが陰性と判定する誤り」の正解を偽陰性（2番）としつつ、正誤ラベルと`questionIntent`を逆に記録している。05では偽陰性だけを正しい選択肢とし、`select_correct`に修正する。
- 演習問題①問80: 取得元の4番は推論時にもドロップアウトを適用する手法と区別できない。[通常のドロップアウトの原論文](https://www.jmlr.org/papers/v15/srivastava14a.html)と[推論時のドロップアウトに関する研究](https://proceedings.mlr.press/v48/gal16.html)を踏まえ、05では「推論時にも無効化が必須」という誤りに限定する。
- 演習問題①問85: 取得元のBICの説明は対数尤度とペナルティの符号を曖昧にしている。[statsmodelsのBIC定義](https://www.statsmodels.org/v0.14.6/generated/statsmodels.tsa.statespace.mlemodel.MLEResults.info_criteria.html)に従い、05の選択肢1を「負の対数尤度にペナルティを加える」と表現する。
- 演習問題①問98: 生体情報は、本人を識別できるように変換した符号が個人識別符号に該当する。[個人情報保護委員会のガイドライン](https://www.ppc.go.jp/personalinfo/legal/guidelines_tsusoku/)を確認し、05の設問では顔・指紋の特徴データにこの条件を明示する。
- 演習問題①問99: SegNetの復元処理は単純な逆畳み込み層だけで表せない。[SegNetの原論文](https://arxiv.org/abs/1511.00561)に基づき、05では畳み込み層を使うEncoder-Decoder構造として説明し、プーリングインデックスを用いる特徴を選択肢3で維持する。
- 演習問題②問5: 取得元の「CRISP-ML」は、この設問が説明するライフサイクルと品質保証の枠組みを示す原論文の名称「CRISP-ML(Q)」に合わせる。[原論文](https://arxiv.org/abs/2003.05155)に従い、05の設問では正式名称を使用する。
- 演習問題②問6: 取得元は「has-a」を全体と部分の関係ではないとするが、[W3CのpartOf/hasPartの説明](https://www.w3.org/2001/sw/BestPractices/OEP/SimplePartWhole/)では両者は逆向きの関係として使える。05では`has-a`を下位概念とする誤答肢へ修正し、`is-a`との違いを問う。
