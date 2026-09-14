"""
arXiv and DBLP metadata lookup helpers.

Untrusted PDF parsing belongs exclusively to the isolated Document Worker.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

import arxiv
import html
import xml.etree.ElementTree as ET

from ipaper.tools.basic_tools.arxiv_client import get_bibtex_enhanced
from ipaper.tools.basic_tools.arxiv_network import arxiv_get, configure_arxiv_client

# ============================================================================
# Utility function
# ============================================================================


def _make_arxiv_client(*args: Any, **kwargs: Any) -> arxiv.Client:
    return configure_arxiv_client(arxiv.Client(*args, **kwargs))


def _normalize_arxiv_id(arxiv_id: str) -> str:
    """
    standardization arXiv ID

    Args:
        arxiv_id: may be "arXiv:2502.05383", "2502.05383", "2502.05383v1" etc format

    Returns:
        standardized arXiv ID(like "2502.05383"), remove the version number
    """
    # Remove "arXiv:" prefix (case insensitive)
    arxiv_id = re.sub(r"^arxiv\s*:\s*", "", arxiv_id.strip(), flags=re.IGNORECASE)

    # Remove version number (v1, v2 wait)
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)

    # Extract core ID（YYYY.NNNNN Format)
    match = re.search(r"(\d{4}\.\d{4,5})", arxiv_id)
    if match:
        return match.group(1)

    return arxiv_id


def _extract_arxiv_id_from_url(url: str) -> Optional[str]:
    """
    from URL extracted from arXiv ID

    Args:
        url: may be "https://arxiv.org/abs/2511.13720v1" or "https://doi.org/10.48550/arXiv.2511.13720"

    Returns:
        extracted arXiv ID, return on failure None
    """
    patterns = [
        r"arxiv\.org/(?:abs|pdf)/([\d.]+(?:v\d+)?)",
        r"doi\.org/10\.48550/arXiv\.([\d.]+)",
        r"arxiv\.org/abs/([\d.]+(?:v\d+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            arxiv_id = match.group(1)
            return _normalize_arxiv_id(arxiv_id)

    return None


def _extract_arxiv_id_from_filename(filename: str) -> Optional[str]:
    """
    Extract from file name arXiv ID

    Supported formats:
    - 1706.03762v7.pdf
    - arXiv:1706.03762v7.pdf
    - 1706.03762.pdf

    Args:
        filename: PDF file name

    Returns:
        extracted arXiv ID, return on failure None
    """
    # Remove .pdf suffix
    base = os.path.splitext(filename)[0]

    # match YYYY.NNNNNvN Format
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", base)
    if match:
        return _normalize_arxiv_id(match.group(0))

    # match arXiv:YYYY.NNNNNvN Format
    match = re.search(r"arxiv[:\-\s]?(\d{4}\.\d{4,5})(v\d+)?", base, re.IGNORECASE)
    if match:
        return _normalize_arxiv_id(match.group(0))

    return None


# ============================================================================
# Way1: pass arXiv ID Get paper information
# ============================================================================


def _import_arxiv_records(params):
    """One bounded official query for imports that explicitly need a download."""
    from ipaper.database.connection import DB_PATH
    from ipaper.security.identity import current_user_id
    from ipaper.metadata.store import MetadataStore
    from ipaper.metadata.providers import BibliographicHTTP, PARSERS
    store = MetadataStore(DB_PATH, current_user_id())
    http = BibliographicHTTP(store, lambda: None, lambda: None)
    return PARSERS['arxiv'](http.get('arxiv', '/api/query', params, ttl=86400))


def _import_arxiv_value(record):
    fields = record['fields']
    identifier = fields['arxiv_id'] + (('v'+str(fields['arxiv_version'])) if fields.get('arxiv_version') else '')
    return {**fields, 'arxiv_id': identifier, 'arxiv_url': 'https://arxiv.org/abs/'+identifier,
            'pdf_url': 'https://arxiv.org/pdf/'+identifier+'.pdf', 'summary': fields.get('abstract',''),
            'published_date': fields.get('preprint_date'), 'bibtex': ''}


def fetch_paper_by_arxiv_id_fast(arxiv_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a requested arXiv version without upgrading the downloaded file."""
    from ipaper.metadata.model import arxiv
    try:
        base, version = arxiv(arxiv_id)
        identifier = base + (('v'+str(version)) if version else '')
        records = _import_arxiv_records({'id_list': identifier})
        records = [r for r in records if r['fields'].get('arxiv_id') == base
                   and (not version or str(r['fields'].get('arxiv_version')) == str(version))]
        return _import_arxiv_value(records[0]) if len(records) == 1 else None
    except Exception:
        # Supplier diagnostics belong to the persistent enrichment queue. Never
        # fall back to an unbounded SDK call or an unverified HTML first hit.
        return None


def fetch_bibtex_from_dblp(title: str, authors: str, arxiv_id: str) -> Optional[str]:
    """
    from DBLP get BibTeX(Can be called in the background)

    Args:
        title: Paper title
        authors: author string
        arxiv_id: arXiv ID

    Returns:
        BibTeX String, returned on failure None
    """
    try:
        print(f"[DBLP] get BibTeX: {title[:50]}...")
        bibtex = get_bibtex_enhanced(title=title, authors=authors, arxiv_id=arxiv_id)
        if bibtex:
            print(f"[DBLP] ✅ successfully obtained BibTeX")
        else:
            print(f"[DBLP] ❌ Not obtained BibTeX")
        return bibtex
    except Exception as exc:
        print(f"[DBLP] ❌ get BibTeX fail: {exc}")
        return None


