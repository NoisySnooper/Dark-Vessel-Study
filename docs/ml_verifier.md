# Model card: CA-CFAR + CNN verifier (verifier_v0)

Two-stage vessel detector for Sentinel-1 IW GRD. Stage 1 is the project's CA-CFAR baseline
(`darkvessel.pipeline.run_baseline`), which proposes bright objects. Stage 2 is a small CNN
that looks at a 64 x 64 px (640 m) VV/VH chip around each object and scores it as vessel or
clutter. The CNN was trained on expert point labels from Sentinel-1A/1B scenes of 2020 to
2022 and then applied, without any Sentinel-1D ground truth, to a Sentinel-1D scene of
29 September 2026 off Ca Mau. That application is the first experiment of the planned
letter on detector transfer from Sentinel-1A to Sentinel-1C/1D.

Everything below was produced by `scripts/04_build_training_set.py`, `scripts/05_train_verifier.py`
and `scripts/06_apply_verifier.py` on 2 October 2026 (CPU only). Numbers come from
`data/ml/build_summary.json`, `data/ml/metrics.json` and `data/ml/apply_summary.json`.

## Data and licences

| Source | Use | Licence and attribution |
|---|---|---|
| AI2 Skylight Sentinel-1 vessel labels, `allenai/vessel-detection-sentinels` `data/metadata.sqlite3` | point labels (dataset 1) and vessel attributes (dataset 2) | Apache-2.0 (LICENSE in that repository) |
| Sentinel-1A/1B IW GRD, 2020 to 2022, AWS Open Data mirror `sentinel-s1-l1c` | training and evaluation imagery, read as windows over HTTPS | Contains modified Copernicus Sentinel data 2020-2022 |
| Sentinel-1D IW GRD `S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA` | application scene | Contains modified Copernicus Sentinel data 2026 |
| ESA WorldCover 2021 v200 | sea mask | CC BY 4.0 |

The AI2 label set holds 61,655 point labels in 39,954 windows on 2,913 Sentinel-1 scenes
(2,832 S1A, 81 S1B; 14 scenes from 2020, 285 from 2021, 2,614 from 2022). 20,678 of the
windows carry no label. Windows are 1024 x 1024 px on a Web Mercator tiling (zoom 13, tile
512, about 9.55 m per pixel at the equator) in most cases; some are 846 to 1583 px.
Vessel attributes (AIS length, width, heading, speed, type) exist for 17,582 windows of
dataset 2. Their 128 x 128 px windows are centred exactly (0 px) on a dataset-1 point of the
same scene, so attributes were joined on that centre. 17,582 attribute windows fall on 16,494
distinct positions; at 843 positions duplicates disagree on length, and those were blanked
(`attr_conflict`). 15,654 point labels end up with a usable length.

Decoding rule (`darkvessel.ml.labels`): pixel column c and row r on the tiling map to Web
Mercator metres x = c S - H, y = H - r S with H = 20037508.342789244 m and S = 2H / (2^13 x 512),
then to lon/lat on the WGS 84 sphere of radius 6378137 m. This reproduces the lon/lat bounds
stored with every AI2 image to better than 1e-4 degree (checked on all 4,315 S1 images).

## Scene and window selection

Selection is deterministic (seed 20261002) and recorded in `data/ml/scene_sample.csv` and
`data/ml/window_sample.parquet`.

- Every AI2 scene whose bounds intersect Southeast Asia, 95E to 125E and 10S to 25N: 334 scenes.
- Plus 120 scenes from the rest of the world, stratified by 30 x 30 degree cell of the scene
  centre with allocation proportional to each cell's labelled windows (at least one per cell),
  chosen among scenes with at least one labelled window.
- Per scene at most 16 windows with labels and 3 without, chosen at random.
- Held-out test set: 20 % of the selected scenes in each region, held out whole (every window
  of those scenes). On the remaining scenes, AI2 `-val` windows form the validation set used
  for early stopping and the operating threshold, and `-train` windows the training set. No
  AI2 validation window is ever trained on.

Result of the selection: 454 scenes (447 S1A, 7 S1B; 4 from 2020, 66 from 2021, 384 from
2022), 41 strata outside Southeast Asia, 3,188 windows (2,544 with labels, 644 without) holding
10,633 point labels. Windows per AI2 campaign: jun-july-aug-2022 1,044, jan-march-may-2022 966,
apr-2022 946, nov-2021 172, jun-2020 60.

| Region | Scenes train+val / test | Windows train / val / test | Labels train / val / test |
|---|---|---|---|
| Southeast Asia | 267 / 67 | 1,566 / 188 / 392 | 6,279 / 669 / 1,439 |
| Rest of world | 96 / 24 | 724 / 94 / 224 | 1,491 / 236 / 519 |

