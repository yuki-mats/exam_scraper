from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE = importlib.import_module("scripts.merge.02_merge_questiontype")


class MergeQuestionTypeCompatibilityTests(unittest.TestCase):
    def test_no_arguments_never_mutate_the_historical_default_group(self) -> None:
        with mock.patch.object(MODULE.MERGE_ALL_MODULE, "merge_all") as merge:
            result = MODULE.main([])

        self.assertEqual(result, 2)
        merge.assert_not_called()

    def test_patch_entrypoint_delegates_to_binding_aware_full_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory) / "questions_json"
            patch = (
                base_dir
                / "2026"
                / "10_questionType_fixed"
                / "question_2026_1_questionType_fixed.json"
            )
            patch.parent.mkdir(parents=True)
            patch.write_text("[]", encoding="utf-8")

            with mock.patch.object(MODULE.MERGE_ALL_MODULE, "merge_all") as merge:
                MODULE.process_patch_file(
                    patch,
                    require_answer_result_text=False,
                )

        merge.assert_called_once_with(
            "2026",
            base_dir.resolve(),
            require_answer_result_text=False,
        )

    def test_multiple_patch_arguments_regenerate_each_group_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory) / "questions_json"
            patches = []
            for source_stem in ("question_2026_1", "question_2026_2"):
                patch = (
                    base_dir
                    / "2026"
                    / "10_questionType_fixed"
                    / f"{source_stem}_questionType_fixed.json"
                )
                patch.parent.mkdir(parents=True, exist_ok=True)
                patch.write_text("[]", encoding="utf-8")
                patches.append(patch)

            with mock.patch.object(MODULE.MERGE_ALL_MODULE, "merge_all") as merge:
                result = MODULE.main(
                    [
                        "--allow-missing-answer-result",
                        *(str(path) for path in patches),
                    ]
                )

        self.assertEqual(result, 0)
        merge.assert_called_once_with(
            "2026",
            base_dir.resolve(),
            require_answer_result_text=False,
        )

    def test_rejects_a_non_question_type_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            patch = (
                Path(directory)
                / "2026"
                / "15_correctChoiceText_fixed"
                / "question_2026_1_correctChoiceText_fixed.json"
            )
            patch.parent.mkdir(parents=True)
            patch.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "10_questionType_fixed"):
                MODULE.group_for_patch_file(patch)


if __name__ == "__main__":
    unittest.main()
