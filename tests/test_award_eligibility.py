"""Exercise the actual view predicates with a cancelled award above the valid one.

SQLite runs the SELECT/WHERE/ORDER BY semantics locally; PostgreSQL-specific
schema upgrades and JSON backfill require the database integration check.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mira_etl.adapters.ocds import build_record
from mira_etl.config import SourceConfig
from mira_etl.db import AWARD_SQL, Database

ROOT = Path(__file__).parents[1]


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        create table processes (
            process_id text primary key, country_code text, process_status text,
            data_quality_status text, normalisation_status text
        );
        create table awards (
            award_id text, process_id text, source_award_id text, award_date text,
            awarded_amount numeric, currency_code text, award_status text
        );
    """)
    sql = (ROOT / "sql/002_indexes_and_views.sql").read_text()
    for view in ("v_awards_all", "v_awards"):
        match = re.search(
            rf"create or replace view query\.{view} as\n(.*?);", sql, re.DOTALL
        )
        assert match is not None
        select = (
            match[1]
            .replace("mart.", "")
            .replace("query.", "")
            .replace("btrim(", "trim(")
        )
        connection.execute(f"create view {view} as {select}")
    yield connection
    connection.close()


def add_award(
    db,
    name,
    amount,
    *,
    process="AWARDED",
    quality="COMPLETE",
    normalisation="PROCESSED",
    status="active",
    process_id=None,
):
    process_id = process_id or name
    db.execute(
        "insert or ignore into processes values (?, 'GT', ?, ?, ?)",
        (process_id, process, quality, normalisation),
    )
    db.execute(
        "insert into awards values (?, ?, ?, '2026-01-01', ?, 'GTQ', ?)",
        (name, process_id, name, amount, status),
    )


def test_highest_award_is_valid_even_when_cancelled_amount_is_higher(db):
    add_award(db, "cancelled-process", 9000, process="CANCELLED")
    add_award(db, "cancelled-award", 7000, status="cancelled", process_id="mixed")
    add_award(db, "valid", 100, process_id="mixed")
    row = db.execute("""
        select a.award_id from v_awards a join processes p using (process_id)
        where p.country_code = 'GT' order by a.awarded_amount desc nulls last limit 1
    """).fetchone()
    assert row == ("valid",)
    assert db.execute("select count(*) from v_awards").fetchone() == (1,)
    assert db.execute("select max(awarded_amount) from v_awards").fetchone() == (100,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"process": "DESERTED"},
        {"process": "SUSPENDED"},
        {"process": "OPEN", "status": None},
        {"process": None, "status": None},
        {"status": "pending"},
        {"status": "unsuccessful"},
        {"status": "cancelled"},
        {"status": "unknown-source-status"},
    ],
)
def test_excluded_states_never_appear_by_default(db, overrides):
    add_award(db, "excluded", **{"amount": 999, **overrides})
    assert db.execute("select * from v_awards").fetchall() == []
    assert db.execute("select is_valid_award from v_awards_all").fetchone() == (0,)


@pytest.mark.parametrize(
    "process,status,quality",
    [
        ("AWARDED", "active", "COMPLETE"),
        ("CONTRACTED", "active", "PARTIAL"),
        ("COMPLETED", "complete", "COMPLETE"),
        ("AWARDED", None, "PARTIAL"),
    ],
)
def test_accepts_valid_states_and_uses_process_if_award_status_not_published(
    db, process, status, quality
):
    add_award(db, "valid", 100, process=process, status=status, quality=quality)
    assert db.execute("select award_id from v_awards").fetchone() == ("valid",)


def test_explicit_cancelled_and_error_requests_keep_statuses_and_filter_before_limit(
    db,
):
    add_award(db, "cancelled-award", 900, status="cancelled")
    add_award(db, "cancelled-process", 800, process="CANCELLED")
    add_award(db, "unsuccessful", 950, status="unsuccessful")
    add_award(db, "valid", 100)
    cancelled = db.execute("""
        select award_id, award_status, process_status from v_awards_all
        where (award_status = 'cancelled' or process_status = 'CANCELLED')
        order by awarded_amount desc limit 1
    """).fetchone()
    assert cancelled == ("cancelled-award", "cancelled", "AWARDED")
    assert db.execute(
        "select award_id from v_awards_all where award_status = 'unsuccessful'"
    ).fetchone() == ("unsuccessful",)


def test_ocds_preserves_individual_award_status_and_loader_writes_it():
    config = SourceConfig.load(ROOT / "config/sources", "guatemala_guatecompras")
    record = build_record(
        config=config,
        period="202601",
        connector_version="test",
        source_row={
            "ocid": "test",
            "compiledRelease": {
                "tender": {"statusDetails": "Adjudicado"},
                "awards": [
                    {"id": "a", "status": "cancelled"},
                    {"id": "b", "status": "active"},
                ],
            },
        },
    )
    assert record["process_status"] == "AWARDED"
    assert [a["award_status"] for a in record["awards"]] == ["cancelled", "active"]
    database = Database.__new__(Database)
    database.conn = MagicMock()
    database.upsert_record_core_batch = MagicMock()
    database.resolve_buyer_ids = MagicMock(return_value=[])
    database.resolve_supplier_ids = MagicMock(return_value=[])
    database.upsert_mart_split_records([record])
    cursor = database.conn.cursor.return_value.__enter__.return_value
    rows = next(
        call.args[1]
        for call in cursor.executemany.call_args_list
        if call.args[0] == AWARD_SQL
    )
    assert [row[-1] for row in rows] == ["cancelled", "active"]


def test_ocds_does_not_invent_status_when_source_omits_it():
    config = SourceConfig.load(ROOT / "config/sources", "guatemala_guatecompras")
    record = build_record(
        config=config,
        period="202601",
        connector_version="test",
        source_row={"ocid": "test", "awards": [{"id": "a"}]},
    )
    assert record["awards"][0]["award_status"] is None


@pytest.mark.parametrize("quality,normalisation", [
    ("INVALID", "ERROR"), ("PARTIAL", "REVIEW_REQUIRED"), ("COMPLETE", "PROCESSED"),
])
def test_etl_status_does_not_decide_if_award_is_active(db, quality, normalisation):
    add_award(db, "active", 100, quality=quality, normalisation=normalisation)
    add_award(db, "cancelled", 900, status="cancelled", quality=quality,
              normalisation=normalisation)
    assert db.execute("select award_id from v_awards").fetchall() == [("active",)]


def test_explicit_active_award_status_works_without_completed_process(db):
    add_award(db, "active", 100, process="OPEN", status="active")
    assert db.execute("select award_id from v_awards").fetchone() == ("active",)
