# Run harness: the pipeline in dependency order. Each step is checkpointed by its script, so a rerun
# skips finished work. Use the conda environment from environment.yml (`conda activate darkvessel`).
#
#   make test               offline tests
#   make regional           AOI, scene search, coverage, 12-day regional detection, density, look probability
#   make context            weather at each radar object, VIIRS night lights (27 nights), Sentinel-2 optical check
#   make camau              Ca Mau detail scene and the CNN verifier scores
#   make demo               self-contained demo page (OUT=path/to/page.html)
#   make all                everything above
#
# Live AIS and live passes (open build):
#   make ais-watchdog       start the watchdog (keeps the aisstream recorder, live watcher and CNN run alive)
#   make ais-status         watchdog and recorder status
#   make ais-reach          AIS reach rasters and tables from the recording
#   make ais-passes         refresh the Sentinel-1 pass plan from ESA's acquisition plan
#   make live               one live-pass cycle: new scenes, detection, CNN, AIS match, identity
#   make live-watch         live-pass watcher in the background (log: data/cache/live/watch.log)
#   make live-review RUN=live_S1D_20261010T1032    hand-check tables data/live/<RUN>_review_{matched,unmatched}.csv
#   make live-figures RUN=live_S1D_20261010T1032   map, match chips and AIS-only panels of a processed pass
# CNN verification of the regional run:
#   make cnn-regional       detached run, classes high, medium, fixed, then low
#   make cnn-regional-build outputs from the checkpoints;  make cnn-regional-status  progress
# Context, leads and model:
#   make ocean              static and daily ocean layers (depth, ports, shipping presence, SST, fronts, ...)
#   make object-context     ocean and weather context at every radar contact and light
#   make expected           expected-activity model and anomalies
#   make length-cal         radar length calibration (open from live AIS pairs; research from GFW registry lengths)
#   make leads              leads queue, open and research builds (research reads the committed data/research/ files)
#   make leads-open, make leads-research   one build only
#   make gfw                Global Fishing Watch pull and September identity, RESEARCH ONLY (CC BY-NC 4.0,
#                           noncommercial, outputs under data/research/); never part of all
# Product (SCS Vessel Watch):
#   make serve              local web app (BUILD=open|research, PORT=8750)
#   make app-build          frontend (app/frontend/dist/) and single-file shell (app/frontend/dist-single/); needs Node 22
#   make app-single         both shareable single-file pages in app/build/out/ (open and research, at most 15,000,000
#                           bytes each; FRONTEND=path/to/shell.html to build from a frozen shell copy)
#   make app-check          bundle check (Playwright, 1280 and 390 px, dark and light) and the frontend smoke check
#                           on both pages; Node 22 and the preinstalled Chromium in /opt/pw-browsers
#
# Keys, read from the git-ignored .env at the repo root: the aisstream recorder, started by
# make ais-watchdog, needs AISSTREAM_API_KEY; ais-status, ais-reach, ais-passes, live, live-watch,
# live-review and live-figures use the recording and need no key. The GFW pull (gfw) needs
# GFW_API_TOKEN. The research builds of leads, length-cal, serve and app-single read the GFW pull's
# committed outputs under data/research/ and need no key; their open builds never read data/research/.
# app-single reads .env only to scan each page for credential prefixes (never printed or written).
# The rest of the open pipeline needs no key.

PY ?= python
BUILD ?= open
PORT ?= 8750
RUN ?= live_S1D_20261010T1032
FRONTEND ?= app/frontend/dist-single/index.html
PW_ENV ?= NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
OUT ?= data/outputs/demo/scs_vessel_watch.html
DAYS ?= 12
VIIRS_START ?= 2026-09-05
VIIRS_END ?= 2026-10-01

.PHONY: all test regional context camau demo aoi search coverage detect merge density look weather viirs optical \
	ais-watchdog ais-status ais-reach ais-passes live live-watch live-review live-figures cnn-regional \
	cnn-regional-build cnn-regional-status ocean gfw leads leads-open leads-research length-cal expected \
	object-context serve app-build app-single app-check

all: regional context camau demo

test:
	$(PY) -m pytest -q

aoi:
	$(PY) scripts/01_make_aoi.py

search: aoi
	$(PY) scripts/02_search_scenes.py

coverage: search
	$(PY) scripts/08_coverage.py

detect: coverage
	$(PY) scripts/09_run_regional.py --days $(DAYS)

merge: detect
	$(PY) scripts/09_run_regional.py --merge

density: merge
	$(PY) scripts/10_regional_density.py

look: coverage
	$(PY) scripts/17_look_probability.py

