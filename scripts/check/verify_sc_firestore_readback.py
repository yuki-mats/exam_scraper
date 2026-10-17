import os
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)

db = firestore.client()

# 1. sc の総ドキュメント件数
docs = db.collection("questions").where("qualificationId", "==", "sc").select(["questionId"]).get()
print(f"Total SC documents in Firestore: {len(docs)}")

# 2. 修正したドキュメントの内容検証
target_ids = [
    "d1168f6c3a5b8d6e",  # 201002 午前Ⅰ問29（改行修正）
    "400724ed369806b5",  # 201801 午前Ⅰ問11（改行修正）
    "7d06a8f9b5976ba2_4", # 201301 問25（正解＝正しい）
    "7d06a8f9b5976ba2_1", # 201301 問25（不正解＝間違い）
    "b5a2453241284cd7_1", # 202101 問20（正解＝正しい）
    "b5a2453241284cd7_2", # 202101 問20（不正解＝間違い）
]
for qid in target_ids:
    doc = db.collection("questions").document(qid).get()
    if not doc.exists:
        print(f"ERROR: {qid} does not exist in Firestore!")
        continue
    data = doc.to_dict()
    exp = data.get("explanationText") or ""
    correct = data.get("correctChoiceText") or ""
    has_literal_newline = "\\n" in exp
    print(f"\n--- Checking {qid} ({data.get('year')} {data.get('term')} Q{data.get('questionNumber')}) ---")
    print(f"correctChoiceText: {correct}")
    print(f"Literal '\\n' present in explanationText: {has_literal_newline}")
    print(f"explanationText sample (first 100 chars): {repr(exp[:100])}")

# 3. official_exam_years_by_qualification の確認
cfg_doc = db.collection("config").document("08zYvCuKUcvGTNYqehrm").get()
if cfg_doc.exists:
    manifest = cfg_doc.to_dict().get("official_exam_years_by_qualification", {})
    print(f"\nManifest official_exam_years for sc: {manifest.get('sc')}")
else:
    print("\nWarning: config/08zYvCuKUcvGTNYqehrm not found")
