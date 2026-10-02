"""Bibliometric scan of dark-vessel research built from the OpenAlex works snapshot.

The scan reads the public OpenAlex Parquet snapshot on S3 with HTTP range
requests (column projection, no whole-file downloads), keeps works whose title
or abstract matches one of seven themes, and turns the result into tables and
charts.

Modules:
    themes    theme regexes, abstract reconstruction, Southeast Asia and Vietnam flags
    dedupe    duplicate removal (OpenAlex ID, DOI, normalised title) and merge notes
    snapshot  HTTP range client and sparse in-memory Parquet access
    scan      per-file scanner used by scripts/biblio_scan.py
    corpus    corpus build, counting rules and summary tables
    figures   matplotlib charts

Importing this package does not import pyarrow or pandas, so the pure-Python
pieces (themes, dedupe) can be unit tested offline.

"Dark" here means only that a vessel does not broadcast AIS. It does not mean illegal.
"""

__all__ = ["themes", "dedupe", "snapshot", "scan", "corpus", "figures"]
