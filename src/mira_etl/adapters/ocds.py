from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from mira_etl.config import SourceConfig
from mira_etl.hashutil import stable_id


MINIMUM_FIELDS = [
    "process_number",
    "title",
    "buyer_name",
    "buyer_tax_id",
    "procurement_method",
    "process_status",
    "publication_date",
]

STATUS_DETAILS_MAP = {
    "elaboracion": "PLANNED",
    "revisado": "PUBLISHED",
    "publicado": "PUBLISHED",
    "recepcion de ofertas": "OPEN",
    "evaluacion": "EVALUATION",
    "adjudicado": "AWARDED",
    "fracasado": "DESERTED",
    "fracasados": "DESERTED",
    "desierto": "DESERTED",
    "cancelado": "CANCELLED",
    "suspendido": "SUSPENDED",
    "finalizado": "COMPLETED",
}

TENDER_STATUS_MAP = {
    "active": "OPEN",
    "planned": "PLANNED",
    "complete": "COMPLETED",
    "cancelled": "CANCELLED",
    "unsuccessful": "DESERTED",
    "withdrawn": "CANCELLED",
}


def build_record(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_row: dict[str, Any],
) -> dict[str, Any]:
    compiled = source_row.get("compiledRelease") or source_row
    tender = compiled.get("tender") or {}
    awards = compiled.get("awards") or []
    contracts = compiled.get("contracts") or []
    award = awards[0] if awards else {}
    contract = contracts[0] if contracts else {}
    buyer_parties = all_buyers(compiled, tender)
    buyer = buyer_parties[0] if buyer_parties else {}
    source_record_id = (
        source_row.get("ocid")
        or compiled.get("ocid")
        or compiled.get("id")
    )
    if not source_record_id:
        raise ValueError(f"OCDS source '{config.source}' record is missing ocid/id")

    id_prefix = str(config.transform.get("id_prefix", f"MIRA-{config.country_code}-"))
    item_prefix = f"{id_prefix.rstrip('-')}-ITEM-"
    award_prefix = f"{id_prefix.rstrip('-')}-AWARD-"

    parties = compiled.get("parties") or []
    estimated_value = tender.get("value") or {}
    source_url = first_release_url(source_row) or config.source_url_for_period(period)
    raw_payload_hash = json_hash(source_row)

    item_sections = [tender, *awards, *contracts]
    items: list[dict[str, Any]] = []
    item_ids_by_source: dict[str, str] = {}
    seen_item_ids: set[str] = set()
    for section in item_sections:
        for position, source_item in enumerate(section.get("items") or []):
            source_item_id = source_item.get("id")
            item_id = stable_id(
                config.country_code, source_record_id,
                source_item_id or source_item.get("description") or str(position),
                prefix=item_prefix,
            )
            if source_item_id:
                item_ids_by_source[str(source_item_id)] = item_id
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)
            items.append({
                "item_id": item_id,
                "source_item_id": source_item_id,
                "line_number": None,
                "item_description": source_item.get("description"),
                "category_source": (source_item.get("classification") or {}).get("id"),
                "category_normalised": None,
            })

    if not items and (award.get("description") or tender.get("description")):
        desc = award.get("description") or tender.get("description")
        item_id = stable_id(
            config.country_code, source_record_id,
            desc or "0",
            prefix=item_prefix,
        )
        items.append({
            "item_id": item_id,
            "source_item_id": None,
            "line_number": None,
            "item_description": desc,
            "category_source": None,
            "category_normalised": None,
        })

    normalised_awards: list[dict[str, Any]] = []
    award_sections = awards or contracts
    for position, source_award in enumerate(award_sections):
        source_award_id = source_award.get("id") or source_award.get("awardID")
        value = source_award.get("value") or {}
        award_suppliers = source_award.get("suppliers") or []
        linked_item_ids = [
            item_ids_by_source[str(source_item["id"])]
            for source_item in source_award.get("items") or []
            if source_item.get("id") is not None
            and str(source_item["id"]) in item_ids_by_source
        ]
        if not linked_item_ids and items:
            linked_item_ids = [items[0]["item_id"]]
        award_date = (
            source_award.get("dateSigned")
            or source_award.get("date")
            or (contracts[0].get("dateSigned") if contracts else None)
        )
        normalised_awards.append({
            "award_id": stable_id(
                config.country_code, source_record_id,
                source_award_id or str(position), prefix=award_prefix,
            ),
            "source_award_id": source_award_id,
            "item_ids": linked_item_ids,
            "award_date": parse_datetime(award_date),
            "awarded_amount": parse_decimal(value.get("amount")),
            "currency_code": value.get("currency"),
            "suppliers": [
                {
                    "supplier_name": party.get("name"),
                    "supplier_id_source": party.get("id"),
                    "supplier_tax_id": entity_tax_id(party, parties),
                    "supplier_type": normalise_supplier_type(
                        party_by_id(parties, party.get("id")) or party
                    ),
                }
                for party in award_suppliers
            ],
        })

    awards_by_source = {
        str(item["source_award_id"]): item
        for item in normalised_awards
        if item.get("source_award_id") is not None
    }
    for contract_item in contracts:
        target = awards_by_source.get(str(contract_item.get("awardID")))
        if target is None:
            if not normalised_awards and contract_item.get("value"):
                pass
            continue
        if target.get("awarded_amount") is None and contract_item.get("value"):
            contract_value = contract_item.get("value") or {}
            target["awarded_amount"] = parse_decimal(contract_value.get("amount"))
            target["currency_code"] = contract_value.get("currency")
        if target.get("award_date") is None and contract_item.get("dateSigned"):
            target["award_date"] = parse_datetime(contract_item.get("dateSigned"))
        seen_suppliers = {
            (item.get("supplier_id_source"), item.get("supplier_tax_id"), item.get("supplier_name"))
            for item in target["suppliers"]
        }
        for party in contract_item.get("suppliers") or []:
            candidate = {
                "supplier_name": party.get("name"),
                "supplier_id_source": party.get("id"),
                "supplier_tax_id": entity_tax_id(party, parties),
                "supplier_type": normalise_supplier_type(
                    party_by_id(parties, party.get("id")) or party
                ),
            }
            key = (
                candidate["supplier_id_source"], candidate["supplier_tax_id"],
                candidate["supplier_name"],
            )
            if key not in seen_suppliers:
                seen_suppliers.add(key)
                target["suppliers"].append(candidate)

    record = {
        "process_id": stable_id(
            config.country_code,
            source_record_id,
            prefix=id_prefix,
        ),
        "process_number": str(tender.get("id") or source_record_id),
        "title": tender.get("title") or award.get("title"),
        "description": tender.get("description") or tender.get("title") or award.get("description"),
        "buyer_name": buyer.get("name"),
        "buyer_id_source": entity_id_source(buyer),
        "buyer_tax_id": entity_tax_id(buyer, parties),
        "buyers": [
            {
                "buyer_name": item.get("name"),
                "buyer_id_source": entity_id_source(item),
                "buyer_tax_id": entity_tax_id(item, parties),
            }
            for item in buyer_parties
        ],
        "procurement_method": (
            tender.get("procurementMethodDetails")
            or tender.get("procurementMethod")
        ),
        "process_status": normalise_status(
            tender,
            award=award,
            contract=contract,
        ),
        "source_status": (
            tender.get("statusDetails")
            or contract.get("statusDetails")
            or award.get("statusDetails")
            or contract.get("status")
            or award.get("status")
            or tender.get("status")
        ),
        "publication_date": parse_datetime(
            tender.get("datePublished")
            or (tender.get("tenderPeriod") or {}).get("startDate")
            or compiled.get("date")
        ),
        "closing_date": parse_datetime(
            (tender.get("tenderPeriod") or {}).get("endDate")
        ),
        "estimated_amount": parse_decimal(estimated_value.get("amount")),
        "currency_code": (
            estimated_value.get("currency")
            or (normalised_awards[0].get("currency_code") if normalised_awards else None)
        ),
        "items": items,
        "awards": normalised_awards,
        "country_code": config.country_code,
        "source_system": config.source_system,
        "source_record_id": source_record_id,
        "source_url": source_url,
        "extracted_at": datetime.now(UTC),
        "source_last_modified_at": parse_datetime(
            compiled.get("publishedDate") or compiled.get("date")
        ),
        "connector_version": connector_version,
        "raw_payload": source_row,
        "raw_payload_hash": raw_payload_hash,
        "normalisation_status": "PROCESSED",
        "normalised_at": datetime.now(UTC),
        "data_quality_status": "PARTIAL",
        "missing_fields": [],
    }
    record["missing_fields"] = [
        field for field in MINIMUM_FIELDS if record.get(field) is None
    ]
    record["data_quality_status"] = (
        "COMPLETE" if not record["missing_fields"] else "PARTIAL"
    )
    return record


