"""Use session-local temporary objects only, including the real eligibility views."""

import os
import re
from pathlib import Path

import psycopg
import pytest

from mira_etl.db import Database

DSN = os.environ.get("MIRA_TEST_TOTALS_DB_URL") or os.environ.get("MIRA_TEST_DB_URL")


class TemporaryDatabase(Database):
    def execute(self, sql, params=()):
        self.conn.execute(sql.replace("mart.", "pg_temp.").replace("query.", "pg_temp."), params)


@pytest.mark.skipif(not DSN, reason="requires PostgreSQL with TEMP permission")
def test_totals_preserve_currency_equal_amounts_shared_awards_and_refresh():
    root = Path(__file__).parents[1]
    with psycopg.connect(DSN, connect_timeout=10, autocommit=True) as conn:
        conn.execute("""
            create temporary table processes (
                process_id text primary key, country_code text, process_status text,
                data_quality_status text, normalisation_status text
            );
            create temporary table awards (
                award_id text primary key, process_id text, source_award_id text,
                award_date date, awarded_amount numeric, currency_code text, award_status text
            );
            create temporary table award_suppliers (
                award_id text, supplier_id bigint, primary key(award_id, supplier_id)
            );
        """)
        init = (root / "sql/001_init.sql").read_text(encoding="utf-8")
        table = re.search(r"create table if not exists mart.supplier_award_totals \(.*?\);",
                          init, re.S)[0]
        conn.execute(table.replace("create table if not exists mart.", "create temporary table "))
        views = (root / "sql/002_indexes_and_views.sql").read_text(encoding="utf-8")
        for name in ("v_awards_all", "v_awards"):
            sql = re.search(rf"create or replace view query\.{name} as\n.*?;", views, re.S)[0]
            conn.execute(sql.replace("query.", "pg_temp.").replace("mart.", "pg_temp."))
        conn.execute("insert into pg_temp.processes values ('gt','GT','AWARDED',null,null),"
                     "('cr','CR','AWARDED',null,null)")
        for award_id, amount, currency, status, process in [
            ('a', '100.10', 'GTQ', 'active', 'gt'),
            ('b', '100.10', 'GTQ', 'active', 'gt'),
            ('c', '999.99', 'USD', 'active', 'gt'),
            ('d', '9000', 'GTQ', 'cancelled', 'gt'),
            ('shared', '30', 'GTQ', 'active', 'gt'),
            ('unknown-amount', None, 'GTQ', 'active', 'gt'),
            ('unknown-currency', '8000', None, 'active', 'gt'),
            ('cr', '5000', 'GTQ', 'active', 'cr'),
        ]:
            conn.execute("insert into pg_temp.awards values (%s,%s,%s,null,%s,%s,%s)",
                         (award_id, process, award_id, amount, currency, status))
            conn.execute("insert into pg_temp.award_suppliers values (%s,1)", (award_id,))
        conn.execute("insert into pg_temp.award_suppliers values ('shared',2)")
        db = object.__new__(TemporaryDatabase)
        db.conn = conn
        select = ("select supplier_id,currency_code,total_awarded_amount::text,award_count,"
                  "shared_award_count from pg_temp.supplier_award_totals order by 1,2")
        expected = [(1, 'GTQ', '230.20', 3, 1), (1, 'USD', '999.99', 1, 0),
                    (2, 'GTQ', '30', 1, 1)]
        db.refresh_supplier_award_totals(country_code="GT")
        assert conn.execute(select).fetchall() == expected
        db.refresh_supplier_award_totals(country_code="GT")
        assert conn.execute(select).fetchall() == expected
        # A failure after DELETE must roll back the complete previous snapshot.
        with pytest.raises(psycopg.errors.DivisionByZero):
            with conn.transaction():
                db.execute("delete from mart.supplier_award_totals where country_code='GT'")
                conn.execute("select 1/0")
        assert conn.execute(select).fetchall() == expected
        conn.execute("update pg_temp.awards set award_status='cancelled' where award_id='c'")
        db.refresh_supplier_award_totals(country_code="GT")
        assert conn.execute(select).fetchall() == [expected[0], expected[2]]
