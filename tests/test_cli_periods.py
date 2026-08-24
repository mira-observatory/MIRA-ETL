from __future__ import annotations

import re
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from mira_etl.cli import main, parse_period_expression, resolve_period, resolve_periods


CONFIG_DIR = Path(__file__).parents[1] / "config" / "sources"


class CliPeriodTest(unittest.TestCase):
    def test_html_source_defaults_to_current_period(self) -> None:
        period = resolve_period(
            source="nicaragua_siscae",
            period=None,
            config_dir=CONFIG_DIR,
        )

        self.assertRegex(period, re.compile(r"^\d{6}$"))

    def test_historical_source_requires_period(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_period(
                source="guatemala_guatecompras",
                period=None,
                config_dir=CONFIG_DIR,
            )

    def test_explicit_period_is_preserved(self) -> None:
        self.assertEqual(
            resolve_period(
                source="nicaragua_siscae",
                period="202607",
                config_dir=CONFIG_DIR,
            ),
            "202607",
        )

    def test_expands_inclusive_period_range(self) -> None:
        self.assertEqual(
            parse_period_expression("202501 - 202504"),
            ["202501", "202502", "202503", "202504"],
        )

    def test_expands_period_range_across_years(self) -> None:
        self.assertEqual(
            parse_period_expression("202511-202602"),
            ["202511", "202512", "202601", "202602"],
        )

    def test_rejects_invalid_or_reversed_period_range(self) -> None:
        for value in ("202513", "202512 - 202501", "2025-01"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                parse_period_expression(value)

    def test_historical_source_accepts_period_range(self) -> None:
        self.assertEqual(
            resolve_periods(
                source="guatemala_guatecompras",
                period="202501 - 202503",
                config_dir=CONFIG_DIR,
            ),
            ["202501", "202502", "202503"],
        )

    def test_current_html_source_rejects_period_range(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_periods(
                source="nicaragua_siscae",
                period="202501 - 202503",
                config_dir=CONFIG_DIR,
            )

    def test_backfill_continues_after_errors_and_fails_at_end(self) -> None:
        outcomes = ["SKIPPED", RuntimeError("download failed"), "SUCCESS"]
        output = StringIO()
        with patch.object(
            sys,
            "argv",
            [
                "mira-etl",
                "run",
                "--source",
                "guatemala_guatecompras",
                "--period",
                "202601 - 202603",
            ],
        ), patch("mira_etl.cli.run_pipeline", side_effect=outcomes) as runner, (
            redirect_stdout(output)
        ), self.assertRaises(SystemExit) as raised:
            main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(runner.call_count, 3)
        log = output.getvalue()
        self.assertIn("202601 - SKIPPED", log)
        self.assertIn("202602 - ERROR", log)
        self.assertIn("202603 - SUCCESS", log)
        self.assertIn("Processed: 1", log)
        self.assertIn("Skipped: 1", log)
        self.assertIn("Failed: 1", log)


if __name__ == "__main__":
    unittest.main()
