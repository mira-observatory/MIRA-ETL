from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from mira_etl.config import SourceConfig
from mira_etl.pipeline import filter_source_rows_for_mart, transform_source


CONFIG_DIR = Path(__file__).parents[1] / "config" / "sources"


class ConnectorRoutingTest(unittest.TestCase):
    def test_discovers_the_configured_extraction_types(self) -> None:
        configs = SourceConfig.discover(CONFIG_DIR)
        actual = {config.source: config.download["type"] for config in configs}
        self.assertEqual(
            actual,
            {
                "costa_rica_sicop": "http_zip_csv",
                "guatemala_guatecompras": "http_zip_json",
                "honduras_oncae": "http_jsonl_gz",
                "nicaragua_siscae": "html_session_scrape",
            },
        )

    def test_every_source_selects_a_transform_adapter(self) -> None:
        configs = SourceConfig.discover(CONFIG_DIR)
        self.assertEqual(
            {config.source: config.transform_adapter for config in configs},
            {
                "costa_rica_sicop": "relational_awards_csv",
                "guatemala_guatecompras": "ocds",
                "honduras_oncae": "ocds",
                "nicaragua_siscae": "nicaragua_siscae",  # combina 3 datasets + proveedor/monto, no cabe en el generico "active_procedures"
            },
        )

    def test_new_source_name_reuses_existing_adapter_without_python_routing(self) -> None:
        config = SourceConfig(
            source="el_salvador_active_portal",
            country_code="SV",
            source_system="Portal activo El Salvador",
            connector_version="test",
            download={"type": "html_session_scrape", "base_url": "https://example.test"},
            transform={
                "adapter": "active_procedures",
                "id_prefix": "MIRA-SV-",
                "dataset": "procesos_activos",
                "status_map": {"vigente": "OPEN"},
            },
        )
        rows = {"procesos_activos": [{
            "numero_proceso": "SV-1/2026",
            "tipo_procedimiento": "Licitacion",
            "estado": "Vigente",
            "institucion": "Comprador SV",
        }]}

        records = transform_source(config=config, period="202608", source_rows=rows)

        self.assertEqual(records[0]["country_code"], "SV")
        self.assertEqual(records[0]["process_status"], "OPEN")
        self.assertTrue(records[0]["process_id"].startswith("MIRA-SV-"))

    def test_costa_rica_uses_its_csv_transformer(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "costa_rica_sicop")
        rows = {
            "DetalleCarteles.csv": [
                {
                    "NRO_SICOP": "SICOP-1",
                    "NRO_PROCEDIMIENTO": "CR-1",
                    "CARTEL_NM": "Compra de prueba",
                    "CEDULA_INSTITUCION": "BUYER-1",
                }
            ],
            "ProcedimientoAdjudicacion.csv": [
                {
                    "NRO_SICOP": "SICOP-1",
                    "LINEA": "1",
                    "CEDULA_PROVEEDOR": "SUPPLIER-1",
                    "PROD_ID": "ITEM-1",
                }
            ],
            "InstitucionesRegistradas.csv": [
                {"CEDULA": "BUYER-1", "NOMBRE_INSTITUCION": "Comprador"}
            ],
            "Proveedores.csv": [
                {"CEDULA_PROVEEDOR": "SUPPLIER-1", "NOMBRE_PROVEEDOR": "Proveedor"}
            ],
        }
        records = transform_source(config=config, period="202608", source_rows=rows)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["country_code"], "CR")
        self.assertTrue(records[0]["process_id"].startswith("MIRA-CR-"))

    def test_costa_rica_labels_crc_converted_award_amount_as_crc(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "costa_rica_sicop")
        rows = {
            "DetalleCarteles.csv": [{"NRO_SICOP": "SICOP-CRC"}],
            "ProcedimientoAdjudicacion.csv": [{
                "NRO_SICOP": "SICOP-CRC",
                "LINEA": "1",
                "MONTO_ADJU_LINEA_CRC": "3746618531",
                "MONTO_ADJU_LINEA": "7000000",
                "MONEDA_ADJUDICADA": "USD",
            }],
        }

        award = transform_source(
            config=config, period="202608", source_rows=rows
        )[0]["awards"][0]

        self.assertEqual(award["awarded_amount"], Decimal("3746618531"))
        self.assertEqual(award["currency_code"], "CRC")

    def test_costa_rica_keeps_original_currency_when_crc_amount_is_missing(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "costa_rica_sicop")
        rows = {
            "DetalleCarteles.csv": [{"NRO_SICOP": "SICOP-USD"}],
            "ProcedimientoAdjudicacion.csv": [{
                "NRO_SICOP": "SICOP-USD",
                "LINEA": "1",
                "MONTO_ADJU_LINEA": "7000000",
                "MONEDA_ADJUDICADA": "USD",
            }],
        }

        award = transform_source(
            config=config, period="202608", source_rows=rows
        )[0]["awards"][0]

        self.assertEqual(award["awarded_amount"], Decimal("7000000"))
        self.assertEqual(award["currency_code"], "USD")

    def test_costa_rica_raw_files_match_current_mart_mapping(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "costa_rica_sicop")
        self.assertEqual(
            config.files,
            {
                "required": [
                    "DetalleCarteles.csv",
                    "ProcedimientoAdjudicacion.csv",
                    "InstitucionesRegistradas.csv",
                    "Proveedores.csv",
                ],
                "optional": [],
            },
        )

    def test_costa_rica_raw_rows_keep_only_rows_used_by_mart(self) -> None:
        rows = {
            "DetalleCarteles.csv": [
                {"NRO_SICOP": "SICOP-1", "CEDULA_INSTITUCION": "BUYER-1"},
                {"NRO_SICOP": "SICOP-2", "CEDULA_INSTITUCION": "BUYER-2"},
            ],
            "ProcedimientoAdjudicacion.csv": [
                {"NRO_SICOP": "SICOP-1", "CEDULA": "", "CEDULA_PROVEEDOR": "SUPPLIER-1"}
            ],
            "InstitucionesRegistradas.csv": [
                {"CEDULA": "BUYER-1"},
                {"CEDULA": "BUYER-2"},
            ],
            "Proveedores.csv": [
                {"CEDULA_PROVEEDOR": "SUPPLIER-1"},
                {"CEDULA_PROVEEDOR": "SUPPLIER-2"},
            ],
            "Ofertas.csv": [
                {"NRO_SICOP": "SICOP-1"},
            ],
        }

        filtered = filter_source_rows_for_mart("costa_rica_sicop", rows)

        self.assertEqual(len(filtered["ProcedimientoAdjudicacion.csv"]), 1)
        self.assertEqual(filtered["DetalleCarteles.csv"], [rows["DetalleCarteles.csv"][0]])
        self.assertEqual(filtered["InstitucionesRegistradas.csv"], [rows["InstitucionesRegistradas.csv"][0]])
        self.assertEqual(filtered["Proveedores.csv"], [rows["Proveedores.csv"][0]])
        self.assertNotIn("Ofertas.csv", filtered)

    def test_nicaragua_uses_its_html_transformer(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "nicaragua_siscae")
        rows = {
            "procesos_vigentes": [
                {
                    "tipo_procedimiento": "Licitacion",
                    "numero_proceso": "1/2026",
                    "estado": "Vigente",
                    "codigo_sigaf": None,
                    "institucion": "Comprador",
                    "categoria": "Servicios (12345678)",
                    "descripcion": "Servicio de prueba",
                    "fecha_publicacion": "01/08/2026",
                    "fecha_cierre": "15/08/2026",
                    "ultima_actualizacion": "02/08/2026",
                }
            ]
        }
        records = transform_source(config=config, period="202608", source_rows=rows)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["country_code"], "NI")
        self.assertTrue(records[0]["process_id"].startswith("MIRA-NI-"))

if __name__ == "__main__":
    unittest.main()
