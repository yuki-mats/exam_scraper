"""取得済み丙種と公式PDFの一問別照合資料を作る。OCRは未確定草案。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check.build_gas_shunin_official_pdf_index import (
    extract_answer_rows, ocr_image, parse_official_question_header,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def question_blocks(pages: list[dict]) -> dict[str, dict]:
    section, seen, current = "law", set(), None
    blocks = {}
    for page in pages:
        for line in page["text"].splitlines():
            header = parse_official_question_header(line, section, seen)
            if header:
                section = header[0]
                seen.add(header)
                current = f"{section}:{header[1]}"
                blocks.setdefault(current, {"pages": [], "lines": []})
            if current:
                block = blocks[current]
                if page["pdfPage"] not in block["pages"]:
                    block["pages"].append(page["pdfPage"])
                block["lines"].append(line)
    return blocks


def prepare_year(year: int, catalog: dict, output: Path, dpi: int) -> dict:
    import fitz
    from pypdf import PdfReader
    from bs4 import BeautifulSoup
    from scrape_gassyunin import SUBJECT_DEFINITIONS, collect_nodes_until_next_h2

    folder = output / str(year)
    pdfs = {}
    for role in ("mark_sheet_question", "answer"):
        entries = [x for x in catalog["files"] if x["year"] == year
                   and x["grades"] == ["hei"] and x["documentType"] == role]
        if len(entries) != 1:
            raise ValueError(f"PDF対応が一意ではありません: {year} {role}")
        entry = entries[0]
        path = ROOT / entry["localPath"]
        if digest(path) != entry["sha256"]:
            raise ValueError(f"PDF hash不一致: {path}")
        doc = fitz.open(path)
        pages = []
        for number in range(1, len(doc) + 1):
            image_path = folder / role / f"page-{number:02}.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            if not image_path.exists():
                doc[number - 1].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72)).save(image_path)
            text_path = image_path.with_suffix(".ocr.json")
            binding = {"pdfSha256": entry["sha256"], "pdfPage": number,
                       "imageSha256": digest(image_path), "dpi": dpi}
            if text_path.exists():
                result = json.loads(text_path.read_text())
                if result["binding"] != binding:
                    raise ValueError(f"OCR cache binding不一致: {text_path}")
            else:
                result = {"binding": binding, "reviewState": "needs_visual_review",
                          "text": ocr_image(image_path)}
                save(text_path, result)
            pages.append({"pdfPage": number, "imagePath": str(image_path.relative_to(ROOT)),
                          "imageSha256": binding["imageSha256"], "text": result["text"]})
            print(f"{year} {role} {number}/{len(doc)}", flush=True)
        pdfs[role] = {"path": entry["localPath"], "sha256": entry["sha256"], "pages": pages}

    # Embedded text is a separate draft; image-only tables remain for visual reading.
    answer_text = PdfReader(ROOT / pdfs["answer"]["path"]).pages[0].extract_text()
    answer_rows = extract_answer_rows(answer_text) if answer_text.strip() else None
    blocks = question_blocks(pdfs["mark_sheet_question"]["pages"])
    source = {}
    for path in sorted((ROOT / f"output/gas-shunin-hei/questions_json/{year}/00_source").glob("*.json")):
        for record in json.loads(path.read_text())["question_bodies"]:
            sid = record["source_question_id"]
            if sid in source:
                raise ValueError(f"source ID重複: {sid}")
            source[sid] = {"record": record, "path": str(path.relative_to(ROOT)), "sha256": digest(path)}
    if len(source) != 58:
        raise ValueError(f"source件数不一致: {year} {len(source)}")
    requests = ROOT / f"output/gas-shunin-hei/acquisition/{year}/requests.jsonl"
    logs = [json.loads(x) for x in requests.read_text().splitlines()]
    fetched = [x for x in logs if x.get("status") == 200 and x.get("htmlPath")]
    if len(fetched) != 1:
        raise ValueError("照合するHTML snapshotを一意に固定してください")
    html = requests.parent / fetched[0]["htmlPath"]
    if digest(html) != fetched[0]["sha256"]:
        raise ValueError("HTML hash不一致")
    soup = BeautifulSoup(html.read_bytes(), "html.parser")
    html_blocks = {}
    for topic, class_name, _ in SUBJECT_DEFINITIONS:
        div = soup.select_one(f".{class_name}")
        for h in div.find_all("h2"):
            label = h.get_text(strip=True)
            sid = f"{year}:{topic}:{label}"
            if sid in source:
                nodes = collect_nodes_until_next_h2(h)
                html_blocks[sid] = "\n".join(str(x) for x in nodes)

    packets = []
    for sid, item in source.items():
        record = item["record"]
        _, topic, label = sid.split(":")
        section = {"law": "law", "kiso": "basic"}.get(topic, "gas")
        number = int(label.removeprefix("問"))
        key = f"{section}:{number}"
        block = blocks.get(key)
        row = {"law": 0, "kiso": 1, "seizo": 2, "kyokyu": 3, "shohi": 4}[topic]
        column_number = number - {"law": 0, "kiso": 0, "seizo": 0, "kyokyu": 9, "shohi": 18}[topic]
        packets.append({
            "sourceQuestionId": sid, "qualification": "gas-shunin-hei", "year": year,
            "section": section, "questionNumber": number, "source": item,
            "htmlSnapshot": {"path": str(html.relative_to(ROOT)), "sha256": digest(html)},
            "questionHtml": html_blocks[sid],
            "officialQuestionPdf": {k: v for k, v in pdfs["mark_sheet_question"].items() if k != "pages"},
            "officialAnswerPdf": {k: v for k, v in pdfs["answer"].items() if k != "pages"},
            "candidateQuestionPages": block["pages"] if block else [],
            "ocrDraft": "\n".join(block["lines"]) if block else "",
            "officialAnswerNumberDraft": answer_rows[row][column_number - 1] if answer_rows else None,
            "answerTableRow": row + 1, "answerTableQuestionNumber": number,
            "reviewState": "needs_visual_review",
        })
    result = {"schemaVersion": "gas-shunin-hei-pdf-review-packet/v1", "year": year,
              "reviewState": "needs_visual_review", "pdfs": pdfs, "questions": packets}
    save(folder / "packet.json", result)
    print(f"PACKET {year} source={len(source)} OCR-header-keys={len(blocks)} missing={sum(not x['candidateQuestionPages'] for x in packets)}", flush=True)
    return {"year": year, "questionCount": len(source), "packet": str((folder / "packet.json").relative_to(ROOT)),
            "sha256": digest(folder / "packet.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2017, 2026)))
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()
    catalog = json.loads((ROOT / "document/sources/gas-shunin/official_exam_pdf_catalog.json").read_text())
    output = ROOT / "output/gas-shunin-hei/pdf_review"
    for year in args.years:
        prepare_year(year, catalog, output, args.dpi)


if __name__ == "__main__":
    main()
