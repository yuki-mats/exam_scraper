"""丙種の表示本文から作業経緯だけを内部記録へ分け、既存24審査を受ける。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_public_text_review', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver


def prepare(year):
    configurations = {
        '2021': ('2021:law:問16', 14,
                 'これは出題時及び現行のガス事業法施行規則第202条第1号の適用範囲を明示する編集補足である。'),
        '2025': ('2025:seizo:問8', 28,
                 'これは液状化の発生条件を明示する編集補足であり、公式PDFには印字されていない。'),
    }
    key, page, internal_sentence = configurations[year]
    work = RUN / 'scope_corrections' / (year + '-public-text-r1')
    plan = work / 'plan.json'
    if plan.exists():
        return plan
    packet = json.loads((RUN / 'records.json').read_text())[key]
    binding = packet['sourceIdentity']
    source = next(s for s in module.load_source_record_inventory(ROOT / f'output/gas-shunin-hei/questions_json/{year}/00_source',
        qualification='gas-shunin-hei', list_group_id=year) if s.record['source_question_id'] == key)
    projected = module.QuestionInventory(ROOT).projected_input('gas-shunin-hei', year, binding['sourceRecordRef'])
    if projected.errors:
        raise ValueError(projected.errors)
    record = projected.record
    body = record['questionBodyText']
    if body.count(internal_sentence) != 1:
        raise ValueError('確認済みの作業経緯の一文を特定できない')
    changes = {'questionBodyText': body.replace(internal_sentence, '')}
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    images = [f'output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{page:02}.png',
              f'output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png']
    pdf_path = str(Path(packet['officialQuestionPdf']['path']))
    if Path(pdf_path).is_absolute():
        pdf_path = str(Path(pdf_path).relative_to(ROOT))
    evidence = {'sourceClass': 'official', 'title': f'{year}年度丙種の公式問題・正答と表示本文の照合',
        'locator': f'{pdf_path} / PDF {page}頁 / {key}。条件補足の根拠と公式原文との差は先行24審査の内部記録に保持する。',
        'verifiedAt': created, 'contentHash': driver.digest(ROOT / pdf_path)}
    transcription = ('【現在の本文と全選択肢】\n' + body + '\n' + json.dumps(record['choiceTextList'], ensure_ascii=False)
        + '\n【今回の表示候補】\n' + changes['questionBodyText']
        + '\n【表示から内部記録へ分ける作業経緯。削除して履歴を隠すのではなく、この固定入力と既存審査記録へ保持する】\n' + internal_sentence)
    canonical = {'qualificationId': 'gas-shunin-hei', 'listGroupId': year, 'originalQuestionId': binding['reviewQuestionId'],
        'officialEvidenceCandidates': [{**evidence, 'localSourcePath': pdf_path,
            'localRenderedPagePaths': images, 'localRenderedPageHashes': {p: driver.digest(ROOT / p) for p in images},
            'verifiedTranscription': transcription, 'candidateChanges': changes,
            'transcriptionRole': '学習に必要な条件補足を維持し、作業経緯の説明一文だけを公開本文から内部監査記録へ分ける候補。'}],
        'report': ('ユーザーが、公式PDFにない補足であるとの説明がアプリ利用者へ見えるかを確認した。'
            '主担当は現行convertで本文が表示問題文へ入ることを確認した。今回の候補は作業経緯の説明一文だけを表示対象から除き、'
            '学習に必要な対象範囲・地盤条件、原設問、全選択肢、正答、共通注を保持する。'
            '補足が公式原文にない事実、先行24審査、根拠資料、変更前後を本planとreview及び旧patchに残す。'
            '公式原文どおりという虚偽表示は加えない。システム実装は変更しない。'),
        'displayFormatRules': [
            '公式冊子との照合と、既に審査済みの条件補足の存在を区別する。今回は作業経緯の説明だけが変更範囲である。',
            '条件を削除・弱化しない。問題文、全選択肢、正答及び解説の意味を変える候補はholdとする。',
            '出典や編集経緯の説明は公開本文へ繰り返さず、内部のreport、transcription、review証跡で保全する。',
            '公式原文と異なる補足を公式原文だと表示しない。補足の見出しは保持する。']}
    case = 'operator-public-text-' + binding['reviewQuestionId']
    item = {'sourceQuestionId': key, 'currentRecord': record, 'sourceBinding': binding,
        'workItem': {'workId': year + '-public-text', 'qualificationId': 'gas-shunin-hei', 'listGroupId': year,
            'questionId': binding['reviewQuestionId'], 'originalQuestionId': binding['reviewQuestionId'], **binding,
            'caseIds': [case], 'caseInputHashes': {case: driver.sha256_json(canonical)},
            'caseSnapshots': [{'id': case, 'canonicalSnapshot': canonical}]},
        'evidence': [evidence], 'imagePaths': images, 'verifiedTranscription': transcription, 'candidateChanges': changes,
        'sourcePath': str(source.path.relative_to(ROOT)), 'sourceSha256': driver.digest(source.path),
        'questionPdfPath': pdf_path, 'questionPdfSha256': evidence['contentHash']}
    driver.freeze(plan, {'schemaVersion': 'gas-shunin-hei-official-correction-review-plan/v1', 'year': int(year),
        'createdAt': created, 'batchId': 'zzz-hei-direct-public-text-' + year + '-r1', 'category': 'question_content', 'items': [item]})
    return plan


if __name__ == '__main__':
    plan = prepare(sys.argv[1])
    task = driver.Driver(plan, plan.parent / 'reviews', root=ROOT)
    if sys.argv[2] == 'save':
        print(task.save(allow_save=True))
    else:
        task.phase(sys.argv[2])
