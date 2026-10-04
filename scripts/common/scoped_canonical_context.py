"""Explicit read-only canonical input for a single recovery operation.

This does not change the whole-group resolver or its fail-closed contract.
"""
from __future__ import annotations

import hashlib
import ast
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from scripts.common.question_identity import (
    SourceIdentityBinding, load_source_record_inventory,
    source_identity_aliases, workflow_identity_aliases,
)
from scripts.merge.patch_views import build_layered_patch_index_from_paths, extract_patch_entries
from scripts.merge.question_issue_corrections import selected_question_issue_correction_paths
from scripts.merge.record_projection import ensure_projection_indexes_valid
from tools.question_review_console.projection import (
    STAGE_SPECS, selected_patch_paths, build_question_issue_index,
    build_identity_candidate_index, project_record, sha256_json,
)

SCHEMA = "scoped-canonical-context/v1"


class SnapshotCorrectionContext:
    """Explicit source/public adapter using the canonical readers and file proofs.

    Source identity resolves against the preserved reacquisition, while publication
    IDs come exclusively from the selected snapshot, never from the converter.
    """
    def __init__(self, root, source_root, qualification, group, binding, snapshots, references=()):
        self.root = Path(root).resolve()
        self.binding = SourceIdentityBinding.from_mapping(binding)
        sources = load_source_record_inventory(Path(source_root) / qualification / 'questions_json' / group / '00_source',
            qualification=qualification, list_group_id=group)
        matches = [source for source in sources if source.identity.binding == self.binding]
        if len(matches) != 1:
            raise ValueError('snapshot correction source binding must resolve exactly once')
        self.source = matches[0]
        self.publication_ids = [snapshot['questionId'] for snapshot in snapshots]
        if not self.publication_ids or len(set(self.publication_ids)) != len(self.publication_ids):
            raise ValueError('ambiguous selected publication IDs')
        paths = {entry.path for entry in sources} | {Path(p) for p in references}
        paths.update(reader_dependencies(self.root, [self.root / 'scripts/common/scoped_canonical_context.py',
            self.root / 'tools/question_review_console/scoped_corrections.py']))
        self.files = {str(p): file_hash(p) for p in sorted(paths)}
        self.input_hash = sha256_json(self.files)

    def assert_unchanged(self):
        if any(file_hash(Path(path)) != digest for path, digest in self.files.items()):
            raise ValueError('snapshot correction inputs changed')
EXPLICIT_BINDINGS = (
    ("sourceQuestionKey", "source_question_key"),
    ("reviewQuestionId", "review_question_id"),
    ("sourceRecordRef", "source_record_ref"),
)


