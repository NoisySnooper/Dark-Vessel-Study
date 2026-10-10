"""Single-file bundle builder of SCS Vessel Watch (app/CONTRACT.md section 6): encoders, parts, chips, budget, checks.

Entry point: app/build/build_single.py. The package reads the product files through the backend's catalog, loaders and
record builders (app/backend/scs_api), so a record decoded from the page equals the API record on the D1 fields.
"""

BUILDER_VERSION = "1.2.0"  # 1.1.0 (R3-T12): live identification records, rule 7 keeps matched passes, compact passes, row counts
# 1.2.0 (R3-T12 fix round): object context blocks (README reading 13) in the room under the cap, record-only fields listed
