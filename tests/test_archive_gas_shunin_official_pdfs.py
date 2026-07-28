from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from scripts.scrape.archive_gas_shunin_official_pdfs import (
    SCHEMA_VERSION,
    _catalog_payload,
    build_source_specs,
    inspect_pdf,
    verify_catalog,
)


class GasShuninOfficialPdfArchiveTests(unittest.TestCase):
    def test_source_specs_cover_all_years_grades_and_documents(self) -> None:
        specs = build_source_specs()

        self.assertEqual(len(specs), 72)
        self.assertEqual(
            sorted({int(spec["year"]) for spec in specs}),
            list(range(2017, 2026)),
        )
        for year in range(2017, 2026):
            yearly = [spec for spec in specs if spec["year"] == year]
            self.assertEqual(len(yearly), 8)
            grades = {
                grade
                for spec in yearly
                for grade in spec["grades"]
            }
            self.assertEqual(grades, {"kou", "otsu", "hei"})
            self.assertEqual(
                sum(spec["documentType"] == "answer" for spec in yearly),
                3,
            )
            self.assertEqual(
                sum(
                    spec["documentType"] == "mark_sheet_question"
                    for spec in yearly
                ),
                3,
            )
            self.assertEqual(
                sum(
                    spec["documentType"] == "essay_question"
                    for spec in yearly
                ),
                2,
            )

    def test_every_source_is_bound_to_jia_and_stable_local_path(self) -> None:
        for spec in build_source_specs():
            self.assertTrue(
                spec["originalUrl"].startswith(
                    "https://www.jia-page.or.jp/files/user/doc/exam/"
                )
            )
            self.assertTrue(spec["downloadUrls"])
            self.assertEqual(
                spec["localPath"],
                (
                    "output/pdf/gas-shunin-official/"
                    f"{spec['year']}/{spec['filename']}"
                ),
            )
            if spec["year"] < 2024:
                self.assertTrue(
                    spec["downloadUrls"][0].startswith(
                        "https://web.archive.org/web/"
                    )
                )
                self.assertTrue(
                    any(
                        "id_/https://www.jia-page.or.jp/" in url
                        for url in spec["downloadUrls"]
                    )
                )
                self.assertTrue(
                    any(
                        "id_/http://www.jia-page.or.jp/" in url
                        for url in spec["downloadUrls"]
                    )
                )

    def test_pdf_inspection_and_catalog_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"
            pdf = archive / "2025" / "sample.pdf"
            pdf.parent.mkdir(parents=True)
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf.open("wb") as stream:
                writer.write(stream)

            metadata = inspect_pdf(pdf)
            entry = {
                "year": 2025,
                "era": "令和7年度",
                "grades": ["kou"],
                "documentType": "answer",
                "filename": "sample.pdf",
                "localPath": (
                    "output/pdf/gas-shunin-official/2025/sample.pdf"
                ),
                "originalUrl": "https://www.jia-page.or.jp/sample.pdf",
                "downloadUrls": ["https://www.jia-page.or.jp/sample.pdf"],
                "archiveTimestamp": None,
                "resolvedUrl": "https://www.jia-page.or.jp/sample.pdf",
                "downloadStatus": "downloaded",
                **metadata,
            }
            catalog = _catalog_payload([entry])

            self.assertEqual(catalog["schemaVersion"], SCHEMA_VERSION)
            self.assertNotIn("downloadStatus", catalog["files"][0])
            self.assertNotIn("resolvedUrl", catalog["files"][0])
            self.assertEqual(verify_catalog(catalog, archive), [])

            pdf.write_bytes(pdf.read_bytes() + b"changed")
            errors = verify_catalog(catalog, archive)
            self.assertTrue(any("sha256" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
