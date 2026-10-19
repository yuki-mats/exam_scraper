import os
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)

db = firestore.client()

# 1. nw の総ドキュメント件数
docs = db.collection("questions").where("qualificationId", "==", "nw").select(["questionId"]).get()
print(f"Total NW documents in Firestore: {len(docs)}")

# 2. フォルダと問題セット件数
folders = list(db.collection("folders").where("qualificationId", "==", "nw").select([]).get())
qsets = list(db.collection("questionSets").where("qualificationId", "==", "nw").select([]).get())
print(f"Total NW folders in Firestore: {len(folders)} (expected 7)")
print(f"Total NW questionSets in Firestore: {len(qsets)} (expected 29)")

# 3. 公式試験年度マニフェスト確認
cfg_doc = db.collection("config").document("08zYvCuKUcvGTNYqehrm").get()
if cfg_doc.exists:
    manifest = cfg_doc.to_dict().get("official_exam_years_by_qualification", {})
    nw_years = manifest.get("nw", [])
    print(f"\nManifest official_exam_years for nw: {len(nw_years)} years -> {nw_years}")
else:
    print("\nWarning: config/08zYvCuKUcvGTNYqehrm not found")

# 4. サンプル問題の実機Readback検証
target_ids = [
    "81f156287441621f",       # 201102 午前Ⅰ問28（EAC計算・画像付き）
    "d7a8c70813d2b32d_2",     # 201002 午前Ⅱ問11（WebDAV）
    "eaf8b4b1a98fbc9b",       # 201302 午前Ⅰ問28（開発進捗計算・画像付き）
    "a387b9fe237640b8_2",     # 202401 午前Ⅱ問7（IPアドレス/16計算）
    "ac32ab2feba14c19_4",     # 201602 午前Ⅰ問30（損益分岐点・画像付き）
    "a39c773b453c5cab_3",     # 202501 午前Ⅰ問23（システム監査・正解）
]

print("\n--- Spot check documents readback from Firestore ---")
for qid in target_ids:
    doc = db.collection("questions").document(qid).get()
    if not doc.exists:
        print(f"ERROR: {qid} does not exist in Firestore!")
        continue
    data = doc.to_dict()
    qtext = (data.get("questionText") or "")[:50]
    correct = data.get("correctChoiceText") or ""
    exp = (data.get("explanationText") or "")[:80]
    imgs = data.get("questionImageUrls") or []
    has_literal_newline = "\\n" in (data.get("explanationText") or "")
    
    print(f"\nDoc [{qid}]")
    print(f"  Year/Term: {data.get('year')} {data.get('term')} Q{data.get('questionNumber')}")
    print(f"  QText: {qtext}...")
    print(f"  Correct: {correct}")
    print(f"  Images: {len(imgs)} items")
    print(f"  Literal '\\n': {has_literal_newline}")
    print(f"  Exp: {exp}...")

print("\nVerification completed successfully.")
