# Radar length calibration

Task R3-T9, 2026-10-10. Script `scripts/35_length_calibration.py`, module `src/darkvessel/detect/length_cal.py`, tests `tests/test_length_cal.py`. Outputs: `data/length_calibration.json` and `data/length_calibration_open_pairs.parquet` (open build), `data/research/length_calibration_research.json` and `data/research/length_calibration_research_pairs.parquet` (research build, Global Fishing Watch data, CC BY-NC 4.0, noncommercial), `docs/figures/length_calibration.png`. Every number below is in those files.

'Dark' means only that no AIS position was matched to a radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS both have blind spots. A calibrated radar length is a statistical estimate from the extent of a radar return, not a measurement of a hull.

## 1. Summary

The radar length estimate `length_est_m` runs long. On every pair set the median ratio of radar length to hull length lies between 1.59 and 1.87, and only 53 to 71 % of pairs agree within a factor 2.

- **Open calibration** (`data/length_calibration.json`, aisstream pairs only): calibrated length = `length_est_m` / 1.761, 80 % interval 0.387 to 1.405 times the calibrated length. Fitted on 27 Pearl River pairs from one scene. Checked out of sample on 1,376 open AI2 Sentinel-1A pairs from 173 other scenes: mean absolute error (MAE) 95.6 m before and 39.4 m after, median ratio 1.582 before and 0.899 after, share within a factor 2 from 70.9 % to 93.1 %, interval coverage 77.8 %. No grouped cross-validation of the open model is possible yet, because all its pairs come from one scene. Treat it as provisional.
- **Research calibration** (`data/research/length_calibration_research.json`): loglinear median model with blob fill width and brightness, plus a length-only fallback. Leave-one-pass-out cross-validation on 89 pairs (62 GFW registry lengths on 17 passes and the 27 live pairs): MAE 136.3 m before and 38.5 m after, median ratio 1.799 before and 1.055 after, within a factor 2 from 58.4 % to 93.3 %, 80 % interval coverage 77.5 %.
- **Diagnosis.** The data support five causes of the overestimate: an additive component that makes short hulls read relatively longest, brightness (bright returns spread), vessel motion, orientation (ships lying along the azimuth axis read longer than ships lying along range) and the 2-pixel floor for small returns. They do not support a diagonal-orientation error, a speckle filter, or the polarisation maximum as material causes.
- **Limits.** The pairs hold almost no hulls under 25 m (0 live, 1 GFW, 8 AI2), so neither calibration makes a claim below 25 m, and both return null at the 2-pixel floor (37.3 % of regional contacts, 28.3 % of live contacts).
- **Recommendation.** An open model fitted on the AI2 pairs is better validated (grouped 10-fold CV by scene over 173 scenes: MAE 35.0 m, within a factor 2 94.1 %, coverage 80.3 %; on the 27 live pairs MAE 27.8 m, coverage 81.5 %). Switching the open calibration to AI2 plus live pairs is a decision for the lead (section 8).

## 2. What `length_est_m` is

`detect.cfar.extract_detections` groups CFAR-detected pixels into objects (pixels within one pixel of each other join, `merge_px = 1`). An object's length is the extent of its pixels projected on their principal axis, plus one pixel, times the 10 m pixel spacing (`_principal_extent`). Objects need at least 2 pixels, so a 2-pixel object reads 20 m along an image axis and 24.1 m on the diagonal. When VV and VH both detect a contact, `pipeline.fuse_polarisations` keeps the longer of the two extents. `detect.postprocess.assign_confidence` moves any object longer than 450 m to the low class (`oversized`), so contacts never exceed 450 m. No speckle filter is applied anywhere in this chain: `s1.grd.GRDScene.read_sigma0` returns calibrated sigma0 of the GRD product, and the CFAR runs on it directly. These statements come from the code; `postprocess.py` was read and not changed (board D5.7).

