# Manuscript draft, paper 1 (letter)

Status: skeleton with the measured parts written out, 2026-10-03. Sections marked [PENDING] wait for the owner's labels (`docs/OWNER_ACTIONS.md`, action 4) or an AIS source (action 3). Target: IEEE Geoscience and Remote Sensing Letters, about 5 pages, or IGARSS 2027, 4 pages; the owner decides (`docs/OWNER_ACTIONS.md`, action 5). Design, power analysis and confounds: `docs/paper1_design.md`. Every number below comes from a file in this repository, named in the comment after it; remove the comments before submission.

References are numbered in order of first use. DOIs are taken from the OpenAlex snapshot of 2026-09-23 used for `docs/bibliometrics.md`; doi.org was blocked from the build environment, so they have not been resolved there. Resolve each one before submission.

---

## Does a Sentinel-1A-Trained Vessel Detector Transfer to Sentinel-1C and Sentinel-1D? A Measurement over the South China Sea

**Abstract.** Sentinel-1A, the source of most published Sentinel-1 vessel labels, produced its last interferometric wide-swath products in June 2026, and operational vessel monitoring now depends on Sentinel-1C and Sentinel-1D. Whether detectors trained on Sentinel-1A imagery keep their performance on the new satellites has not been reported. We apply a two-stage detector, cell-averaging constant false alarm rate (CA-CFAR) detection followed by a convolutional neural network (CNN) verifier trained on expert labels from Sentinel-1A and Sentinel-1B, to Sentinel-1C and Sentinel-1D ground range detected imagery of the South China Sea without retraining. On held-out Sentinel-1A/1B scenes the verifier raises precision from 0.09 to 0.77 at a recall of 0.75. The noise-equivalent sigma zero annotated in the products is 1.4 dB (VV) and 1.7 to 1.9 dB (VH) lower for Sentinel-1C and Sentinel-1D than for Sentinel-1A products of 2022, and equal between the two new satellites. On 373 grid cells imaged by both new satellites within one 12-day repeat cycle, candidate density and verifier acceptance agree within 6 % and 3 percentage points. [PENDING: verifier precision on Sentinel-1D and Sentinel-1C from hand labels, and the change against Sentinel-1A/1B.] The labels and code are released.

**Index Terms.** Sentinel-1C, Sentinel-1D, ship detection, synthetic aperture radar, constant false alarm rate, convolutional neural network, domain shift, noise-equivalent sigma zero.

### I. Introduction

Wide-swath C-band synthetic aperture radar (SAR) from Sentinel-1 is the backbone of open, large-area vessel monitoring. It has been used to map industrial fishing and energy activity at global scale [1], to reveal longline fleets that do not broadcast their positions [2], and to build the xView3-SAR benchmark for detecting vessels that do not appear in automatic identification system (AIS) records [3]. Detection performance has been measured against AIS for Sentinel-1 in European waters [4], [5] and for small vessels off Korea [6].

Nearly all of these labels and models come from Sentinel-1A and Sentinel-1B. Sentinel-1B failed in December 2021 (UNVERIFIED in this draft; cite the ESA statement), and the Amazon Web Services (AWS) mirror of the Sentinel-1 archive holds no Sentinel-1A interferometric wide-swath (IW) product after 29 June 2026 (`docs/data_landscape.md`; the ESA end-of-operations statement is UNVERIFIED). Sentinel-1C and Sentinel-1D now carry the operational load. They share the Sentinel-1 design, but a new instrument, a new calibration state and a newer processor version can each shift the image statistics on which a detector depends.

A scan of 4,328 works on SAR vessel detection, SAR and AIS fusion and dark vessels, published from 2015 to 2026, found no peer-reviewed vessel detection result on Sentinel-1C or Sentinel-1D imagery and no measurement of transfer within the mission (`docs/bibliometrics.md`, gap analysis; a 2025 conference presentation on Sentinel-1C ship detection is UNVERIFIED).

This letter asks whether a detector trained on Sentinel-1A/1B labels keeps its precision on Sentinel-1C and Sentinel-1D, and why it might not. Its contributions are: (i) the first measurement of vessel-detection precision on Sentinel-1C and Sentinel-1D imagery against hand labels; (ii) the annotated noise floor of the three satellites as an explanatory variable; (iii) a same-sea comparison of the two new satellites; and (iv) the release of the first public Sentinel-1C/1D vessel labels. A small change is itself a useful result, because it licenses the reuse of existing Sentinel-1A label sets on the new satellites.

### II. Data

**Study area.** The South China Sea, Gulf of Tonkin and Gulf of Thailand as delineated by Natural Earth marine areas, 3.58 million km2 (`docs/scs_regional.md`).

