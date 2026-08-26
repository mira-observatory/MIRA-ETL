from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mira_etl.pipeline import run_pipeline


CONFIG_DIR = Path(__file__).parents[1] / "config" / "sources"


class FakeDatabase:
    def __init__(self, successful: bool) -> None:
        self.successful = successful
        self.success_checks: list[tuple[str, str]] = []
        self.inserted_runs = 0
        self.finished_errors = 0

    def validate_schema(self) -> None:
        pass

    def has_successful_run(self, *, source: str, period: str) -> bool:
        self.success_checks.append((source, period))
        return self.successful

    def insert_run(self, **kwargs: Any) -> int:
        self.inserted_runs += 1
        return 123

    def finish_run_after_error(self, run_id: int, error_message: str) -> None:
        self.finished_errors += 1


class ReprocessingTest(unittest.TestCase):
    def run_until_extraction(
        self,
        *,
        source: str = "guatemala_guatecompras",
        successful: bool,
        force_reprocess: bool = False,
    ) -> FakeDatabase:
        db = FakeDatabase(successful)
        with tempfile.TemporaryDirectory() as directory, patch(
            "mira_etl.pipeline.Database.from_env",
            return_value=nullcontext(db),
        ), patch(
            "mira_etl.pipeline.obtain_zip",
            side_effect=RuntimeError("extraction reached"),
        ), patch(
            "mira_etl.pipeline.obtain_source_rows",
            side_effect=RuntimeError("extraction reached"),
        ):
            try:
                run_pipeline(
                    source=source,
                    period="202607",
                    config_dir=CONFIG_DIR,
                    work_dir=Path(directory),
                    local_zip=None,
                    force_reprocess=force_reprocess,
                )
            except RuntimeError as exc:
                self.assertEqual(str(exc), "extraction reached")
        return db

    def test_successful_period_is_skipped(self) -> None:
        db = self.run_until_extraction(successful=True)

        self.assertEqual(
            db.success_checks,
            [("guatemala_guatecompras", "202607")],
        )
        self.assertEqual(db.inserted_runs, 0)

    def test_error_or_missing_success_allows_retry(self) -> None:
        db = self.run_until_extraction(successful=False)

        self.assertEqual(db.inserted_runs, 1)
        self.assertEqual(db.finished_errors, 1)

    def test_force_reprocess_bypasses_success(self) -> None:
        db = self.run_until_extraction(successful=True, force_reprocess=True)

        self.assertEqual(db.success_checks, [])
        self.assertEqual(db.inserted_runs, 1)

    def test_current_state_source_is_never_monthly_skipped(self) -> None:
        db = self.run_until_extraction(
            source="nicaragua_siscae",
            successful=True,
        )

        self.assertEqual(db.success_checks, [])
        self.assertEqual(db.inserted_runs, 1)


if __name__ == "__main__":
    unittest.main()
