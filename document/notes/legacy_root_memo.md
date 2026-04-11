questionSetIdの更新が必要。
"explanationText"も必要。

questionSetIdを紐づけた新規ファイルを作成してください。

questionSetIdの紐付けはpyファイルを作成せず、生成AIの処理でおがいしたい。紐付け後の確認では既存のpyファイルを実行してチェックをしてほしい。

marge前とmarge後のファイルで問題数が変わっていないことも確認する。

2025/12/2時点で、まだ、emptyの問題のアップロードはできていない。

作業
upload_to_firestoreディレクトリの配下のquestionを最終版として、firestoreに


list_group_idをquestionのドキュメントのフィールドの項目として含めていた方が良さそう。

## 特定のディレクトリ内にあるJSONファイルを調べて、各ファイルに含まれる質問の数を比較するためのPythonスクリプトです。
python3 - <<'PY'
import json, pathlib
base = pathlib.Path("output/2nd-class-kenchikushi/questions_json/850003")
for merged in sorted(base.glob("*_merged.json")):
    src = base / merged.name.replace("_merged.json", ".json")
    merged_count = len(json.load(open(merged))["question_bodies"])
    src_count = len(json.load(open(src))["question_bodies"]) if src.exists() else "N/A"
    print(f"{merged.name}: merged={merged_count}, src={src_count}")
PY

# ユーザーが設定する資格コード（UI側で管理）
QUALIFICATION_CODE = "2nd-class-kenchikushi"
# 資格名（examSource等に使用）
QUALIFICATION_NAME = "二級建築士"


"correctChoiceText":が間違っている場合があるため、生成AIの力を借りて修正する。


どんな問題だったか、なにが問われたか、重要なポイントはなんだったかを思い出す
・検索練習は再チェック学習(再度見返すだけの学習)と比べて50%程度記憶の定着率が上がる ・「検索練習＋高ストレス」と「再チェック＋低ストレス」の成績はほぼ同じ という研究結果もあるそう （引用：https://www.psychologicalscience.org/observer/test-enhanced-learning-2）。