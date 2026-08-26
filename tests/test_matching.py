from __future__ import annotations

import unittest

from mira_etl.matching import normalise_name


class NormaliseNameTest(unittest.TestCase):
    def test_preserves_published_spelling(self) -> None:
        self.assertEqual(
            normalise_name("  Constructora  Pérez, S.A.  "),
            "Constructora Pérez, S.A.",
        )

    def test_normalises_unicode_composition(self) -> None:
        self.assertEqual(normalise_name("Pe\u0301rez"), "Pérez")

    def test_does_not_merge_distinct_spellings(self) -> None:
        self.assertNotEqual(normalise_name("Pérez, S.A."), normalise_name("PEREZ"))

    def test_empty_whitespace_is_none(self) -> None:
        self.assertIsNone(normalise_name(" \t\n "))


if __name__ == "__main__":
    unittest.main()
