from __future__ import annotations

import unicodedata
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

#: Nicaragua solo expone un unico estado en texto libre; MIRA lo mapea a su
#: catalogo fijo (sql/001_init.sql, VALID_PROCESS_STATUSES).
#:
#: Las claves van sin acento a proposito: normalise_status le quita los
#: acentos al valor de la fuente antes de buscar aca. Sin eso, "En Evaluacion"
#: y "En Ejecucion" -- que la fuente SI escribe con tilde -- no hacian match
#: y ese proceso entraba con process_status en nulo. Verificado 2026-08-26:
#: "En Evaluacion" es el bucket mas grande que tiene Nicaragua (~2,000
#: procesos), asi que el bug no era un caso raro.
STATUS_MAP = {
    "vigente": "OPEN",
    "adjudicado": "AWARDED",
    "en ejecucion": "CONTRACTED",
    "ejecucion": "CONTRACTED",
    "cancelado": "CANCELLED",
    "desierto": "DESERTED",
    "suspendido": "SUSPENDED",
    "cerrado": "COMPLETED",
    "en evaluacion": "EVALUATION",
    "evaluacion": "EVALUATION",
}


def build_records(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_rows: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, Any]]:
    """Un registro MIRA por proceso de SISCAE, con su adjudicacion si se publica.

    Tres datasets alimentan esto (ver extract_html.scrape_nicaragua_extras):
    "procesos_vigentes" (abiertos), "procesos_cerrados" (cerrados a ofertas,
    "En Evaluacion" en la fuente) y "procesos_adjudicados" (adjudicados, cada
    uno con `adjudicaciones` si SISCAE publico esa pestana).

    Los dos primeros no tienen proveedor ni monto por definicion. Un proceso
    adjudicado que SI publica su detalle produce un `awards` con un item
    (award_id, monto, moneda, fecha) por cada proveedor listado -- mismo grano
    que usa el adaptador de Costa Rica (relational_awards_csv). Uno que no lo
    publica sigue cargando como proceso adjudicado, con `awards` vacio: la
    adjudicacion es real de todas formas, solo la contraparte no esta
    publicada.
    """
    id_prefix = str(config.transform.get("id_prefix", f"MIRA-{config.country_code}-"))
    item_prefix = f"{id_prefix.rstrip('-')}-ITEM-"
    award_prefix = f"{id_prefix.rstrip('-')}-AWARD-"
    extracted_at = datetime.now(UTC)

    records: list[dict[str, Any]] = []
    for row in source_rows.get("procesos_adjudicados", []):
        records.append(
            _build_record(
                config=config,
                connector_version=connector_version,
                row=row,
                extracted_at=extracted_at,
                item_prefix=item_prefix,
                award_prefix=award_prefix,
                id_prefix=id_prefix,
                awards=row.get("adjudicaciones") or [],
            )
        )

    for dataset in ("procesos_vigentes", "procesos_cerrados"):
        for row in source_rows.get(dataset, []):
            records.append(
                _build_record(
                    config=config,
                    connector_version=connector_version,
                    row=row,
                    extracted_at=extracted_at,
                    item_prefix=item_prefix,
                    award_prefix=award_prefix,
                    id_prefix=id_prefix,
                    awards=[],
                )
            )

    return records


