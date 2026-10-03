import unittest
from pathlib import Path
from unittest.mock import patch
from tools.question_review_console.validated_evidence_import import import_native_evidence
class EvidenceImportTests(unittest.TestCase):
    def test_corrupt_manifest_stops_before_import(self):
        with patch('tools.question_review_console.validated_evidence_import.load_scoped_artifacts',side_effect=ValueError('invalid manifest')):
            with self.assertRaisesRegex(ValueError,'invalid manifest'):
                import_native_evidence(Path('/tmp'),Path('/missing'),Path('/missing'),Path('/missing'),Path('/missing'))

    def test_each_saved_evidence_component_change_is_rejected(self):
        import tempfile,json,hashlib
        from tools.question_review_console.validated_evidence_import import validate_fixed_package
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            paths=[root/name for name in ('native-response.json','native-event.jsonl','input.json','candidate.json','sidecar.json','policy.json')]
            for path in paths:path.write_text('{}')
            manifest=root/'package.json'
            manifest.write_text(json.dumps({'files':[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}))
            validate_fixed_package(root,manifest)
            for path in paths:
                path.write_text('{"changed":true}')
                with self.subTest(component=path.name),self.assertRaisesRegex(ValueError,'package changed'):validate_fixed_package(root,manifest)
                path.write_text('{}')
            expected=hashlib.sha256(manifest.read_bytes()).hexdigest()
            manifest.write_text(manifest.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'manifest changed'):validate_fixed_package(root,manifest,expected_hash=expected)
