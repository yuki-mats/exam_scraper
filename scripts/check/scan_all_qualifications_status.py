import os
import glob
import json
import firebase_admin
from firebase_admin import credentials, firestore

if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)

db = firestore.client()

cfg_doc = db.collection("config").document("08zYvCuKUcvGTNYqehrm").get()
manifest = {}
if cfg_doc.exists:
    manifest = cfg_doc.to_dict().get("official_exam_years_by_qualification", {})

folders = db.collection("folders").select(["qualificationId"]).get()
cat_quals = set()
for c in folders:
    q = c.to_dict().get("qualificationId")
    if q:
        cat_quals.add(q)

output_dirs = sorted([d for d in glob.glob("output/*") if os.path.isdir(d)])

summary = []

for d in output_dirs:
    qual = os.path.basename(d)
    q_json_dir = os.path.join(d, "questions_json")
    cat_file = os.path.join(d, "category", "category.json")
    
    has_q_json = os.path.isdir(q_json_dir)
    has_cat = os.path.isfile(cat_file)
    
    # 00_source
    src_files = glob.glob(os.path.join(q_json_dir, "00_source", "*.json"))
    src_files += glob.glob(os.path.join(q_json_dir, "*", "00_source", "*.json"))
    
    # 30_merged_questions
    mrg_files = glob.glob(os.path.join(q_json_dir, "30_merged_questions", "*.json"))
    mrg_files += glob.glob(os.path.join(q_json_dir, "*", "30_merged_questions", "*.json"))
    
    # 40_convert
    conv_files = glob.glob(os.path.join(q_json_dir, "40_converted_questions", "*.json"))
    conv_files += glob.glob(os.path.join(q_json_dir, "*", "40_converted_questions", "*.json"))

    # upload_to_firestore
    up_files = glob.glob(os.path.join(q_json_dir, "upload_to_firestore", "*_firestore_*.json"))
    up_files += glob.glob(os.path.join(q_json_dir, "*", "upload_to_firestore", "*_firestore_*.json"))
    
    # list_groups
    list_groups = set()
    for f in src_files + mrg_files + up_files:
        rel = os.path.relpath(f, q_json_dir)
        parts = rel.split(os.sep)
        if len(parts) >= 2 and parts[0] not in ("00_source", "30_merged_questions", "upload_to_firestore", "40_converted_questions"):
            list_groups.add(parts[0])

    in_manifest = qual in manifest
    in_firestore_folder = qual in cat_quals
    
    summary.append({
        "qualification": qual,
        "src_count": len(src_files),
        "merged_count": len(mrg_files),
        "convert_count": len(conv_files),
        "upload_count": len(up_files),
        "list_groups_count": len(list_groups),
        "has_cat": has_cat,
        "in_manifest": in_manifest,
        "manifest_years": len(manifest.get(qual, [])),
        "in_firestore_folder": in_firestore_folder
    })

# 分類ロジック
# 1. 完了 (Firestore投入済み & マニフェストあり)
# 2. アップロード準備完了 (upload_to_firestore ありだが Firestore未投入)
# 3. 整備途中 (source または merged があるが upload未完了)
# 4. 未着手 (questions_json が空または存在しない)

done = []
ready_to_upload = []
in_progress = []
not_started = []

for it in summary:
    if it["in_manifest"] and it["upload_count"] > 0:
        done.append(it)
    elif it["upload_count"] > 0:
        ready_to_upload.append(it)
    elif it["src_count"] > 0 or it["merged_count"] > 0:
        in_progress.append(it)
    else:
        # 特別な管理用ディレクトリを除外
        if it["qualification"] not in ("pdf", "reports", "sample", "question_issue_reports", "question_review_console", "question_type_audit", "local_storage_migration_20261003"):
            not_started.append(it)

print(f"=== SUMMARY TOTALS ===")
print(f"完了 (Firestore投入済み): {len(done)} 資格")
print(f"投入準備完了 (ローカル生成済・Firestore未投入): {len(ready_to_upload)} 資格")
print(f"整備途中 (source/merged あり・未完成): {len(in_progress)} 資格")
print(f"未着手 (ソース未取得/データなし): {len(not_started)} 資格")

print("\n==========================================")
print("1. 完了 (Firestore投入済み)")
print("==========================================")
for it in done:
    print(f"  {it['qualification']:<30} | src={it['src_count']:<3} mrg={it['merged_count']:<3} up={it['upload_count']:<3} | cat:{'OK' if it['has_cat'] else 'NO'} | manifest:{it['manifest_years']}y")

print("\n==========================================")
print("2. 投入準備完了 (ローカル生成済・Firestore未投入)")
print("==========================================")
for it in ready_to_upload:
    print(f"  {it['qualification']:<30} | src={it['src_count']:<3} mrg={it['merged_count']:<3} up={it['upload_count']:<3} | cat:{'OK' if it['has_cat'] else 'NO'} | in_manifest:{it['in_manifest']}")

print("\n==========================================")
print("3. 整備途中 (source/merged あり・未完成)")
print("==========================================")
for it in in_progress:
    print(f"  {it['qualification']:<30} | src={it['src_count']:<3} mrg={it['merged_count']:<3} up={it['upload_count']:<3} | cat:{'OK' if it['has_cat'] else 'NO'}")

print("\n==========================================")
print("4. 未着手 (ソースなし・空ディレクトリ等)")
print("==========================================")
quals = [it['qualification'] for it in not_started]
print(", ".join(quals))
