"""Strict arXiv identity parsing shared by discovery, assets and routes.

The Daily pipeline mixes two stored forms: the paper row keeps the bare id
(``2609.19915``) while the candidate queue keeps the announced revision
(``2609.19915v1``). Identity must therefore be compared by *base id plus an
optional version*, never by raw string equality and never by fuzzy matching that
could reach a different paper.

Supported inputs: bare modern ids (``2609.19915``), versioned ids
(``2609.19915v3``), legacy slash ids (``cs/0601001``, ``math.GT/0309136v2``),
``arXiv:`` prefixes, ``abs``/``pdf`` URLs and a trailing ``.pdf``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

_PREFIX = re.compile(r"^(?:arxiv:|https?://arxiv\.org/(?:abs|pdf)/)", re.I)
_PDF_SUFFIX = re.compile(r"\.pdf$", re.I)
_VERSION_SUFFIX = re.compile(r"v(\d{1,4})$", re.I)
_MODERN = re.compile(r"^\d{4}\.\d{4,5}$")
_LEGACY = re.compile(r"^[a-z][a-z.\-]*/\d{7}$", re.I)


@dataclass(frozen=True)
class ArxivIdentity:
    """A paper identity: the base id plus an explicitly kept version."""

    base: str
    version: Optional[int] = None

    @property
    def key(self) -> str:
        """Case-insensitive comparison key (legacy ids keep their category)."""
        return self.base.lower()

    @property
    def versioned(self) -> str:
        return f"{self.base}v{self.version}" if self.version else self.base

    def matches(self, other: "ArxivIdentity | None") -> bool:
        return other is not None and self.key == other.key

    def __str__(self) -> str:  # pragma: no cover - debugging helper
        return self.versioned


def parse_arxiv_identity(value: Any) -> Optional[ArxivIdentity]:
    """Return the identity of *value*, or ``None`` when it is not an arXiv id.

    The parser is deliberately strict: an unrecognised string yields ``None`` so
    callers can reject it instead of matching something unintended.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = _PREFIX.sub("", text)
    text = _PDF_SUFFIX.sub("", text)
    text = text.strip().strip("/")
    if not text:
        return None

    version: Optional[int] = None
    match = _VERSION_SUFFIX.search(text)
    if match:
        version = int(match.group(1))
        text = text[: match.start()]

    if not (_MODERN.match(text) or _LEGACY.match(text)):
        return None
    return ArxivIdentity(base=text, version=version)


def base_arxiv_id(value: Any) -> str:
    """Base id without a version, or ``""`` when the value is not an arXiv id."""
    identity = parse_arxiv_identity(value)
    return identity.base if identity else ""


def same_paper(left: Any, right: Any) -> bool:
    """True when both values denote the same paper, ignoring the revision.

    Invalid or missing values are never "the same paper" as anything.
    """
    a, b = parse_arxiv_identity(left), parse_arxiv_identity(right)
    if a is None or b is None:
        return False
    return a.key == b.key


def identity_keys(value: Any) -> list[str]:
    """Stored forms that legitimately denote this identity (lower-cased).

    Used to build strict SQL predicates. Nothing here can match a *different*
    paper: every variant is anchored to this base id.
    """
    identity = parse_arxiv_identity(value)
    if identity is None:
        return []
    base = identity.key
    keys = {base, f"{base}v{identity.version}"} if identity.version else {base}
    # Legacy rows may or may not carry the category prefix.
    if "/" not in base:
        keys.add(base)
    return sorted(keys)


def identity_sql(column: str = "arxiv_id") -> tuple[str, int]:
    """SQL predicate (and placeholder count) for an identity comparison.

    Matches the bare id, the same base with any version suffix, and legacy
    slash-prefixed forms. The suffix wildcards are anchored to the base, so a
    different paper can never satisfy the predicate.
    """
    predicate = (
        f"(lower({column}) = ?"
        f" OR lower({column}) LIKE ? || 'v%'"
        f" OR lower({column}) LIKE '%/' || ?"
        f" OR lower({column}) LIKE '%/' || ? || 'v%')"
    )
    return predicate, 4


def identity_params(value: Any) -> list[str]:
    identity = parse_arxiv_identity(value)
    if identity is None:
        return []
    base = identity.key
    return [base, base, base, base]


def best_match(
    candidates: Iterable[dict], value: Any
) -> Optional[dict]:
    """Pick the row that best represents *value* from same-identity rows.

    Preference: exact stored form, then the bare base id, then the requested
    version, then any other revision of the same paper.
    """
    identity = parse_arxiv_identity(value)
    if identity is None:
        return None
    requested = str(value).strip().lower()
    rows = [row for row in candidates if row]
    if not rows:
        return None

    def stored(row: dict) -> str:
        return str(row.get("arxiv_id") or "").strip().lower()

    for row in rows:
        if stored(row) == requested:
            return row
    for row in rows:
        if stored(row) == identity.key:
            return row
    if identity.version:
        versioned = f"{identity.key}v{identity.version}"
        for row in rows:
            if stored(row) == versioned:
                return row
    return rows[0]