from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mira_etl.config import SourceConfig
from mira_etl.pipeline import process_json_records


class FakeDatabase:
    def __init__(self) -> None:
        self.raw_batch_sizes: list[int] = []
        self.raw_start_rows: list[int] = []
        self.staging_batch_sizes: list[int] = []
        self.source_row_count = 0
        self.mart_count = 0

    def insert_source_file(self, **kwargs: Any) -> int:
        return 1

    def insert_raw_rows(self, *, rows: list[dict[str, Any]], **kwargs: Any) -> int:
        self.raw_batch_sizes.append(len(rows))
        self.raw_start_rows.append(kwargs["start_row_number"])
        return len(rows)

    def insert_staging_candidates(
        self,
        *,
        records: list[dict[str, Any]],
        **kwargs: Any,
    ) -> int:
        self.staging_batch_sizes.append(len(records))
        return len(records)

    def insert_validation_results(self, *, results: list[dict[str, Any]], **kwargs: Any) -> int:
        return len(results)

    def upsert_mart_split_records(self, records: list[dict[str, Any]]) -> int:
        self.mart_count += len(records)
        return len(records)

    def update_source_file_row_count(self, source_file_id: int, row_count: int) -> None:
        self.source_row_count = row_count

    def insert_row_count(self, **kwargs: Any) -> None:
        pass


class JsonStreamingTest(unittest.TestCase):
    def test_json_config_uses_env_batch_size(self) -> None:
        config = SourceConfig.load(
            Path(__file__).parents[1] / "config" / "sources",
            "guatemala_guatecompras",
        )

        with patch.dict("os.environ", {"MIRA_JSON_BATCH_SIZE": "7"}):
            self.assertEqual(config.batch_size, 7)

    def test_processes_records_in_configured_batches(self) -> None:
        config = SourceConfig(
            source="guatemala_guatecompras",
            country_code="GT",
            source_system="Guatecompras OCDS",
            connector_version="test",
            download={
                "url_template": "https://example.test/{year}/{month}",
            },
            files={"required": [], "optional": []},
            csv={"encoding": "utf-8"},
            processing={"batch_size": 2, "record_limit": 3},
            transform={"adapter": "ocds", "id_prefix": "MIRA-GT-"},
        )
        payload = {
            "records": [ocds_record(index) for index in range(5)],
        }

        with tempfile.TemporaryDirectory() as directory:
            extract_dir = Path(directory)
            json_path = extract_dir / "guatemala.json"
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            db = FakeDatabase()

            process_json_records(
                db=db,  # type: ignore[arg-type]
                run_id=1,
                config=config,
                period="202601",
                connector_version="test",
                extract_dir=extract_dir,
            )

        self.assertEqual(db.raw_batch_sizes, [2, 1])
        self.assertEqual(db.raw_start_rows, [1, 3])
        self.assertEqual(db.staging_batch_sizes, [2, 1])
        self.assertEqual(db.source_row_count, 3)
        self.assertEqual(db.mart_count, 3)


def ocds_record(index: int) -> dict[str, Any]:
    ocid = f"ocds-test-{index}"
    return {
        "ocid": ocid,
        "compiledRelease": {
            "ocid": ocid,
            "date": "2026-01-01T00:00:00-06:00",
            "buyer": {"id": f"GT-NIT-{index}", "name": "Buyer"},
            "tender": {
                "id": f"GT-NOG-{index}",
                "title": "Tender",
                "status": "active",
                "procurementMethod": "open",
                "datePublished": "2026-01-01T00:00:00-06:00",
                "items": [{"description": "Item"}],
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
