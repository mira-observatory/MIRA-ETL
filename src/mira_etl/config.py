from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mira_etl.env import load_dotenv


@dataclass(frozen=True)
class SourceConfig:
    source: str
    country_code: str
    source_system: str
    connector_version: str
    download: dict[str, Any]
    files: dict[str, list[str]] = field(default_factory=lambda: {"required": [], "optional": []})
    csv: dict[str, Any] = field(default_factory=dict)
    processing: dict[str, Any] = field(default_factory=dict)
    transform: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: Path, source: str) -> "SourceConfig":
        path = config_dir / f"{source}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Source configuration not found: {path}")
        with path.open("r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        config = cls(**payload)
        if config.source != source:
            raise ValueError(
                f"Configuration {path} declares source '{config.source}', "
                f"expected '{source}'"
            )
        return config

    @classmethod
    def discover(cls, config_dir: Path) -> list["SourceConfig"]:
        configs = [cls.load(config_dir, path.stem) for path in sorted(config_dir.glob("*.json"))]
        if not configs:
            raise FileNotFoundError(f"No source configurations found in {config_dir}")
        return configs

    def delimiter_for(self, filename: str) -> str:
        delimiters = self.csv.get("delimiters", {})
        return delimiters.get(filename, self.csv.get("default_delimiter", ";"))

    @property
    def transform_adapter(self) -> str:
        adapter = self.transform.get("adapter")
        if not adapter:
            raise ValueError(
                f"Source '{self.source}' must define transform.adapter"
            )
        return str(adapter)

    def source_url_for_period(
        self,
        period: str,
    ) -> str:

        if len(period) != 6 or not period.isdigit():
            raise ValueError(
                f"Invalid period '{period}'. "
                "Expected YYYYMM."
            )

        year = period[:4]
        month = str(int(period[4:6]))

        return self.download[
            "url_template"
        ].format(
            period=period,
            year=year,
            month=month,
        )

    @property
    def encoding(self) -> str:
        return self.csv.get("encoding", "utf-8-sig")

    @property
    def batch_size(self) -> int:
        load_dotenv()
        env_value = (
            os.environ.get("MIRA_JSON_BATCH_SIZE")
            if self.download.get("type") in {"http_zip_json", "http_jsonl_gz"}
            else None
        ) or os.environ.get("MIRA_ETL_BATCH_SIZE")
        value = int(env_value or self.processing.get("batch_size", 250))
        if value < 1:
            raise ValueError("batch size must be greater than zero")
        return value

    @property
    def record_limit(self) -> int | None:
        value = self.processing.get("record_limit")
        if value is None:
            return None
        limit = int(value)
        if limit < 1:
            raise ValueError("processing.record_limit must be greater than zero")
        return limit