**Imagery.** Sentinel-1C and Sentinel-1D IW ground range detected high-resolution (GRDH) dual-polarization (VV and VH) products, read from the AWS Open Data mirror by HTTP range request. Over 90 days, 1,042 products in 280 passes touch the area; 55 % of it is imaged at least once and 45 % never (`data/s1_coverage.json`). Detection runs on one 12-day repeat cycle, 20 September to 1 October 2026: 119 scenes, 2.38 million km2 of sea tested (`data/regional_summary.json`). A Sentinel-1D scene off Ca Mau, Vietnam, of 29 September 2026 serves as the detailed test scene.

**Labels for training.** Expert point labels for Sentinel-1A/1B from the AI2 Skylight vessel-detection data set (Apache-2.0 [7]): 454 scenes from 2020 to 2022, 334 of them in Southeast Asia, with 69,274 CFAR candidates. Ninety-one scenes are held out whole for testing (1,422 labels) (`docs/ml_verifier.md`).

**Labels for testing on the new satellites.** [PENDING] Hand labels by one analyst on radar chips: every CNN-accepted contact in the Ca Mau scene (a census of 149) plus a hash-based random sample per detector class, and a regional sample from both satellites (`docs/paper1_design.md`, section 4).

### III. Methods

**A. Candidate detection.** CA-CFAR on calibrated intensity without thermal-noise subtraction, separately in VV and VH. Sea clutter is modelled as gamma distributed with one equivalent number of looks per scene; the false alarm rate is 1e-6; the guard window is 81 pixels (810 m) and the background window 161 pixels. Detections in the two channels within 30 m are fused. Candidates are graded as seen in both channels (high), in one channel (medium), or low (weak VV-only returns, objects longer than 450 m, candidates among five or more weak returns within 1 km, and candidates within 250 m of a fixed structure); objects that return on each of up to two earlier passes of the same orbit are marked fixed (`docs/scs_regional.md`).

**B. CNN verifier.** A 294,000-parameter CNN scores a 64 x 64-pixel (640 m) VV and VH chip around each candidate as vessel or not. It is trained from scratch on the AI2 labels with the held-out split above; the operating threshold (0.632) is set on the validation windows (`docs/ml_verifier.md`). It is applied to Sentinel-1C and Sentinel-1D without retraining or recalibration.

**C. Noise floor.** The noise-equivalent sigma zero (NESZ) is computed from each product's own annotation as the noise vector divided by the square of the sigma-nought calibration vector, binned by incidence angle in 1 degree steps, for 20 scenes per satellite: Sentinel-1A from 2022 in Southeast Asia, and Sentinel-1C and Sentinel-1D from the study area, July to October 2026 (`scripts/13_nesz_compare.py`).

**D. Same-sea comparison.** On 0.25 degree cells imaged by both new satellites in the 12-day cycle, candidate density per look and verifier acceptance are compared, and chip backgrounds are compared after matching on GFS 10 m wind at the radar time (`scripts/14_cnn_shared_cells.py`, `scripts/16_weather_context.py`).

**E. Scoring.** Precision is the share of labelled vessels among accepted contacts, with Wilson 95 % intervals. Shares per detector class use the hash-based random sample; the verifier's recall relative to the CFAR stage uses Horvitz-Thompson weighting with a bootstrap interval (`scripts/12_score_labels.py`). Recall against AIS by vessel length is reported only for Sentinel-1A/1B here, because no AIS source was available for the new satellites [PENDING].

### IV. Results

**A. Reference performance on Sentinel-1A/1B.** On the 91 held-out scenes, under a 50 m matching rule, CFAR alone reaches precision 0.090 [0.086, 0.095] at recall 0.797 [0.775, 0.817]; with the verifier, precision is 0.770 [0.748, 0.791] at recall 0.750 [0.727, 0.772] (`data/ml/metrics.json`). The held-out set holds no label shorter than 15 m and two between 15 and 25 m, so small-vessel recall is not measured (`data/ml/recall_by_length_test.csv`).

**B. Noise floor.** Table I. Relative to Sentinel-1A products of 2022, the annotated NESZ of Sentinel-1C is lower by 1.45 dB in VV (range over incidence bins 1.23 to 1.65 dB) and by 1.87 dB in VH (1.56 to 2.24 dB); that of Sentinel-1D is lower by 1.37 dB in VV and 1.74 dB in VH. Sentinel-1C and Sentinel-1D differ by 0.09 dB (VV) and 0.13 dB (VH) (`data/nesz_by_satellite.csv`; Fig. 1: `docs/figures/nesz_by_satellite.png`).

**Table I.** Annotated NESZ difference against Sentinel-1A (2022), median over incidence bins, dB.

| Satellite | VV | VH |
|---|---|---|
| Sentinel-1C | -1.45 | -1.87 |
| Sentinel-1D | -1.37 | -1.74 |
| Sentinel-1D minus Sentinel-1C | +0.09 | +0.13 |