## Candidate generation (identical to the baseline)

For each selected window: read sigma0 VV and VH without thermal-noise subtraction over the
window plus an 81 px margin; WorldCover sea mask on an 8 x decimated grid with a 1 km shore
buffer; CA-CFAR with PFA 1e-6, guard 81 px, background 161 px, ENL estimated from the window
and clipped to 1 to 10 (default 4.4 when it cannot be estimated); objects of at least 2 pixels;
VV and VH objects fused within 3 px. The heuristic confidence classes of the baseline
(`assign_confidence`) are recorded for every candidate so the two stages can be compared.

Two details differ from the full-scene baseline and are documented in `darkvessel.ml.chips`:

1. Sea mask connectivity. The baseline calls water "sea" only when it connects to a WorldCover
   code-0 (unmapped ocean) pixel inside the read box. A 10 km window often has none; near
   Singapore a 0.7 degree box is coded 80 (permanent water) throughout and the baseline rule
   would mask every pixel. Here water counts as sea if its connected component holds code 0 or
   touches the edge of a box padded by 0.35 degree around the scene's windows.
2. ENL is estimated per window (about 1.2 Mpx) rather than from a 2048 px crop at the centre
   of a 150 km window.

Geolocation. AI2 warped each product with plain `gdalwarp -t_srs epsg:3857` (their
`src/data/warp.py`), which uses GDAL's default polynomial fit to the 210 ground control
points in the TIFF, not the ESA annotation grid. Candidates and labels are therefore both
placed with the same GDAL transformer. On one scene with 119 matched vessels the median
candidate-to-label distance was 2.8 px with the GDAL transform against 3.7 px with the
annotation grid, and a 2 px column bias disappeared. A Web Mercator window is a rotated
quadrilateral in radar geometry, so candidates are kept only when their lon/lat falls inside
the window's box; the corner triangles of the pixel bounding box are outside the labelled
area, and before this filter unlabelled ships there inflated the clutter class.

Labelling rule. A candidate within 50 m of an AI2 point (any point of the scene) is a vessel.
A candidate between 50 m and 150 m, or within 0.75 times a known AIS length, is "ambiguous":
such objects are fragments of the same ship, or clicks placed on the bow or the bridge, and
are excluded from training and from precision, and reported separately. Everything else is
clutter. A label counts as detected by CFAR when some fused object in the read window lies
within 50 m of it (primary rule); the loose variant uses the ambiguous radius. Labels inside
the 1 km shore buffer or outside the swath are excluded from every recall figure and counted.

### Candidate set

Built in 79 minutes with 3 worker processes (06:29 to 07:48 on 2 October 2026, reads over
HTTPS from the AWS mirror; no scene failed). 2,853 of the 3,188 windows produced candidates
(median 12 per window, 90th percentile 53).

| | Train | Val | Test (held-out scenes) | All |
|---|---|---|---|---|
| Candidates | 49,355 | 5,863 | 14,056 | 69,274 |
| of which vessel (within 50 m of a label) | 4,947 | 596 | 1,213 | 6,756 |
| of which ambiguous (50 to 150 m or 0.75 x length) | 2,587 | 314 | 617 | 3,518 |
| of which clutter | 41,821 | 4,953 | 12,226 | 59,000 |
| Labels in the windows | 7,762 | 905 | 1,958 | 10,625 |
| Labels on testable sea (evaluable) | 5,842 | 706 | 1,422 | 7,970 |
| CFAR label recall, strict 50 m | 0.782 | 0.785 | 0.798 | 0.785 |
| CFAR label recall, loose | 0.971 | 0.975 | 0.974 | 0.972 |

2,655 labels (25 %) sit inside the 1 km shore buffer or on masked water and are excluded
from every recall figure. Among the 69,274 candidates the baseline's heuristic classes split
as: high (VV and VH) 4,462 vessel / 1,175 ambiguous / 3,157 clutter; medium 1,360 / 1,080 /
7,546; low 934 / 1,263 / 48,297.

Why the two rules differ by 19 points: the distance from a CFAR centroid to the expert click
grows with ship length. For labelled vessels with an AIS length the median nearest-candidate
distance is 28 m under 50 m length, 29 m at 50 to 100 m, 31 m at 100 to 200 m and 36 m above
200 m, with 90th percentiles of 67, 67, 64 and 82 m; the share within 50 m is 0.83, 0.81,
0.80 and 0.66. Per-scene median offsets are small (13.7 m median over 218 scenes with at
least 8 matches), so this is per-label scatter, not a geolocation bias. The strict rule is
kept because the brief asked for 50 m; the loose rule is the one to read for long ships.

