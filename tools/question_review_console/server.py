from __future__ import annotations

import argparse
import copy
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.firestore_readback import (
    PRODUCTION_PROJECT_ID,
    FirestoreReadback,
)
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.patch_editor import DirectEditError, PatchEditor
from tools.question_review_console.review_store import ReviewStore


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class ApiError(ValueError):
    def __init__(self, status: int, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.details = details


class QuestionReviewApplication:
    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo_root = repo_root.resolve()
        self.session_token = secrets.token_urlsafe(32)
        self.inventory = QuestionInventory(self.repo_root)
        self.reviews = ReviewStore(self.repo_root)
        self.editor = PatchEditor(self.repo_root)
        self.firestore = FirestoreReadback()
        self.live_results: dict[str, dict[str, Any]] = {}
        self._live_lock = threading.RLock()
        self.origin = ""

    def set_origin(self, host: str, port: int) -> None:
        self.origin = f"http://{host}:{port}"

    def get(self, path: str, query: Mapping[str, list[str]]) -> tuple[int, Any]:
        if path == "/api/session":
            return HTTPStatus.OK, {
                "sessionToken": self.session_token,
                "projectId": PRODUCTION_PROJECT_ID,
                "readOnlyFirestore": True,
            }
        if path == "/api/inventory":
            return HTTPStatus.OK, self.inventory.inventory()
        if path == "/api/questions":
            return HTTPStatus.OK, self._questions(query)
        if path.startswith("/api/questions/"):
            suffix = path.removeprefix("/api/questions/")
            if suffix.endswith("/fingerprint"):
                question_id = suffix.removesuffix("/fingerprint")
                question = self._question(question_id, query)
                decorated = self._decorate(question)
                return HTTPStatus.OK, {
                    "id": decorated["id"],
                    "stateHash": decorated["stateHash"],
                    "reviewStatus": decorated["reviewStatus"],
                    "issueCodes": decorated["issueCodes"],
                }
            question = self._question(suffix, query)
            return HTTPStatus.OK, self._decorate(question)
        raise ApiError(HTTPStatus.NOT_FOUND, "APIが見つかりません。")

    def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        if path.startswith("/api/questions/") and path.endswith("/live-readback"):
            question_id = path.removeprefix("/api/questions/").removesuffix(
                "/live-readback"
            )
            question = self._question(question_id, {})
            result = self.firestore.read_question(question)
            with self._live_lock:
                self.live_results[question_id] = result
            return HTTPStatus.OK, result

        if path == "/api/reviews":
            question = self._decorate(
                self._question(str(body.get("questionId") or ""), {})
            )
            request = body.get("review")
            if not isinstance(request, Mapping):
                raise ApiError(HTTPStatus.BAD_REQUEST, "reviewを指定してください。")
            if not str(request.get("note") or "").strip():
                raise ApiError(HTTPStatus.BAD_REQUEST, "指摘内容を入力してください。")
            requested_status = str(body.get("status") or "awaiting_codex")
            if requested_status not in {"needs_review", "awaiting_codex"}:
                raise ApiError(HTTPStatus.BAD_REQUEST, "review作成状態が不正です。")
            review = self.reviews.create(question, request, status=requested_status)
            return HTTPStatus.CREATED, review

        if path.startswith("/api/reviews/") and path.endswith("/status"):
            review_id = path.removeprefix("/api/reviews/").removesuffix("/status")
            existing = self.reviews.get(review_id)
            question = self._question(str(existing.get("questionId") or ""), {})
            status = str(body.get("status") or "")
            review = self.reviews.update_status(
                review_id,
                status,
                current_state_hash=question["stateHash"],
            )
            return HTTPStatus.OK, review

        if path == "/api/direct-edits/preview":
            question = self._question(str(body.get("questionId") or ""), {})
            changes = body.get("changes")
            if not isinstance(changes, Mapping):
                raise ApiError(HTTPStatus.BAD_REQUEST, "changesを指定してください。")
            preview = self.editor.preview(
                question,
                changes,
                str(body.get("reason") or ""),
                str(body.get("stateHash") or ""),
            )
            return HTTPStatus.OK, preview

        if path == "/api/direct-edits/apply":
            question = self._question(str(body.get("questionId") or ""), {})
            changes = body.get("changes")
            if not isinstance(changes, Mapping):
                raise ApiError(HTTPStatus.BAD_REQUEST, "changesを指定してください。")
            result = self.editor.apply(
                question,
                changes,
                str(body.get("reason") or ""),
                str(body.get("stateHash") or ""),
                str(body.get("previewToken") or ""),
            )
            self.inventory.invalidate(question["qualification"], question["listGroupId"])
            updated = self._question(question["id"], {})
            review = self.reviews.create(
                self._decorate(updated),
                {
                    "issueTypes": ["direct_edit"],
                    "fields": sorted(changes),
                    "note": str(body.get("reason") or "").strip()
                    or "ローカルレビューUIで直接編集した。",
                },
                status="post_fix_review",
            )
            return HTTPStatus.OK, {
                **result,
                "question": self._decorate(updated),
                "review": review,
            }

        raise ApiError(HTTPStatus.NOT_FOUND, "APIが見つかりません。")

    def _questions(self, query: Mapping[str, list[str]]) -> dict[str, Any]:
        qualification = _query_value(query, "qualification")
        list_group_id = _query_value(query, "listGroupId")
        if not qualification or not list_group_id:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "qualificationとlistGroupIdを指定してください。",
            )
        group = self.inventory.group(qualification, list_group_id)
        search = _query_value(query, "search").casefold()
        issue = _query_value(query, "issue")
        review_status = _query_value(query, "status")
        exceptions_only = _query_bool(query, "exceptionsOnly", default=True)
        law_only = _query_bool(query, "lawOnly", default=False)
        firestore_mismatch = _query_bool(query, "firestoreMismatch", default=False)
        summaries = []
        decorated_issue_count = 0
        for raw in group["questions"]:
            question = self._decorate(raw)
            decorated_issue_count += bool(question["issues"])
            if search and search not in " ".join(
                (
                    question.get("body", ""),
                    question.get("questionLabel", ""),
                    question.get("sourceQuestionKey", ""),
                )
            ).casefold():
                continue
            if issue and issue not in question["issueCodes"]:
                continue
            if review_status and question["reviewStatus"] != review_status:
                continue
            if law_only and not question["isLawRelated"]:
                continue
            if firestore_mismatch and question["workflow"]["firestore"] not in {
                "mismatch",
                "missing",
                "error",
            }:
                continue
            if exceptions_only and not question["issues"]:
                continue
            summaries.append(self._summary(question))
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "questionCount": group["questionCount"],
            "issueQuestionCount": decorated_issue_count,
            "filteredCount": len(summaries),
            "fingerprint": group["fingerprint"],
            "questions": summaries,
        }

    def _question(
        self, question_id: str, query: Mapping[str, list[str]]
    ) -> dict[str, Any]:
        if not question_id:
            raise ApiError(HTTPStatus.BAD_REQUEST, "questionIdが必要です。")
        try:
            existing = self.inventory.question(question_id)
        except KeyError:
            existing = None
        if existing is not None:
            self.inventory.group(existing["qualification"], existing["listGroupId"])
            try:
                return self.inventory.question(question_id)
            except KeyError:
                pass

        qualification = _query_value(query, "qualification")
        list_group_id = _query_value(query, "listGroupId")
        if qualification and list_group_id:
            self.inventory.group(qualification, list_group_id)
            try:
                return self.inventory.question(question_id)
            except KeyError:
                raise ApiError(HTTPStatus.NOT_FOUND, "対象問題がありません。") from None

        for qualification_info in self.inventory.inventory()["qualifications"]:
            for group_id in qualification_info["listGroupIds"]:
                self.inventory.group(qualification_info["id"], group_id)
                try:
                    return self.inventory.question(question_id)
                except KeyError:
                    continue
        raise ApiError(HTTPStatus.NOT_FOUND, "対象問題がありません。")

    def _decorate(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        question = copy.deepcopy(dict(raw))
        review = self.reviews.latest_for(question)
        review_status = str(review.get("status") if review else "unreviewed")
        issues = list(question.get("issues") or [])
        codes = {str(issue.get("code")) for issue in issues}
        if review_status == "post_fix_review" and "post_fix_review" not in codes:
            issues.append(
                {
                    "code": "post_fix_review",
                    "detail": "修正後の人間確認待ちです。",
                    "fields": [],
                    "priority": 10,
                }
            )
        elif review_status == "needs_review" and "manual_flag" not in codes:
            issues.append(
                {
                    "code": "manual_flag",
                    "detail": "人間が要確認に指定しました。",
                    "fields": [],
                    "priority": 11,
                }
            )
        question["issues"] = sorted(
            issues, key=lambda value: (value.get("priority", 99), value.get("code", ""))
        )
        question["issueCodes"] = [issue["code"] for issue in question["issues"]]
        question["review"] = review
        question["reviewStatus"] = review_status
        with self._live_lock:
            live = copy.deepcopy(self.live_results.get(question["id"]))
        question["liveReadback"] = live
        if live:
            question["workflow"]["firestore"] = live.get("status", "error")
            if live.get("status") in {"mismatch", "missing"}:
                question["issues"].insert(
                    0,
                    {
                        "code": "live_mismatch",
                        "detail": "upload-readyと本番Firestoreが一致しません。",
                        "fields": live.get("differences") or live.get("missingDocumentIds") or [],
                        "priority": -1,
                    },
                )
                question["issueCodes"].insert(0, "live_mismatch")
        return question

    @staticmethod
    def _summary(question: Mapping[str, Any]) -> dict[str, Any]:
        summary = {
            key: question.get(key)
            for key in (
                "id",
                "reviewKey",
                "sourceQuestionKey",
                "questionLabel",
                "examLabel",
                "qualification",
                "listGroupId",
                "body",
                "choiceCount",
                "isLawRelated",
                "issues",
                "issueCodes",
                "reviewStatus",
                "workflow",
                "stateHash",
            )
        }
        body = str(summary.get("body") or "")
        summary["body"] = body if len(body) <= 280 else body[:279] + "…"
        return summary


class QuestionReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "QuestionReviewConsole/1"

    @property
    def app(self) -> QuestionReviewApplication:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/api/"):
            self._serve_api("GET", parsed)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        if self.headers.get("X-Review-Session") != self.app.session_token:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "session tokenが無効です。"})
            return
        origin = self.headers.get("Origin", "")
        if origin != self.app.origin:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "Originが許可されていません。"})
            return
        self._serve_api("POST", parsed)

    def _serve_api(self, method: str, parsed: urllib.parse.SplitResult) -> None:
        try:
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            if method == "GET":
                status, payload = self.app.get(parsed.path, query)
            else:
                status, payload = self.app.post(parsed.path, self._read_json())
            self._send_json(status, payload)
        except DirectEditError as exc:
            self._send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": str(exc), "codexRequired": exc.codex_required},
            )
        except ApiError as exc:
            self._send_json(exc.status, {"error": str(exc), **exc.details})
        except (FileNotFoundError, KeyError) as exc:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "サーバー内部でエラーが発生しました。"},
            )

    def _read_json(self) -> Mapping[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Lengthが不正です。") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "request sizeが不正です。")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ApiError(HTTPStatus.BAD_REQUEST, "JSON objectを指定してください。")
        return payload

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (STATIC_ROOT / relative).resolve()
        if not path.is_relative_to(STATIC_ROOT) or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", STATIC_CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        request_path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and request_path.endswith("/fingerprint"):
            return
        super().log_message(format, *args)


def _query_value(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[-1]).strip() if values else ""


def _query_bool(
    query: Mapping[str, list[str]], key: str, *, default: bool
) -> bool:
    value = _query_value(query, key)
    if not value:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ローカル問題レビューUIを起動します。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--qualification")
    parser.add_argument("--list-group-id")
    return parser


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    qualification: str | None = None,
    list_group_id: str | None = None,
    repo_root: Path = REPO_ROOT,
) -> int:
    if host != "127.0.0.1":
        raise ValueError("review consoleは127.0.0.1にだけbindできます。")
    app = QuestionReviewApplication(repo_root)
    server = ThreadingHTTPServer((host, port), QuestionReviewRequestHandler)
    server.app = app  # type: ignore[attr-defined]
    actual_port = int(server.server_address[1])
    app.set_origin(host, actual_port)
    params = {}
    if qualification:
        params["qualification"] = qualification
    if list_group_id:
        params["listGroupId"] = list_group_id
    suffix = "?" + urllib.parse.urlencode(params) if params else ""
    url = f"{app.origin}/{suffix}"
    print(f"Question review console: {url}", flush=True)
    print(f"Firestore: {PRODUCTION_PROJECT_ID} (read only)", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        qualification=args.qualification,
        list_group_id=args.list_group_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