def _build_record(
    *,
    config: SourceConfig,
    connector_version: str,
    row: dict[str, Any],
    extracted_at: datetime,
    item_prefix: str,
    award_prefix: str,
    id_prefix: str,
    awards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Un proceso, con su unico item resumen y sus adjudicaciones (si hay)."""
    source_record_id = build_source_record_id(row)
    description = row.get("descripcion")

    item_id = stable_id(config.country_code, source_record_id, "summary", prefix=item_prefix)
    items = [
        {
            "item_id": item_id,
            "source_item_id": None,
            "line_number": None,
            "item_description": description,
            "category_source": row.get("categoria"),
            "category_normalised": None,
        }
    ]

    award_records = []
    for award in awards:
        proveedor = award.get("proveedor")
        ruc = award.get("ruc")
        award_records.append(
            {
                # Distintos proveedores en el mismo proceso no pueden colapsar
                # en un mismo id, o el segundo pisaria al primero al cargar.
                "award_id": stable_id(
                    config.country_code, source_record_id, ruc or proveedor, prefix=award_prefix
                ),
                "source_award_id": None,
                "item_ids": [item_id],
                # SISCAE no publica una fecha de adjudicacion propia. "Ultima
                # Actualizacion" es lo mas cercano que trae la fuente, y
                # llamarla fecha de adjudicacion inventaria una precision que
                # la fuente no da.
                "award_date": None,
                "awarded_amount": parse_amount(award.get("monto")),
                "currency_code": award.get("moneda"),
                "suppliers": [
                    {
                        "supplier_name": proveedor,
                        "supplier_id_source": ruc,
                        "supplier_tax_id": ruc,
                        "supplier_type": None,
                    }
                ],
            }
        )

    record = {
        "process_id": stable_id(config.country_code, source_record_id, prefix=id_prefix),
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
        "estimated_amount": None,
        "currency_code": None,
        "items": items,
        "awards": award_records,
        "country_code": config.country_code,
        "source_system": config.source_system,
        "source_record_id": source_record_id,
        "source_url": config.download.get("base_url"),
        "extracted_at": extracted_at,
        "source_last_modified_at": parse_datetime(row.get("ultima_actualizacion")),
        "connector_version": connector_version,
        # `adjudicaciones` es estado de navegacion, no dato de la fuente: si
        # entrara al hash, el mismo proceso se veria "cambiado" en cada
        # corrida solo porque un lote se detuvo en un punto distinto.
        "raw_payload": {"proceso": {k: v for k, v in row.items() if k != "adjudicaciones"}},
        "normalisation_status": "PROCESSED",
        "normalised_at": datetime.now(UTC),
        "data_quality_status": "PARTIAL",
        "missing_fields": [],
    }
    record["raw_payload_hash"] = stable_json_hash(record["raw_payload"])
    record["missing_fields"] = [field for field in MINIMUM_FIELDS if record.get(field) is None]
    record["data_quality_status"] = "COMPLETE" if not record["missing_fields"] else "PARTIAL"
    return record


def parse_amount(value: str | None) -> Decimal | None:
    """"1336229.69" -> Decimal. El scraper ya quito los separadores de miles;
    cualquier otra cosa se deja en null en vez de adivinarla."""
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def build_source_record_id(row: dict[str, str | None]) -> str:
    """El codigo propio de SISCAE (Codigo SIGAF) suele venir vacio (rendido
    como un "#" literal, ya normalizado a None en extract_html). Se cae a un
    compuesto de tipo de procedimiento + numero + comprador, estable entre
    corridas del mismo listado."""
    sigaf_code = row.get("codigo_sigaf")
    if sigaf_code:
        return sigaf_code
    procedure_type = row.get("tipo_procedimiento") or ""
    procedure_number = row.get("numero_proceso") or ""
    buyer_name = (row.get("institucion") or "")[:30]
    return f"{procedure_type}-{procedure_number}-{buyer_name}".strip("-")


def normalise_status(source_status: str | None) -> str | None:
    """Mapea el estado libre de SISCAE al catalogo de MIRA, acentos incluidos.

    La fuente escribe "En Evaluacion" y "En Ejecucion" con tilde, asi que un
    `.lower()` a secas no hace match con ninguno de los dos -- para
    "En Evaluacion" eso son ~2,000 procesos, el bucket mas grande que tiene
    Nicaragua, entrando con process_status en nulo.
    """
    if not source_status:
        return None
    plain = unicodedata.normalize("NFKD", source_status.strip().lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return STATUS_MAP.get(plain)


def parse_datetime(value: str | None) -> datetime | None:
    """"27/04/2026" o "27/05/2026 02:00:00 PM" -> datetime.

    SISCAE no declara zona horaria en ningun punto revisado; se etiqueta tal
    cual como UTC en vez de asumir un desfase que la fuente nunca confirmo."""
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
