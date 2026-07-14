from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import logging
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.question_review_console.firestore_readback import (
    PRODUCTION_PROJECT_ID,
    FirestoreReadback,
)
from tools.question_review_console.bulk_readback import (
    ScopedFirestoreReadback,
    ScopedReadbackError,
)
from tools.question_review_console.canonical_documents import CanonicalDocumentStore
from tools.question_review_console.evaluation import (
    EvaluationError,
    QuestionEvaluationService,
)
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.jobs import JobConflictError, JobManager
from tools.question_review_console.live_readback_store import LiveReadbackStore
from tools.question_review_console.patch_editor import DirectEditError, PatchEditor
from tools.question_review_console.publisher import PublicationError, QuestionPublisher
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunError,
)
from tools.question_review_console.review_store import ReviewStore
from tools.question_review_console.prompt_builder import (
    LAW_AUDIT_ISSUES,
    QUALIFICATION_LAW_AUDIT_REQUEST,
    is_qualification_law_audit,
)
from tools.question_review_console.workflow_runner import ArtifactSynchronizer, WorkflowError


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
ALL_LIST_GROUPS = "__all__"
STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NETWORK = ipaddress.ip_network("fd7a:115c:a1e0::/48")
TAILSCALE_FUNNEL_HEADER = "Tailscale-Funnel-Request"
TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"
TAILSCALE_IDENTITY_INFO_HEADER = "Tailscale-Headers-Info"
TAILSCALE_IDENTITY_INFO_VALUE = "https://tailscale.com/s/serve-headers"
FORWARDED_HEADERS = (
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Proto",
)
LOGGER = logging.getLogger(__name__)


class ApiError(ValueError):
    def __init__(self, status: int, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.details = details


@dataclass(frozen=True)
class TailscaleAccess:
    origin: str
    authority: str
    logins: frozenset[str]
    source_ips: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]


def build_tailscale_access(
    origin: str | None,
    logins: Iterable[str] = (),
    source_ips: Iterable[str] = (),
) -> TailscaleAccess | None:
    normalized_logins = frozenset(_normalize_tailscale_login(value) for value in logins)
    normalized_ips = frozenset(_normalize_tailscale_ip(value) for value in source_ips)
    if not origin and not normalized_logins and not normalized_ips:
        return None
    if not origin or not normalized_logins or not normalized_ips:
        raise ValueError(
            "Tailscale公開には--tailscale-origin、--tailscale-login、"
            "--tailscale-source-ipをすべて指定してください。"
        )
    parsed = urllib.parse.urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("--tailscale-originのportが不正です。") from exc
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not hostname.endswith(".ts.net")
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "--tailscale-originはpathのないhttps://<device>.<tailnet>.ts.netを"
            "指定してください。"
        )
    normalized_origin = f"https://{hostname}"
    return TailscaleAccess(
        origin=normalized_origin,
        authority=hostname,
        logins=normalized_logins,
        source_ips=normalized_ips,
    )


def _normalize_tailscale_login(value: str) -> str:
    login = str(value).strip().casefold()
    if not login or any(character in login for character in "\r\n"):
        raise ValueError("--tailscale-loginが不正です。")
    return login


