import signal
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import create_autospec, patch

from mira_etl.cli import main, stop_requested
from mira_etl.db import Database


class OperationsTest(unittest.TestCase):
    def test_sources_needs_no_database(self):
        output = StringIO()
        with patch.object(sys, "argv", ["mira-etl", "sources"]), \
             patch("mira_etl.cli.Database.from_env") as database, redirect_stdout(output):
            main()
        database.assert_not_called()
        self.assertIn("honduras_oncae", output.getvalue())
        self.assertIn("nicaragua_siscae", output.getvalue())

    def test_check_database_does_not_initialize_schema(self):
        db = create_autospec(Database, instance=True)
        db.fetch_one.return_value = {"db": "mira", "role": "etl"}
        with patch.object(sys, "argv", ["mira-etl", "check-db"]), \
             patch("mira_etl.cli.Database.from_env") as connection, redirect_stdout(StringIO()):
            connection.return_value.__enter__.return_value = db
            main()
        db.validate_schema.assert_called_once()
        db.execute_sql_file.assert_not_called()

    def test_history_is_bounded_and_read_only(self):
        db = create_autospec(Database, instance=True)
        db.fetch_all.return_value = []
        with patch.object(sys, "argv", ["mira-etl", "history", "--limit", "5"]), \
             patch("mira_etl.cli.Database.from_env") as connection, redirect_stdout(StringIO()):
            connection.return_value.__enter__.return_value = db
            main()
        sql, params = db.fetch_all.call_args.args
        self.assertTrue(sql.startswith("select "))
        self.assertEqual(params, (5,))
        db.execute.assert_not_called()

    def test_sigterm_raises_an_exit_caught_by_pipeline_cleanup(self):
        with self.assertRaises(SystemExit) as caught:
            stop_requested(signal.SIGTERM, None)
        self.assertEqual(caught.exception.code, 143)


if __name__ == "__main__":
    unittest.main()
