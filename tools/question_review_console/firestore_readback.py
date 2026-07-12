from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping

from tools.question_review_console.inventory import FIRESTORE_COMPARE_FIELDS


PRODUCTION_PROJECT_ID = "repaso-rbaqy4"


def json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "to_json"):
        return json_safe(value.to_json())
    return value


def recursive_diff(left: Any, right: Any, path: str = "") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        differences: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                differences.append(child)
                continue
            differences.extend(recursive_diff(left[key], right[key], child))
        return differences
    if isinstance(left, list) and isinstance(right, list):
        differences = []
        for index in range(max(len(left), len(right))):
            child = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                differences.append(child)
                continue
            differences.extend(recursive_diff(left[index], right[index], child))
        return differences
    return [] if left == right else [path or "$"]


def compare_documents(
    expected_documents: Iterable[Mapping[str, Any]],
    live_documents: Mapping[str, Mapping[str, Any]],
    fields: Iterable[str] = FIRESTORE_COMPARE_FIELDS,
) -> dict[str, Any]:
    fields = tuple(fields)
    documents: list[dict[str, Any]] = []
    missing_ids: list[str] = []
    all_differences: list[str] = []
    for expected_raw in expected_documents:
        expected = json_safe(expected_raw)
        question_id = str(expected.get("questionId") or "")
        if not question_id:
            documents.append(
                {
                    "questionId": "",
                    "status": "invalid",
                    "differences": ["questionId"],
                }
            )
            all_differences.append("questionId")
            continue
        live_raw = live_documents.get(question_id)
        if live_raw is None:
            missing_ids.append(question_id)
            documents.append(
                {
                    "questionId": question_id,
                    "status": "missing",
                    "differences": [],
                }
            )
            continue
        live = json_safe(live_raw)
        differences: list[str] = []
        for field in fields:
            differences.extend(
                recursive_diff(expected.get(field), live.get(field), field)
            )
        differences = sorted(set(differences))
        all_differences.extend(f"{question_id}.{value}" for value in differences)
        documents.append(
            {
                "questionId": question_id,
                "status": "mismatch" if differences else "match",
                "differences": differences,
                "live": live,
            }
        )

    if missing_ids:
        status = "missing"
    elif all_differences:
        status = "mismatch"
    else:
        status = "match"
    return {
        "status": status,
        "documentCount": len(documents),
        "missingDocumentIds": missing_ids,
        "differenceCount": len(all_differences),
        "differences": sorted(set(all_differences)),
        "documents": documents,
    }


class FirestoreReadback:
    def __init__(
        self,
        db_factory: Callable[[], Any] | None = None,
        *,
        project_id: str = PRODUCTION_PROJECT_ID,
    ):
        if project_id != PRODUCTION_PROJECT_ID:
            raise ValueError("review consoleは本番Firestoreのみを読み取ります。")
        self.project_id = project_id
        self._db_factory = db_factory
        self._db: Any | None = None

    def read_question(self, question: Mapping[str, Any]) -> dict[str, Any]:
        expected = question.get("uploadReadyDocs") or question.get("convertedDocs") or []
        if not isinstance(expected, list) or not expected:
            return {
                "projectId": self.project_id,
                "status": "unavailable",
                "error": "upload-ready又は40_convertの対象documentがありません。",
                "documentCount": 0,
                "documents": [],
            }
        document_ids = [
            str(document.get("questionId") or "")
            for document in expected
            if isinstance(document, Mapping) and document.get("questionId")
        ]
        if not document_ids:
            return {
                "projectId": self.project_id,
                "status": "unavailable",
                "error": "Firestore document IDを特定できません。",
                "documentCount": 0,
                "documents": [],
            }
        try:
            live = self._read_documents(
                document_ids, fields=FIRESTORE_COMPARE_FIELDS
            )
        except RuntimeError:
            return {
                "projectId": self.project_id,
                "status": "error",
                "error": "Firebase credentialが設定されていないか、読み取りに失敗しました。",
                "documentCount": len(document_ids),
                "documents": [],
            }
        except Exception:
            return {
                "projectId": self.project_id,
                "status": "error",
                "error": "Firestoreの読み取りに失敗しました。",
                "documentCount": len(document_ids),
                "documents": [],
            }
        result = compare_documents(expected, live)
        result["projectId"] = self.project_id
        result["expectedSource"] = (
            "upload-ready" if question.get("uploadReadyDocs") else "40_convert"
        )
        return result

    def _read_documents(
        self,
        document_ids: list[str],
        *,
        fields: Iterable[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        db = self._database()
        refs = [db.collection("questions").document(value) for value in document_ids]
        result: dict[str, dict[str, Any]] = {}
        if hasattr(db, "get_all"):
            field_paths = list(dict.fromkeys(fields or ()))
            snapshots = (
                db.get_all(refs, field_paths=field_paths)
                if field_paths
                else db.get_all(refs)
            )
            for snapshot in snapshots:
                if snapshot.exists:
                    result[snapshot.id] = json_safe(snapshot.to_dict() or {})
            return result
        for reference in refs:
            snapshot = reference.get()
            if snapshot.exists:
                result[snapshot.id] = json_safe(snapshot.to_dict() or {})
        return result

    def read_documents(
        self,
        document_ids: list[str],
        *,
        fields: Iterable[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Read only the explicitly requested production question documents."""
        return self._read_documents(
            list(dict.fromkeys(document_ids)), fields=fields
        )

    def _database(self) -> Any:
        if self._db is not None:
            return self._db
        if self._db_factory is not None:
            self._db = self._db_factory()
            return self._db

        import firebase_admin
        from firebase_admin import firestore
        from scripts.upload.firebase_credentials import initialize_firebase_app

        initialize_firebase_app(project_id=self.project_id)
        app = firebase_admin.get_app()
        configured_project = str(
            getattr(app, "project_id", None)
            or getattr(app, "options", {}).get("projectId")
            or ""
        )
        if configured_project and configured_project != self.project_id:
            raise RuntimeError("Firebase appのproject IDが本番と一致しません。")
        self._db = firestore.client(app=app)
        return self._db
