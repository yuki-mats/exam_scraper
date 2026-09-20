from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch

from scripts.scrape.run_qualification_scrape import (
    SOURCE_REFRESH_SCRAPER_TYPES,
    run_scraper_command,
)


class QualificationScrapeSourceRefreshTests(unittest.TestCase):
    def test_kakomonn_uses_manifest_managed_source_refresh(self) -> None:
        self.assertIn("kakomonn", SOURCE_REFRESH_SCRAPER_TYPES)

    @patch("scripts.scrape.run_qualification_scrape.time.sleep")
    @patch("scripts.scrape.run_qualification_scrape.subprocess.run")
    def test_group_scrape_retries_transient_command_failure(
        self,
        run_mock,
        sleep_mock,
    ) -> None:
        run_mock.side_effect = [CalledProcessError(1, ["python", "code.py"]), None]

        run_scraper_command(
            ["python", "code.py"],
            cwd=Path("."),
            env={},
            group_retries=2,
        )

        self.assertEqual(run_mock.call_count, 2)
        sleep_mock.assert_called_once_with(15)

    @patch("scripts.scrape.run_qualification_scrape.time.sleep")
    @patch("scripts.scrape.run_qualification_scrape.subprocess.run")
    def test_group_retry_backoff_grows_and_caps_at_one_minute(
        self,
        run_mock,
        sleep_mock,
    ) -> None:
        run_mock.side_effect = [
            CalledProcessError(1, ["python", "code.py"]),
            CalledProcessError(1, ["python", "code.py"]),
            CalledProcessError(1, ["python", "code.py"]),
            CalledProcessError(1, ["python", "code.py"]),
            None,
        ]

        run_scraper_command(
            ["python", "code.py"],
            cwd=Path("."),
            env={},
            group_retries=4,
        )

        self.assertEqual(run_mock.call_count, 5)
        self.assertEqual(
            [call.args[0] for call in sleep_mock.call_args_list],
            [15, 30, 60, 60],
        )


if __name__ == "__main__":
    unittest.main()
