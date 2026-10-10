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
# CNN verification of the regional run:
#   make cnn-regional       detached run, classes high, medium, fixed, then low
#   make cnn-regional-build outputs from the checkpoints;  make cnn-regional-status  progress
# Context, leads and model:
#   make ocean              static and daily ocean layers (depth, ports, shipping presence, SST, fronts, ...)
#   make object-context     ocean and weather context at every radar contact and light
#   make expected           expected-activity model and anomalies
#   make leads              leads queue, open build;  make leads-research  research build (needs make gfw)
#   make gfw                Global Fishing Watch pull and September identity, RESEARCH ONLY (CC BY-NC 4.0,
#                           noncommercial, outputs under data/research/); never part of all
# Product (SCS Vessel Watch):
#   make serve              local web app (BUILD=open|research, PORT=8750)
#   make app-build          frontend (app/frontend/dist/) and single-file page (app/frontend/dist-single/); needs Node 22
#
# Keys, read from the git-ignored .env at the repo root: the aisstream recorder, started by
# make ais-watchdog, needs AISSTREAM_API_KEY; ais-status, ais-reach, ais-passes, live and live-watch
# use the recording and need no key. The research build (gfw) needs GFW_API_TOKEN. The rest of the
# open pipeline needs none.

PY ?= python
BUILD ?= open
PORT ?= 8750
OUT ?= data/outputs/demo/scs_vessel_watch.html
DAYS ?= 12
VIIRS_START ?= 2026-09-05
VIIRS_END ?= 2026-10-01

.PHONY: all test regional context camau demo aoi search coverage detect merge density look weather viirs optical \
	ais-watchdog ais-status ais-reach ais-passes live live-watch cnn-regional cnn-regional-build \
	cnn-regional-status ocean gfw leads leads-research expected object-context serve app-build

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

# ---- Leads queue ----
leads:
	$(PY) scripts/33_leads.py --build open

leads-research:
	$(PY) scripts/33_leads.py --build research

# ---- Product: SCS Vessel Watch ----
serve:
	PYTHONPATH=app/backend $(PY) -m scs_api.serve --build $(BUILD) --port $(PORT)

app-build:
	cd app/frontend && npm ci && npm run build && npm run build:single