Label completeness: bright objects detected in both polarisations with at least 15 pixels
and no label within 150 m number 391 against 1,516 labelled vessels of the same kind in the
apr-2022 campaign, 378 against 1,236 in jan-march-may-2022, 773 against 1,452 in
jun-july-aug-2022, 104 against 140 in nov-2021 and 17 against 86 in jun-2020. A visual check
of 32 of them from the 2022 campaigns showed ships and fixed structures (cross-shaped
sidelobe patterns of turbines or platforms, arrays of farm structures) in similar numbers.
They are kept as clutter for training and for the primary metrics, and a sensitivity figure
excludes them.

## Model and training

`darkvessel.ml.model.VerifierCNN`: 2 input channels (VV, VH in dB, standardised with the
training-set channel mean and standard deviation, missing pixels set to 2 standard deviations
below the mean), four blocks of two 3 x 3 convolutions with BatchNorm and ReLU followed by
2 x 2 max pooling (64 to 4 px), global average pooling, dropout 0.3, one linear unit.
Trained from scratch (no pretrained weights are reachable from this environment).
Augmentation: the 8 flips and 90 degree rotations, shifts of up to 2 px, per-channel offsets
of up to 1 dB and a gain of up to 0.5 dB. Class imbalance: a weighted sampler draws one third
positives per batch; training negatives are capped at 5 per positive. AdamW, one-cycle
learning rate (peak 1e-3), batch 128, early stopping on validation average precision.
Inference averages the 8 dihedral views. The operating threshold maximises F1 on the
validation windows with CFAR misses counted as false negatives.

Training run (width 16, 293,985 parameters): 29,682 training chips (4,947 vessels, 24,735
clutter drawn from 41,821), 5,549 validation chips (596 vessels), 16,000 balanced draws per
epoch, 3 CPU threads, about 90 s per epoch. Early stopping ended the run after epoch 23;
the best validation average precision, 0.905, was reached at epoch 17 (`data/ml/training_log.csv`,
`docs/figures/ml_training_curves.png`). Training took 40 minutes. The operating threshold
is 0.632 (validation: candidate precision 0.79, label recall 0.73 with CFAR misses counted).
Normalisation constants: VV mean -19.6 dB, SD 5.3 dB; VH mean -25.7 dB, SD 3.5 dB.
Weights: `data/models/verifier_v0.pt` (gitignored), git commit fb8a641 of the working tree at
training time.

## Held-out evaluation

Held-out set: 91 scenes never seen in training (67 Southeast Asia, 24 elsewhere), 616 windows,
14,056 CFAR candidates (1,213 vessel, 617 ambiguous, 12,226 clutter), 1,958 labels of which
1,422 are evaluable (on testable sea); 536 sit inside the shore buffer. Wilson 95 % intervals
in brackets. Candidate-level average precision of the CNN score: 0.893.

| Rule | Stage | Accepted | Precision | Recall | F1 |
|---|---|---|---|---|---|
| strict (50 m) | CFAR only | 14,056 | 0.090 [0.086, 0.095] | 0.797 [0.775, 0.817] | 0.162 |
| strict (50 m) | CFAR + CNN at 0.632 | 1,898 | 0.770 [0.748, 0.791] | 0.750 [0.727, 0.772] | 0.760 |
| loose (150 m or 0.75 x length) | CFAR only | 14,056 | 0.130 | 0.966 | 0.229 |
| loose (150 m or 0.75 x length) | CFAR + CNN at 0.632 | 1,898 | 0.820 | 0.918 | 0.866 |

The CNN removes 11,884 of 12,226 clutter candidates (97.2 %) and keeps 1,145 of 1,213
labelled vessels (94.4 %); the recall drop from 0.797 to 0.750 is 66 labels. Precision is
bounded by label completeness: excluding the 334 bright two-polarisation test candidates that
no label claims, CFAR + CNN precision is 0.896 strict and 0.921 loose (CFAR only 0.093 and
0.133). Figure: `docs/figures/ml_pr_curve.png`.

By region (strict precision / recall, then loose): Southeast Asia 999 labels, CFAR only
0.086 / 0.792 and 0.127 / 0.971, CFAR + CNN 0.755 / 0.745 and 0.811 / 0.923; elsewhere 423
labels, CFAR only 0.102 / 0.809 and 0.139 / 0.955, CFAR + CNN 0.806 / 0.764 and 0.842 / 0.905.
By campaign (strict, CFAR + CNN): apr-2022 0.877 / 0.731, jan-march-may-2022 0.674 / 0.695,
jun-july-aug-2022 0.741 / 0.813, nov-2021 0.810 / 0.754.

