"""Target-journal shortlist for the two planned dark-vessel papers.

The package builds data/journals.csv (one row per venue) from these inputs:

* data/journals/seed.csv: hand-curated facts, each carrying a provenance tag.
* data/journals/openalex_sources.csv: rows cut from the OpenAlex sources snapshot.
* Optional official lists, matched by ISSN: the SCImago CSV, the Scopus
  "discontinued sources" workbook and the Retraction Watch Hijacked Journal Checker.

Modules:
    issn      ISSN and title normalisation
    fetch     HTTP helper that turns blocked hosts into clear errors
    xlsx      minimal reader for .xlsx workbooks (standard library only)
    scimago   parser for the SCImago journal rank CSV
    lists     ISSN matching and screening against discontinued and hijacked lists
    openalex  OpenAlex sources snapshot access and the venue cache
    build     merge of all inputs into the output rows, status rules, docs table

Importing this package does not import pandas, pyarrow or requests, so the
matching and screening logic can be unit tested offline.

"Dark" in this project means only that no AIS position was matched to a radar
detection. It does not mean illegal.
"""

__all__ = ["issn", "fetch", "xlsx", "scimago", "lists", "openalex", "build"]
