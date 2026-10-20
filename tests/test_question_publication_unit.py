import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.common.question_publication_unit import publication_unit, active_unit_document_ids, UNIT_FIELDS
from tools.question_bank import feedback_verified_save as saver


class PublicationUnitTests(unittest.TestCase):
    def setUp(self):
        self.unit = {'qualificationId':'gas-shunin-otsu','examYear':2024,'originalQuestionId':'shared'}
        self.active = {**self.unit,'isDeleted':False,'isChoiceOnly':False}

    def test_explicit_official_unit_never_infers_year_or_qualification_from_id(self):
        self.assertEqual(publication_unit(self.active), self.unit)
        for key, values in {'examYear':[None,'2024',True,1899,2101], 'qualificationId':[None,'',' gas '], 'originalQuestionId':[None,'',' shared']}.items():
            for value in values:
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    publication_unit({**self.active,key:value})

    def test_foreign_qualification_or_year_is_a_separate_unit(self):
        rows=[('target',self.active),('kou',{**self.active,'qualificationId':'gas-shunin-kou'}),('old',{**self.active,'examYear':2023}),('deleted',{**self.active,'isDeleted':True}),('choice',{**self.active,'isChoiceOnly':True})]
        self.assertEqual(active_unit_document_ids(rows,self.unit), {'target'})

    def test_unknown_active_identity_duplicate_and_wrong_query_are_rejected(self):
        for fields in ({**self.active,'examYear':None},{**self.active,'qualificationId':''},{**self.active,'originalQuestionId':'another'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                active_unit_document_ids([('unknown',fields)],self.unit)
        with self.assertRaises(ValueError):
            active_unit_document_ids([('same',self.active),('same',self.active)],self.unit)
        with self.assertRaises(ValueError):
            active_unit_document_ids([], {**self.unit,'unknown':True})

    def test_reader_qualified_membership_preserves_requested_mask_and_old_default(self):
        values={'target':dict(self.active),'kou':{**self.active,'qualificationId':'gas-shunin-kou'},'old':{**self.active,'examYear':2023}}
        def snap(identity):
            return SimpleNamespace(id=identity,exists=True,update_time=SimpleNamespace(rfc3339=lambda:'2026-10-20T00:00:00.123456789Z'),to_dict=lambda:dict(values[identity]))
        class Database:
            def collection(db,_): return db
            def document(db,i): return i
            def get_all(db,refs,field_paths):
                db.mask=field_paths
                return [snap(i) for i in refs]
            def where(db,**kwargs):return db
            def select(db,fields):db.membership_mask=fields;return db
            def stream(db):return [snap(i) for i in values]
        db=Database(); requested=['originalQuestionId','isDeleted','isChoiceOnly']
        with patch.object(saver,'FirestoreReadback',return_value=SimpleNamespace(_database=lambda:db)):
            result=saver._snapshots(['target'],requested,expected_unit=self.unit)
            self.assertEqual(set(result['target']['fields']),set(requested))
            self.assertTrue(set(UNIT_FIELDS)<=set(db.mask))
            self.assertTrue(set(UNIT_FIELDS)<=set(db.membership_mask))
            with self.assertRaisesRegex(ValueError,'membership changed'):
                saver._snapshots(['target'],requested)
            with self.assertRaisesRegex(ValueError,'unit differs'):
                saver._snapshots(['target','kou'],requested,expected_unit=self.unit)
            values['extra']=dict(self.active)
            with self.assertRaisesRegex(ValueError,'membership changed'):
                saver._snapshots(['target'],requested,expected_unit=self.unit)
            del values['extra'];values['target']['isDeleted']=True
            with self.assertRaisesRegex(ValueError,'membership changed'):
                saver._snapshots(['target'],requested,expected_unit=self.unit)


if __name__ == '__main__': unittest.main()
