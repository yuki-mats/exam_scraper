from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

from scripts.check import check_required_fields as required
from scripts.common.fill_in_blank_contract import validate_fill_in_blank_question
from scripts.common.repaso_firestore_schema import validate_question_doc
from scripts.convert.convert_merged_to_firestore import (
    convert_merged_to_firestore,
    convert_question_to_firestore,
)
from scripts.upload import upload_questions_to_firestore as uploader


def content() -> dict:
    return {
        "schemaVersion": 1,
        "segments": [
            {"type": "text", "text": "空欄："},
            {"type": "blank", "blankId": "b1", "placementId": "p1"},
        ],
        "blanks": [{"blankId": "b1", "blankIndex": 1,
                    "correctAnswerText": "答え", "acceptedAnswerTexts": ["答え"]}],
        "answerInput": {"allowedMethods": ["text_input"], "defaultMethod": "text_input"},
    }


def question() -> dict:
    return {
        "questionId": "blank-q1", "questionSetId": "qs1", "questionText": "空欄：",
        "questionType": "fill_in_blank", "fillInBlankContent": content(),
        "qualificationId": "sample", "questionTags": [],
        "isOfficial": True, "isDeleted": False, "isChoiceOnly": False, "isGroupable": False,
        "originalQuestionId": "source-q1", "originalQuestionBodyText": "空欄：",
        "correctChoiceText": "答え", "examSource": "試験, 2026年, 問1", "examYear": 2026,
    }


