from __future__ import annotations

import unicodedata
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
]


def build_records(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_rows: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, Any]]:
    datasets = config.transform.get("datasets") or {}
    carteles = index_by(source_rows.get(datasets.get("processes", "DetalleCarteles.csv"), []), "NRO_SICOP")
    proveedores = index_by(source_rows.get(datasets.get("suppliers", "Proveedores.csv"), []), "CEDULA_PROVEEDOR")
    instituciones = index_by(source_rows.get(datasets.get("buyers", "InstitucionesRegistradas.csv"), []), "CEDULA")
    adjudicaciones = source_rows.get(datasets.get("awards", "ProcedimientoAdjudicacion.csv"), [])
    id_prefix = str(config.transform.get("id_prefix", f"MIRA-{config.country_code}-"))
    item_prefix = f"{id_prefix.rstrip('-')}-ITEM-"
    award_prefix = f"{id_prefix.rstrip('-')}-AWARD-"

    extracted_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []

    by_process: dict[str, list[dict[str, str | None]]] = {}
    for row in adjudicaciones:
        if row.get("NRO_SICOP"):
            by_process.setdefault(str(row["NRO_SICOP"]), []).append(row)

    for nro_sicop, process_awards in by_process.items():
        row = process_awards[0]
        cartel = carteles.get(nro_sicop or "", {})
        institucion_id = row.get("CEDULA") or cartel.get("CEDULA_INSTITUCION")
        institucion = instituciones.get(institucion_id or "", {})

        raw_payload = {
            "procedimiento_adjudicacion": process_awards,
            "detalle_cartel": cartel or None,
            "institucion": institucion or None,
        }

        items: list[dict[str, Any]] = []
        awards: list[dict[str, Any]] = []
        seen_items: set[str] = set()
        for award_row in process_awards:
            amount_crc = award_row.get("MONTO_ADJU_LINEA_CRC")
            if amount_crc:
                awarded_amount = parse_decimal(amount_crc)
                award_currency = "CRC"
            else:
                awarded_amount = parse_decimal(award_row.get("MONTO_ADJU_LINEA"))
                award_currency = award_row.get("MONEDA_ADJUDICADA") or cartel.get("TIPO_MONEDA")

            item_id = stable_id(
                config.country_code, nro_sicop, award_row.get("LINEA"),
                award_row.get("PROD_ID"), prefix=item_prefix,
            )
            if item_id not in seen_items:
                seen_items.add(item_id)
                items.append({
                    "item_id": item_id,
                    "source_item_id": award_row.get("PROD_ID"),
                    "line_number": award_row.get("LINEA"),
                    "item_description": award_row.get("DESCR_BIEN_SERVICIO"),
                    "category_source": award_row.get("OBJETO_GASTO") or cartel.get("CLAS_OBJ"),
                    "category_normalised": None,
                })

            supplier_source_id = award_row.get("CEDULA_PROVEEDOR")
            proveedor = proveedores.get(supplier_source_id or "", {})
            awards.append({
                "award_id": stable_id(
                    config.country_code, nro_sicop, award_row.get("LINEA"),
                    award_row.get("PROD_ID"), supplier_source_id,
                    prefix=award_prefix,
                ),
                "source_award_id": None,
                "item_ids": [item_id],
                "award_date": parse_datetime(award_row.get("FECHA_ADJUD_FIRME")),
                "awarded_amount": awarded_amount,
                "currency_code": award_currency,
                "suppliers": [{
                    "supplier_name": award_row.get("NOMBRE_PROVEEDOR") or proveedor.get("NOMBRE_PROVEEDOR"),
                    "supplier_id_source": supplier_source_id,
                    "supplier_tax_id": supplier_source_id,
                    "supplier_type": normalise_supplier_type(
                        proveedor.get("TIPO_PROVEEDOR"), award_row.get("TIPO_OFERTA")
                    ),
                }],
            })

        record = {
            "process_id": stable_id(
                config.country_code,
                nro_sicop,
                prefix=id_prefix,
            ),
            "process_number": row.get("NUMERO_PROCEDIMIENTO") or cartel.get("NRO_PROCEDIMIENTO"),
            "title": cartel.get("CARTEL_NM") or row.get("DESCR_PROCEDIMIENTO"),
            "description": row.get("DESCR_PROCEDIMIENTO") or cartel.get("CARTEL_NM"),
            "buyer_name": row.get("INSTITUCION") or institucion.get("NOMBRE_INSTITUCION"),
            "buyer_id_source": institucion_id,
            "buyer_tax_id": institucion_id,
            "procurement_method": row.get("TIPO_PROCEDIMIENTO") or cartel.get("TIPO_PROCEDIMIENTO"),
            "process_status": normalise_status(cartel.get("CARTEL_STAT"), has_award=True),
            "source_status": cartel.get("CARTEL_STAT"),
            "publication_date": parse_datetime(cartel.get("FECHA_PUBLICACION")),
            "closing_date": parse_datetime(cartel.get("FECHAH_APERTURA")),
            "estimated_amount": parse_decimal(cartel.get("MONTO_EST")),
            "currency_code": cartel.get("TIPO_MONEDA"),
            "items": items,
            "awards": awards,
            "country_code": config.country_code,
            "source_system": config.source_system,
            "source_record_id": nro_sicop,
            "source_url": config.source_url_for_period(period),
            "extracted_at": extracted_at,
            "source_last_modified_at": max(
                (date for date in (parse_datetime(item.get("fecha_rev")) for item in process_awards) if date),
                default=None,
            ),
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


def index_by(rows: list[dict[str, str | None]], key: str) -> dict[str, dict[str, str | None]]:
    return {row[key]: row for row in rows if row.get(key)}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def normalise_status(source_status: str | None, *, has_award: bool) -> str:
    if source_status:
        lowered = source_status.lower()
        if "desierto" in lowered or "infructuoso" in lowered:
            return "DESERTED"
        if "cancel" in lowered or "anulad" in lowered:
            return "CANCELLED"
        if "suspend" in lowered:
            return "SUSPENDED"
    return "AWARDED" if has_award else "PUBLISHED"


def normalise_supplier_type(source_type: str | None, offer_type: str | None) -> str:
    value = strip_accents(f"{source_type or ''} {offer_type or ''}".lower())
    if "consorcio" in value:
        return "CONSORTIUM"
    if "fisic" in value:
        return "PERSON"
    if "juridic" in value or "sociedad" in value:
        return "COMPANY"
    if "extranj" in value:
        return "FOREIGN_SUPPLIER"
    return "UNKNOWN"


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))
