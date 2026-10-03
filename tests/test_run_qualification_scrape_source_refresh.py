from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch
from types import SimpleNamespace

from scripts.scrape.run_qualification_scrape import (
    SOURCE_REFRESH_SCRAPER_TYPES,
    run_scraper_command,
    main,
)


class QualificationScrapeSourceRefreshTests(unittest.TestCase):
    def test_sg_existing_group_is_refreshed_without_force(self):
        args = SimpleNamespace(output_dir=None, qualification_code='sg', config='config/scrape_presets.json',
            dry_run=False, max_questions=None, list_group_ids=['202601'], max_groups=None,
            force=False, python_executable='python', group_retries=0)
        with patch('scripts.scrape.run_qualification_scrape.parse_args', return_value=args), \
             patch('scripts.scrape.run_qualification_scrape.has_existing_source_json', return_value=True), \
             patch('scripts.check.check_sgsiken_acquisition.check_live_inventory'), \
             patch('scripts.scrape.run_qualification_scrape.run_scraper_command') as scraper, \
             patch('scripts.scrape.run_qualification_scrape.subprocess.run'):
            self.assertEqual(main(), 0)
        self.assertEqual(scraper.call_count, 1)
        self.assertTrue(scraper.call_args.args[0][1].endswith('scrape_sgsiken.py'))

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
