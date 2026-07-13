from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "scrape_presets.json"


@dataclass(frozen=True)
class ScrapeTarget:
    source_list_group_id: str
    output_list_group_id: str


@dataclass(frozen=True)
class ScrapePreset:
    qualification_code: str
    publication_qualification_id: str
    qualification_name: str
    scraper_type: str
    list_first_page_url_template: str
    scrape_targets: list[ScrapeTarget]

    @property
    def list_group_ids(self) -> list[str]:
        return [target.output_list_group_id for target in self.scrape_targets]

    def get_target(self, output_list_group_id: str) -> ScrapeTarget:
        for target in self.scrape_targets:
            if target.output_list_group_id == output_list_group_id:
                return target
        raise KeyError(f"scrape target が見つかりません: {output_list_group_id}")


def load_scrape_preset(
    qualification_code: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> ScrapePreset:
    with config_path.open("r", encoding="utf-8") as fin:
        raw = json.load(fin)

    if qualification_code not in raw:
        raise KeyError(f"scrape preset が見つかりません: {qualification_code}")

    preset = raw[qualification_code]
    raw_targets = preset.get("scrape_targets")
    if raw_targets is None:
        raw_targets = [
            {
                "source_list_group_id": str(group_id),
                "output_list_group_id": str(group_id),
            }
            for group_id in preset["list_group_ids"]
        ]

    return ScrapePreset(
        qualification_code=str(preset.get("qualification_code", qualification_code)),
        publication_qualification_id=str(
            preset.get(
                "publication_qualification_id",
                preset.get("qualification_code", qualification_code),
            )
        ),
        qualification_name=preset["qualification_name"],
        scraper_type=str(preset.get("scraper_type", "kakomonn")),
        list_first_page_url_template=preset["list_first_page_url_template"],
        scrape_targets=[
            ScrapeTarget(
                source_list_group_id=str(target["source_list_group_id"]),
                output_list_group_id=str(target["output_list_group_id"]),
            )
            for target in raw_targets
        ],
    )


def load_qualification_catalog(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, dict[str, str]]:
    """ローカル資格コードごとの和名と公開用qualificationIdを返す。"""
    if not config_path.is_file():
        return {}
    with config_path.open("r", encoding="utf-8") as fin:
        raw = json.load(fin)

    catalog: dict[str, dict[str, str]] = {}
    for preset_key, value in raw.items():
        if not isinstance(value, dict):
            continue
        local_code = str(value.get("qualification_code", preset_key)).strip()
        name = str(value.get("qualification_name", "")).strip()
        if not local_code or not name:
            continue
        publication_id = str(
            value.get("publication_qualification_id", local_code)
        ).strip()
        candidate = {
            "displayName": name,
            "publicationId": publication_id or local_code,
        }
        existing = catalog.get(local_code)
        if existing is not None and existing != candidate:
            raise ValueError(f"資格表示情報が競合しています: {local_code}")
        catalog[local_code] = candidate
    return catalog


def publication_qualification_id_for_code(
    qualification_code: str | None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> str | None:
    if not qualification_code:
        return qualification_code
    metadata = load_qualification_catalog(config_path).get(qualification_code, {})
    return metadata.get("publicationId", qualification_code)


def resolve_target_list_group_ids(
    preset: ScrapePreset,
    requested_list_group_ids: list[str],
) -> list[str]:
    if not requested_list_group_ids:
        return list(preset.list_group_ids)

    requested = [str(group_id) for group_id in requested_list_group_ids]
    known = set(preset.list_group_ids)
    unknown = [group_id for group_id in requested if group_id not in known]
    if unknown:
        raise ValueError(
            f"preset に存在しない list_group_id が指定されました: {', '.join(unknown)}"
        )
    return requested


def build_list_first_page_url(preset: ScrapePreset, list_group_id: str) -> str:
    target = preset.get_target(list_group_id)
    return preset.list_first_page_url_template.format(
        list_group_id=target.source_list_group_id
    )


def has_existing_source_json(
    repo_root: Path,
    qualification_code: str,
    list_group_id: str,
    output_root: Path | None = None,
    filename_glob: str = "question_*.json",
) -> bool:
    root = output_root if output_root is not None else (repo_root / "output")
    source_dir = (
        root
        / qualification_code
        / "questions_json"
        / list_group_id
        / "00_source"
    )
    return source_dir.exists() and any(source_dir.glob(filename_glob))
