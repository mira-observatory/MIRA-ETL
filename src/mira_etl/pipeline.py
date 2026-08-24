from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import ijson

from mira_etl.config import SourceConfig
from mira_etl.adapters import transform_batch, transform_record
from mira_etl.csvio import read_csv_rows
from mira_etl.db import Database
from mira_etl.extract import extract_zip, obtain_zip, resolve_dataset_dir
from mira_etl.validation import validate_records


def run_pipeline(
    *,
    source: str,
    period: str,
    config_dir: Path,
    work_dir: Path,
    local_zip: Path | None,
    limit: int | None = None,
    force_reprocess: bool = False,
) -> str:
    """Run the connector selected exclusively by its source configuration."""
    validate_limit(limit)
    config = SourceConfig.load(config_dir, source)
    work_dir.mkdir(parents=True, exist_ok=True)

    with Database.from_env() as db:
        db.validate_schema()
        is_current_state_source = (
            config.download.get("type") == "html_session_scrape"
        )
        if (
            not is_current_state_source
            and not force_reprocess
            and db.has_successful_run(source=config.source, period=period)
        ):
            print(
                "SKIPPED - Period already processed successfully "
                f"(source={config.source}, period={period})"
            )
            return "SKIPPED"
        run_id = db.insert_run(
            source=config.source,
            period=period,
            connector_version=config.connector_version,
        )
        try:
            download_type = config.download.get("type")
            if download_type == "http_zip_json":
                zip_path = obtain_zip(config, period, work_dir, local_zip)
                extract_dir = extract_zip(zip_path, work_dir, config.source, period)
                process_json_records(
                    db=db,
                    run_id=run_id,
                    config=config,
                    period=period,
                    connector_version=config.connector_version,
                    extract_dir=extract_dir,
                    limit=limit,
                )
            else:
                source_rows = obtain_source_rows(
                    config=config,
                    period=period,
                    work_dir=work_dir,
                    local_zip=local_zip,
                    run_id=run_id,
                    db=db,
                    limit=limit,
                )
                records = transform_source(
                    config=config,
                    period=period,
                    source_rows=source_rows,
                )
                if limit is not None:
                    records = records[:limit]
                load_records(
                    db=db,
                    run_id=run_id,
                    config=config,
                    period=period,
                    records=records,
                )

            db.finish_run(run_id, "SUCCESS")
            db.refresh_web_coverage_source(
                source_key=config.source,
                country_code=config.country_code,
                source_system=config.source_system,
                display_name=config.source_system,
            )
            return "SUCCESS"
        except BaseException as exc:
            db.finish_run_after_error(run_id, str(exc) or type(exc).__name__)
            raise


