# Paper 1 design: does a Sentinel-1A-trained vessel detector transfer to Sentinel-1C and 1D?

Date: 2026-10-02. Status: design and preliminary measurements. Target: IEEE GRSL (letter) or IGARSS 2027 (4-page paper); the owner chooses (`docs/STATUS.md`, next task 3). Venue facts are in `docs/journals.md` and are mostly UNVERIFIED.

> "Dark" does not mean illegal. This paper measures detection, not intent. A radar contact without an AIS match is a lead for review; many lawful vessels carry no AIS.

## 1. Question and hypotheses

Sentinel-1A is the source of almost all published Sentinel-1 vessel labels. Its last IW products in the AWS mirror are dated 29 June 2026, and the ASF documentation gives the same end date (`docs/data_landscape.md`). The ESA statement could not be opened (UNVERIFIED). Operational users now depend on Sentinel-1C and 1D. The gap analysis found no peer-reviewed vessel detection result on 1C or 1D imagery, and no measurement of transfer within the mission (`docs/bibliometrics.md`, Gap analysis).

Question: does a two-stage detector, CA-CFAR followed by a CNN verifier trained on Sentinel-1A/1B expert labels, keep its precision and recall on Sentinel-1C and 1D IW GRD imagery?

- **H1 (transfer).** The precision of CNN-accepted contacts on 1C and on 1D lies within 0.10 of the held-out 1A/1B value, 0.77 [0.75, 0.79] (`docs/ml_verifier.md`).
- **H2 (noise floor).** The annotated noise-equivalent sigma0 (NESZ) of 1C and 1D is lower than that of 1A, and this explains part of the darker chip backgrounds the CNN sees on 1D.
- **H3 (consistency).** On sea imaged by both satellites, 1C and 1D give the same candidate density and channel mix.

A null result for H1 is a useful result: it licenses reuse of every 1A label set on the new satellites.

## 2. Preliminary results (this project, 2 October 2026)

| Item | Result | Source |
|---|---|---|
| Held-out 1A/1B, CFAR alone (50 m rule) | precision 0.090 [0.086, 0.095], recall 0.797 [0.775, 0.817] | `data/ml/metrics.json` |
| Held-out 1A/1B, CFAR + CNN | precision 0.770 [0.748, 0.791], recall 0.750 [0.727, 0.772] | same |
| Ca Mau, Sentinel-1D, 29 Sep 2026 | CNN accepts 149 of 720 baseline candidates; 78 of 285 both-channel candidates (27 %) against about 77 % of both-channel candidates on 1A/1B test scenes; nothing under 25 m | `data/ml/apply_summary.json`, `docs/ml_verifier.md` |
| Chip background on 1D | 3.6 dB (VV) and 3.7 dB (VH) darker than the training clutter | `docs/ml_verifier.md` |
| Annotated NESZ, 1C minus 1A (2022) | VV -1.45 dB (range over incidence bins -1.65 to -1.23), VH -1.87 dB (-2.24 to -1.56) | `data/nesz_by_satellite.csv`, `docs/figures/nesz_by_satellite.png` |
| Annotated NESZ, 1D minus 1A (2022) | VV -1.37 dB (-1.86 to -0.98), VH -1.74 dB (-2.04 to -1.28) | same |
| Annotated NESZ, 1D minus 1C | VV +0.09 dB, VH +0.13 dB | same |
| Candidate density on 373 shared 0.25 degree cells (12 days) | 1C 38.8, 1D 41.1 per 1,000 km2 per look; both-channel share 38.4 % and 38.8 %; median per-cell ratio 1.02 | `docs/scs_regional.md` |
| CNN acceptance on the same cells | both-channel 1C 0.601 [0.587, 0.616], 1D 0.629 [0.616, 0.641]; one-channel 0.101 and 0.106 | `data/ml/shared_cells_cnn.json` |
| Chip background on the same cells, 1D minus 1C (paired by cell) | VV +1.21 dB, VH +0.34 dB (369 cells) | same |

The NESZ values come from the products' own noise and calibration annotation (20 scenes per satellite; 1A from 2022 in Southeast Asia, 1C and 1D from the South China Sea, July to October 2026). Per-scene medians do not overlap between 1A and the new satellites. The annotation reflects both the instrument and the processor version (2022 against 2026 processing), so the letter must say which of the two it measures. The NESZ difference (about 1.8 dB in VH) is about half of the observed 1D chip-background offset; sea state and region are candidates for the rest. On the same sea, 1C and 1D behave alike under the CNN (acceptance within 3 points), as their equal noise floors predict; their VV backgrounds differ by 1.2 dB, which points at wind on different dates. Both accept fewer both-channel candidates (about 61 %) than the held-out 1A/1B scenes (about 77 %), a gap that region and traffic could also explain.

## 3. Measurements

