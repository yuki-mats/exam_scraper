"""Deterministic artifacts selected by an explicit, validated manifest."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from scripts.common.scoped_canonical_context import ScopedCanonicalContext, file_hash
from scripts.merge.patch_views import PatchArtifactEntry
from scripts.merge.record_projection import project_merge_record
from scripts.convert.convert_merged_to_firestore import convert_question_to_firestore
from scripts.scrape.qualification_presets import publication_qualification_id_for_code
from tools.question_review_console.projection import sha256_json

SCHEMA = "scoped-question-artifacts/v1"
PRIVATE_ROOT = Path("output/user_feedback_response_system/staging/recovery-scoped-2025")
CANDIDATE_FIELDS = {"lawReferences", "explanationText", "lawRevisionFacts", "suggestedQuestionDetailsByChoice"}


def write_json(path, value):
    path = Path(path)
    if path.absolute() != path.resolve() or path.is_symlink():
        raise ValueError("private JSON destination must be a physical path")
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix=".private-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _projection(context, candidate_path):
    if candidate_path is None:
        return copy.deepcopy(context.projection.record)
    changes = json.loads(candidate_path.read_text(encoding="utf-8"))
    if set(changes) != CANDIDATE_FIELDS:
        raise ValueError("private candidate must contain exactly four reviewed fields")
    args = {key: tuple(context.stage_maps[stage].by_binding.get(context.binding, ())) for key, stage in (
        ("originalized", "originalized"), ("question_type", "questionType"),
        ("intent_fallback", "questionIntent"), ("strict_correct", "correctChoice"),
        ("law_context", "lawContext"), ("explanation", "explanation"), ("question_set", "questionSet"),
    )}
    args["law_context"] += (PatchArtifactEntry(candidate_path, {**context.binding.as_mapping(), "lawReferences": changes["lawReferences"]}),)
    args["explanation"] += (PatchArtifactEntry(candidate_path, {**context.binding.as_mapping(), **{k: v for k, v in changes.items() if k != "lawReferences"}}),)
    result = project_merge_record(context.target.record, **args,
                                  question_issues=context.issue_index.by_binding.get(context.binding, ()))
    if result.errors:
        raise ValueError(" ".join(result.errors))
    for field in CANDIDATE_FIELDS:
        if result.merged2.get(field) != changes[field]:
            raise ValueError(f"stage allocation changed reviewed value: {field}")
    for field in ("questionBodyText", "choiceTextList", "correctChoiceText", "questionIntent"):
        if result.merged2.get(field) != context.projection.record.get(field):
            raise ValueError(f"private candidate changed protected field: {field}")
    return result.merged2


def documents_for(context, record):
    docs = convert_question_to_firestore(record)
    for doc in docs:
        doc["listGroupId"] = context.list_group_id
        doc.setdefault("qualificationId", publication_qualification_id_for_code(context.qualification))
        doc.setdefault("questionTags", [])
    ids = [str(d.get("questionId") or "") for d in docs]
    if ids != context.publication_ids or len(set(ids)) != len(ids):
        raise ValueError("converter did not preserve the complete publication ID set")
    context.category_membership(docs)
    return docs


def prepare_scoped_artifacts(context, output_directory, *, candidate_path=None):
    output_directory = Path(output_directory)
    private_root = context.overlay_root / PRIVATE_ROOT
    if output_directory.is_symlink() or not output_directory.resolve().is_relative_to(private_root.resolve()):
        raise ValueError("scoped artifacts must use the private recovery destination")
    context.assert_unchanged()
    candidate_path = Path(candidate_path) if candidate_path is not None else None
    candidate_hash = file_hash(candidate_path) if candidate_path else None
    record = _projection(context, candidate_path)
    documents = documents_for(context, record)
    payloads = {
        "merged": {"list_group_id": context.list_group_id, "question_bodies": [record]},
        "converted": {"questions": documents}, "uploadReady": {"questions": documents},
    }
    manifest = {
        "schemaVersion": SCHEMA, "inputContext": context.manifest,
        "projectionHash": sha256_json(record), "publicationIds": context.publication_ids,
        "candidate": {"path": str(candidate_path), "sha256": candidate_hash} if candidate_path else None,
        "formalDataUpdated": False, "publicationReady": False,
        "mode": "private_candidate_unapproved" if candidate_path else "canonical_private_generation",
        "artifacts": {},
    }
    for kind, payload in payloads.items():
        path = output_directory / f"{kind}.json"
        write_json(path, payload)
        manifest["artifacts"][kind] = {"path": str(path.relative_to(context.overlay_root)), "sha256": file_hash(path), "contentHash": sha256_json(payload)}
    context.assert_unchanged()
    if candidate_path and file_hash(candidate_path) != candidate_hash:
        raise ValueError("private candidate changed during generation")
    manifest["manifestHash"] = sha256_json(manifest)
    write_json(output_directory / "manifest.json", manifest)
    return manifest


def load_scoped_artifacts(repo_root, manifest_path):
    repo_root = Path(repo_root).resolve()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    if manifest_path.is_symlink() or not manifest_path.resolve().is_relative_to(repo_root / PRIVATE_ROOT):
        raise ValueError("invalid scoped manifest destination")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = dict(manifest)
    digest = raw.pop("manifestHash", "")
    if raw.get("schemaVersion") != SCHEMA or sha256_json(raw) != digest:
        raise ValueError("invalid scoped artifact manifest")
    context = ScopedCanonicalContext.from_manifest(manifest["inputContext"])
    if context.overlay_root != repo_root:
        raise ValueError("manifest overlay root is not this product root")
    candidate = manifest.get("candidate")
    expected_mode = "private_candidate_unapproved" if candidate else "canonical_private_generation"
    if manifest.get("mode") != expected_mode:
        raise ValueError("scoped artifact mode does not match its canonical inputs")
    candidate_path = Path(candidate["path"]) if candidate else None
    if candidate and file_hash(candidate_path) != candidate["sha256"]:
        raise ValueError("private candidate hash mismatch")
    record = _projection(context, candidate_path)
    documents = documents_for(context, record)
    expected = {"merged": {"list_group_id": context.list_group_id, "question_bodies": [record]},
                "converted": {"questions": documents}, "uploadReady": {"questions": documents}}
    if set(manifest.get("artifacts", {})) != set(expected):
        raise ValueError("manifest artifact set differs from the scoped contract")
    if manifest["projectionHash"] != sha256_json(record) or manifest["publicationIds"] != context.publication_ids:
        raise ValueError("manifest projection or publication identities changed")
    if manifest.get("publicationReady") is not False or manifest.get("formalDataUpdated") is not False:
        raise ValueError("private artifact cannot claim formal publication readiness")
    for kind, value in expected.items():
        artifact = manifest["artifacts"][kind]
        path = repo_root / artifact["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(manifest_path.parent.resolve()):
            raise ValueError("artifact escapes its manifest directory")
        if file_hash(path) != artifact["sha256"] or sha256_json(value) != artifact["contentHash"]:
            raise ValueError("scoped artifact hash mismatch")
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError("scoped artifact differs from canonical conversion")
    context.assert_unchanged()
    return manifest, context, record, documents


def validate_scoped_question(repo_root, question):
    path = question.get("scopedArtifactManifest")
    if not path:
        return None
    manifest, context, record, documents = load_scoped_artifacts(repo_root, path)
    if (question.get("sourceQuestionKey") != context.binding.source_question_key
            or question.get("sourceRecordRef") != context.binding.source_record_ref
            or question.get("originalQuestionId") != context.binding.review_question_id
            or question.get("projected") != record or question.get("uploadReadyDocs") != documents
            or question.get("scopedArtifactHash") != manifest["manifestHash"]):
        raise ValueError("question does not match the scoped artifact manifest")
    return manifest, context, record, documents