def obtain_source_rows(
    *,
    config: SourceConfig,
    period: str,
    work_dir: Path,
    local_zip: Path | None,
    run_id: int,
    db: Database,
    limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract and persist CSV or HTML datasets according to download.type."""
    download_type = config.download.get("type")

    if download_type == "html_session_scrape":
        if local_zip is not None:
            raise ValueError("--local-zip is not valid for an HTML source")
        from mira_etl.extract_html import scrape_html_source

        source_rows = scrape_html_source(config, period, limit=limit)
        source_counts = {name: len(rows) for name, rows in source_rows.items()}
        hashes = {name: rows_hash(rows) for name, rows in source_rows.items()}
    elif download_type == "http_zip_csv":
        zip_path = obtain_zip(config, period, work_dir, local_zip)
        extract_dir = extract_zip(zip_path, work_dir, config.source, period)
        dataset_dir = resolve_dataset_dir(config, extract_dir)
        source_rows = {}
        source_counts = {}
        hashes = {}
        filenames = config.files["required"] + config.files.get("optional", [])
        for filename in filenames:
            csv_path = dataset_dir / filename
            if not csv_path.exists():
                continue
            source_rows[filename] = list(
                read_csv_rows(
                    csv_path,
                    delimiter=config.delimiter_for(filename),
                    encoding=config.encoding,
                )
            )
            source_counts[filename] = len(source_rows[filename])
            hashes[filename] = file_hash(csv_path)
        source_rows = filter_source_rows_for_mart(config.source, source_rows)
    else:
        raise ValueError(f"Unsupported download type: {download_type}")

    for dataset_name, rows in source_rows.items():
        report_dataset_size(
            label=dataset_name,
            unit_singular="fila",
            unit_plural="filas",
            found_count=source_counts[dataset_name],
            work_count=len(rows[:limit] if limit is not None else rows),
        )
        source_file_id = db.insert_source_file(
            run_id=run_id,
            source=config.source,
            period=period,
            filename=dataset_name,
            file_hash=hashes[dataset_name],
            row_count=len(rows),
        )
        raw_rows = rows[:limit] if limit is not None else rows
        db.insert_raw_rows(
            run_id=run_id,
            source_file_id=source_file_id,
            rows=raw_rows,
        )

    return source_rows


def filter_source_rows_for_mart(
    source: str,
    source_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    if source != "costa_rica_sicop":
        return source_rows

    adjudicaciones = source_rows.get("ProcedimientoAdjudicacion.csv", [])
    nro_sicop_values = values_for(adjudicaciones, "NRO_SICOP")
    supplier_ids = values_for(adjudicaciones, "CEDULA_PROVEEDOR")
    buyer_ids = values_for(adjudicaciones, "CEDULA")

    carteles = keep_rows_with_value(
        source_rows.get("DetalleCarteles.csv", []),
        "NRO_SICOP",
        nro_sicop_values,
    )
    buyer_ids.update(values_for(carteles, "CEDULA_INSTITUCION"))

    return {
        "DetalleCarteles.csv": carteles,
        "ProcedimientoAdjudicacion.csv": adjudicaciones,
        "InstitucionesRegistradas.csv": keep_rows_with_value(
            source_rows.get("InstitucionesRegistradas.csv", []),
            "CEDULA",
            buyer_ids,
        ),
        "Proveedores.csv": keep_rows_with_value(
            source_rows.get("Proveedores.csv", []),
            "CEDULA_PROVEEDOR",
            supplier_ids,
        ),
    }


def values_for(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {str(row[key]) for row in rows if row.get(key)}


def keep_rows_with_value(
    rows: list[dict[str, Any]],
    key: str,
    allowed_values: set[str],
) -> list[dict[str, Any]]:
    return [row for row in rows if row.get(key) and str(row[key]) in allowed_values]


def transform_source(
    *,
    config: SourceConfig,
    period: str,
    source_rows: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return transform_batch(config=config, period=period, source_rows=source_rows)


def load_records(
    *,
    db: Database,
    run_id: int,
    config: SourceConfig,
    period: str,
    records: list[dict[str, Any]],
) -> None:
    validation_results = validate_records(records)
    staged = db.insert_staging_candidates(
        run_id=run_id,
        source=config.source,
        period=period,
        records=records,
    )
    insert_row_count(db, run_id, "staging", "normalized_candidates", staged)
    validations = db.insert_validation_results(
        run_id=run_id,
        source=config.source,
        period=period,
        results=validation_results,
    )
    insert_row_count(db, run_id, "audit", "validation_results", validations)
    inserted = db.upsert_mart_split_records(records)
    insert_row_count(db, run_id, "mart", "processes", inserted)


def process_json_records(
    *,
    db: Database,
    run_id: int,
    config: SourceConfig,
    period: str,
    connector_version: str,
    extract_dir: Path,
    limit: int | None = None,
) -> None:
    json_path = find_json_file(extract_dir)
    source_file_id = db.insert_source_file(
        run_id=run_id,
        source=config.source,
        period=period,
        filename=str(json_path.relative_to(extract_dir)),
        file_hash=file_hash(json_path),
        row_count=0,
    )
    batch_size = config.batch_size
    record_limit = limit if limit is not None else config.record_limit
    print(
        f"JSON {json_path.name}: procesamiento por streaming en lotes de "
        f"{batch_size:,} items."
    )
    raw_batch: list[dict[str, Any]] = []
    record_batch: list[dict[str, Any]] = []
    totals = {"raw": 0, "staging": 0, "validations": 0, "mart": 0}

    with json_path.open("rb") as fh:
        for row in ijson.items(fh, "records.item"):
            if not isinstance(row, dict):
                raise ValueError("Every JSON records item must be an object")
            raw_batch.append(row)
            record_batch.append(
                build_json_record(
                    config=config,
                    period=period,
                    connector_version=connector_version,
                    source_row=row,
                )
            )
            if len(raw_batch) >= batch_size:
                flush_record_batch(
                    db=db, run_id=run_id, source_file_id=source_file_id,
                    source=config.source, period=period, raw_batch=raw_batch,
                    record_batch=record_batch, batch_size=batch_size, totals=totals,
                    dataset_name=json_path.name,
                )
            if record_limit is not None and totals["raw"] + len(raw_batch) >= record_limit:
                break

    if raw_batch:
        flush_record_batch(
            db=db, run_id=run_id, source_file_id=source_file_id,
            source=config.source, period=period, raw_batch=raw_batch,
            record_batch=record_batch, batch_size=batch_size, totals=totals,
            dataset_name=json_path.name,
        )

    print(f"JSON {json_path.name}: {totals['raw']:,} items trabajados.")
    db.update_source_file_row_count(source_file_id, totals["raw"])
    insert_row_count(db, run_id, "raw", "source_rows", totals["raw"])
    insert_row_count(db, run_id, "staging", "normalized_candidates", totals["staging"])
    insert_row_count(db, run_id, "audit", "validation_results", totals["validations"])
    insert_row_count(db, run_id, "mart", "processes", totals["mart"])


def build_json_record(
    *,
    config: SourceConfig,
    period: str,
    connector_version: str,
    source_row: dict[str, Any],
) -> dict[str, Any]:
    del connector_version
    return transform_record(config=config, period=period, source_row=source_row)


def flush_record_batch(
    *, db: Database, run_id: int, source_file_id: int, source: str,
    period: str, raw_batch: list[dict[str, Any]],
    record_batch: list[dict[str, Any]], batch_size: int,
    totals: dict[str, int], dataset_name: str | None = None,
) -> None:
    if dataset_name is not None:
        start = totals["raw"] + 1
        end = totals["raw"] + len(raw_batch)
        found_word = "encontrado" if len(raw_batch) == 1 else "encontrados"
        print(
            f"JSON {dataset_name}: "
            f"{format_quantity(len(raw_batch), 'item', 'items')} {found_word}; "
            f"se trabajaran ahora filas {start:,}-{end:,}."
        )
    db.insert_raw_rows(
        run_id=run_id, source_file_id=source_file_id, rows=raw_batch,
        batch_size=batch_size, start_row_number=totals["raw"] + 1,
        progress_offset=totals["raw"],
    )
    totals["raw"] += len(raw_batch)
    results = validate_records(record_batch)
    totals["staging"] += db.insert_staging_candidates(
        run_id=run_id, source=source, period=period, records=record_batch,
        batch_size=batch_size, progress_offset=totals["staging"],
    )
    totals["validations"] += db.insert_validation_results(
        run_id=run_id, source=source, period=period, results=results,
        batch_size=batch_size,
    )
    totals["mart"] += db.upsert_mart_split_records(record_batch)
    raw_batch.clear()
    record_batch.clear()


def insert_row_count(
    db: Database, run_id: int, layer_name: str, table_name: str, row_count: int,
) -> None:
    db.insert_row_count(
        run_id=run_id,
        layer_name=layer_name,
        table_name=table_name,
        row_count=row_count,
    )


def report_dataset_size(
    *,
    label: str,
    unit_singular: str,
    unit_plural: str,
    found_count: int,
    work_count: int,
) -> None:
    found_word = "encontrada" if found_count == 1 else "encontradas"
    print(
        f"Archivo {label}: "
        f"{format_quantity(found_count, unit_singular, unit_plural)} {found_word}; "
        f"se trabajaran ahora {work_count:,}."
    )


def format_quantity(count: int, singular: str, plural: str) -> str:
    unit = singular if count == 1 else plural
    return f"{count:,} {unit}"


def find_json_file(extract_dir: Path) -> Path:
    json_files = sorted(extract_dir.rglob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"No JSON file found in {extract_dir}")
    if len(json_files) > 1:
        raise ValueError(f"Expected one JSON file, found {len(json_files)}")
    return json_files[0]


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def rows_hash(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_limit(limit: int | None) -> None:
    if limit is not None and limit < 1:
        raise ValueError("limit must be greater than zero")