class FillInBlankContractTests(unittest.TestCase):
    def assert_invalid(self, data: dict, field: str) -> None:
        with self.assertRaisesRegex(ValueError, field):
            validate_fill_in_blank_question(data, doc_id="blank-q1")

    def test_valid_text_input_and_repeated_placements_do_not_mutate_data(self):
        data = question()
        data["fillInBlankContent"]["segments"].extend([
            {"type": "text", "text": " "},
            {"type": "blank", "blankId": "b1", "placementId": "p2"},
        ])
        data["fillInBlankContent"]["blanks"][0]["blankIndex"] = 4
        before = copy.deepcopy(data)
        validate_fill_in_blank_question(data, doc_id="blank-q1")
        self.assertEqual(data, before)

    def test_question_type_requires_content_and_rejects_foreign_content_or_group_flags(self):
        data = question()
        del data["fillInBlankContent"]
        self.assert_invalid(data, "fillInBlankContent")
        for other_type in ("single_choice", "true_false", "flash_card", "group_choice"):
            for value in (content(), None):
                with self.subTest(other_type=other_type, value=value):
                    data = question()
                    data.update(questionType=other_type, fillInBlankContent=value)
                    self.assert_invalid(data, "must be omitted")
                    del data["fillInBlankContent"]
                    validate_fill_in_blank_question(data, doc_id="other-q1")
        for flag in ("isGroupable", "isChoiceOnly"):
            data = question()
            data[flag] = True
            self.assert_invalid(data, flag)

    def test_malformed_nested_fields_report_the_relevant_path(self):
        changes = [
            (lambda c: c.update(schemaVersion=True), "schemaVersion"),
            (lambda c: c.update(extra=True), "unexpected field"),
            (lambda c: c.update(segments=[]), "segments"),
            (lambda c: c.update(blanks=c["blanks"] * 65), "blanks"),
            (lambda c: c["blanks"][0].update(blankIndex=True), "blankIndex"),
            (lambda c: c["blanks"][0].update(acceptedAnswerTexts=["別の答え"]), "correctAnswerText"),
            (lambda c: c["blanks"][0].update(acceptedAnswerTexts=[None]), "acceptedAnswerTexts"),
            (lambda c: c["segments"][0].update(blankId="b1"), "text segment"),
            (lambda c: c["segments"][1].update(text="混入"), "segment type"),
            (lambda c: c["answerInput"].update(allowedMethods=["text_input", "text_input"]), "duplicate"),
            (lambda c: c["answerInput"].update(allowedMethods=[{}]), "unsupported"),
            (lambda c: c["answerInput"].update(defaultMethod="word_bank"), "defaultMethod"),
        ]
        for change, field in changes:
            with self.subTest(field=field):
                data = question()
                change(data["fillInBlankContent"])
                self.assert_invalid(data, field)

    def test_missing_unused_and_duplicate_blank_identities_are_rejected(self):
        for change, field in (
            (lambda c: c["segments"][1].update(blankId="unknown"), "references"),
            (lambda c: c.update(segments=[c["segments"][0]]), "references"),
            (lambda c: c["segments"].append(copy.deepcopy(c["segments"][1])), "placementId"),
            (lambda c: c["blanks"].append(copy.deepcopy(c["blanks"][0])), "blankId"),
        ):
            with self.subTest(field=field):
                data = question()
                change(data["fillInBlankContent"])
                self.assert_invalid(data, field)

    def test_word_bank_shared_alternatives_require_simultaneous_capacity(self):
        data = question()
        c = data["fillInBlankContent"]
        c["blanks"][0].update(correctAnswerText="A", acceptedAnswerTexts=["A", "B"])
        c["blanks"].append({"blankId": "b2", "blankIndex": 2,
                            "correctAnswerText": "A", "acceptedAnswerTexts": ["A"]})
        c["segments"].append({"type": "blank", "blankId": "b2", "placementId": "p2"})
        c["answerInput"] = {"allowedMethods": ["word_bank", "text_input"], "defaultMethod": "word_bank"}
        c["wordBank"] = {"origin": "original", "choices": [
            {"choiceId": "a", "text": " A ", "maxUses": 1},
            {"choiceId": "b", "text": "B", "maxUses": 1},
        ]}
        validate_fill_in_blank_question(data, doc_id="blank-q1")
        c["wordBank"]["choices"].pop()
        self.assert_invalid(data, "cannot answer all blanks")
        c["wordBank"]["choices"][0]["maxUses"] = 2
        validate_fill_in_blank_question(data, doc_id="blank-q1")
        c["segments"].append({"type": "blank", "blankId": "b1", "placementId": "p3"})
        validate_fill_in_blank_question(data, doc_id="blank-q1")
        for change, field in (
            (lambda c: c["wordBank"].update(origin="unknown"), "origin"),
            (lambda c: c["wordBank"]["choices"][0].update(maxUses=False), "maxUses"),
            (lambda c: c["wordBank"]["choices"].append(copy.deepcopy(c["wordBank"]["choices"][0])), "choiceId"),
            (lambda c: c.update(wordBank=None), "cannot answer all blanks"),
            (lambda c: c["blanks"][1].update(blankIndex=1), "blankIndex"),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(data)
                change(invalid["fillInBlankContent"])
                self.assert_invalid(invalid, field)

    def test_converter_upload_and_schema_preserve_structured_content_and_ids(self):
        merged = {
            "original_question_id": "source-q1", "firestoreQuestionIds": ["blank-q1"],
            "questionSetId": "qs1", "questionType": "fill_in_blank", "questionBodyText": "空欄：",
            "fillInBlankContent": content(), "correctChoiceText": "正しい", "examYear": 2026,
        }
        before = copy.deepcopy(merged)
        converted, = convert_question_to_firestore(merged)
        self.assertEqual(merged, before)
        self.assertEqual(converted["questionId"], "blank-q1")
        self.assertEqual(converted["originalQuestionBodyText"], "空欄：")
        self.assertIs(converted["isGroupable"], False)
        self.assertEqual(converted["fillInBlankContent"], merged["fillInBlankContent"])
        self.assertIsNot(converted["fillInBlankContent"], merged["fillInBlankContent"])
        converted.update(qualificationId="sample", questionTags=[])
        uploader.validate_required_question_fields([converted], "converted.json")
        doc = uploader.build_doc_data(converted, datetime(2026, 10, 11))
        validate_question_doc(doc, doc_id="blank-q1")
        self.assertEqual(doc["fillInBlankContent"], before["fillInBlankContent"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "output/sample/questions_json/2026/30_merged_2/input.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"list_group_id": "2026", "question_bodies": [merged]}), encoding="utf-8")
            source_bytes = path.read_bytes()
            output = path.parent / "converted.json"
            with redirect_stdout(io.StringIO()):
                result = convert_merged_to_firestore(path, output)
                uploader.upload_questions(str(output), dry_run=True)
            self.assertEqual(result["questions"][0]["fillInBlankContent"], before["fillInBlankContent"])
            self.assertEqual(path.read_bytes(), source_bytes)
            good_output = output.read_bytes()
            del merged["fillInBlankContent"]
            path.write_text(json.dumps({"question_bodies": [merged]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source-q1.*fillInBlankContent"):
                convert_merged_to_firestore(path, output)
            self.assertEqual(output.read_bytes(), good_output)

    def test_upload_dry_run_accepts_valid_and_blocks_invalid_before_firebase(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(uploader, "init_firestore") as init:
            path = Path(temp) / "questions.json"
            data = question()
            path.write_text(json.dumps({"questions": [data]}), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                uploader.upload_questions(str(path), dry_run=True)
            data["fillInBlankContent"]["segments"][1]["blankId"] = "missing"
            path.write_text(json.dumps({"questions": [data]}), encoding="utf-8")
            for dry_run in (True, False):
                with self.assertRaisesRegex(ValueError, "blank-q1.*references"):
                    uploader.upload_questions(str(path), dry_run=dry_run)
            init.assert_not_called()

    def test_changed_blank_content_is_written_and_included_in_conflict_detection(self):
        data = question()
        old_doc = uploader.build_doc_data(data, datetime(2026, 10, 11))
        old_doc["fillInBlankContent"] = copy.deepcopy(old_doc["fillInBlankContent"])
        old_doc["fillInBlankContent"]["blanks"][0].update(
            correctAnswerText="旧答え", acceptedAnswerTexts=["旧答え"]
        )
        snapshot = mock.Mock(id="blank-q1", exists=True)
        snapshot.to_dict.return_value = old_doc
        db = mock.Mock()
        batch = db.batch.return_value
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "questions.json"
            path.write_text(json.dumps({"questions": [data]}), encoding="utf-8")
            with mock.patch.object(uploader, "init_firestore", return_value=db), \
                 mock.patch.object(uploader, "fetch_existing_question_snapshots", return_value=[snapshot]), \
                 mock.patch.object(uploader, "upsert_official_exam_years_manifest"), \
                 mock.patch.dict("os.environ", {"QUESTION_PUBLISH_EXPECTED_LIVE_HASH": ""}), \
                 redirect_stdout(io.StringIO()):
                uploader.upload_questions(str(path))
        batch.commit.assert_called_once()
        written = batch.update.call_args.args[1]
        self.assertEqual(written["fillInBlankContent"], data["fillInBlankContent"])
        self.assertNotEqual(
            uploader.firestore_live_fingerprint(["blank-q1"], {"blank-q1": old_doc}),
            uploader.firestore_live_fingerprint(["blank-q1"], {"blank-q1": written}),
        )
        other_question = {**data, "questionType": "single_choice"}
        del other_question["fillInBlankContent"]
        other_type = uploader.build_doc_data_base(other_question)
        self.assertIn("fillInBlankContent", uploader.stale_public_fields_to_delete(other_type, old_doc))

    def test_required_fields_cli_checks_merged_and_firestore_but_keeps_source_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            merged = {"questionType": "fill_in_blank", "original_question_id": "source-q1",
                      "answer_result_text": "答え", "correctChoiceText": [], "examYear": 2026,
                      "examLabel": "試験", "fillInBlankContent": content()}
            source = {"original_question_id": "source-q1", "question_url": "https://example.test/q1",
                      "answer_result_text": "答え", "questionType": "fill_in_blank"}
            for stage, directory, filename, key, record in (
                ("source", "00_source", "source.json", "question_bodies", source),
                ("merged", "30_merged_2", "merged.json", "question_bodies", merged),
                ("firestore", "40_convert", "sample_firestore_2026.json", "questions", question()),
            ):
                path = base / "2026" / directory / filename
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({key: [record]}), encoding="utf-8")
                before = path.read_bytes()
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(required.main(["--base-dir", str(base), "--stage", stage]), 0)
                self.assertEqual(path.read_bytes(), before)
                if stage == "source":
                    continue
                record["fillInBlankContent"]["blanks"][0]["correctAnswerText"] = "不一致"
                path.write_text(json.dumps({key: [record]}), encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(required.main(["--base-dir", str(base), "--stage", stage]), 1)
                self.assertIn("correctAnswerText", output.getvalue())
                self.assertIn(str(path), output.getvalue())
