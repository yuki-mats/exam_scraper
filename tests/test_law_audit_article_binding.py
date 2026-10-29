import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.law_audit_verification import (
    _json_hash, validate_verification_bundle,
)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class LawAuditArticleBindingTest(unittest.TestCase):
    def setUp(self):
        self.text = '第二条 三 政令で定めるもの １０ 排出され、又は飛散する特定粉じん'
        locator = {'kind': 'article', 'num': '2', 'number': 2}
        self.fact = {'correctChoiceText': '正しい', 'lawId': 'law', 'article': '2',
                     'paragraph': '16', 'referenceDate': '2026-10-30',
                     'lawRevisionId': 'revision', 'articleText': self.text,
                     'articleTextHash': digest(self.text)}
        self.official = {'locator': locator, 'asOf': '2026-10-30', 'revisionId': 'revision',
                         'text': self.text, 'textHash': digest(self.text), 'textTruncated': False}
        self.item = {'lawId': 'law', 'scope': 'choice', 'choiceIndex': 0, 'role': 'current_basis',
                     'locator': locator, 'status': 'complete', 'currentSnapshot': self.official}
        self.bundle = {'question': {'sourceIdentity': {'sourceQuestionKey': 'sample:2021:q1'},
                       'currentRecord': {'examDate': '2026-10-30', 'lawReferences': [{'lawId': 'law',
                           'article': '2', 'scope': 'choice', 'choiceIndex': 0, 'role': 'current_basis'}]}},
                       'proposedFields': {'lawRevisionFacts': [{'current': self.fact}]},
                       'proposedPrimaryLawEvidence': {'currentAsOf': '2026-10-30',
                                                      'examAsOf': '2026-10-30',
                                                      'examAsOfSource': 'record.examDate', 'items': [self.item]}}
        self.item['examAsOfSource'] = 'record.examDate'
        self.root = None

    def validate(self):
        self.bundle['evidenceHash'] = _json_hash({k: v for k, v in self.bundle.items() if k != 'evidenceHash'})
        validate_verification_bundle(self.bundle, repo_root=self.root)

    def test_complete_article_with_paragraph_locator_passes(self):
        self.validate()

    def test_unrelated_later_locator_does_not_change_match(self):
        other = copy.deepcopy(self.item)
        other['locator']['num'] = '22'
        other['locator']['number'] = 22
        other['currentSnapshot']['locator'] = copy.deepcopy(other['locator'])
        self.bundle['proposedPrimaryLawEvidence']['items'].append(other)
        self.validate()

    def test_real_pattern_of_altered_text_and_copied_hash_is_rejected(self):
        self.fact['articleText'] = self.text.replace('政令で定めるもの', '政令で定めるものをいう。').replace(
            '排出され、又は飛散する特定粉じん', '特定粉じん')
        with self.assertRaises(ValueError): self.validate()

    def test_recomputed_hash_of_altered_body_is_not_official_evidence(self):
        self.fact['articleText'] += '追加'
        self.fact['articleTextHash'] = digest(self.fact['articleText'])
        with self.assertRaises(ValueError): self.validate()

    def test_scope_date_revision_locator_and_incomplete_evidence_rejected(self):
        for change in ('law', 'date', 'revision', 'locator', 'choice', 'truncated', 'partial', 'official_hash'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'law': self.item['lawId'] = 'other'
                if change == 'date': self.official['asOf'] = '2026-10-29'
                if change == 'revision': self.official['revisionId'] = 'other'
                if change == 'locator': self.fact['article'] = '3'
                if change == 'choice': self.item['choiceIndex'] = 1
                if change == 'truncated': self.official['textTruncated'] = True
                if change == 'partial': self.item['status'] = 'partial'
                if change == 'official_hash': self.official['textHash'] = '0' * 64
                with self.assertRaises(ValueError): self.validate()

    def test_excerpt_is_not_compared_as_full_article(self):
        self.fact['articleText'] = '第十六項のみ'
        self.fact['articleTextHash'] = digest(self.fact['articleText'])
        with self.assertRaises(ValueError): self.validate()

    def test_exam_time_requires_exam_snapshot(self):
        self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
        del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
        with self.assertRaises(ValueError): self.validate()
        self.item['examSnapshot'] = copy.deepcopy(self.official)
        self.validate()

    def test_legacy_minimal_and_hash_only_snapshots(self):
        del self.fact['articleText']
        self.bundle.pop('proposedPrimaryLawEvidence')
        self.validate()
        del self.fact['articleTextHash']
        self.validate()

    def test_body_without_hash_rejected(self):
        del self.fact['articleTextHash']
        with self.assertRaises(ValueError): self.validate()

    def test_unchanged_current_record_fact_is_checked_and_unset_is_honored(self):
        self.bundle['question']['currentRecord']['lawRevisionFacts'] = [{'current': self.fact}]
        del self.bundle['proposedFields']['lawRevisionFacts']
        self.fact['articleText'] += '改変'
        with self.assertRaises(ValueError): self.validate()
        self.bundle['proposedUnsetFields'] = ['lawRevisionFacts']
        self.validate()

    def test_fact_without_date_or_revision_uses_bundle_time_and_official_revision(self):
        del self.fact['referenceDate']
        del self.fact['lawRevisionId']
        self.validate()
        self.official['asOf'] = '1900-01-01'
        with self.assertRaises(ValueError): self.validate()
        self.official['asOf'] = '2026-10-30'
        self.official['revisionId'] = ''
        with self.assertRaises(ValueError): self.validate()

    def test_exam_body_cannot_bind_to_wrong_exam_date(self):
        del self.fact['referenceDate']
        del self.fact['lawRevisionId']
        self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
        del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
        self.item['examSnapshot'] = copy.deepcopy(self.official)
        self.validate()
        self.bundle['proposedPrimaryLawEvidence']['examAsOf'] = '2020-01-01'
        with self.assertRaises(ValueError): self.validate()

    def test_official_per_law_override_and_forged_or_missing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            self.root = Path(directory)
            path = self.root / 'document/sources/sample/official_exam_pdf_catalog.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'qualificationIds': ['sample'],
                'lawBasisDateOverrides': {'2021': {'law': '2021-06-15'}}}))
            self.bundle['question']['sourceIdentity'] = {'sourceQuestionKey': 'sample:2021:q1'}
            del self.fact['referenceDate']
            del self.fact['lawRevisionId']
            self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
            del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
            self.item['examSnapshot'] = copy.deepcopy(self.official)
            self.item['examSnapshot']['asOf'] = '2021-06-15'
            self.item['examAsOfSource'] = 'document/sources/sample/official_exam_pdf_catalog.json#lawBasisDateOverrides/2021/law'
            self.validate()
            self.item['examAsOfSource'] = 'record.examDate'
            with self.assertRaises(ValueError): self.validate()
            self.item['examAsOfSource'] = 'document/sources/sample/official_exam_pdf_catalog.json#lawBasisDateOverrides/2021/law'
            self.item['examSnapshot']['asOf'] = '1900-01-01'
            with self.assertRaises(ValueError): self.validate()
            self.item['examSnapshot']['asOf'] = '2021-06-15'
            self.bundle['question']['currentRecord']['lawReferences'] = []
            with self.assertRaises(ValueError): self.validate()

    def test_reference_specific_exam_date_is_bound_to_matching_reference(self):
        ref = self.bundle['question']['currentRecord']['lawReferences'][0]
        ref.update(role='exam_time_basis', referenceDate='2021-06-01')
        self.item.update(role='exam_time_basis', examAsOfSource='lawReference.referenceDate')
        del self.fact['referenceDate']
        del self.fact['lawRevisionId']
        self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
        del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
        self.item['examSnapshot'] = copy.deepcopy(self.official)
        self.item['examSnapshot']['asOf'] = '2021-06-01'
        self.validate()
        ref['referenceDate'] = '2021-07-01'
        with self.assertRaises(ValueError): self.validate()

    def test_default_date_cannot_be_forged_even_when_fact_omits_date(self):
        del self.fact['referenceDate']
        del self.fact['lawRevisionId']
        self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
        del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
        self.item['examSnapshot'] = copy.deepcopy(self.official)
        self.validate()
        self.bundle['question'].pop('sourceIdentity')
        with self.assertRaises(ValueError): self.validate()
        self.bundle['question']['sourceIdentity'] = {'sourceQuestionKey': 'sample:2021:q1'}
        self.bundle['question']['currentRecord']['examDate'] = '2020-01-01'
        self.bundle['proposedPrimaryLawEvidence']['examAsOf'] = '2022-01-01'
        self.item['examSnapshot']['asOf'] = '2022-01-01'
        with self.assertRaises(ValueError): self.validate()
        del self.bundle['question']['currentRecord']['examDate']
        with self.assertRaises(ValueError): self.validate()

    def test_catalog_basis_precedes_record_date_and_source_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            self.root = Path(directory)
            path = self.root / 'document/sources/sample/official_exam_pdf_catalog.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'qualificationIds': ['sample'],
                'examDates': {'2021': '2021-11-21'}, 'lawBasisDates': {'2021': '2021-04-01'}}))
            self.bundle['question']['sourceIdentity'] = {'sourceQuestionKey': 'sample:2021:q1'}
            self.bundle['question']['currentRecord']['examDate'] = '2021-11-21'
            del self.fact['referenceDate']
            del self.fact['lawRevisionId']
            self.bundle['proposedFields']['lawRevisionFacts'][0]['examTime'] = self.fact
            del self.bundle['proposedFields']['lawRevisionFacts'][0]['current']
            self.item['examSnapshot'] = copy.deepcopy(self.official)
            self.item['examSnapshot']['asOf'] = '2021-04-01'
            source = 'document/sources/sample/official_exam_pdf_catalog.json#lawBasisDates/2021'
            self.item['examAsOfSource'] = source
            self.bundle['proposedPrimaryLawEvidence'].update(examAsOf='2021-04-01', examAsOfSource=source)
            self.validate()
            self.bundle['proposedPrimaryLawEvidence'].update(examAsOf='2021-11-21', examAsOfSource='record.examDate')
            self.item['examAsOfSource'] = 'record.examDate'
            self.item['examSnapshot']['asOf'] = '2021-11-21'
            with self.assertRaises(ValueError): self.validate()


if __name__ == '__main__':
    unittest.main()
