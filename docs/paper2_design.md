# Paper 2 design: how many vessels does free SAR miss in the South China Sea?

Date: 2026-10-03 (UTC). Status: design and first numbers. Target venues (from `docs/journals.md`): Remote Sensing of Environment, then Fish and Fisheries, then ICES Journal of Marine Science. Gap analysis and closest prior work: `docs/bibliometrics.md`, "Paper 2".

> **"Dark" does not mean illegal.** In this paper "dark" means only that no AIS position was matched to a radar detection, and "missed" means only that free Sentinel-1 did not see the vessel. Neither says anything about the vessel's conduct. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters.

## 1. Question and claim

Question: of the vessels at sea in the South China Sea on a given day, what share does free Sentinel-1 SAR see, by vessel length?

Planned claim: the miss rate is set first by where and when the radar looks, then by vessel length, then by how the fleet is split across lengths. The first term alone, which no detector paper reports, is larger than the detector term for most of the sea. The paper reports a range per length bin, not a single count.

## 2. The miss budget

For a vessel of length L at position r on a given day:

P(seen) = C(r) x D(L, theta, wind)

- **C(r), coverage:** the probability that Sentinel-1 IW images position r on that day. Measured from the archive footprints.
- **D, detection:** the probability that the detector finds a vessel of length L at incidence angle theta and wind speed w, given that it was imaged. Needs truth.
- **F(L), fleet composition:** the share of vessels in each length bin. Turns per-vessel probabilities into a share of the fleet.

Miss share of bin L = 1 - mean over vessels in bin L of C(r) x D(L, ...). Over a window of T days, "never seen" = product over days of (1 - C_d(r) D), which depends on revisit, not only on mean coverage.

A fourth, independent term bounds activity without radar truth:

- **V, lit activity (VIIRS):** lights at sea per night in cells the radar never imaged. VIIRS sees only lit boats, so V is a lower bound on the activity outside radar coverage, not a vessel count.

## 3. What is measured now

| Term | Number | Source in this repo |
|---|---|---|
| C, share of AOI imaged at least once in 90 days | 55 % (1,042 IW products in 280 passes, 2026-07-04 to 2026-10-02) | `data/s1_coverage.json`, `docs/scs_regional.md` |
| C, area never imaged in 90 days | 1.61 million km2 (45 %), the whole central sea including the Spratly area | same |
| C, share of AOI imaged on an average day | 6.3 % | same |
| C, typical revisit where imaged | 8.8 days (median 8 passes in 90 days) | same |
| C, chance of a look within 7 days | AOI mean 34 %; 42 % of the AOI has an even chance or better; 7.9 % is looked at within every 7-day window | `data/s1_look_probability.json`, `docs/figures/look_probability.png` (`scripts/17_look_probability.py`) |
| C, chance of a look within 30 days | AOI mean 50 %; 42 % of the AOI is looked at within every 30-day window; 45 % never | same |
| D, recall by length on Sentinel-1A/1B, AI2 labels (held-out, 50 m rule) | CFAR: 0.94 at 25 to 50 m (15 of 16), 0.88 at 50 to 100 m (63 of 72); 2 labels at 15 to 25 m; none under 15 m | `data/ml/recall_by_length_test.csv`, `docs/ml_verifier.md` |
| D, Sentinel-1C/1D | not measured: no 1C/1D truth yet | owner labeling (precision only), AIS (recall) |
| F, Vietnamese fleet by length | not measured; one search snippet gives 79,360 registered vessels in November 2025 (UNVERIFIED) | `docs/bibliometrics.md`, Table 17 (e) |
| V, lit activity | 27-night run in progress (results pending) | `docs/viirs_lights.md`, `data/viirs_summary.json` |

## 4. What each term still needs

