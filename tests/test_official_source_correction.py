from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.common.question_identity import (
    SourceIdentityBinding,
    SourceRecordIdentity,
)
from tools.question_review_console.official_source_correction import (
    AppServerReviewExecutor,
    OfficialSourceCorrectionService,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "question_issue_reports.json"


class OfficialSourceCorrectionTests(unittest.TestCase):
    def test_app_server_executor_uses_dedicated_read_only_turn(self) -> None:
        class AppServer:
            def run_turn(self, prompt, **options):
                self.prompt = prompt
                self.options = options
                return SimpleNamespace(
                    final_message='{"decision":"hold"}',
                    changed_files=(),
                )

        with tempfile.TemporaryDirectory() as directory:
            app_server = AppServer()
            logs: list[str] = []
            executor = AppServerReviewExecutor(
                app_server,
                repo_root=Path(directory),
                qualification="sample",
                current_record={"choiceTextList": ["A", "B"]},
                evidence_hash="a" * 64,
                evidence_title="公式問題",
                evidence_locator="問1",
                evidence_relative_path="tmp/official.png",
                evidence_verified_at="2026-07-28T00:00:00Z",
                emit=logs.append,
            )

            result = executor.execute(
                work_id="work-1",
                phase="blind_a",
                prompt="review this",
                replacements={},
            )

        self.assertEqual(result, {"decision": "hold"})
        self.assertEqual(app_server.prompt, "review this")
        self.assertEqual(app_server.options["work_type"], "official_source_review")
        self.assertEqual(app_server.options["sandbox"], "read-only")
        self.assertEqual(app_server.options["turn_group"], "sample")
        self.assertTrue(logs)

    def test_app_server_executor_removes_noop_changes_and_normalizes_evidence(
        self,
    ) -> None:
        class AppServer:
            def run_turn(self, _prompt, **_options):
                return SimpleNamespace(
                    final_message=json.dumps(
                        {
                            "proposedChanges": {
                                "questionBodyText": "修正後",
                                "choiceTextList": ["イ", "ロ"],
                            },
                            "evidence": [
                                {
                                    "sourceClass": "official",
                                    "title": "公式問題 レンダリング画像",
                                    "locator": "表記ゆれ",
                                    "verifiedAt": "2026-07-28T00:00:01Z",
                                    "contentHash": "a" * 64,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    changed_files=(),
                )

        executor = AppServerReviewExecutor(
            AppServer(),
            repo_root=REPO_ROOT,
            qualification="sample",
            current_record={
                "questionBodyText": "修正前",
                "choiceTextList": ["イ", "ロ"],
            },
            evidence_hash="a" * 64,
            evidence_title="公式問題",
            evidence_locator="問1",
            evidence_relative_path="tmp/official.png",
            evidence_verified_at="2026-07-28T00:00:00Z",
            emit=lambda _message: None,
        )

        result = executor.execute(
            work_id="work-1",
            phase="blind_a",
            prompt="review this",
            replacements={},
        )

        self.assertEqual(
            result["proposedChanges"],
            {"questionBodyText": "修正後"},
        )
        self.assertEqual(
            result["evidence"][0]["locator"],
            "tmp/official.png / 問1",
        )
        self.assertEqual(result["evidence"][0]["title"], "公式問題")
        self.assertEqual(
            result["evidence"][0]["verifiedAt"],
            "2026-07-28T00:00:00Z",
        )

    def test_fix_writes_one_verified_24_patch_without_changing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "tmp" / "official.png"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_bytes(b"official-page")
            evidence_hash = hashlib.sha256(b"official-page").hexdigest()
            source_path = (
                root
                / "output/sample/questions_json/2026/00_source/question_2026.json"
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text('{"source":"immutable"}\n', encoding="utf-8")
            source_before = source_path.read_bytes()
            current_path = (
                root
                / "output/sample/questions_json/2026/30_merged_2/"
                "question_2026_merged.json"
            )
            current_path.parent.mkdir(parents=True)
            current_path.write_text("{}\n", encoding="utf-8")
            binding = SourceIdentityBinding.from_values(
                "sample:2026:q1",
                "q1",
                "question_2026.json#0",
            )
            source_identity = SourceRecordIdentity(
                binding=binding,
                aliases=frozenset(binding.as_tuple()),
                source_stem="question_2026",
            )
            current_record = {
                "original_question_id": "q1",
                "public_question_id": "q1",
                "questionBodyText": "修正前",
                "choiceTextList": ["誤記", "選択肢2"],
                "questionType": "group_choice",
                "questionIntent": "select_correct",
                "correctChoiceText": ["間違い", "正しい"],
            }
            finder_calls: list[dict[str, object]] = []

            def record_finder(work_item, *, output_root):
                finder_calls.append(
                    {"workItem": work_item, "outputRoot": output_root}
                )
                return current_record, current_path, source_identity

            def review_runner(work_item, **options):
                candidate = work_item["caseSnapshots"][0]["canonicalSnapshot"][
                    "officialEvidenceCandidates"
                ][0]
                evidence = {
                    "sourceClass": "official",
                    "locator": (
                        f'{candidate["localRenderedPagePath"]} / '
                        f'{candidate["locator"]}'
                    ),
                    "title": candidate["title"],
                    "verifiedAt": "2026-07-28T00:00:00Z",
                    "contentHash": candidate["contentHash"],
                }
                changes = {
                    "questionBodyText": "修正後",
                    "choiceTextList": ["公式表記", "選択肢2"],
                }
                return (
                    {"slot": "A", "proposedChanges": changes, "evidence": [evidence]},
                    {"slot": "B", "proposedChanges": changes, "evidence": [evidence]},
                    {
                        "decision": "fix",
                        "changes": changes,
                        "evidence": [evidence],
                    },
                )

            verifier_calls: list[Path] = []

            def patch_verifier(path, **_options):
                verifier_calls.append(path)
                return {
                    **current_record,
                    "questionBodyText": "修正後",
                    "choiceTextList": ["公式表記", "選択肢2"],
                }

            service = OfficialSourceCorrectionService(
                root,
                app_server=object(),
                config_path=CONFIG_PATH,
                review_runner=review_runner,
                record_finder=record_finder,
                patch_verifier=patch_verifier,
            )
            result = service.run(
                {
                    "id": "ui-q1",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "originalQuestionId": "q1",
                    "sourceQuestionKey": "sample:2026:q1",
                    "sourceRecordRef": "question_2026.json#0",
                    "stateHash": "a" * 64,
                },
                state_hash="a" * 64,
                evidence_path=str(evidence_path),
                evidence_title="2026年度 公式問題冊子",
                evidence_locator="問1",
                verified_transcription="問題文: 修正後\n選択肢1: 公式表記",
                emit=lambda _message: None,
            )

            patch_path = root / result["patchPath"]
            patch = json.loads(patch_path.read_text(encoding="utf-8"))

            self.assertEqual(result["decision"], "fix")
            self.assertEqual(result["changedFields"], ["choiceTextList", "questionBodyText"])
            self.assertTrue(patch_path.is_file())
            self.assertEqual(patch_path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(patch["entries"][0]["changes"]["questionBodyText"], "修正後")
            self.assertEqual(
                patch["entries"][0]["evidence"][0]["contentHash"],
                evidence_hash,
            )
            self.assertEqual(
                patch["entries"][0]["sourceRecordRef"],
                "question_2026.json#0",
            )
            self.assertEqual(source_path.read_bytes(), source_before)
            self.assertEqual(len(finder_calls), 1)
            self.assertEqual(len(verifier_calls), 1)

    def test_pdf_evidence_is_rendered_to_the_locator_page_before_review(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "official.pdf"
            evidence_path.write_bytes(b"%PDF-test")
            current_path = root / "current.json"
            current_path.write_text("{}\n", encoding="utf-8")
            binding = SourceIdentityBinding.from_values(
                "sample:2026:q1",
                "q1",
                "question_2026.json#0",
            )
            source_identity = SourceRecordIdentity(
                binding=binding,
                aliases=frozenset(binding.as_tuple()),
                source_stem="question_2026",
            )
            current_record = {
                "original_question_id": "q1",
                "questionBodyText": "修正前",
                "choiceTextList": ["誤記"],
            }
            rendered: list[tuple[Path, int, Path]] = []

            def page_renderer(source, page_index, target):
                rendered.append((source, page_index, target))
                target.write_bytes(b"rendered-page-26")

            def review_runner(work_item, **_options):
                candidate = work_item["caseSnapshots"][0]["canonicalSnapshot"][
                    "officialEvidenceCandidates"
                ][0]
                self.assertEqual(
                    candidate["localSourcePath"],
                    "official.pdf",
                )
                self.assertTrue(
                    candidate["localRenderedPagePath"].endswith(
                        "/official_page_0026.png"
                    )
                )
                self.assertEqual(
                    candidate["localRenderedPageHash"],
                    hashlib.sha256(b"rendered-page-26").hexdigest(),
                )
                return ({}, {}, {"decision": "hold"})

            service = OfficialSourceCorrectionService(
                root,
                app_server=object(),
                config_path=CONFIG_PATH,
                review_runner=review_runner,
                record_finder=lambda *_args, **_kwargs: (
                    current_record,
                    current_path,
                    source_identity,
                ),
                page_renderer=page_renderer,
            )
            result = service.run(
                {
                    "id": "ui-q1",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "originalQuestionId": "q1",
                    "sourceQuestionKey": "sample:2026:q1",
                    "sourceRecordRef": "question_2026.json#0",
                    "stateHash": "a" * 64,
                },
                state_hash="a" * 64,
                evidence_path=str(evidence_path),
                evidence_title="2026年度 公式問題冊子",
                evidence_locator="PDF 26ページ 問1",
                verified_transcription="公式転記",
                emit=lambda _message: None,
            )

            self.assertEqual(result["decision"], "hold")
            self.assertEqual(len(rendered), 1)
            self.assertEqual(rendered[0][0], evidence_path.resolve())
            self.assertEqual(rendered[0][1], 25)
            self.assertTrue(rendered[0][2].is_file())

    def test_pdf_evidence_requires_an_explicit_pdf_page_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "official.pdf"
            evidence_path.write_bytes(b"%PDF-test")
            service = OfficialSourceCorrectionService(
                root,
                app_server=object(),
                config_path=CONFIG_PATH,
            )
            with self.assertRaisesRegex(ValueError, "PDF 26ページ"):
                service.run(
                    {
                        "id": "ui-q1",
                        "qualification": "sample",
                        "listGroupId": "2026",
                        "originalQuestionId": "q1",
                        "stateHash": "a" * 64,
                    },
                    state_hash="a" * 64,
                    evidence_path=str(evidence_path),
                    evidence_title="2026年度 公式問題冊子",
                    evidence_locator="問1",
                    verified_transcription="公式転記",
                    emit=lambda _message: None,
                )


if __name__ == "__main__":
    unittest.main()
