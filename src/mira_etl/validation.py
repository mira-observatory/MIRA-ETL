from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


VALID_PROCESS_STATUSES = {
    "PLANNED",
    "PUBLISHED",
    "OPEN",
    "EVALUATION",
    "AWARDED",
    "CONTRACTED",
    "COMPLETED",
    "CANCELLED",
    "DESERTED",
    "SUSPENDED",
}

VALID_SUPPLIER_TYPES = {
    "PERSON",
    "COMPANY",
    "CONSORTIUM",
    "NONPROFIT",
    "PUBLIC_ENTITY",
    "FOREIGN_SUPPLIER",
    "UNKNOWN",
}

VALID_CURRENCY_CODES = {"CRC", "USD", "EUR", "GTQ", "HNL", "SVC", "NIO", "PAB"}

REQUIRED_FIELDS = {
    "process_number": "MISSING_PROCESS_NUMBER",
    "title": "MISSING_TITLE",
    "buyer_name": "MISSING_BUYER_NAME",
    "buyer_tax_id": "MISSING_BUYER_TAX_ID",
    "procurement_method": "MISSING_PROCUREMENT_METHOD",
    "process_status": "MISSING_PROCESS_STATUS",
    "publication_date": "MISSING_PUBLICATION_DATE",
}


@dataclass(frozen=True)
class ValidationResult:
    source_record_id: str | None
    raw_payload_hash: str | None
    rule_code: str
    severity: str
    field_name: str | None
    raw_value: str | None
    normalised_value: str | None
    message: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "raw_payload_hash": self.raw_payload_hash,
            "rule_code": self.rule_code,
            "severity": self.severity,
            "field_name": self.field_name,
            "raw_value": self.raw_value,
            "normalised_value": self.normalised_value,
            "message": self.message,
            "payload": self.payload,
        }


def validate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    process_id_counts = Counter(record.get("process_id") for record in records)
    results: list[dict[str, Any]] = []

    for record in records:
        record_results = validate_record(record, process_id_counts)
        apply_quality_status(record, record_results)
        results.extend(result.as_dict() for result in record_results)

    return results


def validate_record(
    record: dict[str, Any],
    process_id_counts: Counter[str | None],
) -> list[ValidationResult]:
    results: list[ValidationResult] = []

    for field_name, rule_code in REQUIRED_FIELDS.items():
        if is_blank(record.get(field_name)):
            results.append(
                issue(
                    record,
                    rule_code=rule_code,
                    severity="WARNING",
                    field_name=field_name,
                    raw_value=raw_value_for(record, field_name),
                    normalised_value=record.get(field_name),
                    message=f"Required MIRA field is missing: {field_name}.",
                )
            )

    for field_name in ("estimated_amount",):
        value = record.get(field_name)
        if isinstance(value, Decimal) and value < 0:
            results.append(
                issue(
                    record,
                    rule_code=f"NEGATIVE_{field_name.upper()}",
                    severity="ERROR",
                    field_name=field_name,
                    raw_value=raw_value_for(record, field_name),
                    normalised_value=value,
                    message=f"Amount cannot be negative: {field_name}.",
                )
            )

    publication_date = record.get("publication_date")
    closing_date = record.get("closing_date")

    if publication_date and closing_date and closing_date < publication_date:
        results.append(
            issue(
                record,
                rule_code="CLOSING_BEFORE_PUBLICATION",
                severity="ERROR",
                field_name="closing_date",
                raw_value=raw_value_for(record, "closing_date"),
                normalised_value=closing_date,
                message="Closing date is earlier than publication date.",
            )
        )

    for field_name in ("publication_date", "closing_date", "source_last_modified_at"):
        if raw_value_for(record, field_name) and record.get(field_name) is None:
            results.append(
                issue(
                    record,
                    rule_code="UNPARSEABLE_DATE",
                    severity="ERROR",
                    field_name=field_name,
                    raw_value=raw_value_for(record, field_name),
                    normalised_value=None,
                    message=f"Date exists in source but could not be parsed: {field_name}.",
                )
            )

    currency = record.get("currency_code")
    if currency and str(currency).upper() not in VALID_CURRENCY_CODES:
        results.append(
            issue(
                record,
                rule_code="INVALID_CURRENCY_CODE",
                severity="WARNING",
                field_name="currency_code",
                raw_value=raw_value_for(record, "currency_code"),
                normalised_value=currency,
                message="Currency code is outside the current MIRA catalog.",
            )
        )

    if record.get("estimated_amount") is not None and not currency:
        results.append(
            issue(
                record,
                rule_code="MISSING_CURRENCY_WITH_AMOUNT",
                severity="ERROR",
                field_name="currency_code",
                raw_value=raw_value_for(record, "currency_code"),
                normalised_value=None,
                message="A monetary amount exists but currency_code is missing.",
            )
        )

    process_status = record.get("process_status")
    if process_status and process_status not in VALID_PROCESS_STATUSES:
        results.append(
            issue(
                record,
                rule_code="INVALID_PROCESS_STATUS",
                severity="ERROR",
                field_name="process_status",
                raw_value=raw_value_for(record, "process_status"),
                normalised_value=process_status,
                message="Process status is outside the MIRA catalog.",
            )
        )

    for award in record.get("awards") or []:
        amount = award.get("awarded_amount")
        award_currency = award.get("currency_code")
        award_date = award.get("award_date")
        if isinstance(amount, Decimal) and amount < 0:
            results.append(issue(
                record, rule_code="NEGATIVE_AWARDED_AMOUNT", severity="ERROR",
                field_name="awarded_amount", raw_value=None,
                normalised_value=amount, message="Award amount cannot be negative.",
            ))
        if publication_date and award_date and award_date < publication_date:
            results.append(issue(
                record, rule_code="AWARD_BEFORE_PUBLICATION", severity="ERROR",
                field_name="award_date", raw_value=None,
                normalised_value=award_date,
                message="Award date is earlier than publication date.",
            ))
        if award_currency and str(award_currency).upper() not in VALID_CURRENCY_CODES:
            results.append(issue(
                record, rule_code="INVALID_CURRENCY_CODE", severity="WARNING",
                field_name="currency_code", raw_value=None,
                normalised_value=award_currency,
                message="Award currency is outside the current MIRA catalog.",
            ))
        if amount is not None and not award_currency:
            results.append(issue(
                record, rule_code="MISSING_CURRENCY_WITH_AMOUNT", severity="ERROR",
                field_name="currency_code", raw_value=None,
                normalised_value=None,
                message="An award amount exists but currency_code is missing.",
            ))
        for supplier in award.get("suppliers") or []:
            supplier_type = supplier.get("supplier_type")
            if supplier_type and supplier_type not in VALID_SUPPLIER_TYPES:
                results.append(issue(
                    record, rule_code="INVALID_SUPPLIER_TYPE", severity="ERROR",
                    field_name="supplier_type", raw_value=None,
                    normalised_value=supplier_type,
                    message="Supplier type is outside the MIRA catalog.",
                ))

    process_id = record.get("process_id")
    if process_id and process_id_counts[process_id] > 1:
        results.append(
            issue(
                record,
                rule_code="DUPLICATE_PROCESS_ID_IN_RUN",
                severity="ERROR",
                field_name="process_id",
                raw_value=process_id,
                normalised_value=process_id,
                message="The same process_id appears more than once in this run.",
            )
        )

    return results