def _normalize_tailscale_ip(
    value: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(str(value).strip())
    except ValueError as exc:
        raise ValueError("--tailscale-source-ipが不正です。") from exc
    if address not in TAILSCALE_IPV4_NETWORK and address not in TAILSCALE_IPV6_NETWORK:
        raise ValueError("--tailscale-source-ipはTailscale端末IPで指定してください。")
    return address


class QuestionReviewApplication:
    def __init__(
        self,
        repo_root: Path = REPO_ROOT,
        *,
        tailscale_access: TailscaleAccess | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.tailscale_access = tailscale_access
        self.session_token = secrets.token_urlsafe(32)
        self.inventory = QuestionInventory(self.repo_root)
        self.reviews = ReviewStore(self.repo_root)
        self.live_store = LiveReadbackStore(self.repo_root)
        self.editor = PatchEditor(self.repo_root)
        self.firestore = FirestoreReadback()
        self.jobs = JobManager()
        self.scoped_readback = ScopedFirestoreReadback(
            self.inventory,
            self.firestore,
            self.session_token,
            self._store_live_result,
        )
        self.synchronizer = ArtifactSynchronizer(
            self.repo_root, self.inventory, self.session_token
        )
        self.evaluations = QuestionEvaluationService(
            self.repo_root, self.session_token
        )
        self.question_publisher = QuestionPublisher(
            self.repo_root,
            self.inventory,
            self.firestore,
            self.evaluations,
            self.session_token,
        )
        self.qualification_workflow = QualificationWorkflow(
            self.repo_root, self.inventory
        )
        self.canonical_documents = CanonicalDocumentStore(self.repo_root)
        self.qualification_runs = QualificationRunCoordinator(
            self.repo_root,
            self.qualification_workflow,
            self.synchronizer,
            self.jobs,
            self.session_token,
        )
        self.live_results: dict[str, dict[str, Any]] = {}
        self._live_lock = threading.RLock()
        self.origin = ""
        self.local_authority = ""

    def set_origin(self, host: str, port: int) -> None:
        self.origin = f"http://{host}:{port}"
        self.local_authority = f"{host}:{port}"

    def get(self, path: str, query: Mapping[str, list[str]]) -> tuple[int, Any]:
        if path == "/api/session":
            return HTTPStatus.OK, {
                "sessionToken": self.session_token,
                "projectId": PRODUCTION_PROJECT_ID,
                "readOnlyFirestore": False,
                "firestoreWriteEnabled": True,
                "evaluationEnabled": self.evaluations.configured,
                "evaluationProvider": self.evaluations.provider,
            }
        if path == "/api/inventory":
            return HTTPStatus.OK, self.inventory.inventory()
        if path == "/api/workflow-catalog":
            qualification = _query_value(query, "qualification")
            try:
                return HTTPStatus.OK, self.qualification_workflow.catalog(
                    qualification
                )
            except ValueError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
        if path == "/api/document":
            document_path = _query_value(query, "path")
            if not document_path:
                raise ApiError(HTTPStatus.BAD_REQUEST, "pathを指定してください。")
            try:
                return HTTPStatus.OK, self.canonical_documents.read(document_path)
            except FileNotFoundError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if path == "/api/qualification-workflow":
            qualification = _query_value(query, "qualification")
            if not qualification:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "qualificationを指定してください。"
                )
            try:
                return HTTPStatus.OK, self.qualification_workflow.overview(
                    qualification
                )
            except FileNotFoundError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            except ValueError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
        if path == "/api/qualification-runs":
            qualification = _query_value(query, "qualification")
            if not qualification:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "qualificationを指定してください。"
                )
            try:
                return HTTPStatus.OK, self.qualification_runs.recent(qualification)
            except (ValueError, QualificationRunError) as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
        if path == "/api/questions":
            return HTTPStatus.OK, self._questions(query)
        if path.startswith("/api/jobs/"):
            return HTTPStatus.OK, self.jobs.get(path.removeprefix("/api/jobs/"))
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
        if path == "/api/qualification-workflow/prompt":
            qualification = str(body.get("qualification") or "")
            stage_id = str(body.get("stageId") or "")
            raw_stage_ids = body.get("stageIds")
            if raw_stage_ids is not None and (
                not isinstance(raw_stage_ids, list)
                or not all(isinstance(value, str) and value for value in raw_stage_ids)
            ):
                raise ApiError(HTTPStatus.BAD_REQUEST, "stageIdsは文字列配列で指定してください。")
            stage_ids = list(dict.fromkeys(raw_stage_ids or ([stage_id] if stage_id else [])))
            list_group_ids = _body_string_list(body, "listGroupIds")
            if not qualification or not stage_ids:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "qualificationとstageId又はstageIdsを指定してください。",
                )
            try:
                mode = str(body.get("mode") or "remaining")
                list_group_id = str(body.get("listGroupId") or "") or None
                scope = {}
                if list_group_ids is not None:
                    scope["list_group_ids"] = list_group_ids
                elif list_group_id is not None:
                    scope["list_group_id"] = list_group_id
                if raw_stage_ids is None and not scope:
                    return HTTPStatus.OK, self.qualification_workflow.prompt(
                        qualification, stage_ids[0], mode
                    )
                return HTTPStatus.OK, self.qualification_workflow.prompt_many(
                    qualification,
                    stage_ids,
                    mode,
                    **scope,
                )
            except FileNotFoundError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            except ValueError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        if path in {
            "/api/qualification-runs/preview",
            "/api/qualification-runs/start",
            "/api/qualification-runs/resume-prompt",
        }:
            qualification = str(body.get("qualification") or "")
            if not qualification:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST, "qualificationを指定してください。"
                )
            try:
                if path.endswith("/resume-prompt"):
                    run_id = str(body.get("runId") or "")
                    if not run_id:
                        raise ValueError("runIdを指定してください。")
                    return HTTPStatus.OK, self.qualification_runs.resume_prompt(
                        qualification, run_id
                    )
                stage_id = str(body.get("stageId") or "")
                raw_stage_ids = body.get("stageIds")
                if raw_stage_ids is not None and (
                    not isinstance(raw_stage_ids, list)
                    or not all(
                        isinstance(value, str) and value for value in raw_stage_ids
                    )
                ):
                    raise ValueError("stageIdsは文字列配列で指定してください。")
                stage_ids = list(
                    dict.fromkeys(raw_stage_ids or ([stage_id] if stage_id else []))
                )
                list_group_ids = _body_string_list(body, "listGroupIds")
                mode = str(body.get("mode") or "remaining")
                if not stage_ids:
                    raise ValueError("stageId又はstageIdsを指定してください。")
                stage_id = stage_id or stage_ids[0]
                list_group_id = str(body.get("listGroupId") or "") or None
                run_options = {
                    "resumed_from": str(body.get("resumedFrom") or "") or None,
                }
                if raw_stage_ids is not None:
                    run_options["stage_ids"] = stage_ids
                if list_group_ids is not None:
                    run_options["list_group_ids"] = list_group_ids
                elif list_group_id is not None:
                    run_options["list_group_id"] = list_group_id
                if path.endswith("/preview"):
                    return HTTPStatus.OK, self.qualification_runs.preview(
                        qualification,
                        stage_id,
                        mode,
                        **run_options,
                    )
                result = self.qualification_runs.start(
                    qualification,
                    stage_id,
                    mode,
                    str(body.get("previewToken") or ""),
                    **run_options,
                )
                return (
                    HTTPStatus.ACCEPTED if result.get("job") else HTTPStatus.CREATED,
                    result,
                )
            except FileNotFoundError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            except JobConflictError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except ApiError:
                raise
            except (ValueError, QualificationRunError) as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        if path in {
            "/api/firestore-readback/preview",
            "/api/firestore-readback/run",
        }:
            qualification = str(body.get("qualification") or "")
            try:
                if path.endswith("/preview"):
                    preview = self.scoped_readback.preview(qualification)
                    preview["lastReadback"] = self.live_store.load_manifest(
                        qualification
                    )
                    return HTTPStatus.OK, preview
                token = str(body.get("previewToken") or "")
                job = self.jobs.start(
                    kind="firestore-readback",
                    key=f"firestore-readback:{qualification}",
                    worker=lambda emit: self._run_readback_job(
                        qualification, token, emit
                    ),
                )
                return HTTPStatus.ACCEPTED, job
            except JobConflictError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except ScopedReadbackError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        if path in {"/api/evaluations/preview", "/api/evaluations/start"}:
            question_ids = _body_string_list(body, "questionIds")
            if not question_ids:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "評価するquestionIdsを1問以上指定してください。",
                )
            questions = [self._question(question_id, {}) for question_id in question_ids]
            try:
                preview = self.evaluations.preview_many(questions)
                if path.endswith("/preview"):
                    return HTTPStatus.OK, preview
                token = str(body.get("previewToken") or "")
                if not self.evaluations.token_matches(preview, token):
                    raise EvaluationError("確認後に選択問題の内容が更新されました。")
                if not preview.get("canStart"):
                    raise EvaluationError("評価を開始できる問題がありません。")
                key_hash = hashlib.sha256(
                    "\n".join(sorted(question_ids)).encode("utf-8")
                ).hexdigest()[:16]
                job = self.jobs.start(
                    kind="question-evaluation-batch",
                    key=f"evaluation-batch:{key_hash}",
                    worker=lambda emit: self._run_evaluation_batch_job(
                        question_ids, token, emit
                    ),
                )
                return HTTPStatus.ACCEPTED, job
            except JobConflictError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except EvaluationError as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        question_action = _question_action(path)
        if question_action is not None:
            question_id, action = question_action
            question = self._question(question_id, {})
            job_key = f"question:{question['reviewKey']}"
            try:
                if action == "evaluation-preview":
                    return HTTPStatus.OK, self.evaluations.preview(question)
                if action == "evaluation":
                    preview = self.evaluations.preview(question)
                    token = str(body.get("previewToken") or "")
                    if not self.evaluations.token_matches(preview, token):
                        raise EvaluationError("確認後に問題内容が更新されました。")
                    if not preview.get("canEvaluate"):
                        raise EvaluationError(
                            str(preview.get("reason") or "評価を開始できません。")
                        )
                    job = self.jobs.start(
                        kind="question-evaluation",
                        key=job_key,
                        worker=lambda emit: self._run_evaluation_job(
                            question_id, token, emit
                        ),
                    )
                    return HTTPStatus.ACCEPTED, job
                if action == "publish-preview":
                    return HTTPStatus.OK, self.question_publisher.preview(question)
                if action == "publish":
                    if body.get("confirmedProduction") is not True:
                        raise PublicationError("本番反映の確認が必要です。")
                    preview = self.question_publisher.preview(question)
                    token = str(body.get("preflightToken") or "")
                    if not self.question_publisher.token_matches(preview, token):
                        raise PublicationError(
                            "確認後に問題、評価結果又はFirestoreが更新されました。"
                        )
                    if not preview.get("canPublish"):
                        raise PublicationError(
                            str(preview.get("reason") or "本番反映する差分がありません。")
                        )
                    job = self.jobs.start(
                        kind="question-publish",
                        key=job_key,
                        worker=lambda emit: self._run_question_publish_job(
                            question_id, preview, emit
                        ),
                    )
                    return HTTPStatus.ACCEPTED, job
            except JobConflictError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except (EvaluationError, PublicationError) as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        group_action = _group_action(path)
        if group_action is not None:
            qualification, list_group_id, action = group_action
            key = f"{qualification}:{list_group_id}"
            try:
                if action == "sync-preview":
                    return HTTPStatus.OK, self.synchronizer.preview(
                        qualification, list_group_id
                    )
                if action == "sync":
                    token = str(body.get("previewToken") or "")
                    current = self.synchronizer.preview(qualification, list_group_id)
                    if current["previewToken"] != token:
                        raise WorkflowError("確認後に対象状態が更新されました。")
                    job = self.jobs.start(
                        kind="sync",
                        key=key,
                        worker=lambda emit: self._run_sync_job(
                            qualification, list_group_id, token, emit
                        ),
                    )
                    return HTTPStatus.ACCEPTED, job
                if action in {"publish-preview", "publish"}:
                    raise PublicationError(
                        "グループ単位の本番反映は無効です。評価合格した問題を問題詳細から反映してください。"
                    )
            except JobConflictError as exc:
                raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
            except (WorkflowError, PublicationError) as exc:
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc

        if path.startswith("/api/questions/") and path.endswith("/live-readback"):
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "Firestoreの読み取りは資格単位で実行してください。",
            )

        if path == "/api/reviews":
            question = self._decorate(
                self._question(str(body.get("questionId") or ""), {})
            )
            request = body.get("review")
            if not isinstance(request, Mapping):
                raise ApiError(HTTPStatus.BAD_REQUEST, "reviewを指定してください。")
            request = dict(request)
            if is_qualification_law_audit(request):
                request["requestKind"] = QUALIFICATION_LAW_AUDIT_REQUEST
                request["investigationScope"] = "qualification"
                request["targetFiles"] = self._qualification_law_audit_target_files(
                    str(question["qualification"]),
                    request.get("issueTypes") or [],
                )
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

    def _qualification_law_audit_target_files(
        self, qualification: str, issue_types: list[Any]
    ) -> list[str]:
        selected_codes = {str(value) for value in issue_types} & LAW_AUDIT_ISSUES
        if not selected_codes:
            selected_codes = set(LAW_AUDIT_ISSUES)
        qualification_info = next(
            (
                item
                for item in self.inventory.inventory()["qualifications"]
                if item["id"] == qualification
            ),
            None,
        )
        if qualification_info is None:
            return []

        target_files = set()
        for list_group_id in qualification_info["listGroupIds"]:
            group = self.inventory.group(qualification, list_group_id)
            for question in group["questions"]:
                if not (set(question.get("issueCodes") or []) & selected_codes):
                    continue
                explanation_patches = [
                    path
                    for path in question.get("paths", {}).get("patches") or []
                    if "/21_explanationText_added/" in path
                ]
                if explanation_patches:
                    target_files.add(explanation_patches[-1])
                    continue
                source_stem = str(question.get("sourceStem") or "")
                target_files.add(
                    str(
                        Path("output")
                        / qualification
                        / "questions_json"
                        / str(list_group_id)
                        / "21_explanationText_added"
                        / f"{source_stem}_explanationText_added.json"
                    )
                )
        return sorted(target_files)

    def _questions(self, query: Mapping[str, list[str]]) -> dict[str, Any]:
        qualification = _query_value(query, "qualification")
        list_group_id = _query_value(query, "listGroupId")
        if not qualification or not list_group_id:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "qualificationとlistGroupIdを指定してください。",
            )
        if list_group_id == ALL_LIST_GROUPS:
            qualification_info = next(
                (
                    item
                    for item in self.inventory.inventory()["qualifications"]
                    if item["id"] == qualification
                ),
                None,
            )
            if qualification_info is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "対象資格がありません。")
            group_ids = qualification_info["listGroupIds"]
        else:
            group_ids = [list_group_id]
        groups = [self.inventory.group(qualification, group_id) for group_id in group_ids]
        search = _query_value(query, "search").casefold()
        issue = _query_value(query, "issue")
        review_status = _query_value(query, "status")
        evaluation_status = _query_value(query, "evaluationStatus")
        exceptions_only = _query_bool(query, "exceptionsOnly", default=True)
        law_only = _query_bool(query, "lawOnly", default=False)
        firestore_mismatch = _query_bool(query, "firestoreMismatch", default=False)
        summaries = []
        decorated_issue_count = 0
        evaluation_counts = {
            "maintenance": 0,
            "unreviewed": 0,
            "needsRework": 0,
            "publishReady": 0,
            "published": 0,
        }
        for group in groups:
            for raw in group["questions"]:
                question = self._decorate(raw)
                decorated_issue_count += bool(question["issues"])
                quality_bucket = self._quality_bucket(question)
                evaluation_counts[quality_bucket] += 1
                if search and search not in " ".join(
                    (
                        question.get("body", ""),
                        question.get("questionLabel", ""),
                        question.get("sourceQuestionKey", ""),
                        question.get("listGroupId", ""),
                    )
                ).casefold():
                    continue
                if issue and issue not in question["issueCodes"]:
                    continue
                if review_status and question["reviewStatus"] != review_status:
                    continue
                if evaluation_status and quality_bucket != evaluation_status:
                    continue
                if law_only and not question["isLawRelated"]:
                    continue
                if firestore_mismatch and question["workflow"]["firestore"] not in {
                    "mismatch",
                    "missing",
                    "error",
                    "upstream_stale",
                }:
                    continue
                if (
                    exceptions_only
                    and not question["issues"]
                    and quality_bucket == "published"
                ):
                    continue
                summaries.append(self._summary(question))
        offset = _query_int(query, "offset", default=0, minimum=0, maximum=1_000_000)
        limit = _query_int(query, "limit", default=50, minimum=1, maximum=100)
        filtered_count = len(summaries)
        page = summaries[offset : offset + limit]
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "questionCount": sum(group["questionCount"] for group in groups),
            "issueQuestionCount": decorated_issue_count,
            "evaluationCounts": evaluation_counts,
            "filteredCount": filtered_count,
            "offset": offset,
            "limit": limit,
            "hasMore": offset + len(page) < filtered_count,
            "fingerprint": "|".join(group["fingerprint"] for group in groups),
            "questions": page,
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
            if list_group_id == ALL_LIST_GROUPS:
                qualification_info = next(
                    (
                        item
                        for item in self.inventory.inventory()["qualifications"]
                        if item["id"] == qualification
                    ),
                    None,
                )
                group_ids = qualification_info["listGroupIds"] if qualification_info else []
            else:
                group_ids = [list_group_id]
            for group_id in group_ids:
                self.inventory.group(qualification, group_id)
                try:
                    return self.inventory.question(question_id)
                except KeyError:
                    continue
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
        machine_issue_codes = list(question.get("issueCodes") or [])
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
        live = self._live_result_for(question)
        question["liveReadback"] = live
        if live:
            live_status = live.get("status", "error")
            live_stale = bool(live.get("readbackMeta", {}).get("stale"))
            local_ready = all(
                question["workflow"].get(stage) == "match"
                for stage in ("merge", "convert", "upload")
            )
            question["workflow"]["firestore"] = (
                "upstream_stale"
                if live_stale or (live_status == "match" and not local_ready)
                else live_status
            )
            if live_stale:
                if "firestore_readback_stale" not in codes:
                    question["issues"].insert(
                        0,
                        {
                            "code": "firestore_readback_stale",
                            "detail": (
                                "保存済みのFirestore比較結果は現在のローカル成果物"
                                "より古いため、資格全体の再取得が必要です。"
                            ),
                            "fields": [],
                            "priority": -1,
                        },
                    )
                    question["issueCodes"].insert(0, "firestore_readback_stale")
            elif live.get("status") in {"mismatch", "missing"}:
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
        evaluation_input = copy.deepcopy(question)
        evaluation_input["issueCodes"] = machine_issue_codes
        evaluation = self.evaluations.status_for(
            evaluation_input,
            live_status=str(question.get("workflow", {}).get("firestore") or "unread"),
        )
        question["evaluation"] = evaluation
        question["publishReady"] = evaluation["publishReady"]
        question["nextAction"] = evaluation["nextAction"]
        return question

    def _run_evaluation_job(
        self,
        question_id: str,
        preview_token: str,
        emit: Any,
    ) -> dict[str, Any]:
        question = self._question(question_id, {})
        return self.evaluations.run(question, preview_token, emit)

    def _run_evaluation_batch_job(
        self,
        question_ids: list[str],
        preview_token: str,
        emit: Any,
    ) -> dict[str, Any]:
        questions = [self._question(question_id, {}) for question_id in question_ids]
        return self.evaluations.run_many(questions, preview_token, emit)

    def _run_question_publish_job(
        self,
        question_id: str,
        preflight: Mapping[str, Any],
        emit: Any,
    ) -> dict[str, Any]:
        question = self._question(question_id, {})
        result = self.question_publisher.run(question, preflight, emit)
        readback = result.get("readback")
        if isinstance(readback, Mapping):
            self._store_live_result(question_id, dict(readback))
        return result

    def _run_sync_job(
        self,
        qualification: str,
        list_group_id: str,
        preview_token: str,
        emit: Any,
    ) -> dict[str, Any]:
        result = self.synchronizer.run(
            qualification, list_group_id, preview_token, emit
        )
        self._clear_group_live_results(qualification, list_group_id)
        return result

    def _run_readback_job(
        self,
        qualification: str,
        preview_token: str,
        emit: Any,
    ) -> dict[str, Any]:
        result = self.scoped_readback.run(qualification, preview_token, emit)
        self.live_store.save_manifest(qualification, result)
        emit("取得結果をローカルへ保存しました。")
        return result

    def _clear_group_live_results(
        self, qualification: str, list_group_id: str
    ) -> None:
        group = self.inventory.group(qualification, list_group_id)
        question_ids = [
            str(question["id"]) for question in group.get("questions") or []
        ]
        with self._live_lock:
            for question_id in question_ids:
                self.live_results.pop(question_id, None)

    def _store_live_result(self, question_id: str, result: dict[str, Any]) -> None:
        try:
            question = self.inventory.question(question_id)
        except KeyError:
            question = None
        if question is not None:
            result = self.live_store.save(question, result)
        with self._live_lock:
            self.live_results[question_id] = copy.deepcopy(result)

    def _live_result_for(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._live_lock:
            live = copy.deepcopy(self.live_results.get(question["id"]))
        if live:
            return self.live_store.with_current_metadata(question, live)
        return self.live_store.load(question)

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
                "publishReady",
                "nextAction",
            )
        }
        evaluation = question.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, Mapping) else {}
        summary["evaluation"] = {
            key: evaluation.get(key)
            for key in (
                "status",
                "configured",
                "machineReady",
                "publishReady",
                "nextAction",
                "verifiedChoiceCount",
                "choiceCount",
                "explanationScore",
                "summary",
                "evaluatedAt",
            )
        }
        body = str(summary.get("body") or "")
        summary["body"] = body if len(body) <= 280 else body[:279] + "…"
        return summary

    @staticmethod
    def _quality_bucket(question: Mapping[str, Any]) -> str:
        evaluation = question.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, Mapping) else {}
        status = str(evaluation.get("status") or "not_started")
        if not evaluation.get("machineReady"):
            return "maintenance"
        if status in {"not_started", "stale", "running"}:
            return "unreviewed"
        if status == "needs_rework":
            return "needsRework"
        if question.get("workflow", {}).get("firestore") == "match":
            return "published"
        return "publishReady"


class QuestionReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "QuestionReviewConsole/1"

    @property
    def app(self) -> QuestionReviewApplication:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        route = self._authorized_route()
        if route is None:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "アクセス経路が許可されていません。"},
            )
            return
        self._access_route = route
        if parsed.path.startswith("/api/"):
            self._serve_api("GET", parsed)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        route = self._authorized_route()
        if route is None:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "アクセス経路が許可されていません。"},
            )
            return
        self._access_route = route
        if not parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        session_token = self._single_header("X-Review-Session")
        if session_token is None or not secrets.compare_digest(
            session_token, self.app.session_token
        ):
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "session tokenが無効です。"})
            return
        origin = self._single_header("Origin") or ""
        expected_origin = (
            self.app.origin
            if route == "local"
            else self.app.tailscale_access.origin  # type: ignore[union-attr]
        )
        if origin != expected_origin:
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "Originが許可されていません。"},
            )
            return
        self._serve_api("POST", parsed)

    def _authorized_route(self) -> str | None:
        host = self._single_header("Host")
        if host is None:
            return None
        normalized_host = host.casefold()
        if normalized_host == self.app.local_authority.casefold():
            proxy_headers = (
                TAILSCALE_LOGIN_HEADER,
                TAILSCALE_IDENTITY_INFO_HEADER,
                TAILSCALE_FUNNEL_HEADER,
                *FORWARDED_HEADERS,
            )
            if any(self.headers.get_all(header) for header in proxy_headers):
                return None
            return "local"

        access = self.app.tailscale_access
        if access is None or normalized_host != access.authority:
            return None
        if self.headers.get_all(TAILSCALE_FUNNEL_HEADER):
            return None
        login = self._single_header(TAILSCALE_LOGIN_HEADER)
        source = self._single_header("X-Forwarded-For")
        forwarded_host = self._single_header("X-Forwarded-Host")
        forwarded_proto = self._single_header("X-Forwarded-Proto")
        if None in {login, source, forwarded_host, forwarded_proto}:
            return None
        if not any(
            secrets.compare_digest(login.casefold(), allowed_login)
            for allowed_login in access.logins
        ):
            return None
        try:
            source_ip = ipaddress.ip_address(source)
        except ValueError:
            return None
        if source_ip not in access.source_ips:
            return None
        if forwarded_host.casefold() != access.authority or forwarded_proto != "https":
            return None
        identity_info = self.headers.get_all(TAILSCALE_IDENTITY_INFO_HEADER) or []
        if len(identity_info) > 1:
            return None
        if identity_info and identity_info[0] != TAILSCALE_IDENTITY_INFO_VALUE:
            return None
        return "tailscale"

    def _single_header(self, name: str) -> str | None:
        values = self.headers.get_all(name) or []
        if len(values) != 1:
            return None
        value = str(values[0]).strip()
        return value or None

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
            LOGGER.exception("Unhandled review API error: %s %s", method, parsed.path)
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
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _send_security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        if getattr(self, "_access_route", None) == "tailscale":
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def log_message(self, format: str, *args: Any) -> None:
        request_path = urllib.parse.urlsplit(self.path).path
        if self.command == "GET" and request_path.endswith("/fingerprint"):
            return
        super().log_message(format, *args)