def reader_dependencies(root, initial):
    """Fingerprint local Python dependencies without importing or executing them."""
    pending = list(initial)
    found = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        file_hash(path)
        found.add(path)
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [value.name for value in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                modules = [node.module]
            for name in modules:
                relative = Path(*name.split("."))
                for candidate in (root / relative.with_suffix(".py"), root / relative / "__init__.py"):
                    if candidate.is_file() and candidate not in found:
                        pending.append(candidate)
    return tuple(sorted(found))


def file_hash(path: Path) -> str:
    if path.is_symlink() or path.absolute() != path.resolve() or not path.is_file():
        raise ValueError(f"not a regular input file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_question_url(value, host: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("question URL must be a nonempty exact string")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.netloc != host or parts.query
            or parts.fragment or not re.fullmatch(r"/questions/[1-9][0-9]*", parts.path)):
        raise ValueError("question URL is not a canonical individual question URL")
    return value


def aliases(record):
    return source_identity_aliases(record) | workflow_identity_aliases(record)


def identity_pairs(records, host):
    """Reject conflicting ID/URL correspondences across the fixed full set."""
    by_id, by_url = {}, {}
    for record in records:
        legacy = record.get("original_question_id")
        urls = [record[k] for k in ("question_url", "questionUrl") if k in record]
        if len(set(str(v) for v in urls)) > 1:
            raise ValueError("question URL aliases disagree")
        if not legacy or not urls:
            continue
        url = strict_question_url(urls[0], host)
        if not isinstance(legacy, str) or not legacy.strip():
            raise ValueError("legacy ID must be a string")
        if by_id.setdefault(legacy, url) != url or by_url.setdefault(url, legacy) != legacy:
            raise ValueError("ID/URL correspondence is contradictory")
    return by_id, by_url


def prove_outside(candidate, *, identities, target, target_aliases, host, pairs):
    """Candidate-local proof; source stem is recorded, never an allowlist."""
    record = candidate.entry
    if aliases(record) & target_aliases:
        raise ValueError("candidate intersects target alias closure")
    explicit = {k: record[k] for pair in EXPLICIT_BINDINGS for k in pair if k in record}
    for pair in EXPLICIT_BINDINGS:
        values = [record[k] for k in pair if k in record]
        if len(set(str(v) for v in values)) > 1:
            raise ValueError("binding aliases disagree")
    binding = SourceIdentityBinding.from_mapping(record)
    if explicit:
        if not binding.is_complete():
            raise ValueError("explicit partial binding cannot prove outside scope")
        matches = [s for s in identities if s.binding == binding]
        if len(matches) != 1 or binding == target:
            raise ValueError("explicit binding does not resolve to a different source")
        owner = matches[0]
        # Complete binding must not hide inconsistent record identity.
        for field in ("original_question_id", "question_url", "questionUrl"):
            if field in record and record[field] not in owner.aliases:
                raise ValueError("binding and record identity disagree")
    legacy = record.get("original_question_id")
    if not isinstance(legacy, str) or not legacy.strip():
        raise ValueError("orphan legacy ID is missing")
    url = strict_question_url(record.get("question_url"), host)
    if "questionUrl" in record and record["questionUrl"] != url:
        raise ValueError("question URL aliases disagree")
    if pairs[0].get(legacy) != url or pairs[1].get(url) != legacy:
        raise ValueError("orphan ID/URL is not covered by the fixed set")
    single = build_identity_candidate_index(
        [candidate], sources=identities, record_of=lambda c: c.entry,
        source_stem_of=lambda c: c.source_stem, label="outside proof",
    )
    if single.by_binding.get(target) or single.errors_by_binding.get(target):
        raise ValueError("candidate resolver touches target")
    return {
        "candidateHash": sha256_json(record), "sourceStem": candidate.source_stem,
        "legacyId": legacy, "questionUrl": url, "explicitBinding": explicit,
        "aliasIntersection": [], "singleResolver": {
            "bindings": [b.as_mapping() for b in single.by_binding],
            "targetErrors": [], "unmatchedCount": single.unmatched_count,
        },
    }


class ScopedCanonicalContext:
    def __init__(self, *, canonical_root: Path, overlay_root: Path,
                 qualification: str, list_group_id: str, binding: SourceIdentityBinding,
                 publication_ids: list[str], reference_paths=()):
        for value in (qualification, list_group_id):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("invalid canonical segment")
        for root in (canonical_root, overlay_root):
            if root.is_symlink() or root.absolute() != root.resolve():
                raise ValueError("canonical roots must be explicit physical paths")
        if not binding.is_complete() or not publication_ids or len(set(publication_ids)) != len(publication_ids):
            raise ValueError("incomplete binding or duplicate publication IDs")
        self.canonical_root, self.overlay_root = canonical_root, overlay_root
        self.qualification, self.list_group_id = qualification, list_group_id
        self.binding, self.publication_ids = binding, list(publication_ids)
        self.reference_paths = tuple(Path(p) for p in reference_paths)
        self.relative_group = Path("output") / qualification / "questions_json" / list_group_id
        self.group = canonical_root / self.relative_group
        self.overlay_group = overlay_root / self.relative_group
        self.sources = load_source_record_inventory(
            self.group / "00_source", qualification=qualification, list_group_id=list_group_id,
        )
        matches = [e for e in self.sources if e.identity.binding == binding]
        if len(matches) != 1:
            raise ValueError("target binding must resolve exactly once")
        self.target = matches[0]
        self.identities = tuple(e.identity for e in self.sources)
        paths = set(e.path for e in self.sources)
        self.stage_maps = {}
        self.selected = {}
        for stage, subdir, tag in STAGE_SPECS:
            selected = selected_patch_paths(self.group, subdir, tag)
            self.selected[stage] = selected
            paths.update(selected)
            self.stage_maps[stage] = build_layered_patch_index_from_paths(
                selected, patch_tag=tag, sources=self.identities, label=f"{stage} patch",
            )
        # WORK owns normal layers. 24 is a formal ROOT overlay. Identical copies
        # are deduplicated; different copies with the same name are rejected.
        issue_paths = {p.name: p for p in selected_question_issue_correction_paths(self.group / "24_questionIssueCorrections")}
        for p in selected_question_issue_correction_paths(self.overlay_group / "24_questionIssueCorrections"):
            old = issue_paths.get(p.name)
            if old and file_hash(old) != file_hash(p):
                raise ValueError("24 overlay path collision")
            if old:
                paths.add(old)
            issue_paths[p.name] = p
        self.selected["questionIssueCorrection"] = sorted(issue_paths.values())
        paths.update(issue_paths.values())
        self.issue_index = build_question_issue_index(issue_paths.values(), self.identities)
        self.indexes = {**self.stage_maps, "questionIssueCorrection": self.issue_index}
        paths.update(Path(p) for p in self._dependencies()["category"])
        image_root = canonical_root / "output" / qualification / "question_images"
        if image_root.is_dir():
            paths.update(p for p in image_root.rglob("*") if p.is_file())
        paths.update(self.reference_paths)
        # Implementation versions are part of the input; changing a resolver
        # cannot preserve an earlier proof by reusing its contents.
        self.code_paths = reader_dependencies(overlay_root, [overlay_root / p for p in (
            "scripts/common/question_identity.py", "scripts/merge/patch_views.py",
            "scripts/merge/record_projection.py", "scripts/merge/question_issue_corrections.py",
            "tools/question_review_console/projection.py", "scripts/common/scoped_canonical_context.py",
            "scripts/convert/convert_merged_to_firestore.py", "config/question_issue_reports.json",
            "scripts/merge/merge_utils.py",
            "tools/question_review_console/scoped_artifacts.py",
            "tools/question_review_console/inventory.py",
            "tools/question_review_console/evaluation.py",
            "tools/question_review_console/publisher.py",
            "tools/question_review_console/validated_evidence_import.py",
            "config/scrape_presets.json", "config/qualification_display_catalog.json",
            "config/qualification_rules.json", "config/question_maintenance_workflow.toml",
            "config/requirements/required_fields.toml",
            "tools/question_review_console/evaluation_result.schema.json",
        )])
        paths.update(self.code_paths)
        self.files = {str(p): file_hash(p) for p in sorted(paths)}
        self.selection = self._selection()
        closure = set(self.target.identity.aliases) | aliases(self.target.record) | set(binding.as_tuple()) | set(publication_ids)
        for index in self.indexes.values():
            if index.errors_by_binding.get(binding):
                raise ValueError("target binding has layer errors")
            for candidate in index.by_binding.get(binding, ()):
                closure.update(aliases(candidate.entry))
        self.target_aliases = closure
        all_candidates = [c for idx in self.indexes.values() for cs in idx.by_binding.values() for c in cs]
        all_candidates += [c for idx in self.indexes.values() for c in idx.unmatched_candidates]
        for index in self.indexes.values():
            for owner, candidates in index.by_binding.items():
                if owner != binding and any(aliases(c.entry) & closure for c in candidates):
                    raise ValueError("another binding contains a target alias")
        host = urlsplit(self.target.record.get("question_url", "")).netloc
        strict_question_url(self.target.record.get("question_url"), host)
        pairs = identity_pairs([e.record for e in self.sources] + [c.entry for c in all_candidates], host)
        self.proofs = []
        for stage, index in self.indexes.items():
            for candidate in index.unmatched_candidates:
                proof = prove_outside(candidate, identities=self.identities, target=binding,
                                      target_aliases=closure, host=host, pairs=pairs)
                root = overlay_root if candidate.path.is_relative_to(overlay_root) else canonical_root
                entries = extract_patch_entries(json.loads(candidate.path.read_text()))
                positions = [i for i, value in enumerate(entries) if value == candidate.entry]
                if len(positions) != 1:
                    raise ValueError("outside candidate position is not unique")
                proof.update(stage=stage, path=str(candidate.path.relative_to(root)),
                             provenanceRoot=str(root), candidatePosition=positions[0],
                             resolverVersion={str(p.relative_to(overlay_root)): self.files[str(p)] for p in self.code_paths},
                             fullResolver={"assignedToTarget": False, "targetErrors": []})
                self.proofs.append(proof)
        self.projection = project_record(self.target.record, set(self.target.identity.aliases),
                                         self.stage_maps, self.issue_index, source_binding=binding)
        if self.projection.errors:
            raise ValueError(" ".join(self.projection.errors))
        try:
            ensure_projection_indexes_valid(tuple(self.indexes.items()))
            self.group_error = ""
        except RuntimeError as exc:
            self.group_error = str(exc)
        self.manifest = {
            "schemaVersion": SCHEMA, "canonicalRoot": str(canonical_root),
            "overlayRoot": str(overlay_root), "qualification": qualification,
            "listGroupId": list_group_id, "binding": binding.as_mapping(),
            "publicationIds": publication_ids, "files": self.files,
            "selectedPaths": self.selection, "referencePaths": [str(p) for p in self.reference_paths],
            "precedence": "WORK normal layers; ROOT formal24; identical 24 copies deduplicated",
            "sourceInventoryHash": sha256_json([{**e.identity.binding.as_mapping(), "record": e.record} for e in self.sources]),
            "candidateSetHash": sha256_json([(stage, str(c.path), c.entry) for stage, index in self.indexes.items() for cs in [*index.by_binding.values(), index.unmatched_candidates] for c in cs]),
            "targetAliasClosure": sorted(closure), "outsideProofs": self.proofs,
            "resolverVersion": {str(p.relative_to(overlay_root)): self.files[str(p)] for p in self.code_paths},
            "groupDiagnostics": {"unmatched": {s: i.unmatched_count for s, i in self.indexes.items()}, "error": self.group_error},
            "projectionHash": sha256_json(self.projection.record),
        }
        self.manifest["inputHash"] = sha256_json(self.manifest)
        for proof in self.proofs:
            # Proofs are already covered by inputHash; repeat their binding in
            # the separately saved receipt without a circular hash.
            proof["sourceInventoryHash"] = self.manifest["sourceInventoryHash"]
            proof["candidateSetHash"] = self.manifest["candidateSetHash"]
        self.manifest.pop("inputHash")
        self.manifest["inputHash"] = sha256_json(self.manifest)
        self.assert_unchanged()

    def _selection(self):
        result = {s: [str(p) for p in ps] for s, ps in self.selected.items()}
        result["sources"] = [str(e.path) for e in self.sources]
        result["formal24ByRoot"] = {
            str(root): [str(p) for p in selected_question_issue_correction_paths(root / "24_questionIssueCorrections")]
            for root in (self.group, self.overlay_group)
        }
        result["dependencies"] = self._dependencies()
        return result

    def _dependencies(self):
        base = self.canonical_root / "output" / self.qualification
        return {
            "category": sorted({str(p) for p in (base / "category").rglob("*.json")}
                               | {str(p) for p in (base / "questions_json").glob("category*.json")}),
            "images": sorted(str(p) for p in (base / "question_images").rglob("*") if p.is_file()),
            "references": sorted({str(p) for reference in self.reference_paths
                                  for p in reference.parent.rglob("*") if p.is_file()}),
        }

    def assert_unchanged(self):
        current_sources = load_source_record_inventory(self.group / "00_source", qualification=self.qualification, list_group_id=self.list_group_id)
        if [str(e.path) for e in current_sources] != self.selection["sources"]:
            raise ValueError("source inventory changed")
        for stage, subdir, tag in STAGE_SPECS:
            if [str(p) for p in selected_patch_paths(self.group, subdir, tag)] != self.selection[stage]:
                raise ValueError("candidate selection changed")
        current_24 = {str(root): [str(p) for p in selected_question_issue_correction_paths(root / "24_questionIssueCorrections")]
                      for root in (self.group, self.overlay_group)}
        if current_24 != self.selection["formal24ByRoot"]:
            raise ValueError("formal overlay selection changed")
        if self._dependencies() != self.selection["dependencies"]:
            raise ValueError("canonical dependency inventory changed")
        for path, expected in self.files.items():
            if file_hash(Path(path)) != expected:
                raise ValueError(f"canonical input changed: {path}")

    def category_membership(self, documents):
        category = self.canonical_root / "output" / self.qualification / "category" / "category.json"
        if str(category) not in self.files:
            raise ValueError("canonical category/category.json is not fixed in the context")
        payload = json.loads(category.read_text(encoding="utf-8"))
        question_sets = payload.get("questionSets")
        if not isinstance(question_sets, list):
            raise ValueError("canonical category questionSets is not an array")
        ids = [entry.get("questionSetId") for entry in question_sets if isinstance(entry, dict)]
        if len(ids) != len(set(ids)) or any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("canonical category questionSetId is missing or duplicate")
        used = {document.get("questionSetId") for document in documents}
        if None in used or "" in used or not used <= set(ids):
            raise ValueError("scoped documents do not belong to canonical questionSets")
        return sorted(used)

    @classmethod
    def from_manifest(cls, manifest):
        raw = dict(manifest)
        digest = raw.pop("inputHash", "")
        if raw.get("schemaVersion") != SCHEMA or sha256_json(raw) != digest:
            raise ValueError("invalid input manifest")
        context = cls(canonical_root=Path(raw["canonicalRoot"]), overlay_root=Path(raw["overlayRoot"]),
                      qualification=raw["qualification"], list_group_id=raw["listGroupId"],
                      binding=SourceIdentityBinding.from_mapping(raw["binding"]),
                      publication_ids=raw["publicationIds"], reference_paths=raw["referencePaths"])
        if context.manifest != manifest:
            raise ValueError("input manifest does not match canonical context")
        return context