How the CNN treats the baseline's heuristic classes on the test candidates (accepted /
rejected at the threshold):

| Baseline class | Vessel | Ambiguous | Clutter |
|---|---|---|---|
| high (VV and VH) | 809 / 27 | 173 / 19 | 246 / 323 |
| medium | 182 / 28 | 119 / 74 | 66 / 1,077 |
| low | 154 / 13 | 119 / 113 | 30 / 10,484 |

So the CNN keeps 92 % of the labelled vessels that the heuristic had filed as "low" and
rejects 99.7 % of the low-class clutter; in the high class it rejects 323 of 569 unlabelled
bright objects, which is where unlabelled ships and fixed structures sit.

## Recall by AIS length (first look at the flagship question)

Held-out labels with a consistent AIS length: 406 (`data/ml/recall_by_length_test.csv`,
`docs/figures/ml_recall_by_length.png`). Wilson 95 % intervals in brackets. The strict rule
counts a vessel as detected only when a CFAR object lies within 50 m of the expert click; the
loose rule allows 150 m or 0.75 times the AIS length. The bins below 25 m are empty or near
empty because AI2 attributes come from AIS matches, and few small boats carry AIS: this sample
cannot say anything about recall for boats under 25 m, which is the population the flagship
question is about. That gap needs a different ground truth (AIS from the project's own feed
over the Ca Mau scenes, or optical coincidences).

| AIS length | n | CFAR, strict 50 m | CFAR + CNN, strict | CFAR, loose | CFAR + CNN, loose |
|---|---|---|---|---|---|
| 0-15 m | 0 | no data | no data | no data | no data |
| 15-25 m | 2 | 2/2 = 1.00 [0.34, 1.00] | 1/2 = 0.50 [0.10, 0.90] | 2/2 = 1.00 [0.34, 1.00] | 2/2 = 1.00 [0.34, 1.00] |
| 25-50 m | 16 | 15/16 = 0.94 [0.72, 0.99] | 15/16 = 0.94 [0.72, 0.99] | 16/16 = 1.00 [0.81, 1.00] | 16/16 = 1.00 [0.81, 1.00] |
| 50-100 m | 72 | 63/72 = 0.88 [0.78, 0.93] | 63/72 = 0.88 [0.78, 0.93] | 70/72 = 0.97 [0.90, 0.99] | 70/72 = 0.97 [0.90, 0.99] |
| 100+ m | 316 | 228/316 = 0.72 [0.67, 0.77] | 225/316 = 0.71 [0.66, 0.76] | 307/316 = 0.97 [0.95, 0.99] | 300/316 = 0.95 [0.92, 0.97] |
| all | 406 | 308/406 = 0.76 [0.71, 0.80] | 304/406 = 0.75 [0.70, 0.79] | 395/406 = 0.97 [0.95, 0.98] | 388/406 = 0.96 [0.93, 0.97] |

