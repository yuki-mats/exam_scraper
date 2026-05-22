from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ExtractedImage:
    src: str
    alt: str | None
    saved_filename: str | None
    note: str | None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_html_text(input_html_path: Path) -> str:
    # MEC Net. 側の取得（HTTPアクセス）はこのスクリプトでは行いません。
    # ローカルに保存したHTMLを解析する前提です。
    return input_html_path.read_text(encoding="utf-8", errors="ignore")


def _looks_like_mecnet_exercise_list(soup: BeautifulSoup) -> bool:
    return bool(soup.select("a.show_exercise[data-qid]"))


def _extract_qids_from_list_page(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for a in soup.select("a[data-qid]"):
        class_list = a.get("class") or []
        if "show_exercise" not in class_list:
            continue
        qid_raw = str(a.get("data-qid", "")).strip()
        if not qid_raw:
            continue
        label = a.get_text(" ", strip=True)
        qid: int | str
        try:
            qid = int(qid_raw)
        except ValueError:
            qid = qid_raw
        items.append({"qid": qid, "label": label})
    return items


def _extract_page_title(soup: BeautifulSoup) -> str | None:
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        return title or None
    return None


def _extract_page_text(soup: BeautifulSoup) -> str:
    # script/style のテキストは除外
    for tag in soup.select("script,style,noscript"):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    # 連続改行を圧縮
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.IGNORECASE)


def _default_assets_dir_candidates(input_html_path: Path) -> list[Path]:
    # ブラウザの「Webページ、完全」保存で作られることが多い命名
    base = input_html_path.with_suffix("")
    return [
        input_html_path.parent / f"{input_html_path.name}_files",
        input_html_path.parent / f"{base.name}_files",
    ]


def _resolve_local_asset_path(
    input_html_path: Path,
    assets_dir: Path | None,
    src: str,
) -> Path | None:
    # file:// はパスに落とし込み
    if src.startswith("file://"):
        candidate = Path(src[len("file://") :])
        if candidate.exists():
            return candidate
        return None

    # 相対パスの場合
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", src):
        # HTML と同じディレクトリ基準
        candidate = (input_html_path.parent / src).resolve()
        if candidate.exists():
            return candidate

        # assets_dir 基準（指定がある場合）
        if assets_dir is not None:
            candidate2 = (assets_dir / src).resolve()
            if candidate2.exists():
                return candidate2

        # よくある *_files を推測
        for cand_assets in _default_assets_dir_candidates(input_html_path):
            candidate3 = (cand_assets / src).resolve()
            if candidate3.exists():
                return candidate3

    return None


def _safe_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "_", s)
    s = re.sub(r"\s+", "_", s)
    return s or "image"


def _guess_ext_from_mime(mime: str) -> str:
    mime = mime.lower().strip()
    if mime in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime == "image/png":
        return ".png"
    if mime == "image/gif":
        return ".gif"
    if mime == "image/webp":
        return ".webp"
    if mime == "image/svg+xml":
        return ".svg"
    return ".bin"


