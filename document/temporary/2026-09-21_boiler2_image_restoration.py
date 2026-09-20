"""Restore one missing local source image after checking the original question."""

import hashlib
import io
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.common.image_storage_urls import extract_storage_object_path
from tools.question_review_console.http_transport import IPv4HTTPSHandler


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def main():
    source = ROOT / "output/boiler2/questions_json/60021/00_source/question_60021_2.json"
    source_bytes = source.read_bytes()
    record = json.loads(source_bytes)["question_bodies"][0]
    assert record["question_url"] == "https://boiler2.kakomonn.com/questions/84081"
    opener = urllib.request.build_opener(IPv4HTTPSHandler())
    with opener.open(record["question_url"], timeout=20) as response:
        html = response.read()
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalized(soup.get_text(" ", strip=True))
    assert all(normalized(text) in page_text for text in [record["questionBodyText"], *record["choiceTextList"]])
    image_urls = {
        image.get("data-src") or image.get("src")
        for body in soup.select(".detail_list")
        if normalized(body.get_text(" ", strip=True)) == normalized(record["questionBodyText"])
        for image in body.select("img")
    }
    assert len(image_urls) == 1 and len(record["questionImageStorageUrls"]) == 1
    image_url = image_urls.pop()
    assert image_url.startswith("https://")
    with opener.open(image_url, timeout=20) as response:
        image_bytes = response.read(20 * 1024 * 1024 + 1)
    assert 0 < len(image_bytes) <= 20 * 1024 * 1024
    with Image.open(io.BytesIO(image_bytes)) as image:
        assert image.format == "WEBP"
        dimensions = image.size
        image.verify()
    storage_path = extract_storage_object_path(record["questionImageStorageUrls"][0])
    assert storage_path == "question_images/official/boiler2/qa877440d483a0898_q_img01.webp"
    destination = ROOT / "output/boiler2/question_images/60021" / Path(storage_path).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        assert destination.read_bytes() == image_bytes
    else:
        with destination.open("xb") as stream:
            stream.write(image_bytes)
    assert source.read_bytes() == source_bytes
    receipt = {
        "schemaVersion": "source-image-restoration/v1",
        "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "source": source.relative_to(ROOT).as_posix(),
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
        "sourceRecordIndex": 0,
        "questionUrl": record["question_url"],
        "questionAndAllChoicesMatched": True,
        "pageSha256": hashlib.sha256(html).hexdigest(),
        "imageUrl": image_url,
        "imageSha256": hashlib.sha256(image_bytes).hexdigest(),
        "dimensions": dimensions,
        "localPath": destination.relative_to(ROOT).as_posix(),
        "storedReferenceUnchanged": True,
        "sourceUnchanged": True,
        "externalWrites": False,
    }
    receipt_path = ROOT / "document/temporary/2026-09-21_boiler2_image_restoration.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
