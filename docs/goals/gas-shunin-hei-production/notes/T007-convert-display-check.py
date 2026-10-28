"""丙種の現在projectionを固定convertで診断する。公開用成果物・承認は作らない。"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
sys.path.insert(0, str(RUN / 'tool_snapshot'))
from tools.question_review_console.inventory import QuestionInventory
from scripts.convert.convert_merged_to_firestore import convert_question_to_firestore

inventory = QuestionInventory(ROOT)
packets = json.loads((RUN / 'records.json').read_text())
errors, notes, documents, hashes = [], [], [], {}
for key, packet in packets.items():
    projected = inventory.projected_input('gas-shunin-hei', key[:4], packet['sourceIdentity']['sourceRecordRef'])
    if projected.errors:
        raise ValueError(projected.errors)
    hashes[key] = hashlib.sha256(json.dumps(projected.record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    try:
        converted = convert_question_to_firestore(projected.record)
    except Exception as error:
        errors.append({'sourceQuestionId': key, 'error': str(error)})
        continue
    for document in converted:
        for field in ['questionBodyText', 'questionText', 'originalQuestionChoiceText', 'explanationText']:
            if re.search('公式PDF|編集補足|編集注|主担当|印字|スクレイピング', str(document.get(field, ''))):
                notes.append({'sourceQuestionId': key, 'questionId': document['questionId'],
                              'field': field, 'text': document[field]})
    documents.extend(converted)
duplicate_ids = sorted(key for key, count in Counter(document['questionId'] for document in documents).items() if count > 1)
baseline = json.loads((ROOT / 'output/gas-shunin-hei/production/20261027/source-baseline.json').read_text())
source_changes = [name for name, expected in baseline.items()
                  if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
now = datetime.datetime.now(datetime.timezone.utc)
work = RUN / 'convert_display' / now.strftime('%Y%m%dT%H%M%SZ')
work.mkdir(parents=True, exist_ok=False)
converted_path = work / 'converted-diagnostic.json'
converted_path.write_text(json.dumps(documents, ensure_ascii=False, indent=2) + '\n')
receipt = {'schemaVersion': 'hei-current-projection-convert-display-check/v1', 'checkedAt': now.isoformat(),
    'scope': '全522原問の現在projectionのconvert診断。全工程版・法令監査・標準独立評価・品質gateは代替しない。公開用upload-readyではない。',
    'sourceQuestionCount': len(packets), 'convertedDocumentCount': len(documents), 'sourceHashes': hashes,
    'convertErrors': errors, 'duplicateQuestionIds': duplicate_ids, 'publicProcessNoteHits': notes,
    'sourceChanges': source_changes, 'convertedPath': str(converted_path.relative_to(ROOT)),
    'convertedSha256': hashlib.sha256(converted_path.read_bytes()).hexdigest(),
    'productionWritten': False, 'systemCodeModified': False,
    'pass': not errors and not duplicate_ids and not notes and not source_changes and len(packets) == 522}
(work / 'result.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in receipt.items() if key != 'sourceHashes'}, ensure_ascii=False), flush=True)
if not receipt['pass']:
    raise SystemExit(1)