def _extract_and_save_images(
    soup: BeautifulSoup,
    *,
    input_html_path: Path,
    out_dir: Path,
    assets_dir: Path | None,
    max_images: int | None,
) -> list[ExtractedImage]:
    images: list[ExtractedImage] = []
    out_img_dir = out_dir / "images"
    out_img_dir.mkdir(parents=True, exist_ok=True)

    for img_index, img in enumerate(soup.select("img")):
        if max_images is not None and img_index >= max_images:
            break

        src = str(img.get("src") or "").strip()
        if not src:
            continue
        alt = (str(img.get("alt")).strip() if img.get("alt") is not None else None) or None

        # data URL
        m = _DATA_URL_RE.match(src)
        if m:
            mime = m.group("mime")
            b64 = m.group("b64")
            try:
                raw = base64.b64decode(b64, validate=False)
            except Exception:  # noqa: BLE001
                images.append(
                    ExtractedImage(src=src, alt=alt, saved_filename=None, note="data URLのbase64 decodeに失敗")
                )
                continue

            ext = _guess_ext_from_mime(mime)
            filename = f"img{len(images)+1:03d}{ext}"
            (out_img_dir / filename).write_bytes(raw)
            images.append(ExtractedImage(src=src, alt=alt, saved_filename=filename, note="data URLを保存"))
            continue

        # ローカルファイル参照
        local_path = _resolve_local_asset_path(input_html_path, assets_dir, src)
        if local_path is not None:
            suffix = local_path.suffix or ""
            filename = f"img{len(images)+1:03d}{suffix}"
            shutil.copyfile(local_path, out_img_dir / filename)
            images.append(
                ExtractedImage(
                    src=src,
                    alt=alt,
                    saved_filename=filename,
                    note=f"ローカル参照をコピー: {local_path}",
                )
            )
            continue

        # リモートURLはこのスクリプトでは取得しない
        images.append(ExtractedImage(src=src, alt=alt, saved_filename=None, note="リモートURLのため未取得"))

    return images


def extract_saved_html(
    input_html_path: Path,
    out_dir: Path,
    mode: str,
    assets_dir: Path | None,
    max_images: int | None,
) -> dict[str, Any]:
    html_text = _read_html_text(input_html_path)
    soup = BeautifulSoup(html_text, "html.parser")

    detected_mode = "question"
    if _looks_like_mecnet_exercise_list(soup):
        detected_mode = "list"

    if mode == "auto":
        mode = detected_mode

    payload: dict[str, Any] = {
        "extracted_at": _now_iso(),
        "input_html": str(input_html_path),
        "mode": mode,
        "detected_mode": detected_mode,
        "page_title": _extract_page_title(soup),
    }

    if mode == "list":
        payload["items"] = _extract_qids_from_list_page(soup)
    else:
        payload["page_text"] = _extract_page_text(soup)

    images = _extract_and_save_images(
        soup,
        input_html_path=input_html_path,
        out_dir=out_dir,
        assets_dir=assets_dir,
        max_images=max_images,
    )
    payload["images"] = [asdict(img) for img in images]
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "MEC Net. のページをローカル保存したHTMLを解析して、テキスト/画像を抽出します。\n"
            "注意: このスクリプト自体は study.mecnet.jp へHTTPアクセスしません（スクレイピング用途ではありません）。"
        )
    )
    parser.add_argument("--input-html", required=True, help="ローカルに保存したHTMLファイルパス")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="出力先ディレクトリ（省略時は tmp/mecnet_local_extract/<basename>）",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "list", "question"],
        default="auto",
        help="list: exercise-listページ / question: 1問詳細ページ / auto: 自動判定",
    )
    parser.add_argument(
        "--assets-dir",
        default=None,
        help="HTML保存時に生成された *_files ディレクトリ等（画像の相対パス解決用）",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="保存する画像の最大数（省略時は無制限）",
    )
    args = parser.parse_args()

    input_html_path = Path(args.input_html).expanduser().resolve()
    if not input_html_path.exists():
        raise SystemExit(f"input HTML not found: {input_html_path}")

    if args.assets_dir is None:
        assets_dir = None
    else:
        assets_dir = Path(args.assets_dir).expanduser().resolve()

    if args.out_dir is None:
        out_dir = Path("tmp") / "mecnet_local_extract" / _safe_filename(input_html_path.stem)
    else:
        out_dir = Path(args.out_dir).expanduser().resolve()

    out_dir.mkdir(parents=True, exist_ok=True)
    payload = extract_saved_html(
        input_html_path=input_html_path,
        out_dir=out_dir,
        mode=args.mode,
        assets_dir=assets_dir,
        max_images=args.max_images,
    )

    out_json_path = out_dir / "extracted.json"
    out_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[OK] wrote: {out_json_path}")
    if payload.get("mode") == "list":
        print(f"[OK] items: {len(payload.get('items') or [])}")
    print(f"[OK] images: {len(payload.get('images') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

