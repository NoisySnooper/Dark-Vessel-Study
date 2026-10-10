# Radar length calibration

Task R3-T9, 2026-10-10, revised after review the same day. Script `scripts/35_length_calibration.py`, module `src/darkvessel/detect/length_cal.py`, tests `tests/test_length_cal.py`. Outputs: `data/length_calibration.json` and `data/length_calibration_open_pairs.parquet` (open build), `data/research/length_calibration_research.json` and `data/research/length_calibration_research_pairs.parquet` (research build, Global Fishing Watch data, CC BY-NC 4.0, noncommercial), `docs/figures/length_calibration.png`. Every number below is in those files (open calibration `length_cal_open_6ef2ab79`, research calibration `length_cal_research_fd529f83`).

'Dark' means only that no AIS position was matched to a radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS both have blind spots. A calibrated radar length is a statistical estimate from the extent of a radar return, not a measurement of a hull.

## 1. Summary

The radar length estimate `length_est_m` runs long. On every pair set the median ratio of radar length to hull length lies between 1.59 and 1.87, and only 53 to 71 % of pairs agree within a factor 2.

- **Open calibration** (`data/length_calibration.json`, aisstream pairs only): calibrated length = `length_est_m` / 1.780, 80 % interval 0.391 to 1.320 times the calibrated length. Fitted on 26 Pearl River pairs from one scene. Checked out of sample on 1,376 open AI2 Sentinel-1A pairs from 173 other scenes: mean absolute error (MAE) 95.6 m before and 39.6 m after, median ratio 1.582 before and 0.889 after, share within a factor 2 from 70.9 % to 93.2 %. The 80 % interval covers only 68.3 % of the AI2 pairs, and that coverage moves with single live pairs (63.2 to 68.5 % when one pair is left out; 77.8 % in the first run, which still held a pair now graded doubtful). No grouped cross-validation of the open model is possible yet, because all its pairs come from one scene. Treat it as provisional.
- **Research calibration** (`data/research/length_calibration_research.json`): one constant factor (ratio form). Leave-one-pass-out cross-validation on 88 pairs (62 GFW registry lengths on 17 passes and the 26 live pairs): MAE 137.5 m before and 42.9 m after, median ratio 1.801 before and 0.987 after, within a factor 2 from 58.0 % to 86.4 %, 80 % interval coverage 84.1 %. Richer models with blob fill width and a second covariate score better on the full pair set, but re-selection with one pass left out picks six different structures and the full-data choice in only 2 of 18 runs, so the ratio form is applied (section 5).
- **Diagnosis.** The data support five causes of the overestimate: an additive component that makes short hulls read relatively longest, brightness (bright returns spread), vessel motion, orientation (ships lying along the azimuth axis read longer than ships lying along range) and the 2-pixel floor for small returns. They do not support a diagonal-orientation error, a speckle filter, or the polarisation maximum as material causes.
- **Limits.** The pairs hold almost no hulls under 25 m (0 live, 1 GFW, 8 AI2), so neither calibration makes a claim below 25 m, and both return null at the 2-pixel floor (37.3 % of regional contacts, 28.3 % of live contacts).
- **Next step.** The board moves the open calibration to AI2 plus live pairs (decision D7.2, task R4-T2). The AI2 pairs alone give a grouped 10-fold CV by scene over 173 scenes of MAE 39.4 m, within a factor 2 90.1 % and coverage 79.8 % for the ratio form (section 6.2).

## 2. What `length_est_m` is

`detect.cfar.extract_detections` groups CFAR-detected pixels into objects (pixels within one pixel of each other join, `merge_px = 1`). An object's length is the extent of its pixels projected on their principal axis, plus one pixel, times the 10 m pixel spacing (`_principal_extent`). Objects need at least 2 pixels, so a 2-pixel object reads 20 m along an image axis and 24.1 m on the diagonal. When VV and VH both detect a contact, `pipeline.fuse_polarisations` keeps the longer of the two extents and the VV pixel count. `detect.postprocess.assign_confidence` moves any object longer than 450 m to the low class (`oversized`), so contacts never exceed 450 m. No speckle filter is applied anywhere in this chain: `s1.grd.GRDScene.read_sigma0` returns calibrated sigma0 of the GRD product, and the CFAR runs on it directly. These statements come from the code; `postprocess.py` was read and not changed (board D5.7).

