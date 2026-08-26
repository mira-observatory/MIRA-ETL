from __future__ import annotations

import re
import unittest
from pathlib import Path

SQL_DIR = Path(__file__).parents[1] / "sql"


class SemanticDictionaryEnumTest(unittest.TestCase):
    """query.semantic_dictionary hand-copies enum lists from CHECK
    constraints (sql/003_seed_base_data.sql can't read pg_constraint at
    migration time without duplicating logic elsewhere). This test is what
    keeps the copy honest: it fails the moment someone changes a CHECK
    constraint's allowed values without updating the dictionary to match."""

    def test_process_status_matches_001_init(self) -> None:
        self.assert_enum_matches(
            check_sql=read("001_init.sql"),
            check_column="process_status",
            dictionary_view="query.v_process",
            dictionary_column="process_status",
        )

    def test_data_quality_status_matches_001_init(self) -> None:
        self.assert_enum_matches(
            check_sql=read("001_init.sql"),
            check_column="data_quality_status",
            dictionary_view="query.v_process",
            dictionary_column="data_quality_status",
        )

    def test_supplier_type_matches_001_init(self) -> None:
        self.assert_enum_matches(
            check_sql=read("001_init.sql"),
            check_column="supplier_type",
            dictionary_view="query.v_suppliers",
            dictionary_column="supplier_type",
        )

    def test_award_amount_relationship_is_documented(self) -> None:
        seed = read("003_seed_base_data.sql")
        self.assertIn("query.v_awards.process_id", seed)
        self.assertIn("query.v_awards.awarded_amount", seed)
        self.assertIn("No es gasto real ni monto adjudicado", seed)

    def assert_enum_matches(
        self,
        *,
        check_sql: str,
        check_column: str,
        dictionary_view: str,
        dictionary_column: str,
    ) -> None:
        from_constraint = extract_check_enum(check_sql, check_column)
        self.assertTrue(
            from_constraint,
            f"could not find a CHECK ... in (...) constraint for {check_column}",
        )
        from_dictionary = extract_dictionary_enum(
            read("003_seed_base_data.sql"), dictionary_view, dictionary_column
        )
        self.assertTrue(
            from_dictionary,
            f"could not find an array[...] literal for "
            f"({dictionary_view}, {dictionary_column}) in the semantic dictionary seed",
        )
        self.assertEqual(
            from_constraint,
            from_dictionary,
            f"{check_column} CHECK constraint and semantic_dictionary enum_values "
            "have drifted apart",
        )


def read(filename: str) -> str:
    return (SQL_DIR / filename).read_text(encoding="utf-8")


def extract_check_enum(sql: str, column: str) -> set[str]:
    nullable = re.search(
        rf"{re.escape(column)}\s+is\s+null\s+or\s+{re.escape(column)}\s+in\s*\(([\s\S]*?)\)",
        sql,
        flags=re.IGNORECASE,
    )
    if nullable:
        return set(re.findall(r"'([^']+)'", nullable.group(1)))

    not_null = re.search(
        rf"{re.escape(column)}\s+in\s*\(([\s\S]*?)\)",
        sql,
        flags=re.IGNORECASE,
    )
    if not_null:
        return set(re.findall(r"'([^']+)'", not_null.group(1)))

    return set()


def extract_dictionary_enum(sql: str, view_name: str, column_name: str) -> set[str]:
    row = re.search(
        rf"'{re.escape(view_name)}',\s*'{re.escape(column_name)}',.*?array\[([^\]]*)\]",
        sql,
    )
    if not row:
        return set()
    return set(re.findall(r"'([^']+)'", row.group(1)))


if __name__ == "__main__":
    unittest.main()