| Id | Measurement | Truth needed | Estimator | Status |
|---|---|---|---|---|
| M1 | Share of vessels, structures and clutter per heuristic class, per satellite | Owner labels on a random sample | Class share with Wilson 95 % interval; candidate share weighted by class size | Sample in the demo page; labels pending |
| M2 | CNN precision on 1D (Ca Mau) and 1C | Owner labels on every CNN-accepted contact (census) | Share of vessels among accepted contacts, Wilson interval | Census of 149 contacts queued; labels pending |
| M3 | CNN recall on 1D relative to the baseline: the share of labelled vessels among baseline candidates that the CNN keeps | Owner labels on the class sample | Horvitz-Thompson ratio with bootstrap interval | Ready (`scripts/12_score_labels.py`) |
| M4 | Recall of the full detector on 1C and 1D, by length | AIS positions at the radar time, or exhaustive labels of small windows | Matched or found share, Wilson interval, by AIS length bin | Blocked: no AIS source; matcher built (`src/darkvessel/ais/match.py`) |
| M5 | NESZ by satellite and incidence angle | None (annotation) | Median per 1 degree bin | Done |
| M6 | Chip-background offset explained by NESZ and wind | Wind speed at the radar time | Regression of background on NESZ and wind, per sub-swath | Wind source to be chosen (ERA5 or SAR wind retrieval; UNVERIFIED access) |
| M7 | 1C against 1D on shared sea | None | Density ratio per shared cell; CNN acceptance | Done on the 12-day run (373 cells) |

Random contact labels measure precision only. Recall needs the vessels that were missed, so M4 needs AIS (large vessels) or exhaustive labels of whole windows (all vessels).

## 4. How many labels

Interval half-width for a share near 0.5 (Wilson, 95 %): about 0.10 with 96 labels, 0.07 with 196, 0.05 with 384 (n = 1.96^2 x 0.25 / h^2).

To detect a drop in CNN precision from 0.77 (1A/1B) to 0.67 with 80 % power at a two-sided 5 % level, about 149 labelled CNN-accepted contacts are needed (one-sample test against 0.77; n = [1.96 sqrt(0.77 x 0.23) + 0.84 sqrt(0.67 x 0.33)]^2 / 0.10^2). A drop to 0.62 needs about 68. The Ca Mau census of 149 accepted contacts meets the first case exactly, so every accepted contact should be labelled.

Owner labeling budget at about 30 s per contact:
- week 1 (next task 2, about 3 h): the Ca Mau queue of 384 contacts, which holds all 149 CNN-accepted contacts (M2) and 273 from the class sample (M1, M3; 38 contacts are in both);
- a later week (about 1 h): about 100 from the regional sample, both satellites (M1).

A second labeler on 50 contacts would give inter-rater agreement; optional.

## 5. Confounds and controls

- **Region and traffic.** The 1A reference comes from 2020 to 2022 and partly from outside the study area. Closest control: AI2 1A scenes inside the South China Sea AOI (10,364 labels fall inside it; `docs/data_landscape.md`).
- **Sea state.** Wind changes both clutter and the visibility of small boats. Stratify by wind speed once a wind source is in place (M6).
- **Incidence angle.** NESZ varies by about 6 to 7 dB across the swath and has sub-swath steps (figure in section 2). Report per sub-swath.
- **Processor version.** 2022 and 2026 products were made by different processor versions. The NESZ difference may be partly a change in the noise annotation, not only the instrument.
- **Time of day.** Ascending passes are near 18:00 local time, descending near 06:00 (`src/darkvessel/s1/aws.py`); fishing activity differs.
- **1C calibration change.** A new 1C radiometric calibration was reportedly deployed on 3 February 2026 (UNVERIFIED search snippet; gap analysis). All 1C scenes used here are later.

## 6. Planned figures and tables

1. Study area and 90-day coverage by satellite (exists: `docs/figures/coverage.png`).
2. NESZ by incidence angle for 1A, 1C and 1D (exists: `docs/figures/nesz_by_satellite.png`).
3. CNN score distributions: 1A/1B held-out against 1D and 1C, with chip-background offsets (to make).
4. Table: per-class shares by satellite from owner labels (M1).
5. Table: CNN precision and recall, 1A/1B held-out against 1D and 1C (M2, M3).

## 7. Timeline to early January 2027

| When | Owner (at most 5 h a week) | Assistant |
|---|---|---|
| October, weeks 1 and 2 | Unblock network and keys (1 h); label the queues (3 h) | Score labels; extend regional run; queue 1C contacts for labeling |
| October, weeks 3 and 4 | Choose venue and AIS source (1 h) | Wind source, M6; GFW AIS pull if allowed, M4 for large vessels |
| November | Label 1C queue (3 h) | Full analysis, figures 3 to 5, first draft |
| December | Review two drafts (2 x 4 h) | Revisions, reproducibility package |
| Early January 2027 | Submit | IGARSS 2027 deadline not yet published; 2026 was 10 January (UNVERIFIED) |

## 8. Reproducibility

Everything above runs from the repository: `scripts/04` to `06` (ML), `09` to `11` (regional run, density, clutter check), `12_score_labels.py` (labels), `13_nesz_compare.py` (noise floor). Imagery is read from the AWS Open Data mirror of Sentinel-1; labels are the AI2 Skylight set (Apache-2.0).
