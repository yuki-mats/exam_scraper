import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tools.question_review_console.server import (
    QuestionReviewApplication,
    QuestionReviewRequestHandler,
)


class QuestionReviewServerTests(unittest.TestCase):
    def test_mutation_api_requires_session_token_and_local_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            app = QuestionReviewApplication(Path(directory))
            server = ThreadingHTTPServer(("127.0.0.1", 0), QuestionReviewRequestHandler)
            server.app = app
            port = int(server.server_address[1])
            app.set_origin("127.0.0.1", port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={"Content-Type": "application/json", "Origin": app.origin},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("session token", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.invalid",
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertIn("Origin", json.loads(response.read())["error"])
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "POST",
                    "/api/direct-edits/preview",
                    body="{}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": app.origin,
                        "X-Review-Session": app.session_token,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("questionId", json.loads(response.read())["error"])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
