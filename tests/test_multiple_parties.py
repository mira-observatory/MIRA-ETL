from __future__ import annotations

import unittest
from pathlib import Path

from mira_etl.config import SourceConfig
from mira_etl.db import buyer_records_for, supplier_records_for
from mira_etl.adapters.ocds import build_record


class MultiplePartiesTest(unittest.TestCase):
    def test_guatemala_keeps_suppliers_from_all_awards_and_contracts(self) -> None:
        config = SourceConfig.load(
            Path(__file__).parents[1] / "config" / "sources",
            "guatemala_guatecompras",
        )
        source_row = {
            "ocid": "ocds-test-multiple",
            "compiledRelease": {
                "ocid": "ocds-test-multiple",
                "buyer": {"id": "GT-NIT-1", "name": "Comprador"},
                "tender": {"id": "NOG-1", "title": "Compra"},
                "awards": [
                    {"id": "AWARD-1", "suppliers": [
                        {"id": "GT-NIT-10", "name": "Proveedor A"},
                        {"id": "GT-NIT-20", "name": "Proveedor B"},
                    ]}
                ],
                "contracts": [
                    {"awardID": "AWARD-1", "suppliers": [
                        {"id": "GT-NIT-20", "name": "Proveedor B"},
                        {"id": "GT-NIT-30", "name": "Proveedor C"},
                    ]}
                ],
            },
        }

        record = build_record(
            config=config,
            period="202601",
            connector_version="test",
            source_row=source_row,
        )

        self.assertEqual(
            [supplier["supplier_name"] for supplier in record["awards"][0]["suppliers"]],
            ["Proveedor A", "Proveedor B", "Proveedor C"],
        )
        self.assertEqual(
            [candidate["supplier_id_source"] for candidate in supplier_records_for(record)],
            ["GT-NIT-10", "GT-NIT-20", "GT-NIT-30"],
        )

    def test_scalar_supplier_remains_supported(self) -> None:
        record = {
            "process_id": "process-1",
            "supplier_name": "Proveedor unico",
            "supplier_id_source": "supplier-1",
        }

        self.assertEqual(supplier_records_for(record), [record])

    def test_process_without_supplier_creates_no_relationship_candidate(self) -> None:
        self.assertEqual(supplier_records_for({"process_id": "process-1"}), [])

    def test_multiple_buyers_are_expanded(self) -> None:
        record = {
            "process_id": "process-1",
            "country_code": "GT",
            "buyers": [
                {"buyer_name": "Comprador A", "buyer_id_source": "buyer-1"},
                {"buyer_name": "Comprador B", "buyer_id_source": "buyer-2"},
            ],
        }

        self.assertEqual(
            [candidate["buyer_id_source"] for candidate in buyer_records_for(record)],
            ["buyer-1", "buyer-2"],
        )

    def test_guatemala_keeps_buyer_and_distinct_procuring_entity(self) -> None:
        config = SourceConfig.load(
            Path(__file__).parents[1] / "config" / "sources",
            "guatemala_guatecompras",
        )
        record = build_record(
            config=config,
            period="202601",
            connector_version="test",
            source_row={
                "ocid": "ocds-test-buyers",
                "compiledRelease": {
                    "buyer": {"id": "GT-NIT-1", "name": "Comprador A"},
                    "tender": {
                        "procuringEntity": {
                            "id": "GT-NIT-2",
                            "name": "Comprador B",
                        }
                    },
                },
            },
        )

        self.assertEqual(
            [buyer["buyer_name"] for buyer in record["buyers"]],
            ["Comprador A", "Comprador B"],
        )

    def test_scalar_buyer_remains_supported(self) -> None:
        record = {
            "process_id": "process-1",
            "buyer_name": "Comprador unico",
            "buyer_id_source": "buyer-1",
        }
        self.assertEqual(buyer_records_for(record), [record])


if __name__ == "__main__":
    unittest.main()
