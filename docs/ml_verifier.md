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

[[TRAINING_RESULTS]]

## Held-out evaluation

[[EVAL_RESULTS]]

## Recall by AIS length (first look at the flagship question)

[[LENGTH_RESULTS]]

## Application to the Sentinel-1D Ca Mau scene (transfer experiment 1)

[[APPLY_RESULTS]]

## Transfer caveats

- Trained on Sentinel-1A and 1B acquisitions of 2020 to 2022; applied to Sentinel-1D of 2026.
  No Sentinel-1D ground truth exists in this project yet, so nothing in the application
  section is a measured accuracy. The CNN scores on the 1D scene say how 1D chips look to a
  1A/1B-trained model, not how many vessels are there.
- Calibration and noise differences between the satellites are not corrected beyond the
  standard sigma0 calibration; thermal noise is not subtracted in either stage, so the VH
  background level of a different instrument is a plausible source of shift.
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