Sentinel-1 IW GRD high-resolution products have 10 x 10 m pixels, a resolution of 20 x 22 m (range x azimuth; per sub-swath IW1 to IW3 20.4 x 22.5, 20.3 x 22.6 and 20.5 x 22.6 m), 5 x 1 looks, an equivalent number of looks of 4.4, 4.3 and 4.3, and Hamming weighting coefficients of 0.70 to 0.75 in range and azimuth (ESA SentiWiki, Sentinel-1 products, https://sentiwiki.copernicus.eu/web/s1-products, resolved 2026-10-10). A resolution cell therefore spans about two pixels in each direction.

## 3. Data

| Set | Use | Source of the hull length | Licence | Pairs used | Groups |
|---|---|---|---|---|---|
| Live (open) | fit of the open model; part of the research fit | AIS static report, Dimension A + B, as the recorder computes it (`ais.aisstream._dims`) | live AIS relayed by aisstream.io; terms UNVERIFIED | 26 | 1 scene, 1 pass |
| AI2 S1A (open) | structure choice and out-of-sample check of the open model; alternative open model | AI2 label attribute `Length` | Apache-2.0 | 1,399 (1,376 scored by the open model) | 173 scenes |
| GFW registry (research) | research fit | GFW vessels API registry length (`registryInfo` `lengthM`, dataset `public-global-vessel-identity:v4.0`, parsed in `ais.gfw.vessels_to_frame`) | CC BY-NC 4.0, noncommercial | 62 | 17 passes |

**Live pairs.** The three live pass files hold 8,573 contacts, 33 matched to AIS, 31 with an AIS static length. All 31 come from scene `S1D_IW_GRDH_1SDV_20261010T103247` of the Pearl River pass (2026-10-10 10:32 UTC); the other two passes have no matches. 26 pairs are used (hulls 27 to 229 m, median 94 m). Four are excluded because their match position quality is low (three of them graded doubtful in the R3-T7 hand check, one plausible), and one (`S1D_20261010T103247_08819`, a faint VH-only return of 61 m against a 96 m tanker) because the R3-T7 hand check graded it doubtful: probably the wake or smear of the tanker, whose own image lies inside the shore buffer. That regrade came after the first run of this script; the first run still used the pair, and this revision removes it (section 6.1 shows what it changed). The aisstream schema lists a `Dimension` object in the static data message (https://aisstream.io/documentation, resolved 2026-10-10); that A and B are the distances from the position reference to bow and stern follows ITU-R M.1371, which was not opened here (UNVERIFIED). AIS dimensions are self-reported and can be wrong; the error rate is not known (UNVERIFIED).

**AI2 pairs.** `data/ml/candidates.parquet` holds the project's CFAR candidates on 196 Sentinel-1A scenes of the AI2 label set, run with the same CFAR settings as the regional and live runs (`ml.chips.CfarSettings`). A candidate within 50 m of an AI2 point label is a vessel, a rule that does not use length. 1,679 such candidates carry a label `Length`; 1,399 are used, the high and medium classes (277 low-class objects, which include the oversized ones, and 3 others are left out). All used scenes are from 2022 and all are Sentinel-1A, not 1C or 1D. The AI2 README lists the label properties `Length`, `Width`, `Heading`, `ShipAndCargoType` and `Speed`, and the repository licence is Apache-2.0 (https://github.com/allenai/vessel-detection-sentinels; README and LICENSE read at raw.githubusercontent.com, resolved 2026-10-10). That these attributes come from AIS is the project's reading (`docs/ml_verifier.md`; the `ShipAndCargoType` code is the AIS type code); the README does not say so (UNVERIFIED). Whether `Heading` is the true heading or the course over ground is also UNVERIFIED.

**GFW pairs.** Of the 9,954 matched contacts of the September regional run, 161 carry a GFW registry length, all vessel identities (no gear). 62 are used: 89 are excluded on position quality, 8 because the radar return is shorter than a quarter of the hull, and 2 because they sit at the pixel floor. All 62 are rule (a) matches (GFW SAR detection paired by cell and hour, `docs/gfw_identity.md`). GFW publishes a registry length for 1.6 % of the matched contacts, so this set is small and leans to large registered ships (median hull 131.5 m).

**The GFW pairs are selected partly on length.** Rule (a) assigns contacts to GFW SAR detections one to one at the cost of the distance in km plus |log2(radar length / registry length)| (`ais.gfw_identity.pair_sar`). Where a GFW detection had more than one candidate contact, the pair was therefore chosen partly on length agreement, before any quality grade and whatever the fit set. This is the case for 28 of the 62 used pairs (`gfw_sar_n_rivals` above 1); no used contact had more than one candidate GFW detection (`gfw_sar_n_cand` 1). The median radar/hull ratio of those 28 pairs is 1.60, against 2.00 for the 34 without a rival. The live matcher does not do this: its assignment cost is distance only, and length enters only through the feasibility rule that this calibration also applies (`live.assign`). The selection pulls the GFW ratios toward 1, so the research factor is probably slightly too small and its interval slightly too narrow.

**Why position quality, not the stored quality.** The stored `match_quality` of both matchers also grades the radar/AIS length ratio: live high needs a ratio of 0.4 to 3.0 and medium 0.25 to 4.0 (`live.rules.LIVE_QUALITY`); GFW high needs agreement within a factor 2 and any pair off by more than a factor 3 is low (`ais.gfw_identity.sar_quality`). Selecting pairs on that grade would cut off the long tail of the very error being measured and make the intervals too narrow. The fit sets therefore use the same rules with the length terms removed (the script recomputes both grades and reproduces the stored ones exactly). This keeps 13 GFW pairs whose stored grade is low only because of length (all 12 used GFW pairs with a ratio above 3 are among them) and 3 such live pairs. The stored-quality sets are reported as a sensitivity (section 6.4). Two rules that do touch length remain: a pair is dropped when the return is shorter than 0.25 times the hull (the live matcher's own feasibility rule, `MIN_LENGTH_RATIO`), and when it sits at the 2-pixel floor, where the calibration makes no claim. The R3-T7 `review_note` is applied as written in the live files: a pair whose note starts with 'doubtful' is excluded, and a test fails when the live files and the calibration pairs disagree on that (section 9).

## 4. Diagnosis of the overestimate

For each set the script computes the size-adjusted log ratio: log(radar / hull) minus its median fit on log(hull), so that a cause is not credited with an effect of vessel size. Spearman correlations of that residual with each candidate cause are in `diagnosis` of both json files; the AI2 set (n = 1,399) has the power, the live (26) and GFW (62) sets are checks.

| Candidate cause | What the data show | Verdict | Mechanism source |
|---|---|---|---|
| 2-pixel floor (20 to 24.1 m) | 37.3 % of regional contacts and 28.3 % of live contacts sit at the floor. Pairs with a hull under 25 m: 8 AI2 (median ratio 4.41), 1 GFW, 0 live. | Supported for the contact population; the pairs cannot calibrate it. No claim at the floor. | code (`min_pixels = 2`, `_principal_extent`) |
| Additive bias from the resolution cell and spreading | AI2: radar = 65.1 m + 1.094 x hull (median fit); ratio by hull length 2.66 (25 to 50 m, n 85), 1.88 (50 to 100 m, 329), 1.55 (100 to 200 m, 722), 1.35 (200 m and longer, 255). GFW: 2.38, 1.88, 1.65 over the same bins from 50 m. | Supported: short hulls read relatively longest. The 65 m offset is about three resolution cells, more than resolution alone explains. | resolution: SentiWiki (above); the size of the offset: UNVERIFIED |
| Bright-target sidelobes and blooming | Residual against peak-to-background of the brighter channel: AI2 rho 0.478 (p 7e-81), stationary ships only 0.616 (p 7e-113); GFW 0.340 (p 0.007); live 0.098 (p 0.63, n 26). | Supported on AI2 and GFW (association with brightness, not explained by speed); the live set is too small to show it. | Sentinel-1 applies Hamming weighting (SentiWiki); that bright returns still spread above the CFAR threshold into neighbouring pixels is UNVERIFIED |
| Wakes and moving-target smearing | AI2: rho with AIS speed 0.230 (p 3e-18); median ratio 1.50 at under 1 kn (n 1,073; 4.6 % above 3) against 2.20 at 5 kn or more (n 239; 27.6 % above 3). Live: medians 1.74 and 1.80, but 5 of 15 moving pairs and 0 of 10 stationary ones exceed 3; the R3-T7 hand check describes those five returns as smeared, sidelobe-spread or merged. | Supported: moving ships read longer and make the long tail. Wake and defocus are not separated, but the effect is largest for ships along azimuth (next row). | Moving targets often appear two-dimensionally defocused in SAR images (Mao 2015, arXiv:1507.02150, abstract resolved 2026-10-10). Wake as a cause: UNVERIFIED |
| Orientation, diagonal length | The length is projected on the principal axis, not on the image axes, so a diagonal ship is not read as sqrt(2) times longer by construction. AI2: diagonality rho -0.064 (p 0.019); diagonal ships read slightly shorter than ships along an axis. | Not supported | code (`_principal_extent`) |
| Orientation, azimuth against range | AI2 (AIS heading against the image azimuth axis): ships within 30 degrees of azimuth 1.73 (n 466), diagonal 1.53 (501), within 30 degrees of range 1.43 (395); rho 0.271 (p 2e-24); among stationary ships 1.64, 1.46 and 1.40 (rho 0.232, p 3e-14); among moving ships 2.41, 2.07 and 1.80. Live: 1.84 (10), 1.63 (8), 1.43 (4), rho 0.420 (p 0.052, n 22). | Supported: an azimuth/range asymmetry, present at rest and stronger in motion | The product's azimuth resolution (22.5 m) is only slightly coarser than its range resolution (20.4 m) (SentiWiki), so the resolution numbers do not explain the asymmetry; mechanism UNVERIFIED |
| GRD pixel and speckle filter | No speckle filter is applied in this pipeline (code). The GRD's own 5 x 1 multi-looking is part of the 20 x 22 m resolution above. | Not a cause in this pipeline (the filter); the resolution is covered by the additive row | code; SentiWiki |
| Polarisation maximum (found in the code) | Raw medians 1.64 (dual) and 1.45 (single) on AI2, but size-adjusted rho -0.048 (p 0.073); GFW -0.013 (p 0.92); live 0.371 (p 0.062, n 26). | Not supported once size is accounted for (dual-channel detections are larger ships) | code (`fuse_polarisations`) |
| Incidence angle | GFW rho 0.174 (p 0.18); live -0.366 (p 0.066) over 41.9 to 45.7 degrees only; AI2 candidates carry no incidence angle. | Not supported | |
| Pairing errors (found in the GFW pairs) | GFW pairs by cell and hour, so a pair can join a ship to a nearby return that is not the ship. Of the used GFW pairs, 6 have a CNN score below the verifier threshold; their median ratio is 0.736 and 4 of 6 are below 1. | A data problem, not a radar cause. `cnn_score` is excluded as a covariate: it lowers the CV error (mean absolute log error 0.376 to 0.345, MAE 45.5 to 44.5 m, for the loglinear form, `cnn_score_covariate_check`) by flagging probable pairing errors, and would make CNN-rejected contacts read longer. | |

Bins with a hull under 25 m are too thin to describe (8 AI2 pairs), and the GFW 25 to 50 m bin holds 1 pair. The orientation and speed tests need AIS heading and speed, which only the AI2 and live sets have; GFW pairs carry neither.

## 5. Method

**Models** (`length_cal.py`), all on log lengths with x the radar length and y the hull length:

- ratio: log y = c + log x, one constant factor, the median of log(y / x);
- loglinear: log y = a + b log x + sum of g_k (z_k minus its median), fitted by least absolute deviation (a median fit, robust to the long tail), solved as a linear programme;
- isotonic: a non-decreasing step function of log x (pool adjacent violators), linear between knots.

**Covariates.** Only measurement covariates that both the live and the regional producers carry or can derive: `scr_max_db` (peak-to-background of the brighter channel), `fill_width_m` (n_pixels x 100 / length_est_m, the blob's mean width), `inc_angle_deg`, `dual_pol`, `mission_s1d`. At most two. For a contact detected in both channels `fill_width_m` mixes them: `fuse_polarisations` keeps the VV pixel count but the longer of the VV and VH extents, so it is the VV area over a possibly VH length. That is the same in the AI2, regional and live sets, which all go through `fuse_polarisations`, so it does not bias a fit across them. Speed and heading are not covariates, because a dark contact has neither; their effect stays in the interval. The detector confidence class is not a covariate either: within the high and medium classes it is the dual-polarisation indicator (`postprocess.assign_confidence` sets high for detection in both VV and VH, medium for VH only or strong VV only; the class equals `dual_pol` on all 1,399 AI2 pairs, all 62 GFW pairs and the 22 high or medium live pairs; the other 4 live pairs are `fixed`), `dual_pol` is a candidate, and low-class contacts are oversized or weak returns that the pairs do not hold.

**Selection** (`select_model`), by grouped cross-validation on the mean absolute log error: loglinear replaces ratio only for a gain of at least 5 %. Covariates are chosen by best subset, not by forward steps: every subset of one or two candidates is scored, and the best subset of size k replaces the current model of size j only when its error is at most 0.95 to the power k minus j times the current error, so each covariate must earn 5 %, but a pair that helps only jointly is not lost because neither member helps alone (the first run used forward steps, which can reject such a pair). Isotonic replaces the chosen model only for a gain of at least 10 % with an MAE that is not higher. This keeps the simpler model unless the other is clearly better.

**Stability of the selection** (`selection_stability`, `stable_structure`). The selection is rerun with one group left out at a time: one pass for the research pairs (18 runs), one tenth of the scenes for the AI2 pairs (10 runs). A structure richer than the ratio form is applied only when at least 75 % of those re-selections pick the same structure; otherwise the ratio form is applied. Each left-out group is also predicted by the structure re-selected without it, which scores the whole selection procedure out of sample (`selection_procedure_cv`). Selection and the reported CV of a fixed structure share the folds, so the CV figures of a selected structure are slightly optimistic; the selection-procedure CV and the external checks on AI2 do not share that bias.

**Cross-validation.** Grouped, never by row: folds are whole passes for the research model (leave-one-pass-out, 18 folds) and whole scenes for the AI2 models (10 folds of scenes). The 80 % interval of each held-out pair comes from a nested grouped CV on the training groups only (5 inner folds), so a held-out pass never shapes its own interval. Interval bounds are the finite-sample quantiles of held-out log residuals at ranks floor((n + 1) 0.1) and ceil((n + 1) 0.9), following the split conformal construction of Lei et al. (doi:10.1080/01621459.2017.1307116, abstract read through OpenAlex, resolved 2026-10-10), which they show guarantees finite-sample marginal coverage. Grouped pairs are not exchangeable row by row, so the coverage reported here is measured, not guaranteed.

**The open model on one scene.** With one scene there is no grouped CV. Its form comes from the grouped CV on the open AI2 pairs (length only, because 26 pairs are too few to select covariates: ratio, since loglinear lowered the AI2 error by only 2.2 % and every AI2 re-selection kept the ratio as the length-only form). Its interval uses leave-one-pair-out residuals within the scene: the jackknife interval, for which Barber et al. (doi:10.1214/20-AOS1965, abstract resolved 2026-10-10) give no coverage guarantee in general and near-exact coverage when the fit is stable. Its out-of-sample check is the AI2 set, recomputed with each live pair left out in turn.

**Applying a calibration.** `apply_frame(df, cal)` and `length_cal_m(length_est_m, covariates, cal)` return the median, the 80 % interval and a reason code. A model with covariates is used where they are present and inside their fitted range, its length-only fallback elsewhere (both calibrations of this run are ratio forms and need no fallback). The result is null, with a code, when the radar length is missing (`missing_length`), at the 2-pixel floor of 24.1 m or less (`pixel_floor`), outside the radar lengths of the pairs (`below_range`, `above_range`), longer than the radar return itself (`short_return`: a model with a slope under 1, fitted on AIS ships, carries their size into weak returns, see section 7), or under 25 m (`below_min_claim`). Display text for each code is in `length_cal.REASONS`.

## 6. Results

Metrics: MAE in metres; median ratio of calibrated (or raw) to hull length; shares within a factor 1.5 and 2; 80 % interval coverage; median interval width as the factor hi / lo. "Before" is the raw `length_est_m`.

### 6.1 Open model

Calibrated length = `length_est_m` x exp(-0.5764) = `length_est_m` / 1.780; interval 0.391 to 1.320 times that; valid radar lengths 38.3 to 440.1 m; with the 25 m claim floor, the lowest radar length that yields a value is 44.5 m.

| Evaluation | n | MAE before / after (m) | Median ratio before / after | Within x1.5 before / after | Within x2 before / after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| Live pairs, in sample (optimistic) | 26 (25 scored, 1 under 25 m) | 125.7 / 38.1 | 1.780 / 1.011 | 34.6 % / 76.0 % | 69.2 % / 80.0 % | 92.0 % | 3.37 |
| AI2 pairs, out of sample | 1,376 (14 above range, 4 below range, 5 under 25 m) | 95.6 / 39.6 | 1.582 / 0.889 | 43.3 % / 73.8 % | 70.9 % / 93.2 % | 68.3 % | 3.37 |
| GFW pairs, out of sample (research file only) | 60 | 140.7 / 43.6 | 1.867 / 1.049 | 21.7 % / 66.7 % | 55.0 % / 90.0 % | 86.7 % | 3.37 |

On AI2 the open model runs short by about 11 % (median ratio 0.889): the Pearl River scene's median ratio (1.780) is higher than the AI2 median (1.582). By radar length on AI2: 50 to 100 m median ratio 0.854 (n 77, coverage 70.1 %), 100 to 200 m 0.819 (464, 59.9 %), 200 m and longer 0.929 (834, 72.9 %). Fitted on the live pairs and checked on AI2, the loglinear form does about as well as ratio (MAE 39.2 m, coverage 69.9 %) and the isotonic form worse (MAE 45.4 m, coverage 55.2 %).

**The interval rests on single pairs.** The interval's upper bound comes from the few live pairs whose return is short for their hull. Left out one at a time, the 26 live pairs move the factor between 1.761 and 1.799, the upper interval factor between 1.284 and 1.322, and the AI2 coverage between 63.2 % and 68.5 % (`leave_one_live_pair_out`). The first run, which still held pair 08819 (radar 0.64 times its hull), had an upper factor of 1.405 and an AI2 coverage of 77.8 %: one pair at the tail moved the coverage by almost 10 points. The interval of a one-scene fit is not stable enough to carry a product claim; this supports the move to AI2 plus live pairs (D7.2).

**How unreliable the open model is.** 26 pairs, one scene, one sea state, one pass geometry (incidence 41.9 to 45.7 degrees). The in-sample numbers say nothing about other scenes. The AI2 check says the single factor transfers to other scenes with about 11 % bias and clear undercoverage (68 % for a nominal 80 %), but AI2 is Sentinel-1A in 2022, not 1C or 1D in 2026. The interval is wide (factor 3.37) because five of the 26 pairs are moving ships with ratios of 3.8 to 5.3. Rerun the script after every live pass with matches; the open model becomes testable by grouped CV once pairs come from at least two scenes, and should not be called validated before about four.

### 6.2 Alternative open model on the AI2 pairs (reported, not applied)

The grouped CV selects loglinear with `fill_width_m` on the full AI2 set (mean absolute log error 0.2765 against 0.2997 for ratio and 0.2930 for loglinear without covariates; with fill width and brightness 0.2647, a further 4.3 %, under the 5 % bar; isotonic 0.3010). With one tenth of the scenes left out, 7 of 10 re-selections pick the same structure and 3 pick ratio. That is under the 75 % rule, so the applied alternative is the ratio form: `length_est_m` / 1.587, interval factors 0.525 to 1.379.

| Evaluation | n | MAE before / after (m) | Median ratio before / after | Within x1.5 after | Within x2 after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| Ratio form, grouped 10-fold CV by scene, 173 scenes | 1,399 | 96.5 / 39.4 | 1.587 / 1.000 | 79.3 % | 90.1 % | 79.8 % | 2.62 |
| Loglinear with fill width (selected, not applied; CV shares the folds of the selection) | 1,399 | 96.5 / 35.0 | 1.587 / 1.001 | 77.1 % | 94.1 % | 80.3 % | 2.40 |
| Selection procedure (structure re-selected without each tenth of the scenes) | 1,399 | 96.5 / 37.0 | 1.587 / 0.995 | 75.8 % | 93.1 % | n/a | n/a |
| Ratio form on the live pairs, out of sample | 25 scored of 26 | 130.3 / 46.1 | 1.799 / 1.133 | 76.0 % | 80.0 % | 80.0 % | 2.63 |

The fill-width model is the borderline case: it wins on the full set and in 7 of 10 re-selections, and the selection procedure scores between the two fixed forms. A refit on AI2 plus live pairs (R4-T2) should rerun the stability check rather than take either form from this table.

### 6.3 Research model

Ratio form: one constant factor with an 80 % interval from the held-out residuals of the leave-one-pass-out CV; valid radar lengths 30 to 449 m. The factor and interval values are in `data/research/length_calibration_research.json` only (noncommercial; this document reports the research fit, not its coefficients). No fallback is needed.

| Evaluation (leave-one-pass-out, 18 folds) | n | MAE before / after (m) | Median ratio before / after | Within x1.5 before / after | Within x2 before / after | Coverage | Width |
|---|---|---|---|---|---|---|---|
| All research pairs, ratio (applied) | 88 | 137.5 / 42.9 | 1.801 / 0.987 | 25.0 % / 70.5 % | 58.0 % / 86.4 % | 84.1 % | 2.95 |
| of which GFW registry | 62 | 142.4 / 45.6 | 1.867 / 1.038 | 21.0 % / 66.1 % | 53.2 % / 88.7 % | 85.5 % | 2.99 |
| of which live aisstream | 26 | 125.7 / 36.6 | 1.780 / 0.953 | 34.6 % / 80.8 % | 69.2 % / 80.8 % | 80.8 % | 2.89 |
| Length-only loglinear | 88 | 137.5 / 45.5 | 1.801 / 0.966 | 25.0 % / 63.6 % | 58.0 % / 89.8 % | 79.5 % | 2.98 |
| Length-only isotonic | 88 | 137.5 / 42.4 | 1.801 / 0.981 | 25.0 % / 70.5 % | 58.0 % / 92.0 % | 80.7 % | 2.81 |
| Loglinear with dual_pol and fill width (selected, not applied; CV shares the folds of the selection) | 88 | 137.5 / 38.7 | 1.801 / 0.975 | 25.0 % / 70.5 % | 58.0 % / 90.9 % | 80.7 % | 3.03 |
| Selection procedure (structure re-selected without each pass) | 88 | 137.5 / 47.9 | 1.801 / 1.072 | 25.0 % / 54.5 % | 58.0 % / 88.6 % | n/a | n/a |
| GFW pairs only, ratio (17 folds) | 62 | 142.4 / 45.8 | 1.867 / 0.968 | 21.0 % / 61.3 % | 53.2 % / 88.7 % | 82.3 % | 2.97 |
| External: AI2 pairs (1,388 scored, 10 under 25 m, 1 above range) | 1,388 | 96.9 / 40.0 | 1.593 / 0.884 | 43.0 % / 72.3 % | 70.7 % / 93.7 % | 75.8 % | 2.98 |

Selection table (mean absolute log error): ratio 0.3531, loglinear 0.3757; one covariate: fill width 0.3384 (a 4.2 % gain, under the bar), the others 0.3668 to 0.3826; two covariates: fill width with dual_pol 0.3099 (a 12.2 % gain, over the two-covariate bar of 9.75 %), with incidence angle 0.3128, with brightness 0.3129, with mission 0.3260; isotonic 0.3511. Re-selection with one pass left out (18 runs) picks ratio in 6 runs, fill width with incidence angle in 4, fill width with brightness in 3, fill width alone in 2, fill width with dual_pol in 2 and loglinear without covariates in 1. Fill width is in 11 of the 18 picks, but its partner changes with the pass left out, and the full-data structure appears in only 2. The selection procedure scored out of sample (MAE 47.9 m) is worse than the fixed ratio form (42.9 m). The richer models do not win consistently, so the ratio form is applied. The first run of this script, which used forward selection and still held pair 08819, applied loglinear with fill width and brightness; that choice rested on a 6.4 % first-step gain, and without the one pair the gain falls to 4.2 %.

### 6.4 Sensitivity to the pair selection

| Fit set | n | Result |
|---|---|---|
| Open, stored match_quality high or medium | 23 | factor 1.721, interval 0.448 to 1.278; on AI2 MAE 39.0 m, within x2 92.7 %, coverage 66.1 % (narrower interval, lower coverage) |
| GFW, stored match_quality high or medium, ratio form | 49 on 16 passes | CV MAE 121.3 to 36.2 m, median ratio 1.734 to 0.993, within x2 67.3 % to 93.9 %, coverage 83.7 % |

The stored-quality sets look better in their own CV because the length bands have already removed the long tail; on the open data that tail is real (5 of 26 live pairs), and the AI2 coverage of the stored-quality open model drops to 66.1 %.

### 6.5 What the calibrations return on the project's contacts

| Contacts | Calibration | Calibrated | pixel_floor | below_range | short_return | below_min_claim | above_range | Median calibrated length |
|---|---|---|---|---|---|---|---|---|
| Live passes (8,573) | open | 55.0 % | 28.3 % | 8.6 % | 0 | 7.9 % | 0.1 % | 56.6 m |
| Regional run (78,615) | open | 43.8 % | 37.3 % | 10.8 % | 0 | 8.0 % | 0.1 % | 50.5 m |
| Live passes | research | 55.0 % | 28.3 % | 0 | 0 | 16.7 % | 0.03 % | 56.1 m |
| Regional run | research | 43.8 % | 37.3 % | 0 | 0 | 18.9 % | 0.02 % | 50.0 m |

(`on_project_contacts` in each json.) Both calibrations are ratio forms, which never make a hull longer than its return, so `short_return` does not occur. The open and research results differ mainly in the lower end of the valid range: the open pairs start at 38.3 m of radar length (`below_range` under it), the research pairs at 30 m (`below_min_claim` for the calibrated lengths under 25 m).

## 7. Limits

1. **No small boats.** The pairs hold 0 live, 1 GFW and 8 AI2 hulls under 25 m. Neither calibration makes a claim below 25 m or at the 2-pixel floor, which covers more than a third of all regional contacts. A dark lead at the floor has no calibrated length.
2. **The pairs are AIS ships.** Every pair is a vessel that transmits AIS and, for GFW, has a registry record: mostly steel ships of 50 to 230 m. A regression of hull on radar length reproduces the size distribution of its pairs, so for a population with more small boats (dark contacts are likely such a population, UNVERIFIED) a model with a slope under 1 runs long at the short end. The applied calibrations are ratio forms, which do not do this; the module still returns null where any model would make the hull longer than the return (`short_return`), for later fits with covariates or a slope. Under 100 m of radar length the pairs are thin (4 live, 4 GFW).
3. **One scene for the open model**, and an interval that moves with single pairs (section 6.1).
4. **Hull lengths are not ground truth.** AIS dimensions are self-reported; GFW registry lengths cover 1.6 % of GFW matches; AI2 lengths are label attributes of unstated origin (UNVERIFIED). None was checked against an independent register here.
5. **Pairing errors and length selection** remain in the GFW pairs (sections 3 and 4): the GFW assignment cost includes a length term, so 28 of the 62 GFW pairs were chosen partly on length agreement, which pulls their ratios toward 1 and narrows the research interval. The 0.25 feasibility rule removes the worst pairing errors. The live pairs were hand-checked (R3-T7) and the doubtful ones are excluded.
6. **Sensor and period.** AI2 is Sentinel-1A in 2022; the live and GFW pairs are 1C and 1D in September and October 2026. The mission covariate did not help in the research selection (section 6.3), which says little with 10 S1C pairs.
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
- Both calibrations of this run are ratio forms and need only `length_est_m`. A later calibration with covariates uses its fallback where a producer lacks their inputs; the live producer has them all (`scr_vv_db`, `scr_vh_db`, `n_pixels`, `pol_class`), the regional file `data/detections_regional.gpkg` has no `n_pixels` (it is in `data/detections_regional_all.gpkg`).
- Show the calibrated length with its interval ("calibrated radar length 120 m, 80 % interval 47 to 158 m") and the reason when it is null. Never show it as a measurement, and never below 25 m.
- Leads: use the interval, not the median, for any size rule, and say in the lead evidence that moving ships read longer.
- Matching: the live and GFW quality bands compare the raw ratio with fixed bands (0.25 to 4.0). A later matcher could test the AIS length against the calibrated interval instead; that is a matcher decision for the lead, not part of this task.
- Record `length_cal_id` with every output, and rerun `scripts/35_length_calibration.py` after each live pass with matches and after each change to the live review notes; the id changes when the calibration does.
- **For the open refit on AI2 plus live pairs (D7.2, R4-T2).** The live-only interval undercovers on AI2 (68.3 %) and moves with single pairs; the AI2 ratio form covers 79.8 % under grouped CV. Whether fill width earns its place is borderline (7 of 10 re-selections); run `selection_stability` on the combined set and keep the ratio form unless the richer structure is picked in at least 75 % of the runs.

## 9. Reproduce

```
/home/user/.mamba/envs/darkvessel/bin/python scripts/35_length_calibration.py      # about one minute
/home/user/.mamba/envs/darkvessel/bin/python -m pytest -q tests/test_length_cal.py
```

`--select stored` fits on the stored match_quality instead of the position quality. The orientation test reads the live object cache (`data/cache/live/scenes/`) and the aisstream position cache (`data/cache/ais/aisstream/positions/`) when present; the research covariates read `data/detections_regional_all.gpkg` when present (git-ignored). Without them those tests and covariates are reported as unavailable. The live pairs change when R3-T7 or a later pass changes the live files: `test_open_pairs_follow_the_current_live_review` fails when a used pair is now graded doubtful, an excluded doubtful pair is no longer doubtful, or a pair is no longer matched, and warns when matched pairs exist that the calibration does not hold. The numbers above are from the run of 2026-10-10 about 22:55 UTC, on the live files as regraded by R3-T7 at 17:51 UTC.

## 10. Sources

Resolved with curl in this session (2026-10-10):

- ESA SentiWiki, Sentinel-1 products, IW GRD specification: https://sentiwiki.copernicus.eu/web/s1-products
- AI2, vessel-detection-sentinels, README (label properties) and LICENSE (Apache-2.0): https://github.com/allenai/vessel-detection-sentinels , read at https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/README.md and https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/LICENSE
- aisstream.io API documentation (ShipStaticData `Dimension`): https://aisstream.io/documentation
- Mao, X. (2015). SAR imaging of moving target based on knowledge-aided two-dimensional autofocus. arXiv:1507.02150, https://arxiv.org/abs/1507.02150 (abstract)
- Lei, J., G'Sell, M., Rinaldo, A., Tibshirani, R. J. Distribution-free predictive inference for regression. Journal of the American Statistical Association, doi:10.1080/01621459.2017.1307116 (doi.org returned 403 from the publisher in the first run; record and abstract read at https://api.openalex.org/works/doi:10.1080/01621459.2017.1307116)
- Barber, R. F., Candes, E. J., Ramdas, A., Tibshirani, R. J. (2021). Predictive inference with the jackknife+. Annals of Statistics 49(1), doi:10.1214/20-AOS1965 (https://doi.org/10.1214/20-AOS1965 resolved; abstract read through OpenAlex)
- Stasolla, M., Greidanus, H. (2016). The exploitation of Sentinel-1 images for vessel size estimation. Remote Sensing Letters 7(12), 1219-1228, doi:10.1080/2150704X.2016.1226522 (doi.org returned 403 from the publisher in the first run; Crossref record at https://api.crossref.org/works/10.1080/2150704X.2016.1226522 and OpenAlex abstract read). For comparison only: on 127 ships of 24 to 366 m in five Sentinel-1 images at 20 m multi-look resolution, their morphology-based method reached about 15 % relative and 30 m absolute length error, better than a simpler automatic method. Their data and method differ from ours; the figures are not comparable one to one.
- Global Fishing Watch API licence and terms: https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits ; CC BY-NC 4.0: https://creativecommons.org/licenses/by-nc/4.0/

Not opened: ITU-R M.1371 (AIS message content). Statements that depend on it are marked UNVERIFIED.
