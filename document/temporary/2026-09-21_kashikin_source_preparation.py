"""Verify a complete standard scrape and restore only missing kashikin files.

One-off recovery receipt, not a new scraper or normal workflow entrypoint.
Run without --apply first. Existing source bytes and public IDs never change.
"""

import argparse
from collections import Counter
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.check.check_00_source_immutability import (
    DEFAULT_MANIFEST, differences, load_manifest, record_scrape_refresh, save_manifest,
)
from scripts.common.image_storage_urls import extract_storage_object_path
from code import create_http_session, collect_question_urls_from_all_list_pages


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(path):
    return json.loads(path.read_text(encoding="utf-8"))["question_bodies"]


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    stage = args.staging.resolve() / "kashikin"
    dest = ROOT / "output/kashikin"
    assert stage != dest.resolve()
    expected = {
        Path(f"questions_json/{group}/00_source/question_{group}_{part}.json")
        for group in range(93001, 93012) for part in (1, 2)
    }
    staged = {path.relative_to(stage) for path in stage.glob("questions_json/*/00_source/*.json")}
    existing = {path.relative_to(dest) for path in dest.glob("questions_json/*/00_source/*.json")}
    assert staged == expected, ("incomplete scrape", sorted(expected - staged))
    assert existing <= expected
    manifest = load_manifest(ROOT / DEFAULT_MANIFEST)
    before = {str(Path("output/kashikin") / path): digest(dest / path) for path in existing}
    assert all(manifest[path] == sha for path, sha in before.items()), "existing source changed"
    all_rows = [row for path in sorted(staged) for row in records(stage / path)]
    group_counts = {}
    with create_http_session() as session:
        for group in range(93001, 93012):
            urls = collect_question_urls_from_all_list_pages(
                session, f"https://kashikin.kakomonn.com/list1/{group}?page=1"
            )
            saved = [row for row in all_rows if str(row["list_group_id"]) == str(group)]
            assert urls and {row["question_url"] for row in saved} == set(urls), group
            group_counts[str(group)] = len(urls)
    total = sum(group_counts.values())
    assert len(all_rows) == total
    for key in ("source_question_id", "source_public_question_id", "question_url"):
        assert all(row.get(key) for row in all_rows), key
        assert len({row[key] for row in all_rows}) == total, ("duplicate", key)
    identity_fields = ("public_question_id", "original_question_id", "canonical_question_key", "question_url")
    existing_count = 0
    for path in sorted(expected):
        rows = records(stage / path)
        assert 0 < len(rows) <= 25
        if path in existing:
            by_source = {row["source_question_id"]: row for row in rows}
            old_rows = records(dest / path)
            assert {row["source_question_id"] for row in old_rows} == set(by_source)
            for row in old_rows:
                assert all(row[key] == by_source[row["source_question_id"]][key] for key in identity_fields)
            existing_count += len(old_rows)
    images = sorted(path for path in (stage / "question_images").rglob("*") if path.is_file())
    for path in images:
        target = dest / path.relative_to(stage)
        assert not target.exists() or digest(target) == digest(path), ("image conflict", target)
    merged_rows = [row for path in sorted(expected) for row in records((dest if path in existing else stage) / path)]
    for key in ("source_question_id", "source_public_question_id", "question_url"):
        assert len({row[key] for row in merged_rows}) == total, ("mixed source identity conflict", key)
    for path in sorted(expected):
        for value in strings(records((dest if path in existing else stage) / path)):
            object_path = extract_storage_object_path(value)
            if not object_path:
                continue
            assert object_path.startswith("question_images/official/kashikin/")
            relative = Path("question_images") / path.parts[1] / Path(object_path).name
            assert (stage / relative).is_file() or (dest / relative).is_file(), ("missing image", relative)
    subprocess.run([
        sys.executable, str(ROOT / "tools/question_bank/question_bank.py"),
        "quality-gate", "--qualification", "kashikin", "--base-dir", str(stage / "questions_json"),
        "--mode", "source",
    ], cwd=ROOT, check=True)
    missing = sorted(expected - existing)
    receipt = {
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "sourceUrl": "https://kashikin.kakomonn.com/",
        "scraper": "scripts/scrape/kakomonn_inventory.py -> code.py",
        "staging": str(stage), "groups": 11, "questions": total,
        "publishedQuestionCounts": group_counts,
        "publishedUrlSetParity": True,
        "unpublishedOfficialQuestionNumbers": {"93002": [33, 39, 43]},
        "legacyPublicAliasCollisions": {
            key: count for key, count in Counter(row["public_question_id"] for row in merged_rows).items()
            if count > 1
        },
        "legacyAliasHandling": "source bytes and IDs preserved; review must distinguish sourceRecordRef and unique source URL; publication is not authorized",
        "existingQuestionsIdentityVerified": existing_count,
        "existingSourceHashes": before,
        "restoredSourceFiles": [str(path) for path in missing],
        "restoredSourceQuestionIds": [
            row["source_question_id"] for path in missing for row in records(stage / path)
        ],
        "imagesVerified": len(images), "sourceGate": "passed",
        "sourceFilesOverwritten": 0, "externalWrites": False, "applied": args.apply,
    }
    if args.apply:
        for source in [*(stage / path for path in missing), *images]:
            target = dest / source.relative_to(stage)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
        assert all(digest(ROOT / path) == sha for path, sha in before.items())
        current = {str(Path("output/kashikin") / path): digest(dest / path) for path in expected}
        scopes = [f"output/kashikin/questions_json/{group}/00_source" for group in range(93001, 93012)]
        updated = record_scrape_refresh(manifest, current, differences(manifest, current), scopes=scopes)
        save_manifest(ROOT / DEFAULT_MANIFEST, updated)
        assert all(updated[path] == sha for path, sha in current.items())
        receipt["sourceHashes"] = current
        receipt["restoredQuestionCount"] = total - existing_count
        report = ROOT / "document/temporary/2026-09-21_kashikin_source_preparation.json"
        report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