def _query_value(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return str(values[-1]).strip() if values else ""


def _body_string_list(
    body: Mapping[str, Any], key: str
) -> list[str] | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"{key}は空でない文字列配列で指定してください。"
        )
    return list(dict.fromkeys(item.strip() for item in value))


def _query_bool(
    query: Mapping[str, list[str]], key: str, *, default: bool
) -> bool:
    value = _query_value(query, key)
    if not value:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


def _query_int(
    query: Mapping[str, list[str]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _query_value(query, key)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key}は整数で指定してください。") from exc
    return min(max(parsed, minimum), maximum)


def _group_action(path: str) -> tuple[str, str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 5 or parts[:2] != ["api", "groups"]:
        return None
    qualification = urllib.parse.unquote(parts[2])
    list_group_id = urllib.parse.unquote(parts[3])
    action = parts[4]
    if action not in {"sync-preview", "sync", "publish-preview", "publish"}:
        return None
    return qualification, list_group_id, action


def _question_action(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "questions"]:
        return None
    action = parts[3]
    if action not in {
        "evaluation-preview",
        "evaluation",
        "publish-preview",
        "publish",
    }:
        return None
    return urllib.parse.unquote(parts[2]), action


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="問題整備システムを起動します。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--qualification")
    parser.add_argument("--list-group-id")
    parser.add_argument("--tailscale-origin")
    parser.add_argument("--tailscale-login", action="append", default=[])
    parser.add_argument("--tailscale-source-ip", action="append", default=[])
    return parser


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    qualification: str | None = None,
    list_group_id: str | None = None,
    tailscale_origin: str | None = None,
    tailscale_logins: Iterable[str] = (),
    tailscale_source_ips: Iterable[str] = (),
    repo_root: Path = REPO_ROOT,
) -> int:
    if host != "127.0.0.1":
        raise ValueError("review consoleは127.0.0.1にだけbindできます。")
    tailscale_access = build_tailscale_access(
        tailscale_origin,
        tailscale_logins,
        tailscale_source_ips,
    )
    if tailscale_access is not None and port == 0:
        raise ValueError("Tailscale公開時は--portに固定portを指定してください。")
    app = QuestionReviewApplication(repo_root, tailscale_access=tailscale_access)
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
    print(f"問題整備システム: {url}", flush=True)
    if tailscale_access is not None:
        print(f"Tailscale Serve: {tailscale_access.origin}/{suffix}", flush=True)
    print(f"Firestore: {PRODUCTION_PROJECT_ID} (UI publish enabled)", flush=True)
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
        tailscale_origin=args.tailscale_origin,
        tailscale_logins=args.tailscale_login,
        tailscale_source_ips=args.tailscale_source_ip,
    )


if __name__ == "__main__":
    raise SystemExit(main())
