from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mira_etl.adapters.active_procedures import build_records as build_active_procedures
from mira_etl.adapters.ocds import build_record as build_ocds_record
from mira_etl.adapters.relational_awards_csv import build_records as build_relational_awards
from mira_etl.config import SourceConfig
from mira_etl.transform_ni import build_records as build_nicaragua_siscae


BatchAdapter = Callable[..., list[dict[str, Any]]]
RecordAdapter = Callable[..., dict[str, Any]]

BATCH_ADAPTERS: dict[str, BatchAdapter] = {
    "active_procedures": build_active_procedures,
    "relational_awards_csv": build_relational_awards,
    # Nicaragua no cabe en "active_procedures": necesita combinar tres
    # datasets (vigentes, cerrados, adjudicados) y adjuntar proveedor/monto
    # cuando SISCAE publica esa pestana. Ver extract_html.scrape_nicaragua_extras.
    "nicaragua_siscae": build_nicaragua_siscae,
}

RECORD_ADAPTERS: dict[str, RecordAdapter] = {
    "ocds": build_ocds_record,
}


def transform_batch(
    *, config: SourceConfig, period: str,
    source_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    adapter_name = config.transform_adapter
    try:
        adapter = BATCH_ADAPTERS[adapter_name]
    except KeyError as exc:
        if adapter_name in RECORD_ADAPTERS:
            raise ValueError(
                f"Transform adapter '{adapter_name}' requires streaming records"
            ) from exc
        raise ValueError(f"Unsupported transform adapter: {adapter_name}") from exc
    return adapter(
        config=config,
        period=period,
        connector_version=config.connector_version,
        source_rows=source_rows,
    )


def transform_record(
    *, config: SourceConfig, period: str, source_row: dict[str, Any],
) -> dict[str, Any]:
    adapter_name = config.transform_adapter
    try:
        adapter = RECORD_ADAPTERS[adapter_name]
    except KeyError as exc:
        if adapter_name in BATCH_ADAPTERS:
            raise ValueError(
                f"Transform adapter '{adapter_name}' requires a complete source dataset"
            ) from exc
        raise ValueError(f"Unsupported transform adapter: {adapter_name}") from exc
    return adapter(
        config=config,
        period=period,
        connector_version=config.connector_version,
        source_row=source_row,
    )
