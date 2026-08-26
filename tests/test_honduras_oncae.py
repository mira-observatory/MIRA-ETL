from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from mira_etl.config import SourceConfig
from mira_etl.adapters.ocds import build_record
from mira_etl.pipeline import period_of, process_jsonl_records

CONFIG_DIR = Path(__file__).parents[1] / "config" / "sources"


class FakeDatabase:
    def __init__(self) -> None:
        self.raw_batch_sizes: list[int] = []
        self.raw_start_rows: list[int] = []
        self.source_row_count = 0
        self.records: list[dict[str, Any]] = []

    def insert_source_file(self, **kwargs: Any) -> int:
        return 1

    def insert_raw_rows(self, *, rows: list[dict[str, Any]], **kwargs: Any) -> int:
        self.raw_batch_sizes.append(len(rows))
        self.raw_start_rows.append(kwargs["start_row_number"])
        return len(rows)

    def insert_staging_candidates(self, *, records: list[dict[str, Any]], **kwargs: Any) -> int:
        self.records.extend(records)
        return len(records)

    def insert_validation_results(self, *, results: list[dict[str, Any]], **kwargs: Any) -> int:
        return len(results)

    def upsert_mart_split_records(self, records: list[dict[str, Any]]) -> int:
        return len(records)

    def update_source_file_row_count(self, source_file_id: int, row_count: int) -> None:
        self.source_row_count = row_count

    def insert_row_count(self, **kwargs: Any) -> None:
        pass


class HondurasOncaeMappingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = SourceConfig.load(CONFIG_DIR, "honduras_oncae")

    def build(self, release: dict[str, Any]) -> dict[str, Any]:
        return build_record(
            config=self.config,
            period="202410",
            connector_version=self.config.connector_version,
            source_row=release,
        )

    def test_honducompras_release_maps_all_target_fields(self) -> None:
        record = self.build(honducompras_release())

        self.assertEqual(record["process_number"], "0rw29R-CM-SDE-142-2024-1")
        self.assertEqual(record["title"], "CM-SDE-142-2024")
        self.assertEqual(record["procurement_method"], "Compra Menor")
        self.assertEqual(record["process_status"], "CONTRACTED")
        self.assertEqual(record["source_status"], "Adjudicado")
        self.assertEqual(record["currency_code"], "HNL")
        self.assertEqual(record["publication_date"].isoformat(), "2024-10-09T14:16:05.460000-06:00")
        self.assertEqual(record["closing_date"].isoformat(), "2024-10-10T10:15:00-06:00")
        self.assertEqual(record["source_url"], "http://h1.honducompras.gob.hn/")
        self.assertEqual(record["country_code"], "HN")
        self.assertEqual(record["source_record_id"], "ocds-lcuori-CM-SDE-142-2024-1")

        # Comprador: sube por memberOf hasta la institucion raiz
        self.assertEqual(record["buyer_name"], "Secretaria de Desarrollo Economico")
        self.assertEqual(record["buyer_id_source"], "7rBOL0")
        # El codigo interno X-HN-ONCAE-CE NO es RTN -> buyer_tax_id debe ser None
        self.assertIsNone(record["buyer_tax_id"])

        # Proveedor adjudicado y montos
        award = record["awards"][0]
        self.assertEqual(award["awarded_amount"], Decimal("13282.5"))
        self.assertEqual(award["currency_code"], "HNL")
        self.assertEqual(award["award_date"].isoformat(), "2024-10-11T09:00:00-06:00")
        supplier = award["suppliers"][0]
        self.assertEqual(supplier["supplier_name"], "Eventos y Banquetes")
        self.assertEqual(supplier["supplier_id_source"], "HN-RTN-08019004002712")
        self.assertEqual(supplier["supplier_tax_id"], "08019004002712")
        self.assertEqual(supplier["supplier_type"], "UNKNOWN")

        # Items
        item = record["items"][0]
        self.assertEqual(item["item_description"], "38 almuerzos con refresco")
        self.assertEqual(item["category_source"], "90101604")

    def test_open_process_without_awards_has_no_amounts_or_supplier(self) -> None:
        release = honducompras_release()
        release["contracts"] = []
        release["awards"] = []
        release["tender"]["statusDetails"] = "Recepción de Ofertas"
        release["tender"]["status"] = "active"

        record = self.build(release)

        self.assertEqual(record["process_status"], "OPEN")
        self.assertEqual(len(record["awards"]), 0)

    def test_catalogo_electronico_release_without_tender_dates(self) -> None:
        record = self.build(catalogo_release())

        self.assertEqual(record["process_number"], "343064")
        self.assertEqual(record["awards"][0]["awarded_amount"], Decimal("1235500.2"))
        self.assertEqual(record["awards"][0]["currency_code"], "HNL")
        self.assertEqual(record["process_status"], "AWARDED")
        self.assertEqual(record["items"][0]["item_description"], "Compra de llantas")
        # Sin tender.datePublished, la unica fecha de la fuente es la del release.
        self.assertEqual(record["publication_date"].isoformat(), "2024-01-03T15:26:05-06:00")
        self.assertEqual(record["source_url"], "http://h1.honducompras.gob.hn/ConvenioMarco/")

    def test_release_without_ocid_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ocid"):
            self.build({"tender": {"title": "sin ocid"}})


