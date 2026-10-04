"""Generate private scoped artifacts from an explicit canonical context JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.common.scoped_canonical_context import ScopedCanonicalContext
from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.scoped_artifacts import prepare_scoped_artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--context", type=Path)
    inputs.add_argument("--snapshot-corrections", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private-candidate", type=Path)
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()
    if args.snapshot_corrections:
        if not args.preview_only or args.private_candidate:
            parser.error("snapshot corrections require --preview-only without --private-candidate")
        from tools.question_review_console.scoped_corrections import prepare_snapshot_corrections, verify_snapshot_corrections
        from tools.question_review_console.firestore_readback import FirestoreReadback
        manifest = prepare_snapshot_corrections(Path(__file__).resolve().parents[2], args.snapshot_corrections,
            args.output, readback=FirestoreReadback())
        verify_snapshot_corrections(Path(__file__).resolve().parents[2], args.output / 'manifest.json')
        print(json.dumps({"manifestHash": manifest["manifestHash"], "publicationReady": False,
            "formalDataUpdated": False, "selectedCount": sum(c['selectedCount'] for c in manifest['cases'].values())}))
        return
    request = json.loads(args.context.read_text(encoding="utf-8"))
    context = ScopedCanonicalContext(
        canonical_root=Path(request["canonicalRoot"]), overlay_root=Path(request["overlayRoot"]),
        qualification=request["qualification"], list_group_id=request["listGroupId"],
        binding=SourceIdentityBinding.from_mapping(request["binding"]),
        publication_ids=request["publicationIds"], reference_paths=request.get("referencePaths", []),
    )
    manifest = prepare_scoped_artifacts(context, args.output, candidate_path=args.private_candidate)
    print(json.dumps({"manifestHash": manifest["manifestHash"], "publicationReady": False}))


if __name__ == "__main__":
    main()
