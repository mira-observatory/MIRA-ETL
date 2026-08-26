from __future__ import annotations

import unittest

from mira_etl.validation import validate_records


class ValidationTest(unittest.TestCase):
    def test_costa_rica_grouped_awards_raw_payload_validates(self) -> None:
        record = {
            "process_id": "process-1",
            "process_number": "2026-1",
            "title": "Compra",
            "buyer_name": "Comprador",
            "buyer_tax_id": "BUYER-1",
            "procurement_method": "Licitacion",
            "process_status": "AWARDED",
            "publication_date": None,
            "closing_date": None,
            "source_last_modified_at": None,
            "awards": [],
            "raw_payload": {
                "procedimiento_adjudicacion": [
                    {
                        "NUMERO_PROCEDIMIENTO": "2026-1",
                        "INSTITUCION": "Comprador",
                        "FECHA_ADJUD_FIRME": "not-a-date",
                        "fecha_rev": "also-not-a-date",
                    }
                ],
                "detalle_cartel": {
                    "NRO_PROCEDIMIENTO": "2026-1",
                    "CARTEL_NM": "Compra",
                    "FECHA_PUBLICACION": "not-a-date",
                },
            },
        }

        results = validate_records([record])

        self.assertEqual(
            [result["field_name"] for result in results if result["rule_code"] == "UNPARSEABLE_DATE"],
            ["publication_date", "source_last_modified_at"],
        )


if __name__ == "__main__":
    unittest.main()
