from __future__ import annotations

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "output" / "kaigofukushi"

YEAR_MAP = {
    "2019": "2024",
    "2018": "2023",
    "2017": "2022",
    "2016": "2021",
    "2015": "2020",
    "2014": "2019",
    "2013": "2018",
    "2012": "2017",
    "2011": "2016",
    "2010": "2015",
    "2009": "2014",
    "2008": "2013",
    "2007": "2012",
    "2006": "2011",
    "2005": "2010",
    "2004": "2009",
    "2003": "2008",
}


def rename_year_dirs(base_dir: Path) -> None:
    staged_moves: list[tuple[Path, Path]] = []
    for old_year, new_year in YEAR_MAP.items():
        old_path = base_dir / old_year
        if not old_path.exists():
            continue
        staged_path = base_dir / f"__tmp__{old_year}"
        old_path.rename(staged_path)
        staged_moves.append((staged_path, base_dir / new_year))

    for staged_path, final_path in staged_moves:
        if final_path.exists():
            raise FileExistsError(f"移行先が既に存在します: {final_path}")
        staged_path.rename(final_path)


def rewrite_json_file(path: Path, old_year: str, new_year: str) -> None:
    with path.open("r", encoding="utf-8") as fin:
        data = json.load(fin)

    def rewrite_node(node):
        if isinstance(node, dict):
            rewritten = {}
            for key, value in node.items():
                if key == "list_group_id" and value == old_year:
                    rewritten[key] = new_year
                else:
                    rewritten[key] = rewrite_node(value)
            return rewritten
        if isinstance(node, list):
            return [rewrite_node(item) for item in node]
        return node

    rewritten_data = rewrite_node(data)
    with path.open("w", encoding="utf-8") as fout:
        json.dump(rewritten_data, fout, ensure_ascii=False, indent=2)
        fout.write("\n")


def rename_question_files(source_dir: Path, old_year: str, new_year: str) -> None:
    pattern = re.compile(rf"question_{old_year}(_(?:empty_)?\d+\.json)$")
    for path in sorted(source_dir.glob("question_*.json")):
        rewrite_json_file(path, old_year, new_year)
        match = pattern.match(path.name)
        if not match:
            continue
        new_name = f"question_{new_year}{match.group(1)}"
        path.rename(path.with_name(new_name))


def main() -> int:
    questions_json_root = OUTPUT_ROOT / "questions_json"
    question_images_root = OUTPUT_ROOT / "question_images"

    rename_year_dirs(questions_json_root)
    rename_year_dirs(question_images_root)

    for old_year, new_year in YEAR_MAP.items():
        source_dir = questions_json_root / new_year / "00_source"
        if source_dir.exists():
            rename_question_files(source_dir, old_year, new_year)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
