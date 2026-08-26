from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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
    "award_date",
    "awarded_amount",
    "currency_code",
    "supplier_name",
    "supplier_tax_id",
    "item_description",
]

# Nicaragua only exposes a single free-text status; MIRA needs it mapped onto its
# fixed catalog (see sql/001_init.sql, mart.procurement_process_details.process_status).
STATUS_MAP = {
    "vigente": "OPEN",
    "adjudicado": "AWARDED",
    "en ejecucion": "CONTRACTED",
    "ejecucion": "CONTRACTED",
    "cancelado": "CANCELLED",
    "desierto": "DESERTED",
    "suspendido": "SUSPENDED",
    "cerrado": "COMPLETED",
}


def build_records(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_rows: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, Any]]:
    """Builds MIRA-shaped records from SISCAE's process listings.

    Two datasets feed this. "procesos_vigentes" are open and, by definition,
    carry no supplier or amount. "procesos_adjudicados" are awarded, and are
    the reason Nicaragua used to load with zero awards: the connector could
    only ever read the VIGENTE listing.

    An awarded process that publishes its award detail yields one record per
    awarded supplier -- the same grain Costa Rica uses, where `process_id` is
    hashed over the supplier as well. One that does not publish it still
    yields its process row, with the award fields left null: the process is
    real and awarded either way, only the counterparty is undisclosed.
    """
    extracted_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []

    for row in source_rows.get("procesos_adjudicados", []):
        awards = row.get("adjudicaciones") or [None]
        for award in awards:
            records.append(
                _build_record(
                    config=config,
                    connector_version=connector_version,
                    row=row,
                    extracted_at=extracted_at,
                    award=award,
                )
            )

    for row in source_rows.get("procesos_vigentes", []):
        records.append(
            _build_record(
                config=config,
                connector_version=connector_version,
                row=row,
                extracted_at=extracted_at,
                award=None,
            )
        )

    return records


def _build_record(
    *,
    config: SourceConfig,
    connector_version: str,
    row: dict[str, Any],
    extracted_at: datetime,
    award: dict[str, Any] | None,
) -> dict[str, Any]:
    """One MIRA record: a process, optionally joined to one of its awards."""
    source_record_id = build_source_record_id(row)
    if award is not None:
        # Distinct suppliers on the same process must not collapse onto one
        # id, or the second award would silently overwrite the first.
        source_record_id = f"{source_record_id}-{award.get('ruc') or award.get('proveedor')}"

    # `adjudicaciones` is navigation state, not source data: keeping it inside
    # raw_payload would make the hash depend on how much of the detail this
    # particular run managed to fetch.
    proceso = {k: v for k, v in row.items() if k != "adjudicaciones"}
    raw_payload: dict[str, Any] = {"proceso": proceso}
    if award is not None:
        raw_payload["adjudicacion"] = award

    description = row.get("descripcion")
    record = {
        "process_id": stable_id(config.country_code, source_record_id, prefix="MIRA-NI-"),
        "process_number": row.get("numero_proceso"),
        "title": description,
        "description": description,
        "buyer_name": row.get("institucion"),
        "buyer_id_source": None,
        "buyer_tax_id": None,
        "procurement_method": row.get("tipo_procedimiento"),
        "process_status": normalise_status(row.get("estado")),
        "source_status": row.get("estado"),
        "publication_date": parse_datetime(row.get("fecha_publicacion")),
        "closing_date": parse_datetime(row.get("fecha_cierre")),
        # SISCAE publishes no award date of its own; "Ultima Actualizacion" on
        # an awarded process is the closest the source gets, and calling it
        # the award date would be inventing precision the source never gave.
        "award_date": None,
        "estimated_amount": None,
        "awarded_amount": parse_amount(award.get("monto")) if award else None,
        "currency_code": award.get("moneda") if award else None,
        "supplier_name": award.get("proveedor") if award else None,
        "supplier_id_source": award.get("ruc") if award else None,
        "supplier_tax_id": award.get("ruc") if award else None,
        "supplier_type": None,
        "item_description": description,
        "category_source": row.get("categoria"),
        "category_normalised": None,
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
    return record


def parse_amount(value: str | None) -> Decimal | None:
    """"1336229.69" -> Decimal. The scraper already stripped the thousands
    separators; anything else is left as null rather than guessed at."""
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


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


def normalise_status(source_status: str | None) -> str | None:
    if not source_status:
        return None
    return STATUS_MAP.get(source_status.strip().lower())


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
