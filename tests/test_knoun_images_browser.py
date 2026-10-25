"""RUN_KNOUN_BROWSER_TEST=1で実行する、外部通信のない小さなbrowser検証。"""
from __future__ import annotations

import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from scripts.scrape.knoun_images import advance_question


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/delayed-frame":
            time.sleep(0.5)
            body = b"frame ready"
        elif path == "/done":
            body = b"done"
        else:
            body = """<!doctype html><html><body>
                <iframe src="/delayed-frame"></iframe>
                <script>let ready = false; window.addEventListener('load', () => {ready = true;});</script>
                <form action="/done">
                  <button id="writtenQuestionUnknown" type="button" onclick="if(ready){this.hidden=true;document.querySelector('[aria-label=次へ]').hidden=false;}">わから ない</button>
                  <button aria-label="次へ" type="submit" hidden>next</button>
                </form></body></html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(os.getenv("RUN_KNOUN_BROWSER_TEST") == "1", "明示実行のheadless browser fixture")
class KnounBrowserTests(unittest.TestCase):
    def test_written_question_waits_for_load_and_uses_visible_controls(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/question", wait_until="commit")
                    advance_question(page, 0)
                    self.assertEqual(urlsplit(page.url).path, "/done")
                    self.assertEqual(page.locator("body").inner_text(), "done")
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
