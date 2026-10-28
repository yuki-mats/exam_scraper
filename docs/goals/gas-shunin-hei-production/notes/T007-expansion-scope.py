"""比べる液体量が未指定の設問について、同質量の条件補足を既存24審査へ渡す。"""
import datetime
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'output/gas-shunin-hei/direct_publication/20261027'
spec = importlib.util.spec_from_file_location('hei_expansion_scope_review', ROOT / 'docs/goals/gas-shunin-hei-production/notes/T007-note-text.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
driver = module.driver


def prepare(year):
    # 個別の原問を直視した対象だけを指定する。選択肢・正答を別問から転用しない。
    pages_by_year = {'2021': [19, 20], '2025': [24]}
    pages = pages_by_year[year]
    key = year + ':seizo:問1'
    work = RUN / 'scope_corrections' / (year + '-seizo-01-expansion-mass-r1')
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
    current = projected.record
    body = current['questionBodyText']
    if '【比較条件】' in body:
        raise ValueError('既存の条件補足があり、重複させない')
    note = '【比較条件】\n第1肢の体積増加量は、液体のプロパンと水を同じ質量で比較した概数とする。'
    changes = {'questionBodyText': body + '\n\n' + note}
    calculation_path = RUN / 'legal/propane-water-expansion-coolprop8-direct-calculation.json'
    calculation = json.loads(calculation_path.read_text())
    created = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pdf_path = Path(packet['officialQuestionPdf']['path'])
    if pdf_path.is_absolute():
        pdf_path = pdf_path.relative_to(ROOT)
    images = [f'output/gas-shunin-hei/pdf_review/{year}/mark_sheet_question/page-{n:02}.png' for n in pages]
    images.append(f'output/gas-shunin-hei/pdf_review/{year}/answer/page-01.png')
    evidence = {'sourceClass': 'official', 'title': f'{year}年度丙種公式問題・正答の原文照合',
        'locator': f'{pdf_path} / PDF {pages}頁 / {key}。原文には第1肢の比較量の指定がない。',
        'verifiedAt': created, 'contentHash': driver.digest(ROOT / pdf_path)}
    transcription = ('【公式原設問と全5肢・変更なし】\n' + body + '\n'
        + json.dumps(current['choiceTextList'], ensure_ascii=False)
        + '\n【主担当の条件補足候補。公式PDFに印字された文章ではない】\n' + note
        + '\n【公式原文とは別の主担当の物性再現計算。実NIST値の取得ではなくCoolProp 8.0.0によるEOS計算】\n'
        + json.dumps(calculation, ensure_ascii=False)
        + '\n【計算の保存先と固定hash】\n' + str(calculation_path.relative_to(ROOT)) + ' / ' + driver.digest(calculation_path))
    canonical = {'qualificationId': 'gas-shunin-hei', 'listGroupId': year, 'originalQuestionId': binding['reviewQuestionId'],
        'officialEvidenceCandidates': [{**evidence, 'localSourcePath': str(pdf_path),
            'localRenderedPagePaths': images, 'localRenderedPageHashes': {p: driver.digest(ROOT / p) for p in images},
            'verifiedTranscription': transcription, 'candidateChanges': changes,
            'transcriptionRole': '公式原文の転記と、主担当の再現計算・条件補足案を明確に分けた審査入力。'}],
        'report': ('第1肢は15℃から60℃でのプロパンの体積増加量を水の約20倍とするが、比較する初期量の指定がない。'
            'CoolProp 8.0.0のHEOSで両液の15℃・60℃を別々に再現計算した。飽和液の比容積差を同質量で比較すると約22.63倍、'
            '両液3MPa一定では約21.70倍となる。一方、同初期体積で比較すると約11.49倍又は11.13倍であり、比較基準を混同できない。'
            'これはEOSによる計算値であり、出題者の意図が同質量だったという証明ではない。主担当は原設問を変更・削除せず、'
            '初学者が同体積比較と混同しないよう、同質量の概数比較という条件を本文末に明示する編集補足を提案する。'
            '独立審査では、この補足が妥当な学習条件の明示か、それとも正答を保つための不適切な読み替えかを判断する。'
            '採用不能ならholdとし、原問と全肢を保持する。EOS出典は実装元の https://coolprop.org/fluid_properties/fluids/n-Propane.html '
            'でLemmon, McLinden, Wagner (2009), doi:10.1021/je900217vを確認。論文本文の直接取得は403であり読了とは主張しない。'
            '水の実装EOSはWagner-JPCRD-2002。式・単位・両比較条件・全数値・実装version及びgitRevisionは添付計算記録に固定した。'
            '作業経緯はこの内部記録に残し、公開本文には学習に必要な比較条件だけを含める。システムcodeを変更しない。'),
        'displayFormatRules': [
            '公式PDFのOCR訂正とは扱わず、原文にない条件を追加する編集補足として採否を判断する。',
            '同質量と同初期体積の二つの計算を独立に確かめ、約20倍という概数が妥当か評価する。実物性の再現計算を公式出題意図の証明と混同しない。',
            '正答番号を維持するためだけの補足や、条件の過剰な決め打ちなら承認しない。成立しない場合はholdとする。',
            '変更は本文末の比較条件だけ。原設問、全5肢、正答番号及びIDは保持する。全肢の正誤・解説の完成とは別の審査である。']}
    case = 'operator-expansion-mass-' + binding['reviewQuestionId']
    item = {'sourceQuestionId': key, 'currentRecord': current, 'sourceBinding': binding,
        'workItem': {'workId': year + '-seizo-q01-expansion-mass', 'qualificationId': 'gas-shunin-hei', 'listGroupId': year,
            'questionId': binding['reviewQuestionId'], 'originalQuestionId': binding['reviewQuestionId'], **binding,
            'caseIds': [case], 'caseInputHashes': {case: driver.sha256_json(canonical)},
            'caseSnapshots': [{'id': case, 'canonicalSnapshot': canonical}]},
        'evidence': [evidence], 'imagePaths': images, 'verifiedTranscription': transcription, 'candidateChanges': changes,
        'sourcePath': str(source.path.relative_to(ROOT)), 'sourceSha256': driver.digest(source.path),
        'questionPdfPath': str(pdf_path), 'questionPdfSha256': evidence['contentHash']}
    driver.freeze(plan, {'schemaVersion': 'gas-shunin-hei-official-correction-review-plan/v1', 'year': int(year),
        'createdAt': created, 'batchId': 'zzz-hei-direct-scope-' + year + '-seizo-01-expansion-mass-r1',
        'category': 'question_content', 'items': [item]})
    return plan


if __name__ == '__main__':
    plan = prepare(sys.argv[1])
    task = driver.Driver(plan, plan.parent / 'reviews', root=ROOT)
    print(task.save(allow_save=True)) if sys.argv[2] == 'save' else task.phase(sys.argv[2])
