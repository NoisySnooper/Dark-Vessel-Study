"""Build the single-file pages of SCS Vessel Watch: the frontend shell plus the real data, open and research builds.

Purpose: turn app/frontend/dist-single/index.html (all JavaScript and CSS inlined, no data) into self-contained pages
that the lead publishes as private artifacts: app/build/out/scs_vessel_watch_open.html and _research.html, each at most
15,000,000 bytes, with no network request from the page (app/CONTRACT.md section 6).
Method: load the product files with the backend's catalog, loaders and record builders (app/backend/scs_api; the open
build goes through the catalog guard, so nothing under data/research/ is read); encode one part per object type in the
contract 6.2 encodings and the README readings 1 to 12 (columnar typed arrays in base64, narrowest type per column,
identity by reference, records for the subsets the contract names; the board D5.3 object context of contacts and
lights as README reading 13 blocks in the room the other parts leave under the cap); read radar chips remotely in the CNN verifier's
way and cache them (data/cache/chips/); fit the part budgets and the 15,000,000 byte cap with the contract 6.3 drop
rules, every drop listed in meta.dropped; check the page (open: no GFW name or field, no research_only true, EEZ only
as boundary lines; both: no credential prefix, no toolkit holder's name in the data); inject one
<script type="application/json" id="scs-part-NAME"> per part before </body>.
Inputs: the frontend shell; the files of app/CONTRACT.md section 3 through scs_api; data/detections_regional_all.gpkg
  (pixel positions for chips); sentinel-s1-l1c on AWS (anonymous HTTPS, chips only); .env (prefix scan only).
Output: app/build/out/scs_vessel_watch_<build>.html (git-ignored), app/build/out/<build>_report.json (sizes, drops,
  timings, checks) and app/build/out/spotcheck_<build>.json (50 API records for check_bundle.mjs); intermediate parts
  cached under data/cache/bundle/ keyed by the input files' signatures.
Usage: PYTHONPATH=app/backend nice -n 10 python app/build/build_single.py --build open|research|both
         [--frontend app/frontend/dist-single/index.html] [--out app/build/out] [--chips-mb N] [--no-chips]
         [--offline-chips] [--no-cache] [--budget PART=MB ...]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "app" / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import darkvessel  # noqa: E402,F401  (sets PROJ_DATA before rasterio and pyogrio)

from scs_bundle import budget as B  # noqa: E402
from scs_bundle.bundle import Bundle, Options  # noqa: E402


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S", time.gmtime()), msg, flush=True)


def build_one(build: str, args) -> dict:
    t = time.time()
    log(f"{build} build: start")
    overrides = {}
    for item in args.budget or []:
        k, _, v = item.partition("=")
        if k not in B.PART_ORDER or not v:
            raise SystemExit(f"--budget takes PART=MB with PART in {', '.join(B.PART_ORDER)}, not {item!r}")
        overrides[k] = int(round(float(v) * B.MB))
    if overrides:
        log(f"{build} build: budget overrides (not the contract's) {overrides}")
    opt = Options(build=build, frontend=Path(args.frontend), out_dir=Path(args.out), chips_mb=args.chips_mb,
                  no_chips=args.no_chips, fetch_chips=not args.offline_chips, cache=not args.no_cache, log=log,
                  budgets=overrides or None)
    rep = Bundle(opt).run()
    out_dir = Path(args.out)
    spot = rep.pop("spot")
    (out_dir / f"spotcheck_{build}.json").write_text(json.dumps(spot, indent=1, ensure_ascii=False))
    (out_dir / f"{build}_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
    log(f"{build} build: {rep['out']} {rep['bytes']:,} bytes (cap {rep['cap']:,}) in {time.time() - t:.0f} s")
    width = max(len(k) for k in B.PART_ORDER)
    print(f"  {'shell':{width}s} {rep['shell_bytes']:>11,}  budget {B.SHELL_BUDGET:>11,}")
    octx = rep["info"].get("object_context") or {}
    for k in B.PART_ORDER:
        if k in rep["parts"]:
            b = rep["budgets"].get(k, 0)
            c = int((octx.get(k) or {}).get("bytes") or 0)  # object context block: outside the 6.3 budgets
            flag = "" if rep["parts"][k] - c <= b else "  OVER"
            print(f"  {k:{width}s} {rep['parts'][k] - c:>11,}  budget {b:>11,}{flag}"
                  + (f"  + object context {c:,} ({len(octx[k].get('fields') or [])} fields)" if c else ""))
    print(f"  {'total':{width}s} {rep['bytes']:>11,}  cap    {rep['cap']:>11,}")
    for d in rep["dropped"]:
        print(f"  dropped: {d['part']}: {d.get('reason')}" + (f" ({d['trigger']})" if d.get("trigger") else "")
              + (f" columns {d['columns']}" if d.get("columns") else "") + (f" rows {d['rows']:,}" if d.get("rows") else ""))
    info = rep["info"]
    print(f"  contacts {info['contacts']['rows']:,} {info['contacts']['by_status']}; identity {info.get('identity')}")
    print(f"  leads {info['leads']}; chips {{embedded: {info['chips'].get('embedded')}, failed: {info['chips'].get('failed')}}}")
    print(f"  checks {rep['checks']}; timings {rep['timings_s']}")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", choices=["open", "research", "both"], default="both")
    ap.add_argument("--frontend", default=str(REPO / "app" / "frontend" / "dist-single" / "index.html"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--chips-mb", type=float, default=None, help="chip budget in MB (default: the contract budget)")
    ap.add_argument("--no-chips", action="store_true", help="no chips part")
    ap.add_argument("--offline-chips", action="store_true", help="use cached chips only, read nothing from AWS")
    ap.add_argument("--no-cache", action="store_true", help="rebuild every intermediate part (data/cache/bundle/)")
    ap.add_argument("--budget", action="append", metavar="PART=MB",
                    help="override one part budget (for evaluating a contract change; the default is the contract 6.3 table)")
    args = ap.parse_args(argv)
    builds = ["open", "research"] if args.build == "both" else [args.build]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for b in builds:
        build_one(b, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
