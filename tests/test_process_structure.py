from __future__ import annotations

import unittest
from pathlib import Path

from mira_etl.config import SourceConfig
from mira_etl.pipeline import transform_source


CONFIG_DIR = Path(__file__).parents[1] / "config" / "sources"


class ProcessStructureTest(unittest.TestCase):
    def test_costa_rica_groups_lines_under_one_process(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "costa_rica_sicop")
        rows = {
            "DetalleCarteles.csv": [
                {"NRO_SICOP": "SICOP-1", "CEDULA_INSTITUCION": "BUYER-1"}
            ],
            "ProcedimientoAdjudicacion.csv": [
                {"NRO_SICOP": "SICOP-1", "LINEA": "1", "PROD_ID": "ITEM-1"},
                {"NRO_SICOP": "SICOP-1", "LINEA": "2", "PROD_ID": "ITEM-2"},
            ],
            "InstitucionesRegistradas.csv": [{"CEDULA": "BUYER-1"}],
            "Proveedores.csv": [],
        }

        records = transform_source(config=config, period="202608", source_rows=rows)

        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["items"]), 2)
        self.assertEqual(len(records[0]["awards"]), 2)
        self.assertNotIn("grain", records[0])

    def test_nicaragua_emits_process_with_nested_item(self) -> None:
        config = SourceConfig.load(CONFIG_DIR, "nicaragua_siscae")
        records = transform_source(
            config=config,
            period="202608",
            source_rows={"procesos_vigentes": [{
                "tipo_procedimiento": "Licitacion",
                "numero_proceso": "1/2026",
                "estado": "Vigente",
                "institucion": "Comprador",
            }]},
        )

        self.assertEqual(len(records[0]["items"]), 1)
        self.assertEqual(records[0]["awards"], [])
        self.assertNotIn("grain", records[0])


if __name__ == "__main__":
    unittest.main()
