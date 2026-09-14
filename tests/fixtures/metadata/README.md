# Metadata identification samples

These 30 synthetic, author-created cases are CC0-1.0. Names, identifiers and
provider-shaped records are test inputs, not claims of real publications or
live provider success. Each case fixes its own header, identity clues and
expected matching decision. No PDF or third-party paper is redistributed.

They exercise DOI evidence vs references, competing same-title records,
full/partial author names, corporate and Chinese authors, arXiv versions,
preprint/publication ambiguity, malformed embedded metadata, scanned/no-text
and unavailable identity. OpenReview public/submission/anonymous protocol
cases are also exercised in test_metadata_foundations.py. Worker admission
uses existing self-authored PDF fixtures in ../workbench (its font licenses
and SHA-256 manifest apply).

Provider schemas: [Crossref](https://api.crossref.org/swagger-ui/index.html),
[arXiv](https://info.arxiv.org/help/api/user-manual.html),
[DBLP](https://dblp.uni-trier.de/faq/13501473.html),
[OpenReview notes](https://docs.openreview.net/getting-started/objects-in-openreview/introduction-to-notes).
Real requests and field checks are recorded separately in private delivery
evidence. Synthetic coverage must never be reported as a live provider pass.