Sentinel-1 IW GRD high-resolution products have 10 x 10 m pixels, a resolution of 20 x 22 m (range x azimuth; per sub-swath 20.4 x 22.5, 20.3 x 22.6 and 20.5 x 22.6 m), 5 x 1 looks, an equivalent number of looks of 4.4, and Hamming weighting coefficients of 0.70 to 0.75 in range and azimuth (ESA SentiWiki, Sentinel-1 products, https://sentiwiki.copernicus.eu/web/s1-products, resolved 2026-10-10). A resolution cell therefore spans about two pixels in each direction.

## 3. Data

| Set | Use | Source of the hull length | Licence | Pairs used | Groups |
|---|---|---|---|---|---|
| Live (open) | fit of the open model; part of the research fit | AIS static report, Dimension A + B, as the recorder computes it (`ais.aisstream._dims`) | live AIS relayed by aisstream.io; terms UNVERIFIED | 27 | 1 scene, 1 pass |
| AI2 S1A (open) | structure choice and out-of-sample check of the open model; alternative open model | AI2 label attribute `Length` | Apache-2.0 | 1,399 (1,376 scored by the open model) | 173 scenes |
| GFW registry (research) | research fit | GFW vessels API registry length (`registryInfo` `lengthM`, parsed in `ais.gfw.vessels_to_frame`) | CC BY-NC 4.0, noncommercial | 62 | 17 passes |

**Live pairs.** The three live pass files hold 8,573 contacts, 33 matched to AIS, 31 with an AIS static length. All 31 come from scene `S1D_IW_GRDH_1SDV_20261010T103247` of the Pearl River pass (2026-10-10 10:32 UTC); the other two passes have no matches. 27 pairs are used (hulls 27 to 229 m, median 96 m); 4 are excluded because their match position quality is low, and 3 of those 4 were graded doubtful in the R3-T7 hand check (`review_note`). The aisstream schema lists a `Dimension` object in the static data message (https://aisstream.io/documentation, resolved 2026-10-10); that A and B are the distances from the position reference to bow and stern follows ITU-R M.1371, which was not opened here (UNVERIFIED). AIS dimensions are self-reported and can be wrong; the error rate is not known (UNVERIFIED).

**AI2 pairs.** `data/ml/candidates.parquet` holds the project's CFAR candidates on 196 Sentinel-1A scenes of the AI2 label set, run with the same CFAR settings as the regional and live runs (`ml.chips.CfarSettings`). A candidate within 50 m of an AI2 point label is a vessel, a rule that does not use length. 1,679 such candidates carry a label `Length`; 1,399 are used, the high and medium classes (277 low-class objects, which include the oversized ones, and 3 others are left out). All used scenes are from 2022 and all are Sentinel-1A, not 1C or 1D. The AI2 README lists the label properties `Length`, `Width`, `Heading`, `ShipAndCargoType` and `Speed` and the repository licence is Apache-2.0 (https://github.com/allenai/vessel-detection-sentinels and its LICENSE, resolved 2026-10-10). That these attributes come from AIS is the project's reading (`docs/ml_verifier.md`; the `ShipAndCargoType` code is the AIS type code); the README does not say so (UNVERIFIED). Whether `Heading` is the true heading or the course over ground is also UNVERIFIED.

**GFW pairs.** Of the 9,954 matched contacts of the September regional run, 161 carry a GFW registry length, all vessel identities (no gear). 62 are used: 89 are excluded on position quality, 8 because the radar return is shorter than a quarter of the hull, and 2 because they sit at the pixel floor. All 62 are rule (a) matches (GFW SAR detection paired by cell and hour, `docs/gfw_identity.md`). GFW publishes a registry length for 1.6 % of the matched contacts, so this set is small and leans to large registered ships (median hull 131 m).

**Why position quality, not the stored quality.** The stored `match_quality` of both matchers also grades the radar/AIS length ratio: live high needs a ratio of 0.4 to 3.0 and medium 0.25 to 4.0 (`live.rules.LIVE_QUALITY`); GFW high needs agreement within a factor 2 and any pair off by more than a factor 3 is low (`ais.gfw_identity.sar_quality`). Selecting pairs on that grade would cut off the long tail of the very error being measured and make the intervals too narrow. The fit sets therefore use the same rules with the length terms removed (the script recomputes both grades and reproduces the stored ones exactly). This keeps 13 GFW pairs whose stored grade is low only because of length (all 12 used GFW pairs with a ratio above 3 are among them) and 3 such live pairs. The stored-quality sets are reported as a sensitivity (section 6.4). Two rules that do touch length remain: a pair is dropped when the return is shorter than 0.25 times the hull (the live matcher's own feasibility rule, `MIN_LENGTH_RATIO`), and when it sits at the 2-pixel floor, where the calibration makes no claim.

## 4. Diagnosis of the overestimate

For each set the script computes the size-adjusted log ratio: log(radar / hull) minus its median fit on log(hull), so that a cause is not credited with an effect of vessel size. Spearman correlations of that residual with each candidate cause are in `diagnosis` of both json files; the AI2 set (n = 1,399) has the power, the live (27) and GFW (62) sets are checks.

| Candidate cause | What the data show | Verdict | Mechanism source |
|---|---|---|---|
| 2-pixel floor (20 to 24.1 m) | 37.3 % of regional contacts and 28.3 % of live contacts sit at the floor. Pairs with a hull under 25 m: 8 AI2 (median ratio 4.41), 1 GFW, 0 live. | Supported for the contact population; the pairs cannot calibrate it. No claim at the floor. | code (`min_pixels = 2`, `_principal_extent`) |
| Additive bias from the resolution cell and spreading | AI2: radar = 65.1 m + 1.094 x hull (median fit); ratio by hull length 2.66 (25 to 50 m, n 85), 1.88 (50 to 100 m, 329), 1.55 (100 to 200 m, 722), 1.35 (200 m and longer, 255). GFW: 2.38, 1.88, 1.65 over the same bins from 50 m. | Supported: short hulls read relatively longest. The 65 m offset is about three resolution cells, more than resolution alone explains. | resolution: SentiWiki (above); the size of the offset: UNVERIFIED |
| Bright-target sidelobes and blooming | Residual against peak-to-background of the brighter channel: AI2 rho 0.478 (p 7e-81), stationary ships only 0.616 (p 7e-113); GFW 0.337 (p 0.007); live 0.246 (p 0.22, n 27). | Supported (association with brightness, not explained by speed). | Sentinel-1 applies Hamming weighting (SentiWiki); that bright returns still spread above the CFAR threshold into neighbouring pixels is UNVERIFIED |
| Wakes and moving-target smearing | AI2: rho with AIS speed 0.230 (p 3e-18); median ratio 1.50 at under 1 kn (n 1,073; 4.6 % above 3) against 2.20 at 5 kn or more (n 239; 27.6 % above 3). Live: medians 1.74 and 1.76, but 5 of 16 moving pairs and 0 of 10 stationary ones exceed 3; the R3-T7 hand check describes those five returns as smeared, sidelobe-spread or merged. | Supported: moving ships read longer and make the long tail. Wake and defocus are not separated, but the effect is largest for ships along azimuth (next row). | Moving targets often appear two-dimensionally defocused in SAR images (Mao 2015, arXiv:1507.02150, abstract resolved 2026-10-10). Wake as a cause: UNVERIFIED |
| Orientation, diagonal length | The length is projected on the principal axis, not on the image axes, so a diagonal ship is not read as sqrt(2) times longer by construction. AI2: diagonality rho -0.064 (p 0.019); diagonal ships read slightly shorter than ships along an axis. | Not supported | code (`_principal_extent`) |
| Orientation, azimuth against range | AI2 (AIS heading against the image azimuth axis): ships within 30 degrees of azimuth 1.73 (n 466), diagonal 1.53 (501), within 30 degrees of range 1.43 (395); rho 0.271 (p 2e-24); among stationary ships 1.64, 1.46 and 1.40 (rho 0.232, p 3e-14); among moving ships 2.41, 2.07 and 1.80. Live: 1.84 (10), 1.42 (9), 1.43 (4), rho 0.485 (p 0.019, n 23). | Supported: an azimuth/range asymmetry, present at rest and stronger in motion | The product's azimuth resolution (22.5 m) is only slightly coarser than its range resolution (20.4 m) (SentiWiki), so the resolution numbers do not explain the asymmetry; mechanism UNVERIFIED |
| GRD pixel and speckle filter | No speckle filter is applied in this pipeline (code). The GRD's own 5 x 1 multi-looking is part of the 20 x 22 m resolution above. | Not a cause in this pipeline (the filter); the resolution is covered by the additive row | code; SentiWiki |
| Polarisation maximum (found in the code) | Raw medians 1.64 (dual) and 1.45 (single) on AI2, but size-adjusted rho -0.048 (p 0.073); GFW -0.013 (p 0.92); live 0.40 (p 0.039, n 27). | Not supported once size is accounted for (dual-channel detections are larger ships) | code (`fuse_polarisations`) |
| Incidence angle | GFW rho 0.173 (p 0.18); live -0.438 (p 0.022) over 41.9 to 45.7 degrees only; AI2 candidates carry no incidence angle. | Not supported | |
| Pairing errors (found in the GFW pairs) | GFW pairs by cell and hour, so a pair can join a ship to a nearby return that is not the ship. Of the used GFW pairs, 6 have a CNN score below the verifier threshold; their median ratio is 0.736 and 4 of 6 are below 1. | A data problem, not a radar cause. `cnn_score` is excluded as a covariate: it lowers the CV error (MAE 45.5 to 42.1 m for the loglinear form, `cnn_score_covariate_check`) by flagging probable pairing errors, and would make CNN-rejected contacts read longer. | |

Bins with a hull under 25 m are too thin to describe (8 AI2 pairs), and the GFW 25 to 50 m bin holds 1 pair. The orientation and speed tests need AIS heading and speed, which only the AI2 and live sets have; GFW pairs carry neither.

## 5. Method

**Models** (`length_cal.py`), all on log lengths with x the radar length and y the hull length:

- ratio: log y = c + log x, one constant factor, the median of log(y / x);
- loglinear: log y = a + b log x + sum of g_k (z_k minus its median), fitted by least absolute deviation (a median fit, robust to the long tail);
- isotonic: a non-decreasing step function of log x (pool adjacent violators), linear between knots.

**Covariates.** Only measurement covariates that both the live and the regional producers carry or can derive: `scr_max_db` (peak-to-background of the brighter channel), `fill_width_m` (n_pixels x 100 / length_est_m, the blob's mean width), `inc_angle_deg`, `dual_pol`, `mission_s1d`. At most two. Speed and heading are not covariates, because a dark contact has neither; their effect stays in the interval.

**Selection** (`select_model`), by grouped cross-validation on the mean absolute log error: loglinear replaces ratio only for a gain of at least 5 %; a covariate is added only for a gain of at least 5 %; isotonic replaces the chosen model only for a gain of at least 10 % with an MAE that is not higher. This keeps the simpler model unless the other is clearly better. Selection and the reported CV share the folds, so the CV figures of the selected research model are slightly optimistic; the external checks on AI2 do not share that bias.

**Cross-validation.** Grouped, never by row: folds are whole passes for the research model (leave-one-pass-out, 18 folds) and whole scenes for the AI2 models (10 folds of scenes). The 80 % interval of each held-out pair comes from a nested grouped CV on the training groups only (5 inner folds), so a held-out pass never shapes its own interval. Interval bounds are the finite-sample quantiles of held-out log residuals at ranks floor((n + 1) 0.1) and ceil((n + 1) 0.9), following the split conformal construction of Lei et al. (doi:10.1080/01621459.2017.1307116, abstract read through OpenAlex, resolved 2026-10-10), which they show guarantees finite-sample marginal coverage. Grouped pairs are not exchangeable row by row, so the coverage reported here is measured, not guaranteed.

**The open model on one scene.** With one scene there is no grouped CV. Its form comes from the grouped CV on the open AI2 pairs (length only, because 27 pairs are too few to select covariates: ratio, since loglinear lowered the AI2 error by only 2.2 %). Its interval uses leave-one-pair-out residuals within the scene: the jackknife interval, for which Barber et al. (doi:10.1214/20-AOS1965, abstract resolved 2026-10-10) give no coverage guarantee in general and near-exact coverage when the fit is stable. Its out-of-sample check is the AI2 set.

**Applying a calibration.** `apply_frame(df, cal)` and `length_cal_m(length_est_m, covariates, cal)` return the median, the 80 % interval and a reason code. The model is used where its covariates are present and inside their fitted range, the length-only fallback elsewhere. The result is null, with a code, when the radar length is missing (`missing_length`), at the 2-pixel floor of 24.1 m or less (`pixel_floor`), outside the radar lengths of the pairs (`below_range`, `above_range`), longer than the radar return itself (`short_return`: a model fitted on AIS ships carries their size into weak returns, see section 7), or under 25 m (`below_min_claim`). Display text for each code is in `length_cal.REASONS`.

## 6. Results

Metrics: MAE in metres; median ratio of calibrated (or raw) to hull length; shares within a factor 1.5 and 2; 80 % interval coverage; median interval width as the factor hi / lo. "Before" is the raw `length_est_m`.

### 6.1 Open model

Calibrated length = `length_est_m` x exp(-0.5658) = `length_est_m` / 1.761; interval 0.387 to 1.405 times that; valid radar lengths 38.3 to 440.1 m; with the 25 m claim floor, the lowest radar length that yields a value is 44 m.

| Evaluation | n | MAE before / after (m) | Median ratio before / after | Within x1.5 before / after | Within x2 before / after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| Live pairs, in sample (optimistic) | 27 (26 scored, 1 under 25 m) | 122.4 / 39.4 | 1.761 / 1.011 | 33.3 % / 73.1 % | 70.4 % / 76.9 % | 92.3 % | 3.63 |
| AI2 pairs, out of sample | 1,376 (14 above range, 4 below range, 5 under 25 m) | 95.6 / 39.4 | 1.582 / 0.899 | 43.3 % / 74.6 % | 70.9 % / 93.1 % | 77.8 % | 3.63 |
| GFW pairs, out of sample (research file only) | 60 | 140.7 / 44.2 | 1.867 / 1.060 | 21.7 % / 63.3 % | 55.0 % / 90.0 % | 88.3 % | 3.63 |

On AI2 the open model runs short by about 10 % (median ratio 0.899): the Pearl River scene's median ratio (1.761) is higher than the AI2 median (1.582). By radar length on AI2: 50 to 100 m median ratio 0.863 (n 77, coverage 77.9 %), 100 to 200 m 0.828 (464, 72.8 %), 200 m and longer 0.939 (834, 80.7 %). Fitted on the live pairs and checked on AI2, the loglinear form does as well as ratio (MAE 39.2 m, coverage 79.1 %) and the isotonic form worse (MAE 45.8 m, coverage 61.6 %).

**How unreliable the open model is.** 27 pairs, one scene, one sea state, one pass geometry (incidence 41.9 to 45.7 degrees). The in-sample numbers say nothing about other scenes; the AI2 check says the single factor transfers to other scenes with about 10 % bias and slight undercoverage, but AI2 is Sentinel-1A in 2022, not 1C or 1D in 2026. The interval is wide (factor 3.63) because five of the 27 pairs are moving ships with ratios of 3.8 to 5.3. Rerun the script after every live pass with matches; the open model becomes testable by grouped CV once pairs come from at least two scenes, and should not be called validated before about four.

### 6.2 Alternative open model on the AI2 pairs (reported, not applied)

Loglinear with `fill_width_m`: log y = 1.614 + 0.599 log x + 0.00877 (fill_width_m minus 48.9); interval factors 0.610 to 1.478.

| Evaluation | n | MAE before / after (m) | Median ratio before / after | Within x1.5 after | Within x2 after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| Grouped 10-fold CV by scene, 173 scenes | 1,399 | 96.5 / 35.0 | 1.587 / 1.001 | 77.1 % | 94.1 % | 80.3 % | 2.40 |
| Live pairs, out of sample | 27 | 122.4 / 27.8 | 1.761 / 1.037 | 77.8 % | 96.3 % | 81.5 % | 2.42 |

AI2 structure table (mean absolute log error under grouped CV): ratio 0.2997, loglinear 0.2930, loglinear with fill width 0.2765, with fill width and brightness 0.2647 (rejected: 4.3 % gain, under the 5 % bar), isotonic 0.3010.

### 6.3 Research model

log y = 2.563 + 0.4006 log x + 0.01382 (fill_width_m minus 45.7) - 0.02066 (scr_max_db minus 28.7); interval factors 0.613 to 1.698; valid radar lengths 30 to 449 m. Fallback where n_pixels or the scr columns are missing (all regional contacts of `data/detections_regional.gpkg`, which carries no pixel count): ratio, `length_est_m` / 1.799, interval 0.511 to 1.446.

| Evaluation (leave-one-pass-out, 18 folds) | n | MAE before / after (m) | Median ratio before / after | Within x1.5 before / after | Within x2 before / after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| All research pairs | 89 | 136.3 / 38.5 | 1.799 / 1.055 | 24.7 % / 67.4 % | 58.4 % / 93.3 % | 77.5 % | 2.71 |
| of which GFW registry | 62 | 142.4 / 42.1 | 1.867 / 1.056 | 21.0 % / 66.1 % | 53.2 % / 91.9 % | 77.4 % | 2.91 |
| of which live aisstream | 27 | 122.4 / 30.2 | 1.761 / 1.007 | 33.3 % / 70.4 % | 70.4 % / 96.3 % | 77.8 % | 2.66 |
| Length-only ratio | 89 | 136.3 / 43.2 | 1.799 / 0.986 | 24.7 % / 69.7 % | 58.4 % / 85.4 % | 83.1 % | 2.90 |
| Length-only loglinear | 89 | 136.3 / 45.5 | 1.799 / 0.991 | 24.7 % / 64.0 % | 58.4 % / 89.9 % | 79.8 % | 2.77 |
| Length-only isotonic | 89 | 136.3 / 42.2 | 1.799 / 0.972 | 24.7 % / 70.8 % | 58.4 % / 92.1 % | 79.8 % | 2.54 |
| GFW pairs only, ratio (17 folds) | 62 | 142.4 / 45.8 | 1.867 / 0.968 | 21.0 % / 61.3 % | 53.2 % / 88.7 % | 82.3 % | 2.97 |
| External: AI2 pairs (1,355 scored, 43 `short_return`, 1 above range) | 1,355 | 98.5 / 34.6 | 1.584 / 0.954 | 43.5 % / 80.1 % | 70.8 % / 94.8 % | 88.8 % | 2.77 |

Selection table (mean absolute log error): ratio 0.3608, loglinear 0.3760, with fill width 0.3377, with fill width and brightness 0.3134, isotonic 0.3509 (worse than the chosen model). The CV rows score every held-out pair, including those the applied calibration would leave null. The model's slope of 0.40 compresses: on AI2 it makes radar returns of 50 to 100 m too long (median ratio 1.24, coverage 68.1 %, n 47 after the `short_return` nulls), while 100 to 200 m (1.008, 88.5 %) and 200 m and longer (0.912, 90.1 %) hold. Only 9 research pairs have a radar length under 100 m (4 GFW, 5 live).

### 6.4 Sensitivity to the pair selection

| Fit set | n | Result |
|---|---|---|
| Open, stored match_quality high or medium | 24 | factor 1.718, interval 0.447 to 1.359; on AI2 MAE 39.0 m, within x2 92.6 %, coverage 73.9 % (narrower interval, lower coverage) |
| GFW, stored match_quality high or medium, ratio form | 49 on 16 passes | CV MAE 121.3 to 36.2 m, median ratio 1.734 to 0.993, within x2 67.3 % to 93.9 %, coverage 83.7 % |

The stored-quality sets look better in their own CV because the length bands have already removed the long tail; on the open data that tail is real (5 of 27 live pairs), and the AI2 coverage of the stored-quality open model drops to 73.9 %.

### 6.5 What the calibrations return on the project's contacts

| Contacts | Calibration | Calibrated | pixel_floor | below_range | short_return | below_min_claim | above_range | Median calibrated length |
|---|---|---|---|---|---|---|---|---|
| Live passes (8,573) | open | 55.6 % | 28.3 % | 8.6 % | 0 | 7.4 % | 0.1 % | 57.1 m |
| Regional run (78,615) | open | 44.3 % | 37.3 % | 10.8 % | 0 | 7.6 % | 0.1 % | 49.9 m |
| Live passes | research | 41.8 % | 28.3 % | 0 | 26.5 % | 3.3 % | 0.03 % | 84.6 m |
| Regional run | research (fallback) | 43.8 % | 37.3 % | 0 | 0 | 18.9 % | 0.02 % | 50.0 m |

(`on_project_contacts` in each json.)

## 7. Limits

1. **No small boats.** The pairs hold 0 live, 1 GFW and 8 AI2 hulls under 25 m. Neither calibration makes a claim below 25 m or at the 2-pixel floor, which covers more than a third of all regional contacts. A dark lead at the floor has no calibrated length.
2. **The pairs are AIS ships.** Every pair is a vessel that transmits AIS and, for GFW, has a registry record: mostly steel ships of 50 to 230 m. A regression of hull on radar length reproduces the size distribution of its pairs, so for a population with more small boats (dark contacts are likely such a population, UNVERIFIED) it runs long at the short end. This is why the module returns null where a model would make the hull longer than the return (`short_return`) and why the research model's results under 100 m of radar length are weak (section 6.3). The ratio form of the open model never lengthens a return.
3. **One scene for the open model** (section 6.1).
4. **Hull lengths are not ground truth.** AIS dimensions are self-reported; GFW registry lengths cover 1.6 % of GFW matches; AI2 lengths are label attributes of unstated origin (UNVERIFIED). None was checked against an independent register here.
5. **Pairing errors** remain in the GFW pairs (section 4); the 0.25 feasibility rule removes the worst. The live pairs were hand-checked (R3-T7) and the doubtful ones are excluded.
6. **Sensor and period.** AI2 is Sentinel-1A in 2022; the live and GFW pairs are 1C and 1D in September and October 2026. The research model's mission covariate did not help (section 6.3), which says little with 10 S1C pairs.
7. **Motion and orientation are in the interval, not the median.** A moving ship lying along the azimuth axis can read 2.4 times its hull (AI2 median); the 80 % interval is marginal over all pairs, not conditional on motion, so it is too narrow for fast ships and too wide for ships at anchor.
8. **One return, one vessel.** Two hulls side by side, a hull and its wake, or rafted boats read as one long return. No calibration can tell.

## 8. How producers should use the result next round

Producers (live, regional, leads) are not changed by this task (board D6.4). Next round:

```python
from darkvessel.detect.length_cal import apply_frame, load_calibration

cal = load_calibration("data/length_calibration.json")            # open build
# cal = load_calibration("data/research/length_calibration_research.json")   # research build only
contacts = contacts.join(apply_frame(contacts, cal))               # adds length_cal_m, length_cal_lo_m, length_cal_hi_m,
                                                                   # length_cal_reason, length_cal_id
```

- Keep `length_est_m` raw in every file (board D5.7); add the five columns next to it. `length_cal_reason` is a code; the product maps it to `REASONS` text.
- The live producer has every covariate input (`scr_vv_db`, `scr_vh_db`, `n_pixels`, `pol_class`). The regional producer reads `data/detections_regional.gpkg`, which has no `n_pixels`, so the research calibration falls back to its ratio form there unless the producer takes `n_pixels` from `data/detections_regional_all.gpkg`.
- Show the calibrated length with its interval ("calibrated radar length 120 m, 80 % interval 46 to 169 m") and the reason when it is null. Never show it as a measurement, and never below 25 m.
- Leads: use the interval, not the median, for any size rule, and say in the lead evidence that moving ships read longer.
- Matching: the live and GFW quality bands compare the raw ratio with fixed bands (0.25 to 4.0). A later matcher could test the AIS length against the calibrated interval instead; that is a matcher decision for the lead, not part of this task.
- Record `length_cal_id` with every output, and rerun `scripts/35_length_calibration.py` after each live pass with matches; the id changes when the calibration does.
- **Decision for the lead.** The open model rests on one scene. The AI2 alternative (section 6.2) is open data (Apache-2.0), validated over 173 scenes, and better on the live pairs than the live model is on itself. Options: (a) keep the live-only open model until at least four scenes have matches; (b) switch the open calibration to AI2 plus live pairs now. The script reports both; switching needs a small script change (fit set) and a rerun.

## 9. Reproduce

```
/home/user/.mamba/envs/darkvessel/bin/python scripts/35_length_calibration.py      # about 25 s
/home/user/.mamba/envs/darkvessel/bin/python -m pytest -q tests/test_length_cal.py
```

`--select stored` fits on the stored match_quality instead of the position quality. The orientation test reads the live object cache (`data/cache/live/scenes/`) and the aisstream position cache (`data/cache/ais/aisstream/positions/`) when present; the research covariates read `data/detections_regional_all.gpkg` when present (git-ignored). Without them those tests and covariates are reported as unavailable. The live pairs change when R3-T7 or a later pass changes the live files; the numbers above are from the run of 2026-10-10 about 15:40 UTC.

## 10. Sources

Resolved with curl in this session (2026-10-10):

- ESA SentiWiki, Sentinel-1 products, IW GRD specification: https://sentiwiki.copernicus.eu/web/s1-products
- AI2, vessel-detection-sentinels, README (label properties) and LICENSE (Apache-2.0): https://github.com/allenai/vessel-detection-sentinels , https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/LICENSE
- aisstream.io API documentation (ShipStaticData `Dimension`): https://aisstream.io/documentation
- Mao, X. (2015). SAR imaging of moving target based on knowledge-aided two-dimensional autofocus. arXiv:1507.02150, https://arxiv.org/abs/1507.02150 (abstract)
- Lei, J., G'Sell, M., Rinaldo, A., Tibshirani, R. J. Distribution-free predictive inference for regression. Journal of the American Statistical Association, doi:10.1080/01621459.2017.1307116 (doi.org returned 403 from the publisher; record and abstract read at https://api.openalex.org/works/doi:10.1080/01621459.2017.1307116)
- Barber, R. F., Candes, E. J., Ramdas, A., Tibshirani, R. J. (2021). Predictive inference with the jackknife+. Annals of Statistics 49(1), doi:10.1214/20-AOS1965 (https://doi.org/10.1214/20-AOS1965 resolved; abstract read through OpenAlex)
- Stasolla, M., Greidanus, H. (2016). The exploitation of Sentinel-1 images for vessel size estimation. Remote Sensing Letters 7(12), 1219-1228, doi:10.1080/2150704X.2016.1226522 (doi.org returned 403 from the publisher; Crossref record and OpenAlex abstract read). For comparison only: on 127 ships of 24 to 366 m in five Sentinel-1 images at 20 m multi-look resolution, their morphology-based method reached about 15 % relative and 30 m absolute length error, better than a simpler automatic method. Their data and method differ from ours; the figures are not comparable one to one.

Not opened: ITU-R M.1371 (AIS message content). Statements that depend on it are marked UNVERIFIED.