def fetch_paper_by_arxiv_id(arxiv_id: str) -> Optional[Dict[str, Any]]:
    """
    Way1: pass arXiv ID Get complete paper information (including DBLP BibTeX）

    process:
    1. standardization arXiv ID
    2. call arXiv API Get basic information (title, authors, abstract, yearwait)
    3. use title + authors from DBLP get better BibTeX(overwrite if found)

    Args:
        arxiv_id: arXiv ID(like "2502.05383" or "arXiv:2502.05383"）

    Returns:
        Paper information dictionary, including the following fields:
        - title: Paper title
        - authors: Author string (comma separated)
        - abstract: summary
        - year: year of publication
        - arxiv_id: arXiv ID
        - bibtex: BibTeX Quote (priority DBLP, use after failure arXiv）
        - published_date: release date
        - pdf_url: PDF Download link
        - primary_category: Main categories
        If failed return None
    """
    # Get it quickly first arXiv information
    result = fetch_paper_by_arxiv_id_fast(arxiv_id)
    if not result:
        return None

    # then get DBLP BibTeX
    bibtex = fetch_bibtex_from_dblp(
        title=result["title"], authors=result["authors"], arxiv_id=result["arxiv_id"]
    )
    if bibtex:
        result["bibtex"] = bibtex

    return result



def search_arxiv_by_title_and_author_fast(title: str, author: str) -> Optional[Dict[str, Any]]:
    """Retrieve several candidates and require an unambiguous identity match."""
    from ipaper.metadata.model import normalized
    try:
        records = _import_arxiv_records({'search_query': 'ti:"'+title.replace('"',' ')+'"', 'max_results': 5})
        known = normalized(author)
        matches = []
        for record in records:
            fields = record['fields']
            names = [normalized(a.get('name','')) for a in fields.get('author_list',[])]
            if normalized(fields.get('title','')) == normalized(title) and names and sum(bool(n and n in known) for n in names) >= min(2,len(names)):
                matches.append(record)
        return _import_arxiv_value(matches[0]) if len(matches) == 1 else None
    except Exception:
        return None


def search_arxiv_by_title_only_fast(title: str) -> Optional[Dict[str, Any]]:
    """
    Quick version: search using title only arXiv Thesis (no waiting DBLP）

    Args:
        title: Paper title

    Returns:
        Dissertation Information Dictionary,bibtex Field is empty
    """
    try:
        print(f"[Way2.4 Fast] Search using titles arXiv: {title[:50]}...")

        # use arxiv library search
        client = _make_arxiv_client()
        search = arxiv.Search(
            query=f'ti:"{title}"', max_results=1, sort_by=arxiv.SortCriterion.Relevance
        )

        paper = next(client.results(search), None)
        if not paper:
            print(f"[Way2.4 Fast] No matching paper found")
            return None

        # extract arXiv ID
        arxiv_id = paper.entry_id.split("/")[-1]
        arxiv_id = _normalize_arxiv_id(arxiv_id)

        # Get author information
        authors_list = [a.name for a in paper.authors]
        authors_str = ", ".join(authors_list)

        result = {
            "title": paper.title,
            "authors": authors_str,
            "abstract": paper.summary.replace("\n", " ").strip(),
            "summary": paper.summary,
            "year": str(paper.published.year) if paper.published else None,
            "arxiv_id": arxiv_id,
            "bibtex": "",  # Temporarily empty, obtained in the background DBLP post-fill
            "published_date": paper.published.isoformat() if paper.published else None,
            "pdf_url": paper.pdf_url,
            "primary_category": paper.primary_category,
            "categories": paper.categories,
        }

        print(f"[Way2.4 Fast] ✅ Find matching papers: {result['title'][:50]}...")
        return result

    except Exception as exc:
        print(f"[Way2.4 Fast] ❌ Search failed: {exc}")
        import traceback

        traceback.print_exc()
        return None


def search_arxiv_by_title_and_author(
    title: str, author: str
) -> Optional[Dict[str, Any]]:
    """
    Search using title and author arXiv Papers (including DBLP BibTeX）

    use arXiv Query syntax: ti:"title" AND au:"author"

    Args:
        title: Paper title
        author: Author name (can be the first author)

    Returns:
        Paper information dictionary (the format is the same as fetch_paper_by_arxiv_id), return on failure None
    """
    # Get it quickly first arXiv information
    result = search_arxiv_by_title_and_author_fast(title, author)
    if not result:
        return None

    # then get DBLP BibTeX
    bibtex = fetch_bibtex_from_dblp(
        title=result["title"], authors=result["authors"], arxiv_id=result["arxiv_id"]
    )
    if bibtex:
        result["bibtex"] = bibtex

    return result


def search_arxiv_by_title_only(title: str) -> Optional[Dict[str, Any]]:
    """
    Search using title only arXiv Papers (including DBLP BibTeX）

    Args:
        title: Paper title

    Returns:
        Paper information dictionary (the format is the same as fetch_paper_by_arxiv_id), return on failure None
    """
    # Get it quickly first arXiv information
    result = search_arxiv_by_title_only_fast(title)
    if not result:
        return None

    # then get DBLP BibTeX
    bibtex = fetch_bibtex_from_dblp(
        title=result["title"], authors=result["authors"], arxiv_id=result["arxiv_id"]
    )
    if bibtex:
        result["bibtex"] = bibtex

    return result