def apply_quality_status(record: dict[str, Any], results: list[ValidationResult]) -> None:
    missing_fields = sorted({result.field_name for result in results if result.rule_code.startswith("MISSING_") and result.field_name})
    record["missing_fields"] = missing_fields

    if any(result.severity == "ERROR" for result in results):
        record["data_quality_status"] = "INVALID"
        record["normalisation_status"] = "REVIEW_REQUIRED"
    elif results:
        record["data_quality_status"] = "PARTIAL"
        record["normalisation_status"] = "PROCESSED"
    else:
        record["data_quality_status"] = "COMPLETE"
        record["normalisation_status"] = "PROCESSED"


def issue(
    record: dict[str, Any],
    *,
    rule_code: str,
    severity: str,
    field_name: str | None,
    raw_value: object,
    normalised_value: object,
    message: str,
) -> ValidationResult:
    return ValidationResult(
        source_record_id=record.get("source_record_id"),
        raw_payload_hash=record.get("raw_payload_hash"),
        rule_code=rule_code,
        severity=severity,
        field_name=field_name,
        raw_value=to_text(raw_value),
        normalised_value=to_text(normalised_value),
        message=message,
        payload={"process_id": record.get("process_id"), "process_number": record.get("process_number")},
    )


def raw_value_for(record: dict[str, Any], field_name: str) -> object:
    payload = record.get("raw_payload") or {}

    if "proceso" in payload:  # nicaragua_siscae shape
        proceso = first_mapping(payload.get("proceso"))
        mapping = {
            "process_number": proceso.get("numero_proceso"),
            "title": proceso.get("descripcion"),
            "buyer_name": proceso.get("institucion"),
            "buyer_tax_id": None,
            "procurement_method": proceso.get("tipo_procedimiento"),
            "process_status": proceso.get("estado"),
            "publication_date": proceso.get("fecha_publicacion"),
            "closing_date": proceso.get("fecha_cierre"),
            "award_date": None,
            "estimated_amount": None,
            "awarded_amount": None,
            "currency_code": None,
            "supplier_name": None,
            "supplier_tax_id": None,
            "supplier_type": None,
            "item_description": proceso.get("descripcion"),
            "source_last_modified_at": proceso.get("ultima_actualizacion"),
        }
        return mapping.get(field_name)

    adjudication = first_mapping(payload.get("procedimiento_adjudicacion"))
    cartel = first_mapping(payload.get("detalle_cartel"))
    supplier = first_mapping(payload.get("proveedor"))

    mapping = {
        "process_number": adjudication.get("NUMERO_PROCEDIMIENTO") or cartel.get("NRO_PROCEDIMIENTO"),
        "title": cartel.get("CARTEL_NM"),
        "buyer_name": adjudication.get("INSTITUCION"),
        "buyer_tax_id": adjudication.get("CEDULA") or cartel.get("CEDULA_INSTITUCION"),
        "procurement_method": adjudication.get("TIPO_PROCEDIMIENTO") or cartel.get("TIPO_PROCEDIMIENTO"),
        "process_status": cartel.get("CARTEL_STAT"),
        "publication_date": cartel.get("FECHA_PUBLICACION"),
        "closing_date": cartel.get("FECHAH_APERTURA"),
        "award_date": adjudication.get("FECHA_ADJUD_FIRME"),
        "estimated_amount": cartel.get("MONTO_EST"),
        "awarded_amount": adjudication.get("MONTO_ADJU_LINEA_CRC") or adjudication.get("MONTO_ADJU_LINEA"),
        "currency_code": adjudication.get("MONEDA_ADJUDICADA"),
        "supplier_name": adjudication.get("NOMBRE_PROVEEDOR"),
        "supplier_tax_id": adjudication.get("CEDULA_PROVEEDOR"),
        "supplier_type": supplier.get("TIPO_PROVEEDOR"),
        "item_description": adjudication.get("DESCR_BIEN_SERVICIO"),
        "source_last_modified_at": adjudication.get("fecha_rev"),
    }
    return mapping.get(field_name)


def first_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return {}


def is_blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def to_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
