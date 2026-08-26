from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from mira_etl.env import load_dotenv
from mira_etl.matching import normalise_name


# Contract between sql/001_init.sql and every table written by this class.
# Contracted tables must match exactly before an ETL run starts.
SCHEMA_CONTRACT: dict[str, set[str]] = {
    "audit.etl_runs": {
        "id", "pipeline_name", "source", "period", "connector_version",
        "status", "started_at", "finished_at", "error_message",
    },
    "audit.etl_row_counts": {
        "row_count_id", "run_id", "layer_name", "table_name", "row_count",
        "created_at",
    },
    "audit.validation_results": {
        "validation_id", "run_id", "source", "period", "source_record_id",
        "raw_payload_hash", "rule_code", "severity", "field_name",
        "raw_value", "normalised_value", "message", "payload", "created_at",
    },
    "raw.source_files": {
        "source_file_id", "run_id", "source", "period", "filename",
        "file_hash", "row_count", "loaded_at",
    },
    "raw.source_rows": {
        "source_row_id", "run_id", "source_file_id", "row_number", "payload",
        "loaded_at",
    },
    "staging.normalized_candidates": {
        "candidate_id", "run_id", "source", "period", "source_record_id",
        "raw_payload_hash", "payload", "created_at",
    },
    "mart.processes": {
        "process_id", "country_code", "source_system", "source_record_id",
        "source_url", "extracted_at", "source_last_modified_at",
        "connector_version", "raw_payload", "raw_payload_hash",
        "normalisation_status", "normalised_at", "data_quality_status",
        "missing_fields", "process_number", "title", "description",
        "procurement_method", "process_status", "source_status",
        "publication_date", "closing_date", "estimated_amount", "currency_code",
    },
    "mart.process_buyers": {
        "process_id", "buyer_id",
    },
    "mart.items": {
        "item_id", "process_id", "source_item_id", "line_number",
        "item_description", "category_source", "category_normalised",
    },
    "mart.awards": {
        "award_id", "process_id", "source_award_id", "award_date",
        "awarded_amount", "currency_code",
    },
    "mart.award_items": {
        "award_id", "item_id",
    },
    "mart.award_suppliers": {
        "award_id", "supplier_id",
    },
    "mart.suppliers": {
        "supplier_id", "country_code", "source_system", "supplier_tax_id",
        "supplier_id_source", "name_normalised", "supplier_type",
    },
    "mart.buyers": {
        "buyer_id", "country_code", "source_system", "buyer_tax_id",
        "buyer_id_source", "name_normalised",
    },
    "web.coverage_sources": {
        "source_key", "country_code", "source_system", "display_name", "status",
        "process_count", "buyer_count", "supplier_count", "publication_date_min",
        "publication_date_max", "complete_process_count", "partial_process_count",
        "process_without_date_count", "last_successful_load_at", "refreshed_at",
        "sort_order",
    },
    "web.countries": {
        "country_code", "display_name", "flag_asset", "sort_order",
    },
    "query.semantic_dictionary": {
        "id", "view_name", "column_name", "description_es", "data_type",
        "enum_values", "unit", "is_aggregable", "caveat",
    },
}

CORE_SQL = """
    insert into mart.processes (
        process_id, country_code, source_system, source_record_id, source_url,
        extracted_at, source_last_modified_at, connector_version,
        raw_payload, raw_payload_hash, normalisation_status, normalised_at,
        data_quality_status, missing_fields, process_number, title, description,
        procurement_method, process_status, source_status, publication_date,
        closing_date, estimated_amount, currency_code
    )
    values (
        %(process_id)s, %(country_code)s, %(source_system)s, %(source_record_id)s, %(source_url)s,
        %(extracted_at)s, %(source_last_modified_at)s, %(connector_version)s,
        %(raw_payload)s::jsonb, %(raw_payload_hash)s, %(normalisation_status)s, %(normalised_at)s,
        %(data_quality_status)s, %(missing_fields)s::jsonb, %(process_number)s,
        %(title)s, %(description)s, %(procurement_method)s, %(process_status)s,
        %(source_status)s, %(publication_date)s, %(closing_date)s,
        %(estimated_amount)s, %(currency_code)s
    )
    on conflict (process_id)
    do update set
        country_code = excluded.country_code,
        source_system = excluded.source_system,
        source_record_id = excluded.source_record_id,
        source_url = excluded.source_url,
        extracted_at = excluded.extracted_at,
        source_last_modified_at = excluded.source_last_modified_at,
        connector_version = excluded.connector_version,
        raw_payload = excluded.raw_payload,
        normalisation_status = excluded.normalisation_status,
        normalised_at = excluded.normalised_at,
        data_quality_status = excluded.data_quality_status,
        missing_fields = excluded.missing_fields,
        process_number = excluded.process_number,
        title = excluded.title,
        description = excluded.description,
        procurement_method = excluded.procurement_method,
        process_status = excluded.process_status,
        source_status = excluded.source_status,
        publication_date = excluded.publication_date,
        closing_date = excluded.closing_date,
        estimated_amount = excluded.estimated_amount,
        currency_code = excluded.currency_code,
        raw_payload_hash = excluded.raw_payload_hash
"""