regional: density look

weather: merge
	$(PY) scripts/16_weather_context.py

viirs: coverage
	$(PY) scripts/15_viirs_lights.py --start $(VIIRS_START) --end $(VIIRS_END)
	$(PY) scripts/15_viirs_lights.py --retry
	$(PY) scripts/15_viirs_lights.py --clear
	$(PY) scripts/15_viirs_lights.py --merge
	$(PY) scripts/21_viirs_regions.py

optical: merge
	$(PY) scripts/19_optical_check.py
	$(PY) scripts/19_optical_check.py --gallery
	$(PY) scripts/20_satlas_check.py

context: weather viirs optical

# CNN weights are not in git (data/models/ is ignored): build the training set from AI2 labels and train once.
data/models/verifier_v0.pt:
	$(PY) scripts/04_build_training_set.py
	$(PY) scripts/05_train_verifier.py

camau: data/models/verifier_v0.pt
	$(PY) scripts/02_search_scenes.py --aoi ca_mau
	$(PY) scripts/03_run_baseline.py
	$(PY) scripts/06_apply_verifier.py

demo:
	$(PY) scripts/07_build_demo_page.py --out $(OUT)

# ---- Live AIS (aisstream.io, needs AISSTREAM_API_KEY) and live passes ----
ais-watchdog:
	$(PY) scripts/29_ais_watchdog.py --ensure

ais-status:
	$(PY) scripts/29_ais_watchdog.py --status
	$(PY) scripts/26_ais_record.py --status

ais-reach:
	$(PY) scripts/28_ais_reach.py

ais-passes:
	$(PY) scripts/28_ais_reach.py --passes-only --fetch-plan

live:
	$(PY) scripts/30_live_pass.py --once

live-watch:
	nohup setsid nice -n 10 $(PY) scripts/30_live_pass.py --watch >> data/cache/live/watch.log 2>&1 &

live-review:
	$(PY) scripts/30_live_pass.py --review $(RUN)

live-figures:
	nice -n 10 $(PY) scripts/30_live_pass.py --figures $(RUN)

# ---- CNN verifier on every regional contact ----
cnn-regional:
	$(PY) scripts/32_cnn_regional.py --detach --phases main,low

cnn-regional-build:
	$(PY) scripts/32_cnn_regional.py --build

cnn-regional-status:
	$(PY) scripts/32_cnn_regional.py --status

# ---- Ocean context, object context, expected activity ----
ocean:
	$(PY) scripts/22_static_layers.py
	$(PY) scripts/23_daily_ocean.py --start $(VIIRS_START) --end $(VIIRS_END)

object-context:
	$(PY) scripts/25_object_context.py

expected:
	$(PY) scripts/34_expected_activity.py

# ---- Research build only: Global Fishing Watch (CC BY-NC 4.0, needs GFW_API_TOKEN). Never in all. ----
gfw:
	$(PY) scripts/27_gfw_pull.py --steps sar,grids,events,vessels --start 2026-09-01 --end 2026-10-08
	$(PY) scripts/27_gfw_pull.py --steps outputs,compare --offline
	$(PY) scripts/31_gfw_identity.py --steps presence,identify,vessels,outputs

# ---- Radar length calibration (no producer reads it yet: board D6.4) ----
length-cal:
	nice -n 10 $(PY) scripts/35_length_calibration.py

# ---- Leads queue ----
leads:
	nice -n 10 $(PY) scripts/33_leads.py --build both

leads-open:
	nice -n 10 $(PY) scripts/33_leads.py --build open

leads-research:
	nice -n 10 $(PY) scripts/33_leads.py --build research

# ---- Product: SCS Vessel Watch ----
serve:
	PYTHONPATH=app/backend $(PY) -m scs_api.serve --build $(BUILD) --port $(PORT)

app-build:
	cd app/frontend && npm ci && npm run build && npm run build:single

# Single-file pages (app/build/README.md): build both, then check both before the lead publishes them.
app-single:
	PYTHONPATH=app/backend nice -n 10 $(PY) app/build/build_single.py --build both --frontend $(FRONTEND)

app-check:
	$(PW_ENV) nice -n 10 node app/build/check_bundle.mjs app/build/out
	$(PW_ENV) nice -n 10 node app/frontend/checks/smoke.mjs $(CURDIR)/app/build/out/scs_vessel_watch_open.html app/build/out/shots/smoke_open
	$(PW_ENV) nice -n 10 node app/frontend/checks/smoke.mjs $(CURDIR)/app/build/out/scs_vessel_watch_research.html app/build/out/shots/smoke_research