1. **C (done; extend).** Rerun the coverage count for a full year when compute allows, to show the seasonal pattern; add Sentinel-1 wave mode and EW mode to show what the central sea does get (`docs/bibliometrics.md`, Table 17 (f), UNVERIFIED snippet: wave mode is the default over open ocean).
2. **D above 15 to 20 m.** AIS-matched detections on the 119 regional scenes: match AIS to radar time, call a radar detection a hit within a distance rule, and fit recall against AIS length, incidence angle and GFS wind (wind is already attached to every object, `scripts/16_weather_context.py`). Needs an AIS source (owner actions 2 and 3).
3. **D under 15 m.** AIS cannot do it: most small Vietnamese boats need not carry AIS, and the compliance stream for 15 m and longer is VMS, which is not open (`docs/data_landscape.md`). Options, best first: (a) VMS or similar positions through a Vietnamese partner, under a data agreement; (b) AIS class B boats in the AIS feed, which are few but real; (c) the owner's hand labels of radar chips, which give precision, not recall. Report under-15 m recall as unknown if none of these comes through; do not fill it from other seas.
4. **F.** National registry statistics by length class for Vietnam, and FAO figures for the region, read from the source documents (hosts blocked now; UNVERIFIED snippets only).
5. **V.** Done for 27 nights (section 3). Next: the same nights from EOG's VIIRS Boat Detection product as an independent check (owner action 3), and a season of nights.

## 5. Analyses

- **A1 Coverage.** Done: map of passes per cell; share imaged by day and over 90 days; chance of at least one look within 1, 7 and 30 days per cell (COGs in `data/outputs/small/s1_look_prob_*`).
- **A2 Lit activity outside radar coverage.** Done for 27 nights: share of lit vessel candidates in cells with no Sentinel-1 pass in 90 days; lights per 1,000 km2 of clear sea in never-imaged against imaged cells; rank correlation of light density and radar density where both exist. Two confounds are handled: cloud (rates per km2 of clear sea, from the VIIRS cloud mask) and wind (the Gulf of Tonkin lit fleet all but vanished in a wind event in mid-September; `scripts/21_viirs_regions.py`). A season of nights is needed before V can stand for a typical night.
- **A3 Recall by length (AIS).** After AIS: logistic regression of hit or miss on log length, incidence angle and wind, per satellite (1C, 1D). Bootstrap by scene.
- **A4 Budget.** Monte Carlo over the three terms with their intervals; miss share per length bin (under 15 m, 15 to 25 m, 25 to 50 m, 50 m and longer) and per region (coastal ring, central sea).

## 6. Figures planned

1. Coverage: passes per cell in 90 days (`docs/figures/coverage.png`, done) and chance of a look within 1, 7 and 30 days (`docs/figures/look_probability.png`, done).
2. VIIRS lit-vessel density with the never-imaged area outlined (`docs/figures/viirs_lights.png`, done; outline to add).
3. Recall against length, per satellite (needs AIS).
4. The budget: one bar per length bin, split into not imaged, imaged but missed, and seen (needs A3 and F).

## 7. Threats

- **Global Fishing Watch could publish a Southeast Asian cut first.** Their SAR detections and AIS cover the region (Paolo 2024, global, over 15 m). Differentiation: the three-term budget, the under-15 m bin stated as unknown or measured with non-AIS truth, Sentinel-1C/1D, and an independent detector (`docs/bibliometrics.md`).
- **Under-15 m recall may stay unmeasurable.** Then the paper reports the coverage term and the 15 m and longer budget, and gives the under-15 m bin as a bound from lit activity only.
- **VIIRS and radar are hours apart** (about 00:00 to 03:00 against 04:00 to 07:00 and 16:00 to 19:00 UTC+7), so V is compared by area, never matched to radar contacts.
- **Fixed gear and aquaculture** inflate radar counts in coastal cells (`docs/scs_regional.md`); the budget must use verified or CNN-screened detections, not raw CFAR.

## 8. Timeline

The letter (paper 1) is the January 2027 target. Paper 2 follows: A1 and A2 are done; A3 starts the week an AIS source is connected; A4 needs F. A first full draft is realistic about three months after AIS access.
