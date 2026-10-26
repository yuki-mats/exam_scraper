import json
import glob
import os
from pathlib import Path
from generate_birukan_question_set_ids import classify_question

base_dir = 'output/birukan/questions_json'
list_groups = ['95001', '95002', '95003', '95004', '95005', '95006', '95007', '95008']

# load valid questionSetIds from category.json
with open('output/birukan/category/category.json', 'r', encoding='utf-8') as f:
    cat_data = json.load(f)
valid_qs_ids = set([qs['questionSetId'] for qs in cat_data.get('questionSets', [])])

total_updated = 0
for lg in list_groups:
    lg_dir = Path(base_dir) / lg
    p21_dir = lg_dir / '21_explanationText_added'
    p22_dir = lg_dir / '22_questionSetId_linked'
    p22_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, 9):
        p21_file = p21_dir / f'question_{lg}_{i}_merged_explanationText_added.json'
        p22_file = p22_dir / f'question_{lg}_{i}_questionSetId_linked.json'

        if not p21_file.exists():
            continue

        with open(p21_file, 'r', encoding='utf-8') as f:
            p21_items = json.load(f)

        existing_qs_by_key = {}
        if p22_file.exists():
            with open(p22_file, 'r', encoding='utf-8') as f:
                try:
                    p22_existing = json.load(f)
                    for item in p22_existing:
                        key = item.get('sourceQuestionKey') or item.get('public_question_id') or item.get('original_question_id')
                        qs = item.get('questionSetId')
                        if qs and qs in valid_qs_ids:
                            existing_qs_by_key[key] = qs
                except Exception:
                    pass

        new_p22_items = []
        for item in p21_items:
            key = item.get('sourceQuestionKey') or item.get('public_question_id') or item.get('original_question_id')
            assigned_qs = existing_qs_by_key.get(key)
            if not assigned_qs:
                assigned_qs = classify_question(item)
            
            # create clean copy of item with questionSetId
            p22_item = dict(item)
            p22_item['questionSetId'] = assigned_qs
            new_p22_items.append(p22_item)
            total_updated += 1

        with open(p22_file, 'w', encoding='utf-8') as f:
            json.dump(new_p22_items, f, ensure_ascii=False, indent=2)

print(f"Successfully materialized 22_questionSetId_linked across all 8 list_groups! Total questions: {total_updated}")
