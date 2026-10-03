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
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private-candidate", type=Path)
    args = parser.parse_args()
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
