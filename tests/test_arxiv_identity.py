"""Strict arXiv identity parsing (Daily reliability, phase A)."""
from __future__ import annotations

import sqlite3
import unittest

from ipaper.arxiv_identity import (
    ArxivIdentity,
    base_arxiv_id,
    best_match,
    identity_params,
    identity_sql,
    parse_arxiv_identity,
    same_paper,
)


class ParseTests(unittest.TestCase):
    def test_bare_and_versioned_modern_ids(self):
        self.assertEqual(parse_arxiv_identity("2609.19915"), ArxivIdentity("2609.19915", None))
        self.assertEqual(parse_arxiv_identity("2609.19915v1"), ArxivIdentity("2609.19915", 1))
        self.assertEqual(parse_arxiv_identity("2609.19915V12"), ArxivIdentity("2609.19915", 12))

    def test_decorated_inputs_are_accepted(self):
        for value in (
            " 2609.19915v1 ",
            "arXiv:2609.19915v1",
            "https://arxiv.org/abs/2609.19915v1",
            "https://arxiv.org/pdf/2609.19915v1.pdf",
            "2609.19915v1.pdf",
        ):
            self.assertEqual(parse_arxiv_identity(value), ArxivIdentity("2609.19915", 1), value)

    def test_legacy_slash_ids_keep_their_category(self):
        self.assertEqual(parse_arxiv_identity("cs/0601001"), ArxivIdentity("cs/0601001", None))
        self.assertEqual(parse_arxiv_identity("math.GT/0309136v2"), ArxivIdentity("math.GT/0309136", 2))
        self.assertEqual(base_arxiv_id("cs/0601001v1"), "cs/0601001")

    def test_invalid_values_are_rejected_instead_of_guessed(self):
        # Surrounding slashes are treated as decoration and stripped; anything
        # that is not a recognisable arXiv id is rejected.
        for value in ("", None, "not-an-id", "2609", "v1", "2609.19915x", "cs/060100"):
            self.assertIsNone(parse_arxiv_identity(value), value)
            self.assertEqual(base_arxiv_id(value), "")
            self.assertEqual(identity_params(value), [])

    def test_same_paper_ignores_revisions_only(self):
        self.assertTrue(same_paper("2609.19915", "2609.19915v3"))
        self.assertTrue(same_paper("2609.19915v1", "arXiv:2609.19915v2"))
        self.assertTrue(same_paper("cs/0601001", "cs/0601001v1"))
        self.assertFalse(same_paper("2609.19915", "2609.19916"))
        self.assertFalse(same_paper("2609.19915", "2609.199150"))
        self.assertFalse(same_paper("cs/0601001", "cs/0601002"))
        self.assertFalse(same_paper("2609.19915", "not-an-id"))
        self.assertFalse(same_paper(None, "2609.19915"))
        self.assertFalse(same_paper("2609.19915", None))
        self.assertFalse(same_paper(None, None))

    def test_sql_predicate_cannot_reach_a_neighbouring_paper(self):
        rows = [
            ("2609.19915",),
            ("2609.19915v1",),
            ("2609.199150",),
            ("12609.19915",),
            ("cs/0601001",),
            ("cs/0601001v1",),
            ("2609.19916",),
        ]
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE papers(arxiv_id TEXT)")
            connection.executemany("INSERT INTO papers VALUES (?)", rows)
            predicate, _ = identity_sql("arxiv_id")
            found = {
                row[0]
                for row in connection.execute(
                    f"SELECT arxiv_id FROM papers WHERE {predicate}",
                    identity_params("2609.19915v9"),
                )
            }
            legacy = {
                row[0]
                for row in connection.execute(
                    f"SELECT arxiv_id FROM papers WHERE {predicate}",
                    identity_params("cs/0601001v3"),
                )
            }
        # Only revisions of the same paper: never a neighbouring number.
        self.assertEqual(found, {"2609.19915", "2609.19915v1"})
        self.assertEqual(legacy, {"cs/0601001", "cs/0601001v1"})

    def test_best_match_prefers_the_most_specific_row(self):
        rows = [
            {"arxiv_id": "2609.19915", "id": "bare"},
            {"arxiv_id": "2609.19915v1", "id": "v1"},
            {"arxiv_id": "2609.19915v1", "id": "v1-copy"},
        ]
        self.assertEqual(best_match(rows, "2609.19915v1")["id"], "v1")
        self.assertEqual(best_match(rows, "2609.19915")["id"], "bare")
        self.assertEqual(best_match(rows, "2609.19915v7")["id"], "bare")
        self.assertEqual(best_match([{"arxiv_id": "cs/0601001v1", "id": "legacy"}], "cs/0601001")["id"], "legacy")
        self.assertIsNone(best_match(rows, "not-an-id"))
        self.assertIsNone(best_match([], "2609.19915"))


if __name__ == "__main__":
    unittest.main()