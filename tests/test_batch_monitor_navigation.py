from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "tools/question_review_console/static"


class BatchMonitorNavigationTests(unittest.TestCase):
    def test_question_maintenance_links_to_monitor_without_control_actions(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        javascript = (STATIC / "monitor-link.js").read_text(encoding="utf-8")

        self.assertIn('id="batch-monitor-link"', html)
        self.assertIn('href="/monitor"', html)
        self.assertIn('src="/monitor-link.js?v=batch-monitor-v1"', html)
        self.assertIn('query.set("qualification", qualification)', javascript)
        self.assertIn('qualificationSelect?.addEventListener("change"', javascript)
        self.assertNotIn("/api/monitor", javascript)
        self.assertNotIn("fetch(", javascript)


if __name__ == "__main__":
    unittest.main()
