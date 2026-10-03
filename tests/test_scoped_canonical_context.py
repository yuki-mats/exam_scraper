import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from scripts.common.scoped_canonical_context import ScopedCanonicalContext, prove_outside, identity_pairs
from scripts.common.question_identity import SourceIdentityBinding, SourceRecordIdentity
from scripts.merge.patch_views import PatchArtifactEntry

ROOT = Path(__file__).resolve().parents[1]

class OutsideProofTests(unittest.TestCase):
    def setUp(self):
        self.binding = SourceIdentityBinding.from_values('sample:g:target', 'target', 'chunk.json#0')
        self.other = SourceIdentityBinding.from_values('sample:g:other', 'other', 'chunk.json#1')
        self.sources = [SourceRecordIdentity(binding=b, aliases=frozenset({*b.as_tuple(), url}), source_stem='chunk')
            for b, url in [(self.binding, 'https://example.test/questions/1'), (self.other, 'https://example.test/questions/2')]]
        self.closure = {*self.binding.as_tuple(), 'https://example.test/questions/1', *[f'target_{i}' for i in range(1,6)]}
        self.record = {'original_question_id':'orphan','question_url':'https://example.test/questions/3'}

    def proof(self, record):
        records = [{'original_question_id':'target','question_url':'https://example.test/questions/1'},
            {'original_question_id':'other','question_url':'https://example.test/questions/2'}, record]
        return prove_outside(PatchArtifactEntry(Path('chunk_questionType_fixed.json'), record, 'chunk'),
            identities=self.sources,target=self.binding,target_aliases=self.closure,host='example.test',pairs=identity_pairs(records,'example.test'))

    def test_shared_chunk_is_record_identity_proof(self):
        self.assertEqual(self.proof(self.record)['singleResolver']['unmatchedCount'],1)

    def test_all_target_aliases_and_partial_bindings_reject(self):
        for field, value in [('original_question_id','target'),('question_url','https://example.test/questions/1'),
            ('sourceQuestionKey',self.binding.source_question_key),('sourceRecordRef',self.binding.source_record_ref),
            ('reviewQuestionId',self.binding.review_question_id),*[('questionId',f'target_{i}') for i in range(1,6)]]:
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                self.proof({**self.record,field:value})
        with self.assertRaises(ValueError):
            self.proof({**self.record,'sourceQuestionKey':'sample:g:missing'})

    def test_bad_urls_and_alias_conflicts_reject(self):
        for url in [None,'','https://example.test','https://example.test/questions/3?x=1',
                    'https://example.test/questions/1#x','https://example.test/questions/03','http://example.test/questions/3']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.proof({**self.record,'question_url':url})
        with self.assertRaises(ValueError):
            self.proof({**self.record,'questionUrl':'https://example.test/questions/4'})
        with self.assertRaises(ValueError):
            identity_pairs([self.record,{**self.record,'question_url':'https://example.test/questions/4'}],'example.test')
        with self.assertRaises(ValueError):
            identity_pairs([self.record,{**self.record,'original_question_id':'another'}],'example.test')

    def test_complete_other_binding_and_nonexistent_binding(self):
        r={'original_question_id':'other','question_url':'https://example.test/questions/2',**self.other.as_mapping()}
        self.assertEqual(self.proof(r)['singleResolver']['unmatchedCount'],0)
        for change in [{'sourceRecordRef':'missing.json#0'}, {'original_question_id':'orphan'},
                       {'source_question_key':'contradictory'}, {'questionId':'target_5'}]:
            with self.subTest(change=change),self.assertRaises(ValueError):self.proof({**r,**change})

class ContextMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name).resolve()/'work';self.root=Path(self.temp.name).resolve()/'root'
        for directory in (self.work,self.root):directory.mkdir()
        self.group=self.work/'output/sample/questions_json/g';source=self.group/'00_source/chunk.json'
        source.parent.mkdir(parents=True)
        source.write_text(json.dumps({'question_bodies':[{'original_question_id':'target','question_url':'https://example.test/questions/1','questionBodyText':'問題','choiceTextList':['肢'],'correctChoiceText':['正しい'],'examYear':2026,'questionType':'true_false','questionIntent':'select_correct','explanationText':['正しい。基準に適合する。'],'questionSetId':'set','isLawRelated':False,'lawGroundedExplanationNotNeeded':True,'suggestedQuestionDetailsByChoice':[]}]}))
        category=self.work/'output/sample/category/category.json';category.parent.mkdir(parents=True)
        category.write_text(json.dumps({'questionSets':[{'questionSetId':'set'}]}))
        self.binding=SourceIdentityBinding.from_values('sample:g:target','target','chunk.json#0')
        # Copy only reader files needed for version fingerprinting.
        for p in ('scripts/common/question_identity.py','scripts/merge/patch_views.py','scripts/merge/record_projection.py',
            'scripts/merge/question_issue_corrections.py','tools/question_review_console/projection.py',
            'scripts/common/scoped_canonical_context.py','scripts/convert/convert_merged_to_firestore.py',
            'config/question_issue_reports.json','scripts/merge/merge_utils.py','tools/question_review_console/scoped_artifacts.py',
            'tools/question_review_console/inventory.py','tools/question_review_console/evaluation.py','tools/question_review_console/publisher.py',
            'tools/question_review_console/validated_evidence_import.py','config/scrape_presets.json',
            'config/qualification_display_catalog.json','config/qualification_rules.json','config/question_maintenance_workflow.toml',
            'config/requirements/required_fields.toml','tools/question_review_console/evaluation_result.schema.json'):
            dest=self.root/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/p,dest)
        self.context=ScopedCanonicalContext(canonical_root=self.work,overlay_root=self.root,qualification='sample',list_group_id='g',binding=self.binding,publication_ids=['target_1'])

    def test_source_reader_and_dependency_changes_invalidate(self):
        for path in [self.group/'00_source/chunk.json',self.root/'scripts/merge/merge_utils.py',self.work/'output/sample/category/category.json']:
            old=path.read_bytes();path.write_bytes(old+b'\n')
            with self.assertRaises(ValueError):self.context.assert_unchanged()
            path.write_bytes(old)
        for path in [self.work/'output/sample/category/new.json',self.work/'output/sample/question_images/new.png']:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
            with self.assertRaises(ValueError):self.context.assert_unchanged()
            path.unlink()

    def test_source_and_candidate_deletion_invalidate(self):
        source=self.group/'00_source/chunk.json';raw=source.read_bytes();source.unlink()
        with self.assertRaises(ValueError):self.context.assert_unchanged()
        source.write_bytes(raw)
        patch=self.group/'10_questionType_fixed/chunk_questionType_fixed_20260101_0101.json'
        patch.parent.mkdir(parents=True);patch.write_text('[]')
        context=self.new_context();patch.unlink()
        with self.assertRaises(ValueError):context.assert_unchanged()

    def new_context(self):
        return ScopedCanonicalContext(canonical_root=self.work,overlay_root=self.root,qualification='sample',list_group_id='g',binding=self.binding,publication_ids=['target_1'])

    def test_identical_overlay_dedup_collision_and_stale_before_hash(self):
        from scripts.merge.question_issue_corrections import load_category_configs,question_issue_record_hash
        record=self.context.target.record
        payload={'schemaVersion':'question-issue-correction/v1','origin':'user_problem_report','category':'correct_answer','entries':[
            {**self.binding.as_mapping(),'original_question_id':'target','expectedBeforeHash':question_issue_record_hash(record,load_category_configs()['correct_answer']),
             'changes':{'correctChoiceText':['正しい']}}]}
        paths=[root/'24_questionIssueCorrections/same.json' for root in (self.group,self.root/'output/sample/questions_json/g')]
        for p in paths:p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(payload))
        context=self.new_context();self.assertEqual(len(context.selected['questionIssueCorrection']),1)
        paths[1].write_text(json.dumps({**payload,'extra':'collision'}))
        with self.assertRaisesRegex(ValueError,'collision'):self.new_context()
        paths[1].write_bytes(paths[0].read_bytes())
        payload['entries'][0]['expectedBeforeHash']='stale'
        for p in paths:p.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):self.new_context()
        raw=copy.deepcopy(self.context.manifest);raw['publicationIds']=['changed']
        with self.assertRaises(ValueError):ScopedCanonicalContext.from_manifest(raw)

    def test_candidate_addition_and_each_overlay_root_invalidate(self):
        for path in [self.group/'10_questionType_fixed/chunk_questionType_fixed_20260101_0101.json',
                     self.group/'24_questionIssueCorrections/new.json',
                     self.root/'output/sample/questions_json/g/24_questionIssueCorrections/new.json']:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[]')
            with self.assertRaises(ValueError):self.context.assert_unchanged()
            path.unlink()
