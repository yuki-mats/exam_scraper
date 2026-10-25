"""Knoun原画像をローカルOCRし、目視照合前の文字起こし草案を作る。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.check.build_gas_shunin_official_pdf_index import ocr_image
from scripts.scrape.knoun_images import (
    BOOK_ID, QUALIFICATION, REPO_ROOT, atomic_json, digest, now, read_manifest,
)


def make_draft(record: dict, role: str, manifest_path: Path, output: Path, recognize=ocr_image) -> bool:
    image = record[role]
    path = manifest_path.parent / image["relativePath"]
    if digest(path.read_bytes()) != image["sha256"]:
        raise ValueError("OCR入力の画像hashが一致しません")
    binding = {
        "sourceQuestionId": record["sourceQuestionId"],
        "imageRole": role,
        "imageFilename": image["filename"],
        "imageSha256": image["sha256"],
        "imageSourceUrl": image["sourceUrl"],
    }
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("schemaVersion") != "knoun-image-ocr-draft/v1" or previous.get("binding") != binding:
            raise ValueError("既存の文字起こしと原画像の対応が異なります")
        return False
    text = recognize(path)
    if not text.strip():
        raise ValueError(f"文字を検出できませんでした: {path.name}")
    atomic_json(output, {
        "schemaVersion": "knoun-image-ocr-draft/v1",
        "binding": binding,
        "engine": "Apple Vision accurate / ja-JP / language correction enabled",
        "createdAt": now(),
        "reviewState": "needs_visual_review",
        "text": text,
    })
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output")
    parser.add_argument("--groups", nargs="+", type=int, default=list(range(1, 6)))
    args = parser.parse_args(argv)
    if not set(args.groups) <= set(range(1, 6)):
        parser.error("groupsは1〜5にしてください")
    root = args.output.expanduser().resolve() / QUALIFICATION
    total = created = 0
    missing = []
    for group in args.groups:
        group_id = f"knoun-2026-mock-{group}"
        manifest_path = root / "question_images" / group_id / "capture_manifest.json"
        if not manifest_path.exists():
            missing.append(group)
            continue
        manifest = read_manifest(manifest_path, group, BOOK_ID)
        for record in manifest["records"].values():
            for role in ("questionImage", "explanationImage"):
                image = record[role]
                target = root / "ocr" / group_id / "drafts" / f"{Path(image['filename']).stem}.json"
                created += int(make_draft(record, role, manifest_path, target))
                total += 1
        print(f"[OCR_DRAFT] {group_id}: {len(manifest['records'])}問 / 原画像{len(manifest['records']) * 2}枚", flush=True)
    print(f"[SUMMARY] drafts={total} new={created} missingGroups={missing}; 全件目視照合前・00_source未登録", flush=True)
    if total == 0:
        raise RuntimeError("OCR対象の取得済み画像がありません")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