Reading: under the loose rule CA-CFAR at PFA 1e-6 finds 97 % of AIS-carrying vessels of
25 m and above on open sea, and the CNN costs at most 2 points of recall. The strict column's
fall from 0.94 at 25 to 50 m to 0.72 above 100 m is the click-offset effect described above
(a 250 m ship's intensity centroid is often more than 50 m from where the annotator clicked),
not a loss of detections; the loose column is the one to quote for long ships. CFAR misses
under the loose rule are 11 of 406, consistent with the 2.8 % loose miss rate on all 7,970
evaluable labels.

## Application to the Sentinel-1D Ca Mau scene (transfer experiment 1)

Scene S1D_IW_GRDH_1SDV_20260929T111023_20260929T111053_004792_008FC8_A5CA, Sentinel-1D,
ascending, 29 September 2026 11:10 UTC, processed by the baseline over 104.7E to 105.9E,
8.0N to 9.2N: 6,005 CA-CFAR detections (285 high, 435 medium, 349 fixed, 4,936 low;
720 vessel candidates = high + medium). `scripts/06_apply_verifier.py` cut a 64 px chip at
every detection (8 chips touch the swath edge), scored it with model `verifier_v0_356af0ca`
(sha256 prefix of the weights) at threshold 0.632, and wrote `data/detections_ml.gpkg`
(layers `detections_verified_4326`, `detections_verified_utm48n`; all baseline columns kept,
including `caveat`, plus `cnn_score`, `cnn_vessel`, `cnn_threshold`, `cnn_model_id`,
`cnn_chip_valid_frac`, `cnn_training_data`). Runtime 156 s. `data/detections_baseline.gpkg`
was not modified. Summary: `data/ml/apply_summary.json`; chips by class and verdict:
`docs/figures/ml_1d_chips.png`.

| Baseline class | n | CNN vessel | share | median score | score 10th to 90th pct |
|---|---|---|---|---|---|
| high (VV and VH) | 285 | 78 | 0.27 | 0.24 | 0.005 to 0.93 |
| medium | 435 | 12 | 0.03 | 0.02 | 0.001 to 0.33 |
| low | 4,936 | 2 | 0.000 | 0.001 | 0.000 to 0.004 |
| fixed (persistent) | 349 | 57 | 0.16 | 0.23 | 0.007 to 0.74 |
| all | 6,005 | 149 | 0.025 | | |

By polarisation class: VV and VH 578 detections, 135 accepted; VH only 434, 10 accepted;
VV only 4,993, 4 accepted. The CNN therefore cuts the baseline's 720 vessel candidates to
149 and treats the 4,936 low-class objects as the clutter they look like (two accepted: an
862 px, 780 m long, 52 dB-contrast object and an 18 px target).

What the chips show (`ml_1d_chips.png`): the high-class objects the CNN rejects are mostly
bright point targets with cross-shaped sidelobes standing in lines, the signature of fixed
structures (offshore wind turbines and stake lines are both present along this coast;
UNVERIFIED which ones these are), while the accepted high-class objects are compact or
elongated single targets. Accepted objects have a median VV contrast of 15.1 dB and 19.5
pixels against 10.0 dB and 7 pixels for the rejected ones. The CNN also accepts 57 of the
349 persistent ("fixed") objects, which look like the same turbine-type targets; persistence
should keep precedence over the CNN for those, since a single chip cannot tell an anchored
ship from a platform.

Domain shift measured on this scene: the chip background (median of the four 16 x 16 px
corners) is -24.0 dB in VV and -29.0 dB in VH for 240 random 1D detections, against -20.4 dB
and -25.3 dB for 2,000 random training clutter chips from Sentinel-1A/1B, i.e. 3.6 and
3.7 dB darker, or 0.8 and 0.9 training standard deviations below the training mean. The
training augmentation covered offsets of only 1 dB. Whether this is the calm evening sea
of this scene or a lower noise floor of the 1D instrument cannot be separated from one
scene (UNVERIFIED); the model's low acceptance of medium-class (VH-only) objects on 1D may
be a symptom. No Sentinel-1D ground truth exists, so none of these numbers is an accuracy.

## Transfer caveats

- Trained on Sentinel-1A and 1B acquisitions of 2020 to 2022; applied to Sentinel-1D of 2026.
  No Sentinel-1D ground truth exists in this project yet, so nothing in the application
  section is a measured accuracy. The CNN scores on the 1D scene say how 1D chips look to a
  1A/1B-trained model, not how many vessels are there.
- Calibration and noise differences between the satellites are not corrected beyond the
  standard sigma0 calibration; thermal noise is not subtracted in either stage, so the VH
  background level of a different instrument is a plausible source of shift. On the 1D
  scene the chip backgrounds sit 3.6 dB (VV) and 3.7 dB (VH) below the training median
  (see above); the next step for the letter is to repeat this measurement on several 1A
  and 1D scenes of the same orbit and season, and to retrain with wider intensity jitter.
- The 1D scene is a single ascending pass over one coastal region in the southwest monsoon
  season; the training set spans many regions, seasons and sea states but is weighted to
  Southeast Asia by design.
- AIS-length statistics come from AI2's attribute labels, which only exist for vessels that
  carried AIS and were matched by AI2 (UNVERIFIED: AI2 does not document the matching rule).

## Limitations

- Expert point labels miss some vessels, especially small or faint ones, and some labelled
  windows contain unlabelled ships. Bright two-polarisation objects (15 px or more) with no
  label within 150 m are reported per campaign in `data/ml/metrics.json`
  (`label_quality_by_campaign`); they bound the label completeness and inflate the apparent
  false-alarm rate of both stages.
- Label positions are approximate: the median candidate-to-label distance is about 20 to
  30 m even for clear ships, so a 50 m match radius loses a few true matches. The loose
  variant is reported next to it.
- The CFAR stage bounds the whole system: whatever CFAR does not propose, the CNN cannot
  recover. CFAR recall by length therefore caps the verified recall.
- Fixed structures (platforms, wind turbines, buoys) look like ships in a single chip. The
  baseline's persistence class handles them; the CNN does not.
- "Dark" means only that no AIS position was matched to a radar detection. It does not mean
  illegal. The caveat column is kept in every vessel output.
