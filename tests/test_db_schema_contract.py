from __future__ import annotations

import unittest
import re
from pathlib import Path

from mira_etl.db import SCHEMA_CONTRACT, first_entity_id, schema_mismatches


class DatabaseSchemaContractTest(unittest.TestCase):
    def test_entity_matching_does_not_fall_back_when_tax_id_exists(self) -> None:
        entity_id = first_entity_id(
            "CR", "SICOP", "NEW-TAX", None, "SAME NAME",
            {("CR", "OTHER-TAX"): 1}, {}, {("CR", "SAME NAME"): 1},
        )
        self.assertIsNone(entity_id)

    def test_award_supplier_relationship_has_composite_primary_key(self) -> None:
        sql = (Path(__file__).parents[1] / "sql" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        body = create_table_body(sql, "mart.award_suppliers")
        self.assertIsNotNone(body)
        self.assertRegex(
            body or "",
            r"primary\s+key\s*\(\s*award_id\s*,\s*supplier_id\s*\)",
        )

    def test_buyer_relationship_has_composite_primary_key(self) -> None:
        sql = (Path(__file__).parents[1] / "sql" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        body = create_table_body(sql, "mart.process_buyers")
        self.assertIsNotNone(body)
        self.assertRegex(
            body or "",
            r"primary\s+key\s*\(\s*process_id\s*,\s*buyer_id\s*\)",
        )

    def test_initial_schema_contains_no_historical_migrations(self) -> None:
        sql = (Path(__file__).parents[1] / "sql" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(sql, r"(?im)^\s*(alter|drop|delete|update|insert)\b")

    def test_public_coverage_is_source_grain_and_outside_mart(self) -> None:
        sql = (Path(__file__).parents[1] / "sql" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        country_body = create_table_body(sql, "web.countries") or ""
        self.assertTrue(country_body)
        for column in ("country_code", "display_name", "flag_asset"):
            self.assertRegex(country_body, rf"\b{column}\b")
        self.assertNotRegex(country_body, r"\bstatus\b")
        body = create_table_body(sql, "web.coverage_sources") or ""
        self.assertTrue(body)
        for column in ("source_key", "country_code", "source_system", "status"):
            self.assertRegex(body, rf"\b{column}\b")
        self.assertNotIn("mart.web_country_stats", sql)

    def test_mart_uses_process_domain_names_without_redundant_prefixes(self) -> None:
        sql = (Path(__file__).parents[1] / "sql" / "001_init.sql").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("mart.procurement_", sql)
        self.assertNotIn("mart.procurement_process_details", sql)
        process_body = create_table_body(sql, "mart.processes") or ""
        for column in ("title", "process_status", "estimated_amount"):
            self.assertRegex(process_body, rf"\b{column}\b")

    def test_sql_declares_every_table_and_column_used_by_db(self) -> None:
        sql_dir = Path(__file__).parents[1] / "sql"
        sql = (sql_dir / "001_init.sql").read_text(encoding="utf-8")
        # Additions may live in later numbered SQL files, while 001_init.sql
        # remains the CREATE-only definition for fresh installations.
        all_sql = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(sql_dir.glob("*.sql"))
        )
        for table, columns in SCHEMA_CONTRACT.items():
            body = create_table_body(sql, table)
            self.assertIsNotNone(body, f"{table} is not created by any sql/*.sql file")
            for column in columns:
                declared_in_create = re.search(rf"\b{re.escape(column)}\b", body or "")
                declared_by_alter = re.search(
                    rf"alter\s+table\s+{re.escape(table)}.*?"
                    rf"add\s+column\s+if\s+not\s+exists\s+{re.escape(column)}\b",
                    all_sql,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                self.assertTrue(
                    declared_in_create or declared_by_alter,
                    f"{table}.{column} is used by db.py but absent from sql/*.sql",
                )

    def test_accepts_the_declared_schema(self) -> None:
        actual = {table: set(columns) for table, columns in SCHEMA_CONTRACT.items()}
        self.assertEqual(schema_mismatches(actual), [])

    def test_reports_missing_table_and_column(self) -> None:
        actual = {table: set(columns) for table, columns in SCHEMA_CONTRACT.items()}
        del actual["mart.buyers"]
        actual["mart.processes"].remove("source_record_id")

        self.assertEqual(
            schema_mismatches(actual),
            [
                "mart.processes missing columns source_record_id",
                "missing table mart.buyers",
            ],
        )

    def test_reports_a_redundant_column(self) -> None:
        actual = {table: set(columns) for table, columns in SCHEMA_CONTRACT.items()}
        actual["mart.process_buyers"].add("buyer_name")
        self.assertEqual(
            schema_mismatches(actual),
            ["mart.process_buyers has unexpected columns buyer_name"],
        )

def all_sql_text() -> str:
    sql_dir = Path(__file__).parents[1] / "sql"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(sql_dir.glob("*.sql"))
    )


def create_table_body(sql: str, table: str) -> str | None:
    match = re.search(
        rf"create\s+table\s+if\s+not\s+exists\s+{re.escape(table)}\s*\(",
        sql,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    start = match.end()
    depth = 1
    for index in range(start, len(sql)):
        if sql[index] == "(":
            depth += 1
        elif sql[index] == ")":
            depth -= 1
            if depth == 0:
                return sql[start:index]
    return None


if __name__ == "__main__":
    unittest.main()