PROCESS_BUYER_SQL = """
    insert into mart.process_buyers (process_id, buyer_id)
    values (%s, %s)
    on conflict (process_id, buyer_id) do nothing
"""

ITEM_SQL = """
    insert into mart.items (
        item_id, process_id, source_item_id, line_number,
        item_description, category_source, category_normalised
    )
    values (%s, %s, %s, %s, %s, %s, %s)
    on conflict (item_id) do update set
        process_id = excluded.process_id,
        source_item_id = excluded.source_item_id,
        line_number = excluded.line_number,
        item_description = excluded.item_description,
        category_source = excluded.category_source,
        category_normalised = excluded.category_normalised
"""

AWARD_SQL = """
    insert into mart.awards (
        award_id, process_id, source_award_id,
        award_date, awarded_amount, currency_code
    )
    values (%s, %s, %s, %s, %s, %s)
    on conflict (award_id) do update set
        process_id = excluded.process_id,
        source_award_id = excluded.source_award_id,
        award_date = excluded.award_date,
        awarded_amount = excluded.awarded_amount,
        currency_code = excluded.currency_code
"""

AWARD_ITEM_SQL = """
    insert into mart.award_items (award_id, item_id)
    values (%s, %s)
    on conflict (award_id, item_id) do nothing
"""

