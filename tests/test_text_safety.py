import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from mira_etl.db import Database
from mira_etl.text_safety import nul_paths, postgres_safe
from mira_etl.validation import validate_records
from mira_etl.pipeline import run_pipeline


class TextSafetyTest(unittest.TestCase):
    def test_nested_nul_is_replaced_without_changing_original_or_literal_escape(self):
        original = {"name": "antes\x00despues", "nested": ["á😀", None, 0, False, "\\u0000"]}
        before = deepcopy(original)
        safe = postgres_safe(original)
        self.assertEqual(safe["name"], "antes\ufffddespues")
        self.assertEqual(safe["nested"], original["nested"])
        self.assertEqual(original, before)
        self.assertEqual(nul_paths(original), ['$["name"]'])
        self.assertNotIn("\x00", json.loads(json.dumps(safe))["name"])

    def test_key_collision_fails_instead_of_discarding_a_field(self):
        with self.assertRaisesRegex(ValueError, "merge distinct JSON keys"):
            postgres_safe({"a\x00": 1, "a\ufffd": 2})

    def test_audit_preserves_original_json_and_hash(self):
        original = {"title": "Obra\x00publica", "literal": "\\u0000"}
        record = {"process_id": "test", "raw_payload": original, "raw_payload_hash": "original-hash"}
        issues = validate_records([record])
        issue = next(item for item in issues if item["rule_code"] == "SOURCE_NUL_CHARACTER")
        stored = json.loads(json.dumps(postgres_safe(issue)))
        self.assertEqual(json.loads(stored["payload"]["original_payload_json"]), original)
        self.assertEqual(stored["raw_payload_hash"], "original-hash")
        self.assertEqual(record["raw_payload"], original)
        self.assertEqual(issue["severity"], "WARNING")
        self.assertEqual(record["data_quality_status"], "PARTIAL")

    def test_raw_staging_and_validation_bind_safe_values(self):
        db = Database.__new__(Database)
        db.conn = MagicMock()
        cursor = db.conn.cursor.return_value.__enter__.return_value
        # Copy batches: the real implementation clears the list after writing.
        written = []
        cursor.executemany.side_effect = lambda sql, rows: written.extend(deepcopy(rows))
        db.insert_raw_rows(run_id=1, source_file_id=1, rows=[{"text": "a\x00b"}])
        self.assertEqual(json.loads(written[0][-1]), {"text": "a\ufffdb"})
        written.clear()
        record = {"source_record_id": "id\x00", "raw_payload_hash": "hash", "title": "t\x00"}
        db.insert_staging_candidates(run_id=1, source="test", period="202412", records=[record])
        self.assertEqual(written[0][3], "id\ufffd")
        self.assertEqual(json.loads(written[0][-1])["title"], "t\ufffd")
        written.clear()
        db.insert_validation_results(run_id=1, source="test", period="202412", results=[{
            "rule_code": "TEST", "severity": "WARNING", "raw_value": "a\x00b",
            "message": "test", "payload": {"original_payload_json": json.dumps({"t": "\x00"})},
        }])
        self.assertEqual(written[0][8], "a\ufffdb")
        self.assertEqual(json.loads(json.loads(written[0][-1])["original_payload_json"]), {"t": "\x00"})

    def test_mart_text_and_json_are_cleaned_before_writes(self):
        db = Database.__new__(Database)
        db.conn = MagicMock()
        cursor = db.conn.cursor.return_value.__enter__.return_value
        records = [{"description": "a\x00b", "raw_payload": {"text": "a\x00b"}, "missing_fields": []}]
        db.upsert_record_core_batch(records)
        values = cursor.executemany.call_args.args[1][0]
        self.assertEqual(values["description"], "a\ufffdb")
        self.assertEqual(json.loads(values["raw_payload"]), {"text": "a\ufffdb"})
        self.assertEqual(records[0]["description"], "a\x00b")


@unittest.skipUnless(os.environ.get("MIRA_TEST_DB_URL"), "Requires an isolated PostgreSQL test database")
class PostgresTextSafetyTest(unittest.TestCase):
    def test_full_load_and_reprocessing_preserve_nul_evidence(self):
        dsn = os.environ["MIRA_TEST_DB_URL"]
        if not urlsplit(dsn).path.endswith("_test"):
            self.fail("MIRA_TEST_DB_URL must point to a disposable database ending in _test")
        with Database(dsn) as db:
            for path in sorted(Path("sql").glob("*.sql")):
                db.execute_sql_file(path)
        original = {
            "ocid": "ocds-nul-regression",
            "compiledRelease": {
                "ocid": "ocds-nul-regression", "date": "2024-12-01T00:00:00Z",
                "buyer": {"id": "buyer-nul-test", "name": "Buyer\x00name"},
                "tender": {
                    "id": "nul-test", "title": "Title\x00text", "description": "Before\x00after",
                    "items": [{"id": "item-1", "description": "Item\x00description"}],
                },
                "awards": [{"id": "award-1", "items": [{"id": "item-1"}],
                            "suppliers": [{"id": "supplier-1", "name": "Supplier\x00name"}]}],
            },
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SUPABASE_DB_URL": dsn}):
            archive = Path(directory) / "source.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("records.json", json.dumps({"records": [original]}))
            for _ in range(2):
                self.assertEqual(run_pipeline(
                    source="guatemala_guatecompras", period="202412",
                    config_dir=Path("config/sources"), work_dir=Path(directory) / "work",
                    local_zip=archive, force_reprocess=True,
                ), "SUCCESS")
        with Database(dsn) as db:
            process = db.fetch_one("select description,raw_payload_hash from mart.processes where source_record_id=%s", ("ocds-nul-regression",))
            self.assertEqual(process["description"], "Before\ufffdafter")
            evidence = db.fetch_one("select payload,raw_payload_hash from audit.validation_results where rule_code='SOURCE_NUL_CHARACTER' order by validation_id desc limit 1", ())
            self.assertEqual(json.loads(evidence["payload"]["original_payload_json"]), original)
            self.assertEqual(evidence["raw_payload_hash"], process["raw_payload_hash"])
            index = db.fetch_one("select indisvalid from pg_index where indexrelid='mart.idx_award_items_item'::regclass", ())
            self.assertTrue(index["indisvalid"])


if __name__ == "__main__":
    unittest.main()
