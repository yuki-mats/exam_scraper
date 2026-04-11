#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from scripts.common.image_storage_urls import (
        FIREBASE_STORAGE_BUCKET,
        build_public_storage_url,
        build_storage_object_path,
    )
    from scripts.upload.firebase_credentials import initialize_firebase_app
else:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    from scripts.common.image_storage_urls import (
        FIREBASE_STORAGE_BUCKET,
        build_public_storage_url,
        build_storage_object_path,
    )
    from scripts.upload.firebase_credentials import initialize_firebase_app


DEFAULT_OUTPUT_ROOT = ROOT_DIR / "output"
IMAGE_FILE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
)


@dataclass(frozen=True)
class ImageUploadItem:
    filename: str
    local_path: Path
    duplicate_paths: tuple[Path, ...]
    object_path: str
    public_url: str
    sha256: str


@dataclass
class UploadSummary:
    planned: int = 0
    uploaded: int = 0
    skipped_existing: int = 0
    dry_run: int = 0
    failed: int = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_image_root(output_root: Path, qualification: str) -> Path:
    image_root = output_root / qualification / "question_images"
    if not image_root.exists():
        raise FileNotFoundError(f"画像ディレクトリが見つかりません: {image_root}")
    return image_root


def list_group_ids_from_questions_json(output_root: Path, qualification: str) -> set[str] | None:
    questions_json_dir = output_root / qualification / "questions_json"
    if not questions_json_dir.exists():
        return None
    return {
        path.name
        for path in questions_json_dir.iterdir()
        if path.is_dir() and path.name.isdigit()
    }


def iter_local_image_files(
    image_root: Path,
    *,
    list_group_ids: set[str] | None = None,
) -> list[Path]:
    def is_uploadable_image(path: Path) -> bool:
        try:
            relative_parts = path.relative_to(image_root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part.startswith(".") for part in relative_parts):
            return False
        return path.suffix.lower() in IMAGE_FILE_EXTENSIONS

    if list_group_ids:
        files: list[Path] = []
        for list_group_id in sorted(list_group_ids):
            target_dir = image_root / list_group_id
            if not target_dir.exists():
                raise FileNotFoundError(f"list_group_id の画像ディレクトリが見つかりません: {target_dir}")
            files.extend(path for path in target_dir.rglob("*") if path.is_file() and is_uploadable_image(path))
        return sorted(files)

    return sorted(path for path in image_root.rglob("*") if path.is_file() and is_uploadable_image(path))


def build_upload_plan(
    qualification: str,
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    list_group_ids: set[str] | None = None,
) -> list[ImageUploadItem]:
    image_root = resolve_image_root(output_root, qualification)
    resolved_list_group_ids = (
        list_group_ids
        if list_group_ids is not None
        else list_group_ids_from_questions_json(output_root, qualification)
    )
    files_by_name: dict[str, list[Path]] = {}

    for path in iter_local_image_files(image_root, list_group_ids=resolved_list_group_ids):
        files_by_name.setdefault(path.name, []).append(path)

    upload_items: list[ImageUploadItem] = []
    for filename, paths in sorted(files_by_name.items()):
        hashes: dict[str, list[Path]] = {}
        for path in paths:
            hashes.setdefault(sha256_file(path), []).append(path)

        if len(hashes) > 1:
            detail = "; ".join(
                f"{hash_value[:12]}: {', '.join(str(path) for path in grouped_paths)}"
                for hash_value, grouped_paths in hashes.items()
            )
            raise ValueError(f"同名ファイルの内容が一致しません: {filename}: {detail}")

        hash_value, grouped_paths = next(iter(hashes.items()))
        local_path = sorted(grouped_paths)[0]
        duplicate_paths = tuple(path for path in sorted(grouped_paths) if path != local_path)
        object_path = build_storage_object_path(qualification, filename)
        upload_items.append(
            ImageUploadItem(
                filename=filename,
                local_path=local_path,
                duplicate_paths=duplicate_paths,
                object_path=object_path,
                public_url=build_public_storage_url(qualification, filename),
                sha256=hash_value,
            )
        )

    return upload_items


def make_storage_bucket(bucket_name: str, credentials_json: Path | None = None):
    try:
        import firebase_admin
        from firebase_admin import credentials, storage
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "firebase-admin がインストールされていません。"
            " `python3 -m pip install -r requirements_firestore.txt` を実行してください。"
        ) from exc

    initialize_firebase_app(storage_bucket=bucket_name, credentials_json=credentials_json)

    return storage.bucket(name=bucket_name)


def upload_images(
    upload_items: list[ImageUploadItem],
    *,
    bucket,
    dry_run: bool,
    overwrite: bool,
) -> UploadSummary:
    summary = UploadSummary(planned=len(upload_items))

    for item in upload_items:
        blob = bucket.blob(item.object_path)
        exists = blob.exists()
        if exists and not overwrite:
            summary.skipped_existing += 1
            print(f"[SKIP] exists: gs://{bucket.name}/{item.object_path}")
            continue

        content_type, _ = mimetypes.guess_type(item.local_path.name)
        if dry_run:
            summary.dry_run += 1
            action = "overwrite" if exists else "upload"
            print(f"[DRY RUN] {action}: {item.local_path} -> gs://{bucket.name}/{item.object_path}")
            continue

        try:
            blob.upload_from_filename(str(item.local_path), content_type=content_type)
            summary.uploaded += 1
            action = "overwrite" if exists else "upload"
            print(f"[OK] {action}: {item.local_path} -> gs://{bucket.name}/{item.object_path}")
        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            print(f"[ERROR] upload failed: {item.local_path} -> gs://{bucket.name}/{item.object_path}: {exc}")

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ローカルの question_images を Firebase Storage へ一括アップロードする"
    )
    parser.add_argument(
        "qualification",
        help="資格コード。例: 2nd-class-kenchikushi",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="output ディレクトリのパス",
    )
    parser.add_argument(
        "--list-group-id",
        action="append",
        dest="list_group_ids",
        help="対象 list_group_id。複数指定可。未指定時は資格配下の全画像を対象にする。",
    )
    parser.add_argument(
        "--bucket",
        default=FIREBASE_STORAGE_BUCKET,
        help="Firebase Storage bucket 名",
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス。未指定時は GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既に存在する object も再アップロードする。未指定時は未アップロード画像のみアップロードする。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Storage の存在確認と対象表示だけ行い、アップロードしない。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="先頭N件だけ処理する。疎通確認用。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = Path(args.output_root).expanduser().resolve()
    list_group_ids = set(args.list_group_ids) if args.list_group_ids else None

    all_upload_items = build_upload_plan(
        args.qualification,
        output_root=output_root,
        list_group_ids=list_group_ids,
    )
    upload_items = all_upload_items
    if args.limit is not None:
        upload_items = upload_items[: args.limit]

    duplicate_count = sum(len(item.duplicate_paths) for item in all_upload_items)
    print(
        f"[PLAN] qualification={args.qualification} "
        f"unique_files={len(all_upload_items)} duplicate_files={duplicate_count} selected={len(upload_items)}"
    )
    if not upload_items:
        return 0

    try:
        bucket = make_storage_bucket(args.bucket, args.credentials_json)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    summary = upload_images(
        upload_items,
        bucket=bucket,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )

    print(
        "[SUMMARY] "
        f"planned={summary.planned} uploaded={summary.uploaded} "
        f"skipped_existing={summary.skipped_existing} dry_run={summary.dry_run} failed={summary.failed}"
    )
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