AWARD_SUPPLIER_SQL = """
    insert into mart.award_suppliers (award_id, supplier_id)
    values (%s, %s)
    on conflict (award_id, supplier_id) do nothing
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = ensure_sslmode(dsn)
        self.conn = self._connect()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self.dsn,
            row_factory=dict_row,
            autocommit=True,
        )

    @classmethod
    def from_env(cls) -> "Database":
        load_dotenv()
        dsn = os.environ.get("SUPABASE_DB_URL")
        if not dsn:
            raise RuntimeError("SUPABASE_DB_URL is required.")
        return cls(dsn)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.conn.close()

    def execute_sql_file(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as fh:
            with self.conn.cursor() as cur:
                cur.execute(fh.read())

    def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)

    def validate_schema(self) -> None:
        """Fail before loading when PostgreSQL does not match our SQL contract."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select table_schema, table_name, column_name
                  from information_schema.columns
                 where table_schema in (
                    'raw', 'staging', 'mart', 'audit', 'web', 'query', 'analytics'
                 )
                """
            )
            rows = cur.fetchall()

        actual: dict[str, set[str]] = {}
        for row in rows:
            table = f"{row['table_schema']}.{row['table_name']}"
            actual.setdefault(table, set()).add(row["column_name"])

        mismatches = schema_mismatches(actual)
        if mismatches:
            details = "; ".join(mismatches)
            raise RuntimeError(
                "Database schema does not match sql/001_init.sql: " + details
                + ". Run `mira-etl init-db` with the same SUPABASE_DB_URL."
            )

    def insert_row_count(
        self,
        *,
        run_id: int,
        layer_name: str,
        table_name: str,
        row_count: int,
    ) -> None:
        self.execute(
            """
            insert into audit.etl_row_counts
                (run_id, layer_name, table_name, row_count)
            values (%s, %s, %s, %s)
            """,
            (run_id, layer_name, table_name, row_count),
        )

    def insert_run(self, *, source: str, period: str, connector_version: str) -> int:
        row = self.fetch_one(
            """
            insert into audit.etl_runs (pipeline_name, source, period, connector_version, status)
            values (%s, %s, %s, %s, 'RUNNING')
            returning id
            """,
            (source, source, period, connector_version),
        )
        assert row is not None
        return int(row["id"])

    def has_successful_run(self, *, source: str, period: str) -> bool:
        row = self.fetch_one(
            """
            select 1
              from audit.etl_runs
             where source = %s
               and period = %s
               and status = 'SUCCESS'
             limit 1
            """,
            (source, period),
        )
        return row is not None

    def finish_run(self, run_id: int, status: str, error_message: str | None = None) -> None:
        self.execute(
            """
            update audit.etl_runs
               set status = %s,
                   error_message = %s,
                   finished_at = now()
             where id = %s
            """,
            (status, error_message, run_id),
        )

    def refresh_web_coverage_source(
        self,
        *,
        source_key: str,
        country_code: str,
        source_system: str,
        display_name: str,
    ) -> None:
        """Recompute the exact public summary for one successfully loaded source."""
        self.execute(
            """
            with source_processes as (
                select process_id, publication_date, data_quality_status
                from mart.processes
                where country_code = %s and source_system = %s
            ),
            process_stats as (
                select
                    count(*) as process_count,
                    min(publication_date)::date as publication_date_min,
                    max(publication_date)::date as publication_date_max,
                    count(*) filter (
                        where data_quality_status = 'COMPLETE'
                    ) as complete_process_count,
                    count(*) filter (
                        where data_quality_status = 'PARTIAL'
                    ) as partial_process_count,
                    count(*) filter (
                        where publication_date is null
                    ) as process_without_date_count
                from source_processes
            ),
            buyer_stats as (
                select count(distinct pb.buyer_id) as buyer_count
                from source_processes p
                join mart.process_buyers pb on pb.process_id = p.process_id
            ),
            supplier_stats as (
                select count(distinct aws.supplier_id) as supplier_count
                from source_processes p
                join mart.awards a on a.process_id = p.process_id
                join mart.award_suppliers aws on aws.award_id = a.award_id
            )
            insert into web.coverage_sources (
                source_key, country_code, source_system, display_name, status,
                process_count, buyer_count, supplier_count,
                publication_date_min, publication_date_max,
                complete_process_count, partial_process_count,
                process_without_date_count, last_successful_load_at, refreshed_at
            )
            select
                %s, %s, %s, %s, 'ACTIVE',
                ps.process_count,
                bs.buyer_count,
                ss.supplier_count,
                ps.publication_date_min,
                ps.publication_date_max,
                ps.complete_process_count,
                ps.partial_process_count,
                ps.process_without_date_count,
                (
                    select max(r.finished_at)
                    from audit.etl_runs r
                    where r.source = %s and r.status = 'SUCCESS'
                ),
                now()
            from process_stats ps
            cross join buyer_stats bs
            cross join supplier_stats ss
            on conflict (source_key) do update set
                country_code = excluded.country_code,
                source_system = excluded.source_system,
                display_name = excluded.display_name,
                status = excluded.status,
                process_count = excluded.process_count,
                buyer_count = excluded.buyer_count,
                supplier_count = excluded.supplier_count,
                publication_date_min = excluded.publication_date_min,
                publication_date_max = excluded.publication_date_max,
                complete_process_count = excluded.complete_process_count,
                partial_process_count = excluded.partial_process_count,
                process_without_date_count = excluded.process_without_date_count,
                last_successful_load_at = excluded.last_successful_load_at,
                refreshed_at = excluded.refreshed_at
            """,
            (
                country_code, source_system,
                source_key, country_code, source_system, display_name, source_key,
            ),
        )

    def finish_run_after_error(
        self,
        run_id: int,
        error_message: str,
    ) -> None:
        try:
            self.finish_run(run_id, "ERROR", error_message)
            return
        except (psycopg.Error, OSError):
            pass

        recovery_conn = self._connect()
        try:
            with recovery_conn.cursor() as cur:
                cur.execute(
                    """
                    update audit.etl_runs
                       set status = 'ERROR',
                           error_message = %s,
                           finished_at = now()
                     where id = %s
                    """,
                    (error_message, run_id),
                )
        finally:
            recovery_conn.close()

    def insert_source_file(
        self,
        *,
        run_id: int,
        source: str,
        period: str,
        filename: str,
        file_hash: str,
        row_count: int,
    ) -> int:
        row = self.fetch_one(
            """
            insert into raw.source_files (run_id, source, period, filename, file_hash, row_count)
            values (%s, %s, %s, %s, %s, %s)
            returning source_file_id
            """,
            (run_id, source, period, filename, file_hash, row_count),
        )
        assert row is not None
        return int(row["source_file_id"])

    def update_source_file_row_count(
        self,
        source_file_id: int,
        row_count: int,
    ) -> None:
        self.execute(
            """
            update raw.source_files
               set row_count = %s
             where source_file_id = %s
            """,
            (row_count, source_file_id),
        )

    def insert_raw_rows(
        self,
        *,
        run_id: int,
        source_file_id: int,
        rows: Iterable[dict[str, Any]],
        batch_size: int = 250,
        start_row_number: int = 1,
        progress_offset: int = 0,
    ) -> int:
        sql = """
            insert into raw.source_rows (
                run_id,
                source_file_id,
                row_number,
                payload
            )
            values (%s, %s, %s, %s::jsonb)
        """

        batch = []
        total = 0

        with self.conn.cursor() as cur:
            for index, row in enumerate(rows, start_row_number):
                batch.append(
                    (
                        run_id,
                        source_file_id,
                        index,
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )

                if len(batch) >= batch_size:
                    cur.executemany(sql, batch)

                    total += len(batch)

                    print(
                        f"RAW inserted: {progress_offset + total:,}"
                    )

                    batch.clear()

            if batch:
                cur.executemany(sql, batch)

                total += len(batch)

                print(
                    f"RAW inserted: {progress_offset + total:,}"
                )

        return total

    def insert_staging_candidates(
        self,
        *,
        run_id: int,
        source: str,
        period: str,
        records: Iterable[dict[str, Any]],
        batch_size: int = 250,
        progress_offset: int = 0,
    ) -> int:
        sql = """
            insert into staging.normalized_candidates (
                run_id,
                source,
                period,
                source_record_id,
                raw_payload_hash,
                payload
            )
            values (%s, %s, %s, %s, %s, %s::jsonb)
        """

        batch = []
        total = 0

        with self.conn.cursor() as cur:
            for record in records:
                batch.append(
                    (
                        run_id,
                        source,
                        period,
                        record["source_record_id"],
                        record["raw_payload_hash"],
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )

                if len(batch) >= batch_size:
                    cur.executemany(sql, batch)

                    total += len(batch)

                    print(
                        f"STAGING inserted: {progress_offset + total:,}"
                    )

                    batch.clear()

            if batch:
                cur.executemany(sql, batch)

                total += len(batch)

                print(
                    f"STAGING inserted: {progress_offset + total:,}"
                )

        return total

    def insert_validation_results(
        self,
        *,
        run_id: int,
        source: str,
        period: str,
        results: Iterable[dict[str, Any]],
        batch_size: int = 250,
    ) -> int:
        sql = """
            insert into audit.validation_results (
                run_id, source, period, source_record_id, raw_payload_hash,
                rule_code, severity, field_name, raw_value, normalised_value,
                message, payload
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """
        batch = []
        total = 0
        with self.conn.cursor() as cur:
            for result in results:
                batch.append(
                    (
                        run_id,
                        source,
                        period,
                        result.get("source_record_id"),
                        result.get("raw_payload_hash"),
                        result["rule_code"],
                        result["severity"],
                        result.get("field_name"),
                        result.get("raw_value"),
                        result.get("normalised_value"),
                        result["message"],
                        json.dumps(
                            result.get("payload"),
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )
                if len(batch) >= batch_size:
                    cur.executemany(sql, batch)
                    total += len(batch)
                    batch.clear()
            if batch:
                cur.executemany(sql, batch)
                total += len(batch)
        return total

    def get_or_create_supplier(
        self,
        *,
        country_code: str,
        source_system: str,
        supplier_tax_id: str | None,
        supplier_id_source: str | None,
        supplier_name: str | None,
        supplier_type: str | None,
    ) -> int | None:
        """Resolves a supplier to a stable mart.suppliers row, trying three
        tiers in order of confidence (tax id -> source id -> normalised
        name) and creating a new row on the first tier that has a value to
        key on. Returns None only if there is nothing at all to identify the
        supplier by (no tax id, no source id, no name)."""
        name_normalised = normalise_name(supplier_name)

        if supplier_tax_id:
            existing = self.fetch_one(
                "select supplier_id from mart.suppliers where country_code = %s and supplier_tax_id = %s",
                (country_code, supplier_tax_id),
            )
            if existing:
                self._fill_missing_name(
                    "mart.suppliers", "supplier_id", existing["supplier_id"], name_normalised
                )
                return int(existing["supplier_id"])
            return self._insert_entity(
                "mart.suppliers", "supplier_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="supplier_tax_id", tax_id=supplier_tax_id,
                id_source_column="supplier_id_source", id_source=supplier_id_source,
                name_normalised=name_normalised,
                extra_columns={"supplier_type": supplier_type},
            )

        if supplier_id_source:
            existing = self.fetch_one(
                """select supplier_id from mart.suppliers
                   where country_code = %s and source_system = %s and supplier_id_source = %s""",
                (country_code, source_system, supplier_id_source),
            )
            if existing:
                self._fill_missing_name(
                    "mart.suppliers", "supplier_id", existing["supplier_id"], name_normalised
                )
                return int(existing["supplier_id"])
            return self._insert_entity(
                "mart.suppliers", "supplier_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="supplier_tax_id", tax_id=None,
                id_source_column="supplier_id_source", id_source=supplier_id_source,
                name_normalised=name_normalised,
                extra_columns={"supplier_type": supplier_type},
            )

        if name_normalised:
            existing = self.fetch_one(
                "select supplier_id from mart.suppliers where country_code = %s and name_normalised = %s",
                (country_code, name_normalised),
            )
            if existing:
                self._fill_missing_name(
                    "mart.suppliers", "supplier_id", existing["supplier_id"], name_normalised
                )
                return int(existing["supplier_id"])
            return self._insert_entity(
                "mart.suppliers", "supplier_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="supplier_tax_id", tax_id=None,
                id_source_column="supplier_id_source", id_source=None,
                name_normalised=name_normalised,
                extra_columns={"supplier_type": supplier_type},
            )

        return None

    def get_or_create_buyer(
        self,
        *,
        country_code: str,
        source_system: str,
        buyer_tax_id: str | None,
        buyer_id_source: str | None,
        buyer_name: str | None,
    ) -> int | None:
        """Same three-tier resolution as get_or_create_supplier, for buyers."""
        name_normalised = normalise_name(buyer_name)

        if buyer_tax_id:
            existing = self.fetch_one(
                "select buyer_id from mart.buyers where country_code = %s and buyer_tax_id = %s",
                (country_code, buyer_tax_id),
            )
            if existing:
                self._fill_missing_name(
                    "mart.buyers", "buyer_id", existing["buyer_id"], name_normalised
                )
                return int(existing["buyer_id"])
            return self._insert_entity(
                "mart.buyers", "buyer_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="buyer_tax_id", tax_id=buyer_tax_id,
                id_source_column="buyer_id_source", id_source=buyer_id_source,
                name_normalised=name_normalised,
                extra_columns={},
            )

        if buyer_id_source:
            existing = self.fetch_one(
                """select buyer_id from mart.buyers
                   where country_code = %s and source_system = %s and buyer_id_source = %s""",
                (country_code, source_system, buyer_id_source),
            )
            if existing:
                self._fill_missing_name(
                    "mart.buyers", "buyer_id", existing["buyer_id"], name_normalised
                )
                return int(existing["buyer_id"])
            return self._insert_entity(
                "mart.buyers", "buyer_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="buyer_tax_id", tax_id=None,
                id_source_column="buyer_id_source", id_source=buyer_id_source,
                name_normalised=name_normalised,
                extra_columns={},
            )

        if name_normalised:
            existing = self.fetch_one(
                "select buyer_id from mart.buyers where country_code = %s and name_normalised = %s",
                (country_code, name_normalised),
            )
            if existing:
                self._fill_missing_name(
                    "mart.buyers", "buyer_id", existing["buyer_id"], name_normalised
                )
                return int(existing["buyer_id"])
            return self._insert_entity(
                "mart.buyers", "buyer_id",
                country_code=country_code, source_system=source_system,
                tax_id_column="buyer_tax_id", tax_id=None,
                id_source_column="buyer_id_source", id_source=None,
                name_normalised=name_normalised,
                extra_columns={},
            )

        return None

    def _insert_entity(
        self,
        table: str,
        id_column: str,
        *,
        country_code: str,
        source_system: str,
        tax_id_column: str,
        tax_id: str | None,
        id_source_column: str,
        id_source: str | None,
        name_normalised: str | None,
        extra_columns: dict[str, Any],
    ) -> int:
        columns = [
            "country_code", "source_system", tax_id_column, id_source_column,
            "name_normalised", *extra_columns.keys(),
        ]
        values = [
            country_code, source_system, tax_id, id_source,
            name_normalised, *extra_columns.values(),
        ]
        placeholders = ", ".join(["%s"] * len(values))
        row = self.fetch_one(
            f"insert into {table} ({', '.join(columns)}) values ({placeholders}) returning {id_column}",
            tuple(values),
        )
        assert row is not None
        return int(row[id_column])

    def _fill_missing_name(
        self,
        table: str,
        id_column: str,
        entity_id: int,
        name_normalised: str | None,
    ) -> None:
        self.execute(
            f"""update {table}
                   set name_normalised = coalesce(name_normalised, %s)
                 where {id_column} = %s""",
            (name_normalised, entity_id),
        )

    def upsert_mart_split_records(self, records: Iterable[dict[str, Any]]) -> int:
        record_list = list(records)
        if not record_list:
            return 0

        self.upsert_record_core_batch(record_list)

        buyer_records = [
            buyer_record
            for record in record_list
            for buyer_record in buyer_records_for(record)
        ]
        buyer_ids = self.resolve_buyer_ids(buyer_records)
        supplier_records = [
            supplier_record
            for record in record_list
            for supplier_record in supplier_records_for(record)
        ]
        supplier_ids = self.resolve_supplier_ids(supplier_records)

        item_rows = []
        award_rows = []

        for record in record_list:
            process_id = record["process_id"]
            for item in record.get("items") or []:
                item_rows.append(
                    (
                        item["item_id"],
                        process_id,
                        item.get("source_item_id"),
                        item.get("line_number"),
                        item.get("item_description"),
                        item.get("category_source"),
                        item.get("category_normalised"),
                    )
                )

            for award in record.get("awards") or []:
                award_rows.append(
                    (
                        award["award_id"],
                        process_id,
                        award.get("source_award_id"),
                        award.get("award_date"),
                        award.get("awarded_amount"),
                        award.get("currency_code"),
                    )
                )

        with self.conn.cursor() as cur:
            cur.executemany(
                "delete from mart.process_buyers where process_id = %s",
                [(record["process_id"],) for record in record_list],
            )
            buyer_rows = [
                (buyer_record["process_id"], buyer_id)
                for buyer_record, buyer_id in zip(
                    buyer_records, buyer_ids, strict=True
                )
                if buyer_id is not None
            ]
            if buyer_rows:
                cur.executemany(PROCESS_BUYER_SQL, buyer_rows)
            # Replace child rows and relationship sets so source corrections
            # cannot leave stale items, awards, or suppliers behind.
            cur.executemany(
                """delete from mart.award_suppliers
                    where award_id in (
                        select award_id from mart.awards where process_id = %s
                    )""",
                [(record["process_id"],) for record in record_list],
            )
            cur.executemany(
                """delete from mart.award_items
                    where award_id in (
                        select award_id from mart.awards where process_id = %s
                    )""",
                [(record["process_id"],) for record in record_list],
            )
            cur.executemany(
                "delete from mart.awards where process_id = %s",
                [(record["process_id"],) for record in record_list],
            )
            cur.executemany(
                "delete from mart.items where process_id = %s",
                [(record["process_id"],) for record in record_list],
            )
            if item_rows:
                cur.executemany(ITEM_SQL, item_rows)
            if award_rows:
                cur.executemany(AWARD_SQL, award_rows)
            award_item_rows = [
                (award["award_id"], item_id)
                for record in record_list
                for award in record.get("awards") or []
                for item_id in award.get("item_ids") or []
            ]
            if award_item_rows:
                cur.executemany(AWARD_ITEM_SQL, award_item_rows)
            award_supplier_rows = [
                (supplier_record["award_id"], supplier_id)
                for supplier_record, supplier_id in zip(
                    supplier_records, supplier_ids, strict=True
                )
                if supplier_id is not None and supplier_record.get("award_id")
            ]
            if award_supplier_rows:
                cur.executemany(AWARD_SUPPLIER_SQL, award_supplier_rows)

        return len(record_list)

    def resolve_buyer_ids(self, records: list[dict[str, Any]]) -> list[int | None]:
        rows = self.fetch_all("select * from mart.buyers")
        tax, source, name = entity_indexes(rows, "buyer")
        pending: list[tuple[Any, ...]] = []
        markers: list[int | None] = []

        for record in records:
            country = record["country_code"]
            source_system = record["source_system"]
            tax_id = record.get("buyer_tax_id")
            source_id = record.get("buyer_id_source")
            normalised = normalise_name(record.get("buyer_name"))
            entity_id = first_entity_id(
                country, source_system, tax_id, source_id, normalised,
                tax, source, name,
            )
            if entity_id is None and (tax_id or source_id or normalised):
                entity_id = -(len(pending) + 1)
                pending.append((country, source_system, tax_id, source_id, normalised))
                index_entity(
                    entity_id, country, source_system, tax_id, source_id,
                    normalised, tax, source, name,
                )
            markers.append(entity_id)

        if pending:
            with self.conn.cursor() as cur:
                cur.executemany(
                    """insert into mart.buyers
                       (country_code, source_system, buyer_tax_id,
                        buyer_id_source, name_normalised)
                       values (%s, %s, %s, %s, %s)""",
                    pending,
                )
            rows = self.fetch_all("select * from mart.buyers")
            tax, source, name = entity_indexes(rows, "buyer")
            markers = [
                first_entity_id(
                    record["country_code"], record["source_system"],
                    record.get("buyer_tax_id"), record.get("buyer_id_source"),
                    normalise_name(record.get("buyer_name")), tax, source, name,
                )
                for record in records
            ]

        return markers

    def resolve_supplier_ids(self, records: list[dict[str, Any]]) -> list[int | None]:
        rows = self.fetch_all("select * from mart.suppliers")
        tax, source, name = entity_indexes(rows, "supplier")
        pending: list[tuple[Any, ...]] = []
        markers: list[int | None] = []

        for record in records:
            country = record["country_code"]
            source_system = record["source_system"]
            tax_id = record.get("supplier_tax_id")
            source_id = record.get("supplier_id_source")
            normalised = normalise_name(record.get("supplier_name"))
            entity_id = first_entity_id(
                country, source_system, tax_id, source_id, normalised,
                tax, source, name,
            )
            if entity_id is None and (tax_id or source_id or normalised):
                entity_id = -(len(pending) + 1)
                pending.append(
                    (country, source_system, tax_id, source_id, normalised,
                     record.get("supplier_type"))
                )
                index_entity(
                    entity_id, country, source_system, tax_id, source_id,
                    normalised, tax, source, name,
                )
            markers.append(entity_id)

        if pending:
            with self.conn.cursor() as cur:
                cur.executemany(
                    """insert into mart.suppliers
                       (country_code, source_system, supplier_tax_id,
                        supplier_id_source, name_normalised, supplier_type)
                       values (%s, %s, %s, %s, %s, %s)""",
                    pending,
                )
            rows = self.fetch_all("select * from mart.suppliers")
            tax, source, name = entity_indexes(rows, "supplier")
            markers = [
                first_entity_id(
                    record["country_code"], record["source_system"],
                    record.get("supplier_tax_id"), record.get("supplier_id_source"),
                    normalise_name(record.get("supplier_name")), tax, source, name,
                )
                for record in records
            ]

        return markers

    def upsert_record_core_batch(self, records: list[dict[str, Any]]) -> None:
        rows = [
            {
                **record,
                "raw_payload": json.dumps(record["raw_payload"], ensure_ascii=False, default=str),
                "missing_fields": json.dumps(record["missing_fields"], ensure_ascii=False, default=str),
            }
            for record in records
        ]
        with self.conn.cursor() as cur:
            cur.executemany(CORE_SQL, rows)

def ensure_sslmode(dsn: str) -> str:
    if "sslmode=" in dsn:
        return dsn
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}sslmode=require"


def supplier_records_for(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand award suppliers into entity-matching candidates.

    Each returned row retains award_id so the resolved supplier can be linked
    to the correct award without duplicating the award amount.
    """
    awards = record.get("awards")
    if awards is not None:
        expanded: list[dict[str, Any]] = []
        for award in awards:
            if not isinstance(award, dict):
                continue
            for supplier in award.get("suppliers") or []:
                if not isinstance(supplier, dict):
                    continue
                candidate = {**record, **supplier, "award_id": award.get("award_id")}
                if any(
                    candidate.get(field)
                    for field in ("supplier_tax_id", "supplier_id_source", "supplier_name")
                ):
                    expanded.append(candidate)
        return expanded

    suppliers = record.get("suppliers")
    if suppliers is None:
        if not any(
            record.get(field)
            for field in ("supplier_tax_id", "supplier_id_source", "supplier_name")
        ):
            return []
        return [record]

    expanded: list[dict[str, Any]] = []
    for supplier in suppliers:
        if not isinstance(supplier, dict):
            continue
        candidate = {**record, **supplier}
        if any(
            candidate.get(field)
            for field in ("supplier_tax_id", "supplier_id_source", "supplier_name")
        ):
            expanded.append(candidate)
    return expanded


def buyer_records_for(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a process into all buyer candidates linked to it.

    The scalar buyer fields remain supported for connectors that expose only
    one buyer. New or richer connectors can provide ``buyers`` as a list.
    """
    buyers = record.get("buyers")
    if buyers is None:
        if not any(
            record.get(field)
            for field in ("buyer_tax_id", "buyer_id_source", "buyer_name")
        ):
            return []
        return [record]

    expanded: list[dict[str, Any]] = []
    for buyer in buyers:
        if not isinstance(buyer, dict):
            continue
        candidate = {**record, **buyer}
        if any(
            candidate.get(field)
            for field in ("buyer_tax_id", "buyer_id_source", "buyer_name")
        ):
            expanded.append(candidate)
    return expanded


def schema_mismatches(actual: dict[str, set[str]]) -> list[str]:
    mismatches: list[str] = []
    for table, expected_columns in SCHEMA_CONTRACT.items():
        if table not in actual:
            mismatches.append(f"missing table {table}")
            continue
        missing_columns = sorted(expected_columns - actual[table])
        if missing_columns:
            mismatches.append(
                f"{table} missing columns {', '.join(missing_columns)}"
            )
        unexpected_columns = sorted(actual[table] - expected_columns)
        if unexpected_columns:
            mismatches.append(
                f"{table} has unexpected columns {', '.join(unexpected_columns)}"
            )
    return mismatches


def entity_indexes(
    rows: list[dict[str, Any]], prefix: str,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int], dict[tuple[str, str], int]]:
    tax: dict[tuple[str, str], int] = {}
    source: dict[tuple[str, str, str], int] = {}
    name: dict[tuple[str, str], int] = {}
    id_column = f"{prefix}_id"
    tax_column = f"{prefix}_tax_id"
    source_column = f"{prefix}_id_source"
    for row in rows:
        entity_id = int(row[id_column])
        index_entity(
            entity_id, row["country_code"], row["source_system"],
            row.get(tax_column), row.get(source_column), row.get("name_normalised"),
            tax, source, name,
        )
    return tax, source, name


def index_entity(
    entity_id: int,
    country: str,
    source_system: str,
    tax_id: str | None,
    source_id: str | None,
    normalised: str | None,
    tax: dict[tuple[str, str], int],
    source: dict[tuple[str, str, str], int],
    name: dict[tuple[str, str], int],
) -> None:
    if tax_id:
        tax[(country, tax_id)] = entity_id
    if source_id:
        source[(country, source_system, source_id)] = entity_id
    if normalised:
        name[(country, normalised)] = entity_id


def first_entity_id(
    country: str,
    source_system: str,
    tax_id: str | None,
    source_id: str | None,
    normalised: str | None,
    tax: dict[tuple[str, str], int],
    source: dict[tuple[str, str, str], int],
    name: dict[tuple[str, str], int],
) -> int | None:
    if tax_id:
        return tax.get((country, tax_id))
    if source_id:
        return source.get((country, source_system, source_id))
    if normalised:
        return name.get((country, normalised))
    return None
