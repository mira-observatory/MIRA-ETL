"""Run the actual historical backfill against session-local temporary tables.

MIRA_TEST_BACKFILL_DB_URL needs only TEMP permission; permanent data is untouched.
CI falls back to its disposable MIRA_TEST_DB_URL.
"""
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

DSN = os.environ.get("MIRA_TEST_BACKFILL_DB_URL") or os.environ.get("MIRA_TEST_DB_URL")


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL with TEMP permission")
def test_historical_backfill_preserves_source_rules_and_is_idempotent():
    sql = (Path(__file__).parents[1] / "sql/002_indexes_and_views.sql").read_text()
    extraction = sql.split("-- Correlate API responses", 1)[0]
    update = sql[sql.index("alter table mart.awards add column"):sql.index("-- Full source records")]
    backfill = (extraction + update).replace("mart.processes", "pg_temp.processes").replace(
        "mart.awards", "pg_temp.awards"
    )
    assert "mart." not in backfill
    with psycopg.connect(DSN, connect_timeout=15) as conn:
        conn.execute("create temporary table processes (process_id text primary key, raw_payload jsonb)")
        conn.execute("create temporary table awards (award_id text primary key, process_id text, source_award_id text)")
        sources = {
            "compiled": {"compiledRelease": {"awards": [
                {"id": "a", "status": " ACTIVE "}, {"id": "b", "status": "cancelled"},
                {"id": "c", "status": "  "}, {"id": "d"},
            ], "contracts": [{"id": "e", "status": "complete"}]}},
            "contracts": {"awards": [], "contracts": [
                {"awardID": "a", "status": "Complete"},
            ]},
            "wrong_type": {"awards": {}, "contracts": [{"id": "a", "status": "active"}]},
            "no_arrays": {"awards": {}, "contracts": {}},
            "missing": {},
        }
        for process_id, payload in sources.items():
            conn.execute("insert into pg_temp.processes values (%s,%s)", (process_id, Jsonb(payload)))
            for source_id in ("a", "b", "c", "d", "e", "unmatched"):
                conn.execute("insert into pg_temp.awards values (%s,%s,%s)",
                             (process_id + ":" + source_id, process_id, source_id))
        conn.commit()
        conn.execute(backfill)
        first = dict(conn.execute("select award_id,award_status from pg_temp.awards").fetchall())
        known = {"compiled:a": "active", "compiled:b": "cancelled",
                 "contracts:a": "complete", "wrong_type:a": "active"}
        assert {key: value for key, value in first.items() if value is not None} == known
        conn.commit()
        assert conn.execute("select to_regclass('pg_temp.mira_award_status_backfill')").fetchone() == (None,)
        conn.execute(backfill)
        assert dict(conn.execute("select award_id,award_status from pg_temp.awards").fetchall()) == first
        conn.commit()
        # A subsequent source correction must not overwrite an existing status.
        conn.execute("update pg_temp.awards set award_status='pending' where award_id='compiled:a'")
        conn.execute(backfill)
        assert conn.execute("select award_status from pg_temp.awards where award_id='compiled:a'").fetchone() == ("pending",)
        conn.rollback()