class HondurasStreamingTest(unittest.TestCase):
    def test_config_declares_the_yearly_jsonl_download(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "honduras_oncae")

        self.assertEqual(config.download["type"], "http_jsonl_gz")
        self.assertEqual(
            config.source_url_for_period("202410"),
            "https://data.open-contracting.org/en/publication/122/download?name=2024.jsonl.gz",
        )

    def test_period_of_reads_the_publisher_local_month(self) -> None:
        self.assertEqual(period_of({"date": "2024-10-09T14:16:05.460000-06:00"}), "202410")
        self.assertEqual(period_of({"compiledRelease": {"date": "2024-01-03T00:00:00-06:00"}}), "202401")
        self.assertIsNone(period_of({"date": "no es una fecha"}))
        self.assertIsNone(period_of({}))

    def test_only_the_requested_month_is_loaded_and_in_batches(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "honduras_oncae")
        lines = [
            release_for_month(index, month)
            for month in ("09", "10", "11")
            for index in range(3)
        ]

        with tempfile.TemporaryDirectory() as directory:
            jsonl_path = Path(directory) / "2024.jsonl.gz"
            with gzip.open(jsonl_path, "wt", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")

            db = FakeDatabase()
            with patch.dict("os.environ", {"MIRA_JSON_BATCH_SIZE": "2"}):
                process_jsonl_records(
                    db=db,  # type: ignore[arg-type]
                    run_id=1,
                    config=config,
                    period="202410",
                    connector_version=config.connector_version,
                    jsonl_path=jsonl_path,
                )

        self.assertEqual(db.raw_batch_sizes, [2, 1])
        self.assertEqual(db.raw_start_rows, [1, 3])
        self.assertEqual(db.source_row_count, 3)
        self.assertEqual(
            [record["source_record_id"] for record in db.records],
            ["ocds-lcuori-10-0", "ocds-lcuori-10-1", "ocds-lcuori-10-2"],
        )


def honducompras_release() -> dict[str, Any]:
    return {
        "id": "ocds-lcuori-CM-SDE-142-2024-1-2024-10-09T14:16:05.460000-06:00",
        "tag": ["compiled"],
        "date": "2024-10-09T14:16:05.460000-06:00",
        "ocid": "ocds-lcuori-CM-SDE-142-2024-1",
        "buyer": {"id": "0rw29R-7rBOL0", "name": "Unidad Central"},
        "awards": [
            {
                "id": "CM-SDE-142-2024-1",
                "items": [
                    {
                        "id": "1918024",
                        "description": "38 almuerzos con refresco",
                        "classification": {
                            "id": "90101604",
                            "scheme": "UNSPSC",
                            "description": "Servicios de catering",
                        },
                    }
                ],
                "suppliers": [
                    {"id": "HN-RTN-08019004002712", "name": "Eventos y Banquetes"}
                ],
            }
        ],
        "tender": {
            "id": "0rw29R-CM-SDE-142-2024-1",
            "title": "CM-SDE-142-2024",
            "status": "complete",
            "statusDetails": "Adjudicado",
            "tenderPeriod": {
                "startDate": "2024-10-09T14:15:00-06:00",
                "endDate": "2024-10-10T10:15:00-06:00",
            },
            "datePublished": "2024-10-09T14:16:05.460000-06:00",
            "procurementMethod": "open",
            "procurementMethodDetails": "Compra Menor",
        },
        "parties": [
            {
                "id": "7rBOL0",
                "name": "Secretaria de Desarrollo Economico",
                "roles": ["buyer"],
                "identifier": {"id": "7rBOL0", "scheme": "X-HN-ONCAE-CE"},
            },
            {
                "id": "0rw29R-7rBOL0",
                "name": "Unidad Central",
                "roles": ["buyer"],
                "memberOf": [{"id": "7rBOL0", "name": "Secretaria de Desarrollo Economico"}],
                "identifier": {"id": "0rw29R-7rBOL0", "scheme": "X-HN-ONCAE-CE"},
            },
            {
                "id": "HN-RTN-08019004002712",
                "name": "Eventos y Banquetes",
                "roles": ["supplier"],
                "identifier": {"id": "08019004002712", "scheme": "HN-RTN"},
            },
        ],
        "sources": {
            "id": "honducompras-1",
            "url": "http://h1.honducompras.gob.hn/",
            "name": "HonduCompras 1.0",
        },
        "contracts": [
            {
                "id": "CM-SDE-142-2024",
                "awardID": "CM-SDE-142-2024-1",
                "value": {"amount": 13282.5, "currency": "HNL"},
                "dateSigned": "2024-10-11T09:00:00-06:00",
            }
        ],
    }


def catalogo_release() -> dict[str, Any]:
    return {
        "id": "ocds-lcuori-343064-2024-01-03T15:26:05-06:00",
        "tag": ["compiled"],
        "date": "2024-01-03T15:26:05-06:00",
        "ocid": "ocds-lcuori-343064",
        "buyer": {"id": "byPqXK", "name": "Alcaldia Municipal"},
        "awards": [
            {
                "id": 343064,
                "value": {"amount": 1235500.2, "currency": "HNL"},
                "description": "Compra de llantas",
            }
        ],
        "tender": {
            "id": 343064,
            "procurementMethod": "open",
            "procurementMethodDetails": "Convenio Marco",
        },
        "parties": [
            {
                "id": "byPqXK",
                "name": "Alcaldia Municipal",
                "roles": ["buyer"],
                "identifier": {"id": "byPqXK", "scheme": "X-HN-ONCAE-CE"},
            }
        ],
        "sources": [
            {
                "id": "catalogo-electronico",
                "url": "http://h1.honducompras.gob.hn/ConvenioMarco/",
                "name": "Catalogo Electronico",
            }
        ],
    }


def release_for_month(index: int, month: str) -> dict[str, Any]:
    return {
        "ocid": f"ocds-lcuori-{month.lstrip('0')}-{index}",
        "date": f"2024-{month}-05T10:00:00-06:00",
        "buyer": {"id": "B1", "name": "Comprador"},
        "tender": {
            "id": f"T-{month}-{index}",
            "title": f"CM-{month}-{index}",
            "status": "active",
            "statusDetails": "Recepción de Ofertas",
            "procurementMethodDetails": "Compra Menor",
            "datePublished": f"2024-{month}-05T10:00:00-06:00",
            "items": [{"description": "Bien de prueba"}],
        },
        "parties": [{"id": "B1", "name": "Comprador", "roles": ["buyer"]}],
    }


if __name__ == "__main__":
    unittest.main()
