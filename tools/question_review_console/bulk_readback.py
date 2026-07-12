from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import Counter
from typing import Any, Callable, Mapping

from tools.question_review_console.firestore_readback import (
    PRODUCTION_PROJECT_ID,
    FirestoreReadback,
    compare_documents,
)
from tools.question_review_console.inventory import (
    FIRESTORE_COMPARE_FIELDS,
    QuestionInventory,
)


READ_CHUNK_SIZE = 400


class ScopedReadbackError(RuntimeError):
    pass


class ScopedFirestoreReadback:
    def __init__(
        self,
        inventory: QuestionInventory,
        firestore: FirestoreReadback,
        secret: str,
        result_sink: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self.inventory = inventory
        self.firestore = firestore
        self.secret = secret.encode("utf-8")
        self.result_sink = result_sink

    def preview(
        self, qualification: str, list_group_ids: list[str]
    ) -> dict[str, Any]:
        selection = self._selection(qualification, list_group_ids)
        token_payload = {
            "qualification": qualification,
            "listGroupIds": selection["listGroupIds"],
            "fingerprints": selection["fingerprints"],
            "documentIdsHash": self._document_ids_hash(selection["documentIds"]),
        }
        return {
            "qualification": qualification,
            "projectId": PRODUCTION_PROJECT_ID,
            "listGroupIds": selection["listGroupIds"],
            "groupCount": len(selection["listGroupIds"]),
            "scopeLabel": (
                "年度"
                if all(
                    re.fullmatch(r"(?:19|20)\d{2}", value)
                    for value in selection["listGroupIds"]
                )
                else "フォルダ"
            ),
            "questionCount": len(selection["questions"]),
            "documentCount": len(selection["documentIds"]),
            "unavailableQuestionCount": selection["unavailableQuestionCount"],
            "groups": selection["groups"],
            "previewToken": self._token(token_payload),
        }

    def run(
        self,
        qualification: str,
        list_group_ids: list[str],
        preview_token: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        preview = self.preview(qualification, list_group_ids)
        if not hmac.compare_digest(preview["previewToken"], preview_token):
            raise ScopedReadbackError(
                "確認後に対象フォルダ又はupload-readyが更新されました。"
            )
        selection = self._selection(qualification, list_group_ids)
        document_ids = selection["documentIds"]
        emit(
            f"本番Firestore読取: {qualification} / "
            f"{len(selection['listGroupIds'])}フォルダ / {len(document_ids)} documents"
        )

        live_documents: dict[str, dict[str, Any]] = {}
        try:
            for start in range(0, len(document_ids), READ_CHUNK_SIZE):
                chunk = document_ids[start : start + READ_CHUNK_SIZE]
                live_documents.update(
                    self.firestore.read_documents(
                        chunk, fields=FIRESTORE_COMPARE_FIELDS
                    )
                )
                emit(
                    f"読取進捗: {min(start + len(chunk), len(document_ids))}"
                    f" / {len(document_ids)} documents"
                )
        except Exception as exc:  # noqa: BLE001
            emit(f"読取エラー: {type(exc).__name__}: {str(exc)[:500]}")
            raise ScopedReadbackError(
                "Firestoreの一括読み取りに失敗しました。実行ログを確認してください。"
            ) from exc

        status_counts: Counter[str] = Counter()
        group_counts: dict[str, Counter[str]] = {
            group_id: Counter() for group_id in selection["listGroupIds"]
        }
        for item in selection["questions"]:
            expected = item["expectedDocuments"]
            if expected:
                result = compare_documents(expected, live_documents)
                result["projectId"] = PRODUCTION_PROJECT_ID
                result["expectedSource"] = item["expectedSource"]
            else:
                result = {
                    "projectId": PRODUCTION_PROJECT_ID,
                    "status": "unavailable",
                    "error": "upload-ready又は40_convertの対象documentがありません。",
                    "documentCount": 0,
                    "documents": [],
                }
            self.result_sink(item["questionId"], result)
            status = str(result.get("status") or "error")
            status_counts[status] += 1
            group_counts[item["listGroupId"]][status] += 1

        emit("選択範囲のFirestore状態を更新しました。")
        return {
            **preview,
            "statusCounts": dict(sorted(status_counts.items())),
            "groups": [
                {
                    **group,
                    "statusCounts": dict(
                        sorted(group_counts[group["listGroupId"]].items())
                    ),
                }
                for group in preview["groups"]
            ],
            "message": (
                f"{preview['groupCount']}フォルダ・{preview['questionCount']}問の"
                "Firestore状態を更新しました。"
            ),
        }

    def _selection(
        self, qualification: str, list_group_ids: list[str]
    ) -> dict[str, Any]:
        if not isinstance(list_group_ids, list):
            raise ScopedReadbackError("listGroupIdsを配列で指定してください。")
        selected = list(dict.fromkeys(str(value).strip() for value in list_group_ids))
        if not selected or any(not value for value in selected):
            raise ScopedReadbackError("対象フォルダを1件以上選択してください。")

        inventory = self.inventory.inventory()
        qualification_info = next(
            (
                item
                for item in inventory.get("qualifications") or []
                if item.get("id") == qualification
            ),
            None,
        )
        if qualification_info is None:
            raise ScopedReadbackError("対象資格が見つかりません。")
        available = set(qualification_info.get("listGroupIds") or [])
        unknown = [value for value in selected if value not in available]
        if unknown:
            raise ScopedReadbackError(
                "対象資格に存在しないフォルダです: " + ", ".join(unknown)
            )

        questions: list[dict[str, Any]] = []
        document_ids: list[str] = []
        groups: list[dict[str, Any]] = []
        fingerprints: dict[str, str] = {}
        unavailable_count = 0
        for group_id in selected:
            group = self.inventory.group(qualification, group_id)
            fingerprints[group_id] = str(group["fingerprint"])
            group_document_ids: list[str] = []
            group_unavailable = 0
            for question in group.get("questions") or []:
                upload_documents = question.get("uploadReadyDocs") or []
                converted_documents = question.get("convertedDocs") or []
                expected = (
                    upload_documents
                    if isinstance(upload_documents, list) and upload_documents
                    else converted_documents
                    if isinstance(converted_documents, list)
                    else []
                )
                expected = [value for value in expected if isinstance(value, Mapping)]
                ids = [
                    str(value.get("questionId") or "")
                    for value in expected
                    if value.get("questionId")
                ]
                if not ids:
                    group_unavailable += 1
                    unavailable_count += 1
                group_document_ids.extend(ids)
                document_ids.extend(ids)
                questions.append(
                    {
                        "questionId": str(question["id"]),
                        "listGroupId": group_id,
                        "expectedDocuments": expected,
                        "expectedSource": (
                            "upload-ready" if upload_documents else "40_convert"
                        ),
                    }
                )
            groups.append(
                {
                    "listGroupId": group_id,
                    "questionCount": int(group.get("questionCount") or 0),
                    "documentCount": len(set(group_document_ids)),
                    "unavailableQuestionCount": group_unavailable,
                }
            )
        return {
            "listGroupIds": selected,
            "fingerprints": fingerprints,
            "questions": questions,
            "documentIds": sorted(set(document_ids)),
            "unavailableQuestionCount": unavailable_count,
            "groups": groups,
        }

    @staticmethod
    def _document_ids_hash(document_ids: list[str]) -> str:
        return hashlib.sha256("\n".join(document_ids).encode("utf-8")).hexdigest()

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
