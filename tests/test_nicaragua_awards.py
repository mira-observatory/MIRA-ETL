"""Nicaragua award extraction.

The values here are real: they were captured from the live SISCAE portal on
2026-08-26 while working out why Nicaragua loaded with zero awards.
"""
from __future__ import annotations

from decimal import Decimal

from bs4 import BeautifulSoup

from mira_etl.config import SourceConfig
from mira_etl.extract_html import parse_award_summary
from mira_etl.transform_ni import build_records, parse_amount


def _soup(rows_html: str) -> BeautifulSoup:
    return BeautifulSoup(f"<table>{rows_html}</table>", "html.parser")


# --- parse_award_summary ----------------------------------------------------


def test_parses_supplier_ruc_amount_and_currency() -> None:
    """Verbatim from the portal: the only place supplier and amount exist."""
    soup = _soup(
        "<tr><td>ENERGIA ELECTRICA SOL Y VIENTO SOCIEDAD ANONIMA - J0310000350337</td>"
        "<td></td><td>US$ 1,336,229.69</td><td>1</td></tr>"
    )

    awards = parse_award_summary(soup)

    assert len(awards) == 1
    assert awards[0]["proveedor"] == "ENERGIA ELECTRICA SOL Y VIENTO SOCIEDAD ANONIMA"
    assert awards[0]["ruc"] == "J0310000350337"
    assert awards[0]["monto"] == "1336229.69"
    assert awards[0]["moneda"] == "USD"


def test_cordoba_amounts_are_not_mistaken_for_dollars() -> None:
    """The source writes a symbol, never a code, and both currencies appear.
    Reading C$ as USD would inflate a Nicaraguan award roughly 36-fold."""
    soup = _soup(
        "<tr><td>NARVAEZ SANDOVAL, JUAN ANDRES - 0410401930001Y</td>"
        "<td></td><td>C$ 7,324,833.36</td><td>3</td></tr>"
    )

    awards = parse_award_summary(soup)

    assert awards[0]["moneda"] == "NIO"
    assert awards[0]["monto"] == "7324833.36"


def test_ignores_rows_that_are_not_awards() -> None:
    """The award page nests the same content inside several wrapper tables,
    so anything that is not an amount cell has to be rejected outright."""
    soup = _soup(
        "<tr><td>Proveedor-RUC</td><td>Representada</td>"
        "<td>Monto Adjudicado</td><td>Renglones Adjudicados</td></tr>"
        "<tr><td>Observaciones:</td><td>-</td><td>x</td><td>y</td></tr>"
    )

    assert parse_award_summary(soup) == []


def test_supplier_without_a_ruc_still_yields_the_name() -> None:
    soup = _soup("<tr><td>PROVEEDOR SIN CODIGO</td><td></td><td>C$ 1,000.00</td><td>1</td></tr>")

    awards = parse_award_summary(soup)

    assert awards[0]["proveedor"] == "PROVEEDOR SIN CODIGO"
    assert awards[0]["ruc"] is None


# --- parse_amount -----------------------------------------------------------


def test_amount_becomes_a_decimal_not_a_float() -> None:
    """Money through a float would lose cents on the way to the database."""
    assert parse_amount("1336229.69") == Decimal("1336229.69")


def test_unparseable_amount_is_null_rather_than_zero() -> None:
    """Zero is a claim about the world; null says the source did not say."""
    assert parse_amount("") is None
    assert parse_amount("no disponible") is None


# --- build_records ----------------------------------------------------------


CONFIG = SourceConfig(
    source="nicaragua_siscae",
    country_code="NI",
    source_system="SISCAE Nicaragua",
    connector_version="ni-siscae-0.2.0",
    download={"type": "html_session_scrape", "base_url": "https://example.test"},
    files={"required": [], "optional": []},
    csv={},
)

AWARDED_ROW = {
    "tipo_procedimiento": "LICITACION SELECTIVA",
    "numero_proceso": "3/2026",
    "estado": "Adjudicado",
    "codigo_sigaf": None,
    "institucion": "Alcaldía Potosí",
    "categoria": "Obras (72100000)",
    "descripcion": "Mejoramiento de calle",
    "fecha_publicacion": "07/08/2026",
    "fecha_cierre": "12/08/2026 03:00:00 PM",
    "ultima_actualizacion": "19/08/2026 07:54:32 PM",
}


def _records(source_rows: dict) -> list[dict]:
    return build_records(
        config=CONFIG,
        period="2026",
        connector_version="ni-siscae-0.2.0",
        source_rows=source_rows,
    )


def test_awarded_process_carries_supplier_and_amount() -> None:
    row = dict(AWARDED_ROW)
    row["adjudicaciones"] = [
        {
            "proveedor": "NARVAEZ SANDOVAL, JUAN ANDRES",
            "ruc": "0410401930001Y",
            "moneda": "NIO",
            "monto": "7324833.36",
            "renglones": "3",
        }
    ]

    records = _records({"procesos_adjudicados": [row]})

    assert len(records) == 1
    assert records[0]["awarded_amount"] == Decimal("7324833.36")
    assert records[0]["currency_code"] == "NIO"
    assert records[0]["supplier_tax_id"] == "0410401930001Y"
    assert records[0]["process_status"] == "AWARDED"


def test_each_awarded_supplier_becomes_its_own_record() -> None:
    """Same grain as Costa Rica. Collapsing two suppliers onto one row would
    make the second award vanish."""
    row = dict(AWARDED_ROW)
    row["adjudicaciones"] = [
        {"proveedor": "EMPRESA A", "ruc": "J001", "moneda": "NIO", "monto": "100", "renglones": "1"},
        {"proveedor": "EMPRESA B", "ruc": "J002", "moneda": "NIO", "monto": "200", "renglones": "2"},
    ]

    records = _records({"procesos_adjudicados": [row]})

    assert len(records) == 2
    assert {r["supplier_name"] for r in records} == {"EMPRESA A", "EMPRESA B"}
    # Distinct ids, or the mart upsert would keep only the last one.
    assert len({r["process_id"] for r in records}) == 2


def test_awarded_process_without_published_detail_is_still_recorded() -> None:
    """SISCAE publishes award detail for only a minority of awarded processes
    (the formal tenders). Dropping the rest would hide most of what the
    country actually awarded -- the process is real either way, only the
    counterparty is undisclosed."""
    row = dict(AWARDED_ROW)
    row["adjudicaciones"] = []

    records = _records({"procesos_adjudicados": [row]})

    assert len(records) == 1
    assert records[0]["process_status"] == "AWARDED"
    assert records[0]["awarded_amount"] is None
    assert records[0]["supplier_name"] is None


def test_open_processes_keep_loading_alongside_awarded_ones() -> None:
    open_row = dict(AWARDED_ROW, estado="Vigente", numero_proceso="9/2026")

    records = _records(
        {"procesos_adjudicados": [dict(AWARDED_ROW)], "procesos_vigentes": [open_row]}
    )

    assert {r["process_status"] for r in records} == {"AWARDED", "OPEN"}


def test_payload_hash_does_not_depend_on_how_much_detail_was_fetched() -> None:
    """`adjudicaciones` is navigation state. If it fed the hash, the same
    process would look "changed" on every run purely because a batch stopped
    at a different point."""
    without = _records({"procesos_adjudicados": [dict(AWARDED_ROW)]})[0]
    with_empty = _records({"procesos_adjudicados": [dict(AWARDED_ROW, adjudicaciones=[])]})[0]

    assert without["raw_payload_hash"] == with_empty["raw_payload_hash"]
    assert "adjudicaciones" not in without["raw_payload"]["proceso"]
