from __future__ import annotations

import unittest

from scripts.common.image_storage_urls import (
    build_public_storage_url,
    canonicalize_image_field_value,
    canonicalize_storage_url,
    extract_filename_from_storage_url,
    normalize_image_url_fields,
)


class ImageStorageUrlTests(unittest.TestCase):
    def test_build_public_storage_url_uses_flat_qualification_layout(self) -> None:
        self.assertEqual(
            build_public_storage_url("sample-qualification", "qsample_q_img01.png"),
            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
            "question_images%2Fofficial%2Fsample-qualification%2Fqsample_q_img01.png?alt=media",
        )

    def test_extract_filename_from_storage_url_supports_legacy_nested_paths(self) -> None:
        legacy_url = (
            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
            "question_images%2Fofficial%2Fsample-qualification%2F85010%2Fqsample_q_img01.png?alt=media"
        )
        self.assertEqual(
            extract_filename_from_storage_url(legacy_url),
            "qsample_q_img01.png",
        )

    def test_canonicalize_storage_url_normalizes_legacy_nested_paths(self) -> None:
        legacy_url = (
            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
            "question_images%2Fofficial%2Fsample-qualification%2F2024%2Fqsample_q_img01.png?alt=media"
        )
        self.assertEqual(
            canonicalize_storage_url(legacy_url, "sample-qualification"),
            build_public_storage_url("sample-qualification", "qsample_q_img01.png"),
        )

    def test_canonicalize_image_field_value_preserves_nested_list_shape(self) -> None:
        legacy_url = (
            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
            "question_images%2Fofficial%2Fsample-qualification%2F85010%2Fqsample_ch01_img01.png?alt=media"
        )
        self.assertEqual(
            canonicalize_image_field_value([[legacy_url], []], "sample-qualification"),
            [[build_public_storage_url("sample-qualification", "qsample_ch01_img01.png")], []],
        )

    def test_normalize_image_url_fields_is_idempotent_and_ignores_other_domains(self) -> None:
        payload = {
            "questionImageUrls": [
                build_public_storage_url("sample-qualification", "qsample_q_img01.png"),
                "https://example.com/image.png",
            ],
            "children": [
                {
                    "originalQuestionChoiceImageUrls": [
                        [
                            "https://firebasestorage.googleapis.com/v0/b/repaso-rbaqy4.appspot.com/o/"
                            "question_images%2Fofficial%2Fsample-qualification%2F85010%2Fqsample_ch01_img01.png?alt=media"
                        ]
                    ]
                }
            ],
        }

        first_changes = normalize_image_url_fields(payload, "sample-qualification")
        second_changes = normalize_image_url_fields(payload, "sample-qualification")

        self.assertEqual(first_changes, 1)
        self.assertEqual(second_changes, 0)
        self.assertEqual(
            payload["questionImageUrls"][0],
            build_public_storage_url("sample-qualification", "qsample_q_img01.png"),
        )
        self.assertEqual(payload["questionImageUrls"][1], "https://example.com/image.png")
        self.assertEqual(
            payload["children"][0]["originalQuestionChoiceImageUrls"][0][0],
            build_public_storage_url("sample-qualification", "qsample_ch01_img01.png"),
        )


if __name__ == "__main__":
    unittest.main()
