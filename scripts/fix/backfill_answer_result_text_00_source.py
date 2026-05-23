#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_BASE_DIR = ROOT_DIR / "output"


SINGLE_ANSWER_RESULT_RE = re.compile(r"正解は\s*([1-5])\s*です。")
MULTI_ANSWER_RESULT_RE = re.compile(r"正解は\s*([1-5](?:\s*,\s*[1-5]){1,})\s*です。")


def is_multi_answer_result_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"\s+", " ", value).strip()
    return MULTI_ANSWER_RESULT_RE.search(normalized) is not None


@dataclass(frozen=True)
class TargetRef:
    json_path: Path
    body_index: int
    question_url: str
    public_question_id: str | None
    source_correct_choice_numbers: list[int]
    source_question_intent: str | None


def is_missing_answer_result_text(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def normalize_answer_result_text(text: str) -> str:
    """
    取得した answer_result_text を「正解は N です。」形式に正規化する。
    """
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    # まず単一番号の正解表示を優先して抽出する
    match = SINGLE_ANSWER_RESULT_RE.search(normalized)
    if match:
        return f"正解は {match.group(1).strip()} です。"
    # 次に複数番号の正解表示を抽出する（2つ選べ等の設問用）
    multi_match = MULTI_ANSWER_RESULT_RE.search(normalized)
    if multi_match:
        choices = [part.strip() for part in multi_match.group(1).split(",") if part.strip()]
        return f"正解は {', '.join(choices)} です。"
    return normalized


def synthesize_answer_result_text(correct_choice_numbers: list[int]) -> str:
    choices = [str(number) for number in correct_choice_numbers if number > 0]
    if not choices:
        return ""
    return f"正解は {', '.join(choices)} です。"


def fetch_answer_result_for_url(
    code_mod: Any,
    beautiful_soup_cls: Any,
    question_url: str,
) -> tuple[str, dict[str, Any]]:
    http_session = code_mod.create_http_session()
    html_text = code_mod.fetch_html_text(http_session, question_url)
    soup = beautiful_soup_cls(html_text, "html.parser")
    answer_result_data = code_mod.fetch_answer_result_data(
        http_session,
        soup,
        question_url,
    )
    if answer_result_data is None:
        raise RuntimeError("fetch_answer_result_data returned None")

    raw_text = answer_result_data.answer_result_text
    normalized_text = normalize_answer_result_text(raw_text)
    if not normalized_text:
        raise RuntimeError("answer_result_text is empty after normalization")
    normalized_compact = re.sub(r"\s+", " ", normalized_text).strip()
    # 「正解は N です。」形式（単一/複数どちらも）で取得できていることを最低条件とする。
    if (
        SINGLE_ANSWER_RESULT_RE.fullmatch(normalized_compact) is None
        and MULTI_ANSWER_RESULT_RE.fullmatch(normalized_compact) is None
    ):
        raise RuntimeError(f"unexpected answer_result_text format: {normalized_text!r} (raw={raw_text!r})")

    return question_url, {
        "answer_result_text": normalized_text,
        "answer_result_inferred_correct_choice_numbers": list(
            answer_result_data.inferred_correct_choice_numbers
        ),
    }


def collect_targets(
    base_dir: Path,
    *,
    public_question_id: str | None,
    mode: str,
) -> tuple[list[TargetRef], int]:
    targets: list[TargetRef] = []
    matched = 0

    for json_path in sorted(base_dir.rglob("00_source/*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"JSON parse failed: {json_path} ({e})") from e

        bodies = payload.get("question_bodies")
        if not isinstance(bodies, list):
            continue

        for i, body in enumerate(bodies):
            if not isinstance(body, dict):
                continue

            if public_question_id and body.get("public_question_id") != public_question_id:
                continue

            answer_result_text_value = body.get("answer_result_text")

            is_match = False
            if mode == "all":
                is_match = True
            elif mode == "missing":
                is_match = is_missing_answer_result_text(answer_result_text_value)
            elif mode == "multiple":
                is_match = is_multi_answer_result_text(answer_result_text_value)
            else:
                raise RuntimeError(f"unknown mode: {mode}")

            if not is_match:
                continue

            qurl = str(body.get("question_url") or "").strip()
            if not qurl:
                continue
            targets.append(
                TargetRef(
                    json_path=json_path,
                    body_index=i,
                    question_url=qurl,
                    public_question_id=body.get("public_question_id"),
                    source_correct_choice_numbers=[
                        int(number)
                        for number in (body.get("correct_choice_numbers") or [])
                        if isinstance(number, int) or (isinstance(number, str) and number.isdigit())
                    ],
                    source_question_intent=str(body.get("questionIntent") or "") or None,
                )
            )
            matched += 1

    return targets, matched


def backfill_targets(
    targets: list[TargetRef],
    *,
    apply: bool,
) -> int:
    # code.py を利用（import 時に main は実行されない）
    sys.path.insert(0, str(ROOT_DIR))
    import code as code_mod  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore

    code_mod.slow_down = lambda *args, **kwargs: None  # type: ignore[attr-defined]

    cache: dict[str, Any] = {}

    failures: list[tuple[TargetRef, str]] = []

    unique_refs: list[TargetRef] = []
    seen_urls: set[str] = set()
    for ref in targets:
        if ref.question_url in seen_urls:
            continue
        seen_urls.add(ref.question_url)
        unique_refs.append(ref)

    # まず全て取得できるかを検証（apply の場合でも、失敗があれば一切書き換えない）
    total = len(unique_refs)
    worker_count = min(4, total) if total else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(fetch_answer_result_for_url, code_mod, BeautifulSoup, ref.question_url): ref
            for ref in unique_refs
        }
        for idx, future in enumerate(concurrent.futures.as_completed(future_map), start=1):
            ref = future_map[future]
            try:
                question_url, payload = future.result()
                cache[question_url] = payload
                if idx == 1 or idx % 20 == 0 or idx == total:
                    print(f"[INFO] fetched {idx}/{total}: {question_url}", flush=True)
            except Exception as e:
                failures.append((ref, str(e)))

    if failures:
        print("[WARN] 一部の URL 取得に失敗しました。ローカル推定は行いません。")
        for ref, err in failures:
            print(f"- {ref.question_url} ({ref.json_path}#{ref.body_index}): {err}")

    if not apply:
        per_file: dict[Path, int] = {}
        for ref in targets:
            per_file[ref.json_path] = per_file.get(ref.json_path, 0) + 1
        print(f"[DRY-RUN] targets={len(targets)} files={len(per_file)}")
        for path in sorted(per_file.keys()):
            print(f"- {path}: {per_file[path]} records")
        # 代表として1件だけ表示（取得に失敗したURLを避ける）
        if targets:
            sample = next((ref for ref in targets if ref.question_url in cache), targets[0])
            print("[DRY-RUN] sample:")
            print(f"  - question_url: {sample.question_url}")
            print(f"  - public_question_id: {sample.public_question_id}")
            print(f"  - json_path: {sample.json_path}")
            print(f"  - body_index: {sample.body_index}")
            if sample.question_url in cache:
                print(f"  - new_answer_result_text: {cache[sample.question_url]['answer_result_text']}")
            else:
                print("  - new_answer_result_text: (FETCH FAILED)")
        return 0

    # apply: ファイル単位で読み込み→該当レコードを更新→上書き
    refs_by_file: dict[Path, list[TargetRef]] = {}
    for ref in targets:
        refs_by_file.setdefault(ref.json_path, []).append(ref)

    updated_records = 0
    updated_files = 0

    for json_path, refs in sorted(refs_by_file.items(), key=lambda x: str(x[0])):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        bodies = payload.get("question_bodies")
        if not isinstance(bodies, list):
            continue

        for ref in refs:
            body = bodies[ref.body_index]
            if not isinstance(body, dict):
                continue
            if ref.question_url not in cache:
                print(f"[SKIP] {ref.question_url} (fetch failed)")
                continue
            new_fields = cache[ref.question_url]
            body["answer_result_text"] = new_fields["answer_result_text"]
            body["answer_result_inferred_correct_choice_numbers"] = new_fields[
                "answer_result_inferred_correct_choice_numbers"
            ]
            # デバッグ用フィールドは残さない
            body.pop("answer_result_selected_choice_numbers", None)
            body.pop("answer_result_is_selected_choice_correct", None)
            updated_records += 1

        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        updated_files += 1
        print(f"[UPDATED] {json_path} ({len(refs)} records)")

    print(f"[OK] updated_files={updated_files} updated_records={updated_records}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="00_source の answer_result_text を再スクレイピングして上書きする（複数正解が仕様の資格では複数番号も保持する）",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE_DIR,
        help="questions_json のベースディレクトリ",
    )
    parser.add_argument(
        "--public-question-id",
        default=None,
        help="この public_question_id のみを対象にする（任意）",
    )
    parser.add_argument(
        "--mode",
        choices=("missing", "multiple", "all"),
        default="missing",
        help="対象抽出モード: missing(未取得), multiple(複数番号), all(全件上書き)。注意: multiple は誤り検出ではなく“複数番号の再取得”用途。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際に JSON を上書きする（省略時は dry-run）",
    )
    args = parser.parse_args()

    base_dir: Path = args.base_dir.resolve()
    if not base_dir.exists():
        print(f"[ERROR] base_dir not found: {base_dir}")
        return 1

    targets, total_missing = collect_targets(
        base_dir,
        public_question_id=args.public_question_id,
        mode=args.mode,
    )
    print(f"[INFO] matched records = {total_missing} (mode={args.mode})")
    if not targets:
        print("[INFO] 更新対象がありません。")
        return 0

    return backfill_targets(targets, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
