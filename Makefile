# Run harness: the pipeline in dependency order. Each step is checkpointed by its script, so a rerun
# skips finished work. Use the conda environment from environment.yml (`conda activate darkvessel`).
#
#   make test          offline tests
#   make regional      AOI, scene search, coverage, 12-day regional detection, density, look probability
#   make context       weather at each radar object, VIIRS night lights (27 nights), Sentinel-2 optical check
#   make camau         Ca Mau detail scene and the CNN verifier scores
#   make demo          self-contained demo page (OUT=path/to/page.html)
#   make all           everything above
#
# Nothing here needs a key. AIS matching waits for an AIS source (docs/OWNER_ACTIONS.md).

PY ?= python
OUT ?= data/outputs/demo/scs_vessel_watch.html
DAYS ?= 12
VIIRS_START ?= 2026-09-05
VIIRS_END ?= 2026-10-01

.PHONY: all test regional context camau demo aoi search coverage detect merge density look weather viirs optical

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

optical: merge
	$(PY) scripts/19_optical_check.py
	$(PY) scripts/19_optical_check.py --gallery

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
