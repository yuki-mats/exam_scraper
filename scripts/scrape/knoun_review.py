"""原画像を目視した後の転記を、当該問題の画像hashへ結び付けて保存する。"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.scrape.knoun_images import BOOK_ID, atomic_json, now, read_manifest


def load_pair(root: Path, group: int, source_id: str, *, verified_manifest: dict | None = None) -> tuple[dict, dict]:
    group_id = f"knoun-2026-mock-{group}"
    manifest = verified_manifest if verified_manifest is not None else read_manifest(root / "question_images" / group_id / "capture_manifest.json", group, BOOK_ID)
    record = manifest["records"][source_id]
    drafts = {}
    for role in ("questionImage", "explanationImage"):
        image = record[role]
        path = root / "ocr" / group_id / "drafts" / (Path(image["filename"]).stem + ".json")
        draft = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "sourceQuestionId": source_id, "imageRole": role,
            "imageFilename": image["filename"], "imageSha256": image["sha256"],
            "imageSourceUrl": image["sourceUrl"],
        }
        if draft.get("schemaVersion") != "knoun-image-ocr-draft/v1" or draft.get("binding") != expected:
            raise ValueError("文字起こしと当該原画像の対応が一致しません")
        drafts[role] = (path, draft)
    return record, drafts


def save_review(root: Path, group: int, source_id: str, *, question_hash: str,
                explanation_hash: str, question_text: str, explanation_text: str,
                question_image_content: str) -> None:
    """画像を見た担当者が明示した一問だけを保存する。OCRを自動承認しない。"""
    record, drafts = load_pair(root, group, source_id)
    if (record["questionImage"]["sha256"], record["explanationImage"]["sha256"]) != (question_hash, explanation_hash):
        raise ValueError("目視した原画像のhashと一致しません")
    if not question_text.strip() or not explanation_text.strip():
        raise ValueError("問題・解答解説の転記が必要です")
    if question_image_content not in ("text_only", "contains_figure"):
        raise ValueError("問題画像が文字だけか図表を含むかを明示してください")
    review = {
        "method": "visual_pair_comparison", "reviewer": "Codex", "reviewedAt": now(),
        "questionImageSha256": question_hash, "explanationImageSha256": explanation_hash,
        "contentAudit": "not_performed",
        "questionImageContent": question_image_content,
    }
    for role, text in (("questionImage", question_text), ("explanationImage", explanation_text)):
        path, draft = drafts[role]
        draft.update(reviewState="visually_checked", verifiedText=text, visualReview=review)
        atomic_json(path, draft)


def reviewed_pair(root: Path, group: int, source_id: str, *, verified_manifest: dict | None = None) -> tuple[dict, str, str]:
    record, drafts = load_pair(root, group, source_id, verified_manifest=verified_manifest)
    texts = {}
    for role, (_, draft) in drafts.items():
        review = draft.get("visualReview", {})
        if (draft.get("reviewState") != "visually_checked"
                or not isinstance(draft.get("verifiedText"), str) or not draft["verifiedText"].strip()
                or review.get("method") != "visual_pair_comparison"
                or not review.get("reviewer") or not review.get("reviewedAt")
                or review.get("questionImageSha256") != record["questionImage"]["sha256"]
                or review.get("explanationImageSha256") != record["explanationImage"]["sha256"]):
            raise ValueError(f"目視照合が未完了又は画像対応が古い問題です: {source_id}")
        texts[role] = draft["verifiedText"]
    return record, texts["questionImage"], texts["explanationImage"]
