"""Live-pass pipeline: detect, verify, match to recorded AIS and identify, one Sentinel-1 pass at a time.

For every Sentinel-1C/1D IW scene over the South China Sea AOI that lands on the AWS mirror while live AIS
(aisstream.io, scripts/26_ais_record.py) is being recorded:
  scene.py     detection with the regional settings, persistence, clutter and near-fixed rules, CNN verifier scores
  matching.py  AIS window around the scene time, track interpolation, speed-aware gate, Hungarian assignment,
               AIS status (matched, unmatched, no_coverage) with evidence, identity from aisstream static messages
  rules.py     the status rules and their constants (one place, quoted in the about layer)
  identity.py  identity fields from static messages, flag from the MMSI's MID (mid.py, ITU table)
  outputs.py   GeoPackages per pass and combined, live_summary.json, the about layer
  watch.py     mirror polling, AIS coverage check, checkpoints, pass naming, the --once / --watch loop

"Dark" means only that no AIS position was matched to a radar contact. It never means illegal
(darkvessel.config.DARK_CAVEAT). A contact in a cell where the live feed hears nothing is `no_coverage`, not dark.
"""

from darkvessel.live.schema import AIS_ONLY_COLUMNS, D1_COLUMNS, EXTRA_COLUMNS

__all__ = ["AIS_ONLY_COLUMNS", "D1_COLUMNS", "EXTRA_COLUMNS"]
