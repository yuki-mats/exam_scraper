from __future__ import annotations

import unittest

from scripts.scrape.run_qualification_scrape import SOURCE_REFRESH_SCRAPER_TYPES


class QualificationScrapeSourceRefreshTests(unittest.TestCase):
    def test_kakomonn_uses_manifest_managed_source_refresh(self) -> None:
        self.assertIn("kakomonn", SOURCE_REFRESH_SCRAPER_TYPES)


if __name__ == "__main__":
    unittest.main()
