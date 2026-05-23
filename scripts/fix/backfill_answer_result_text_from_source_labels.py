#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class UpdatedBodyRef:
    json_path: Path
    body_index: int
    question_url: str


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def normalize_numbers(numbers: list[int]) -> list[int]:
    seen: set[int] = set()
    normalized: list[int] = []
    for number in numbers:
        if number <= 0 or number in seen:
            continue
        seen.add(number)
        normalized.append(number)
    return normalized


def synthesize_answer_result_text(numbers: list[int]) -> str:
    normalized = normalize_numbers(numbers)
    if not normalized:
        return ""
    return f"正解は {', '.join(str(number) for number in normalized)} です。"


def load_code_module() -> Any:
    sys.path.insert(0, str(ROOT_DIR))
    import code as code_mod  # type: ignore

    return code_mod


def fetch_remote_answer_result(
    code_mod: Any,
    beautiful_soup_cls: Any,
    question_url: str,
    *,
    max_attempts: int = 4,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            http_session = code_mod.create_http_session()
            html_text = code_mod.fetch_html_text(http_session, question_url)
            soup = beautiful_soup_cls(html_text, "html.parser")
            answer_result_data = code_mod.fetch_answer_result_data(http_session, soup, question_url)
            if answer_result_data is None:
                raise RuntimeError("fetch_answer_result_data returned None")

            normalized_text = normalize_answer_result_text(answer_result_data.answer_result_text)
            if not normalized_text:
                raise RuntimeError("answer_result_text is empty after normalization")
            if ANSWER_RESULT_RE.fullmatch(normalized_text) is None:
                raise RuntimeError(f"unexpected answer_result_text format: {normalized_text!r}")

            return {
                "answer_result_text": normalized_text,
                "answer_result_inferred_correct_choice_numbers": list(
                    answer_result_data.inferred_correct_choice_numbers
                ),
            }
        except Exception as exc:  # pragma: no cover - network dependent
            last_error = exc
            if attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1), 4))

    assert last_error is not None
    raise last_error


def infer_answer_numbers(body: dict[str, Any]) -> list[int]:
    candidates = body.get("answer_result_inferred_correct_choice_numbers")
    if isinstance(candidates, list):
        numbers = [int(value) for value in candidates if isinstance(value, int) or str(value).isdigit()]
        if numbers:
            return normalize_numbers(numbers)

    inferred_choice = body.get("explanation_common_prefix_inferred_correct_choice")
    if isinstance(inferred_choice, int) and inferred_choice > 0:
        return [inferred_choice]

    choice_labels = body.get("correctChoiceText")
    if not isinstance(choice_labels, list):
        return []

    question_intent = str(body.get("questionIntent") or "").strip()
    target_label = "間違い" if question_intent == "select_incorrect" else "正しい"

    numbers: list[int] = []
    for index, label in enumerate(choice_labels, start=1):
        if str(label).strip() == target_label:
            numbers.append(index)

    if numbers:
        return normalize_numbers(numbers)

    explanation_correctness = body.get("explanation_choice_correctness")
    if isinstance(explanation_correctness, list):
        for index, label in enumerate(explanation_correctness, start=1):
            if str(label).strip() == target_label:
                numbers.append(index)
        if numbers:
            return normalize_numbers(numbers)

    return []


def collect_targets(base_dir: Path) -> list[UpdatedBodyRef]:
    targets: list[UpdatedBodyRef] = []
    for json_path in sorted(base_dir.rglob("00_source/*.json")):
        data = load_json(json_path)
        bodies = data.get("question_bodies")
        if not isinstance(bodies, list):
            continue

        for index, body in enumerate(bodies):
            if not isinstance(body, dict):
                continue
            if not is_missing(body.get("answer_result_text")):
                continue
            question_url = str(body.get("question_url") or "").strip()
            if not question_url:
                continue
            targets.append(
                UpdatedBodyRef(
                    json_path=json_path,
                    body_index=index,
                    question_url=question_url,
                )
            )
    return targets


