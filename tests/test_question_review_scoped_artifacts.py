import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import test_scoped_canonical_context as fixtures
from tools.question_review_console.scoped_artifacts import prepare_scoped_artifacts, load_scoped_artifacts, documents_for, write_json, PRIVATE_ROOT
from tools.question_review_console.projection import sha256_json

class ScopedArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ContextMutationTests('test_source_reader_and_dependency_changes_invalidate')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.context=self.fixture.context;self.root=self.fixture.root
        self.out=self.root/PRIVATE_ROOT/'runs/test/canonical'
        self.manifest=prepare_scoped_artifacts(self.context,self.out)

    def load(self):return load_scoped_artifacts(self.root,self.out/'manifest.json')
    def change_manifest(self,change,rehash=True):
        raw=copy.deepcopy(self.manifest);change(raw)
        if rehash:raw.pop('manifestHash');raw['manifestHash']=sha256_json(raw)
        write_json(self.out/'manifest.json',raw)

    def test_actual_roundtrip_and_private_permissions(self):
        self.assertEqual(self.load()[3][0]['questionId'],'target_1')
        for p in [self.out,*self.out.iterdir()]:self.assertEqual(p.stat().st_mode&0o777,0o700 if p.is_dir() else 0o600)
        second=prepare_scoped_artifacts(self.context,self.root/PRIVATE_ROOT/'runs/test/repeat')
        self.assertEqual({k:v['contentHash'] for k,v in self.manifest['artifacts'].items()},{k:v['contentHash'] for k,v in second['artifacts'].items()})

    def test_manifest_content_hash_mode_and_ids_rejected(self):
        changes=[lambda d:d.update(projectionHash='bad'),lambda d:d.update(mode='private_candidate_unapproved'),
            lambda d:d.update(publicationReady=True),lambda d:d.update(publicationIds=['changed']),lambda d:d.update(publicationIds=['target_1','target_1'])]
        for change in changes:
            self.change_manifest(change)
            with self.assertRaises(ValueError):self.load()
        self.change_manifest(lambda d:d.update(projectionHash='bad'),False)
        with self.assertRaises(ValueError):self.load()

    def test_artifact_change_missing_escape_and_symlink_rejected(self):
        path=self.out/'uploadReady.json';raw=path.read_bytes();path.write_text('{}')
        with self.assertRaises(ValueError):self.load()
        path.unlink()
        with self.assertRaises(ValueError):self.load()
        outside=self.out.parent/'outside.json';outside.write_bytes(raw);path.symlink_to(outside)
        with self.assertRaises(ValueError):self.load()
        path.unlink();path.write_bytes(raw)
        self.change_manifest(lambda d:d['artifacts']['uploadReady'].update(path='../outside.json'))
        with self.assertRaises(ValueError):self.load()
        with self.assertRaises(ValueError):prepare_scoped_artifacts(self.context,self.root/'outside')

    def test_converter_changed_or_duplicate_ids_rejected(self):
        for ids in [['bad'],['target_1','target_1']]:
            with patch('tools.question_review_console.scoped_artifacts.convert_question_to_firestore',return_value=[{'questionId':x} for x in ids]):
                with self.assertRaisesRegex(ValueError,'publication ID'):documents_for(self.context,{})

    def test_candidate_change_invalidates_private_manifest(self):
        candidate=self.root/PRIVATE_ROOT/'runs/test/candidate.json'
        record=self.context.projection.record
        write_json(candidate,{f:record.get(f,[] if f!='lawRevisionFacts' else []) for f in ('lawReferences','explanationText','lawRevisionFacts','suggestedQuestionDetailsByChoice')})
        private=self.root/PRIVATE_ROOT/'runs/test/private'
        prepare_scoped_artifacts(self.context,private,candidate_path=candidate)
        candidate.write_text('{}')
        with self.assertRaises(ValueError):load_scoped_artifacts(self.root,private/'manifest.json')