**C. Sentinel-1C against Sentinel-1D on the same sea.** On 373 shared cells, candidate density is 38.8 (Sentinel-1C) and 41.1 (Sentinel-1D) per 1,000 km2 per look, the both-channel share 38.4 % and 38.8 %, and the median per-cell density ratio 1.02 (`docs/scs_regional.md`). The verifier accepts 0.601 [0.587, 0.616] and 0.629 [0.616, 0.641] of both-channel candidates (`data/ml/shared_cells_cnn.json`). Chip backgrounds rise with wind in VV (0.47 dB per m/s on Sentinel-1D, 0.71 dB per m/s on Sentinel-1C) and not in VH, where the noise floor dominates; within wind bins the Sentinel-1D minus Sentinel-1C VV difference changes sign, so no stable sensor offset is seen (`data/weather_context.json`).

**D. Precision on Sentinel-1D and Sentinel-1C.** [PENDING: Table II with per-class shares (vessel, structure, clutter, unsure) by satellite, and verifier precision on the Ca Mau census and the regional sample, against 0.77 on Sentinel-1A/1B. Power: 149 labelled accepted contacts detect a fall from 0.77 to 0.67 with 80 % power at the 5 % level (`docs/paper1_design.md`).]

**E. Where the verifier disagrees.** On the Ca Mau Sentinel-1D scene the verifier accepts 149 of 720 candidates, 27 % of both-channel candidates against about 77 % of both-channel candidates on held-out Sentinel-1A/1B scenes, and none shorter than 25 m. Chip backgrounds there are 3.6 dB (VV) and 3.7 dB (VH) darker than the training clutter (`docs/ml_verifier.md`). The lower noise floor accounts for about half of the VH offset. [PENDING: whether the rejected contacts are vessels, from the labels.]

### V. Discussion

[PENDING: rewrite once Section IV-D exists.] Three readings are open. If precision on the new satellites matches Sentinel-1A/1B, Sentinel-1A label sets can be reused as they are, and the lower acceptance off Ca Mau reflects the scene (many small boats and fixed gear) rather than the sensor. If precision holds but acceptance falls, the verifier is conservative on darker backgrounds and needs recalibration of its threshold, not new labels. If precision falls, a lower noise floor that makes clutter and small targets look different from the training data is the first candidate, and the remedy is fine-tuning on a few hundred new labels.

Two caveats bound the noise result. The annotation reflects both the instrument and the processor version, and the Sentinel-1A products date from 2022 while the new ones date from 2026; the letter measures the annotated noise floor that a detector sees, not instrument noise alone. A new Sentinel-1C radiometric calibration was reportedly deployed in February 2026 (UNVERIFIED); all Sentinel-1C scenes used here are later.

### VI. Conclusion

[PENDING: one paragraph with the transfer result.] The annotated noise floor of Sentinel-1C and Sentinel-1D is 1.4 to 1.9 dB below that of Sentinel-1A products of 2022, and the two new satellites behave alike on shared sea.

### Data and code availability

Code, labels and derived products: the project repository (to be archived with a DOI at submission). Sentinel-1 data: Copernicus, via the AWS Open Data mirror. AI2 Skylight labels: Apache-2.0 [7].

### References

[1] F. S. Paolo et al., "Satellite mapping reveals extensive industrial activity at sea," Nature, 2024, doi:10.1038/s41586-023-06825-8.

[2] D. A. Kroodsma et al., "Revealing the global longline fleet with satellite radar," Scientific Reports, 2022, doi:10.1038/s41598-022-23688-7.

[3] xView3-SAR: Detecting dark fishing activity using synthetic aperture radar imagery, Advances in Neural Information Processing Systems 35, 2022, doi:10.52202/068431-2726. [Complete the author list from the source.]

[4] R. Pelich et al., "Performance evaluation of Sentinel-1 data in SAR ship detection," in Proc. IGARSS, 2015, doi:10.1109/igarss.2015.7326217.

[5] R. Pelich et al., "Large-scale automatic vessel monitoring based on dual-polarization Sentinel-1 and AIS data," Remote Sensing, vol. 11, no. 9, 1078, 2019, doi:10.3390/rs11091078.

[6] D.-W. Shin, C.-S. Yang and S. J. K. Chowdhury, "Enhancement of small ship detection using polarimetric combination from Sentinel-1 imagery," Remote Sensing, 2024, doi:10.3390/rs16071198.

[7] Allen Institute for AI, vessel-detection-sentinels (Sentinel-1 labels and models), GitHub repository allenai/vessel-detection-sentinels, Apache-2.0 licence (`docs/data_landscape.md`).

---

Writing notes for the owner:
- GRSL letters run about five pages including figures; the draft above is near that length once [PENDING] parts are filled. IGARSS allows four pages.
- Figures planned: Fig. 1 NESZ by incidence angle (exists); Fig. 2 study area and coverage (exists, `docs/figures/coverage.png`); Fig. 3 verifier score distributions by satellite (to make after labels).
- Do not call any contact "dark" in the letter: no AIS is matched, and the letter measures detection only.
