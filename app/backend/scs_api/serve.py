"""Start the local SCS Vessel Watch backend with uvicorn (the `make serve` entry, board D4.4).

Purpose: serve the product files of app/CONTRACT.md as the section 5 API plus the built frontend, on 127.0.0.1 only.
Method: load every existing product file at start (in parallel), re-read a file when its mtime changes, append lead
decisions and contact labels to their logs.
Inputs: data/ (open build: never data/research/), app/frontend/dist/ when built.
Output: HTTP on --host:--port (default 127.0.0.1:8750); data/labels/lead_decisions.jsonl (open) or
data/research/lead_decisions.jsonl (research); data/labels/contact_labels.csv.
Usage: PYTHONPATH=app/backend python -m scs_api.serve --build open|research --port 8750 --host 127.0.0.1
"""

from __future__ import annotations

import argparse
import os
import time


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", choices=["open", "research"], default=os.environ.get("BUILD", "open"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8750)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default=None, help="data directory (default: the repo's data/)")
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        ap.error("the local app binds to 127.0.0.1 only (stack decision 4.4)")
    t = time.time()
    import uvicorn

    from .app import create_app
    from .config import settings_from_env

    # the research events load and the index warm-up start 1 s after the server listens (Settings.background_delay_s)
    settings = settings_from_env(build=a.build, data_dir=a.data_dir, host=a.host, port=a.port, background_delay_s=1.0)
    app = create_app(settings)
    store = app.state.store
    c = store.counts()  # never waits for the background events load
    bg = store.loading()
    print(f"SCS Vessel Watch backend, {settings.build} build, data {settings.data_dir}; loaded in {store.load_seconds} s "
          f"(start {time.time() - t:.1f} s): {c['contacts']} contacts, {c['vessels']} vessels, {c['lights']} lights, "
          f"{c['leads']} leads, {c['events']} events{' (loading in the background)' if 'events' in bg else ''}, "
          f"{c['passes']} passes, {c['cells']} cells", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level=a.log_level)


if __name__ == "__main__":
    main()
