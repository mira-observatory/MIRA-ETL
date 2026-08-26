from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from mira_etl.config import SourceConfig
from mira_etl.hashutil import stable_id, stable_json_hash


MINIMUM_FIELDS = [
    "process_number",
    "title",
    "buyer_name",
    "buyer_tax_id",
    "procurement_method",
    "process_status",
    "publication_date",
]

def build_records(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_rows: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, Any]]:
    """Build MIRA records from the shared active-procedures row shape."""
    dataset = str(config.transform.get("dataset", "procesos_vigentes"))
    active_procedures = source_rows.get(dataset, [])
    id_prefix = str(config.transform.get("id_prefix", f"MIRA-{config.country_code}-"))
    item_prefix = f"{id_prefix.rstrip('-')}-ITEM-"
    status_map = {
        str(key).lower(): str(value)
        for key, value in (config.transform.get("status_map") or {}).items()
    }
    extracted_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []

    for row in active_procedures:
        source_record_id = build_source_record_id(row)
        raw_payload = {"proceso": row}

        description = row.get("descripcion")

        record = {
            "process_id": stable_id(config.country_code, source_record_id, prefix=id_prefix),
            "process_number": row.get("numero_proceso"),
            "title": description,
            "description": description,
            "buyer_name": row.get("institucion"),
            "buyer_id_source": None,
            "buyer_tax_id": None,
            "procurement_method": row.get("tipo_procedimiento"),
            "process_status": normalise_status(row.get("estado"), status_map),
            "source_status": row.get("estado"),
            "publication_date": parse_datetime(row.get("fecha_publicacion")),
            "closing_date": parse_datetime(row.get("fecha_cierre")),
            "estimated_amount": None,
            "currency_code": None,
            "items": [{
                "item_id": stable_id(
                    config.country_code, source_record_id, "summary",
                    prefix=item_prefix,
                ),
                "source_item_id": None,
                "line_number": None,
                "item_description": description,
                "category_source": row.get("categoria"),
                "category_normalised": None,
            }],
            "awards": [],
            "country_code": config.country_code,
            "source_system": config.source_system,
            "source_record_id": source_record_id,
            "source_url": config.download.get("base_url"),
            "extracted_at": extracted_at,
            "source_last_modified_at": parse_datetime(row.get("ultima_actualizacion")),
            "connector_version": connector_version,
            "raw_payload": raw_payload,
            "raw_payload_hash": stable_json_hash(raw_payload),
            "normalisation_status": "PROCESSED",
            "normalised_at": datetime.now(UTC),
            "data_quality_status": "PARTIAL",
            "missing_fields": [],
        }
        record["missing_fields"] = [field for field in MINIMUM_FIELDS if record.get(field) is None]
        record["data_quality_status"] = "COMPLETE" if not record["missing_fields"] else "PARTIAL"
        records.append(record)

    return records


def build_source_record_id(row: dict[str, str | None]) -> str:
    """SISCAE's own reference code (Codigo SIGAF) is frequently unset (rendered
    as a literal "#" placeholder, already normalised to None in extract_html).
    Fall back to a composite of procedure type + number + buyer, which is
    stable across re-scrapes of the same listing."""
    sigaf_code = row.get("codigo_sigaf")
    if sigaf_code:
        return sigaf_code
    procedure_type = row.get("tipo_procedimiento") or ""
    procedure_number = row.get("numero_proceso") or ""
    buyer_name = (row.get("institucion") or "")[:30]
    return f"{procedure_type}-{procedure_number}-{buyer_name}".strip("-")


def normalise_status(
    source_status: str | None,
    status_map: dict[str, str],
) -> str | None:
    if not source_status:
        return None
    return status_map.get(source_status.strip().lower())


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
