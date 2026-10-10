"""Live-pass pipeline: detect, verify, match to recorded AIS and identify, one Sentinel-1 pass at a time.

For every Sentinel-1C/1D IW scene over the South China Sea AOI that lands on the AWS mirror while live AIS
(aisstream.io, scripts/26_ais_record.py) is being recorded:
  scene.py     detection with the regional settings, persistence, clutter and near-fixed rules, CNN verifier scores,
               imaging geometry per object from the annotation (azimuth time, slant range, track and range directions),
               the detector's tested sea as a polygon (for the AIS-only on_tested_sea test)
  matching.py  AIS window around the scene time, gear-beacon filter, AIS status (matched, unmatched, no_coverage)
               with evidence, identity from aisstream static messages
  assign.py    pairing in dense traffic: contact azimuth time, SAR azimuth-shift correction, speed-aware gate,
               pairing rules (length, fixed contacts, oversized returns), minimum-cost assignment with a cost for
               unpaired AIS vessels, ambiguity test
  weather.py   GFS 10 m wind and Himawari-9 cloud tops per contact (sidecar per pass, for the lead gate)
  rules.py     the status rules and their constants (one place, quoted in the about layer)
  identity.py  identity fields from static messages, flag from the MMSI's MID (mid.py, ITU table)
  outputs.py   GeoPackages per pass and combined, live_summary.json, the about layer
  watch.py     mirror polling, AIS coverage check, checkpoints, pass naming, the --once / --watch loop, --rematch
  review.py    hand-check evidence tables, the reviewer's grades (review_note) and radar chips
  figures.py   pass map, chip panels of the matches, AIS-only and match-quality panels

"Dark" means only that no AIS position was matched to a radar contact. It never means illegal
(darkvessel.config.DARK_CAVEAT). A contact in a cell where the live feed hears nothing is `no_coverage`, not dark.
"""

from darkvessel.live.schema import AIS_ONLY_COLUMNS, D1_COLUMNS, EXTRA_COLUMNS

__all__ = ["AIS_ONLY_COLUMNS", "D1_COLUMNS", "EXTRA_COLUMNS"]