def all_buyers(
    compiled: dict[str, Any],
    tender: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return every distinct buyer/procuring entity exposed by OCDS."""
    parties = compiled.get("parties") or []
    primary = resolve_buyer(compiled, tender)
    candidates = [primary, compiled.get("buyer"), tender.get("procuringEntity")]
    candidates.extend(
        party
        for party in parties
        if {str(role).lower() for role in party.get("roles") or []}
        & {"buyer", "procuringentity"}
    )
    result: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for buyer in candidates:
        if not isinstance(buyer, dict) or not buyer:
            continue
        resolved = resolve_party_root(parties, buyer)
        key = (
            resolved.get("id"),
            entity_tax_id(resolved, parties),
            resolved.get("name"),
        )
        if key not in seen and any(part for part in key):
            seen.add(key)
            result.append(resolved)
    return result


def resolve_buyer(compiled: dict[str, Any], tender: dict[str, Any]) -> dict[str, Any]:
    parties = compiled.get("parties") or []
    buyer = compiled.get("buyer") or tender.get("procuringEntity") or {}
    return resolve_party_root(parties, buyer)


def resolve_party_root(parties: list[dict[str, Any]], party: dict[str, Any]) -> dict[str, Any]:
    current = party
    party_id = current.get("id")
    if party_id:
        current = party_by_id(parties, party_id) or current
    seen: set[str] = set()
    while True:
        parents = current.get("memberOf") or []
        parent_id = parents[0].get("id") if parents else None
        if not parent_id or str(parent_id) in seen:
            return current
        seen.add(str(parent_id))
        current = party_by_id(parties, parent_id) or parents[0]


def party_by_id(
    parties: list[dict[str, Any]],
    party_id: Any,
) -> dict[str, Any]:
    if party_id is None:
        return {}
    return next(
        (party for party in parties if str(party.get("id")) == str(party_id)),
        {},
    )


def entity_tax_id(party: dict[str, Any], parties: list[dict[str, Any]] | None = None) -> str | None:
    if parties and party.get("id"):
        full = party_by_id(parties, party.get("id"))
        if full:
            party = {**party, **full}
    identifier = party.get("identifier") or {}
    scheme = str(identifier.get("scheme") or "").upper()
    if scheme in {"HN-RTN", "GT-NIT", "CR-CPJ", "HN_RTN", "GT_NIT"}:
        return str(identifier.get("id")) if identifier.get("id") else None
    if scheme == "X-HN-ONCAE-CE":
        return None
    party_id = str(party.get("id") or "")
    if party_id.startswith("HN-RTN-"):
        return party_id[len("HN-RTN-"):]
    if party_id.startswith("GT-NIT-"):
        return party_id[len("GT-NIT-"):]
    if identifier.get("id") and not scheme.startswith("X-"):
        return str(identifier.get("id"))
    return strip_identifier_prefix(party.get("id"))


def entity_id_source(party: dict[str, Any]) -> str | None:
    identifier = party.get("identifier") or {}
    return str(identifier.get("id") or party.get("id") or "") or None


def identifier_value(party: dict[str, Any]) -> str | None:
    return entity_tax_id(party) or entity_id_source(party)


def strip_identifier_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return value.rsplit("-", 1)[-1]


def first_release_url(source_row: dict[str, Any]) -> str | None:
    compiled = source_row.get("compiledRelease") or source_row
    sources = compiled.get("sources")
    if isinstance(sources, dict) and sources.get("url"):
        return str(sources["url"])
    if isinstance(sources, list):
        for s in sources:
            if isinstance(s, dict) and s.get("url"):
                return str(s["url"])
    releases = source_row.get("releases") or []
    return releases[-1].get("url") if releases else None


def normalise_status(
    tender: dict[str, Any] | str | None,
    *,
    award: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> str:
    tender_dict = tender if isinstance(tender, dict) else {}
    tender_status = tender if isinstance(tender, str) else tender_dict.get("status")
    award = award or {}
    contract = contract or {}

    contract_status = str(contract.get("status") or "").lower()
    if contract_status in {"complete", "terminated"}:
        return "COMPLETED"
    if contract_status == "cancelled":
        return "CANCELLED"
    if contract.get("dateSigned"):
        return "CONTRACTED"

    status_details = tender_dict.get("statusDetails")
    if status_details:
        mapped = STATUS_DETAILS_MAP.get(status_key(status_details))
        if mapped:
            return mapped

    if award:
        return "CANCELLED" if award.get("status") == "cancelled" else "AWARDED"

    status = str(tender_status or "").lower()
    return TENDER_STATUS_MAP.get(status, "PUBLISHED")


def status_key(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).strip().lower())
    return "".join(char for char in text if not unicodedata.combining(char))


def normalise_supplier_type(party: dict[str, Any]) -> str:
    details = party.get("details") or {}
    legal_type = details.get("legalEntityTypeDetail") or {}
    value = str(legal_type.get("description") or "").lower()
    if "individual" in value or "persona individual" in value:
        return "PERSON"
    if "jur" in value or "sociedad" in value or "empresa" in value:
        return "COMPANY"
    return "UNKNOWN"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