def apply_backfill(base_dir: Path) -> int:
    targets = collect_targets(base_dir)
    if not targets:
        print("[INFO] answer_result_text の欠損は見つかりませんでした。")
        return 0

    refs_by_file: dict[Path, list[UpdatedBodyRef]] = {}
    for ref in targets:
        refs_by_file.setdefault(ref.json_path, []).append(ref)

    updated_files = 0
    updated_bodies = 0
    unresolved: list[UpdatedBodyRef] = []

    for json_path, refs in sorted(refs_by_file.items(), key=lambda item: str(item[0])):
        data = load_json(json_path)
        bodies = data.get("question_bodies")
        if not isinstance(bodies, list):
            continue

        file_modified = False
        for ref in refs:
            body = bodies[ref.body_index]
            if not isinstance(body, dict):
                continue

            numbers = infer_answer_numbers(body)
            answer_result_text = synthesize_answer_result_text(numbers)
            if not answer_result_text:
                unresolved.append(ref)
                continue

            body["answer_result_text"] = answer_result_text
            body["answer_result_inferred_correct_choice_numbers"] = numbers
            body.pop("answer_result_selected_choice_numbers", None)
            body.pop("answer_result_is_selected_choice_correct", None)
            file_modified = True
            updated_bodies += 1

        if file_modified:
            save_json(json_path, data)
            updated_files += 1
            print(f"[UPDATED] {json_path} ({len(refs)} records)")

    if unresolved:
        code_mod = load_code_module()
        from bs4 import BeautifulSoup  # type: ignore

        remote_failures: list[UpdatedBodyRef] = []
        remote_cache: dict[str, dict[str, Any]] = {}

        print(f"[INFO] remote fallback phase for {len(unresolved)} bodies")
        for index, ref in enumerate(unresolved, start=1):
            try:
                remote_cache[ref.question_url] = fetch_remote_answer_result(
                    code_mod,
                    BeautifulSoup,
                    ref.question_url,
                )
                if index == 1 or index % 10 == 0 or index == len(unresolved):
                    print(f"[INFO] fetched_remote {index}/{len(unresolved)}: {ref.question_url}")
            except Exception as exc:
                remote_failures.append(ref)
                print(f"[WARN] remote fetch failed: {ref.question_url} -> {exc}")

        if remote_cache:
            refs_by_file = {}
            for ref in unresolved:
                if ref.question_url in remote_cache:
                    refs_by_file.setdefault(ref.json_path, []).append(ref)

            for json_path, refs in sorted(refs_by_file.items(), key=lambda item: str(item[0])):
                data = load_json(json_path)
                bodies = data.get("question_bodies")
                if not isinstance(bodies, list):
                    continue

                file_modified = False
                for ref in refs:
                    body = bodies[ref.body_index]
                    if not isinstance(body, dict):
                        continue

                    new_fields = remote_cache[ref.question_url]
                    body["answer_result_text"] = new_fields["answer_result_text"]
                    body["answer_result_inferred_correct_choice_numbers"] = new_fields[
                        "answer_result_inferred_correct_choice_numbers"
                    ]
                    body.pop("answer_result_selected_choice_numbers", None)
                    body.pop("answer_result_is_selected_choice_correct", None)
                    file_modified = True
                    updated_bodies += 1

                if file_modified:
                    save_json(json_path, data)
                    updated_files += 1
                    print(f"[REMOTE UPDATED] {json_path} ({len(refs)} records)")

        unresolved = remote_failures

    print(f"[OK] updated_files={updated_files} updated_bodies={updated_bodies}")
    if unresolved:
        print("[WARN] unresolved bodies:")
        for ref in unresolved:
            print(f"- {ref.question_url} ({ref.json_path}#{ref.body_index})")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="00_source の answer_result_text:null をローカルの正解ラベルから補完する",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=ROOT_DIR / "output",
        help="questions_json のベースディレクトリ",
    )
    args = parser.parse_args()

    base_dir: Path = args.base_dir.resolve()
    if not base_dir.exists():
        print(f"[ERROR] base_dir not found: {base_dir}")
        return 1

    return apply_backfill(base_dir)


if __name__ == "__main__":
    raise SystemExit(main())
