# Target journals for the two planned papers

Updated: 2026-10-02. The owner reviews; the assistant did the research. The machine-readable version is `data/journals.csv` (22 venues, one row each). The table in this file is generated from that CSV by `scripts/journals_build.py`.

## Purpose

This file shortlists publication venues for two planned papers of the dark-vessel project. A dark vessel here is a vessel with a radar detection and no matched AIS position. Dark does not mean illegal.

1. **Letter.** Transfer of a ship detector from Sentinel-1A imagery to Sentinel-1C and 1D imagery. The project brief says Sentinel-1A ended operations on 30 June 2026; this file does not check that statement. Target: a short format. The brief named IEEE GRSL and IGARSS.
2. **Flagship paper.** How many vessels SAR misses in Southeast Asia, by vessel length bin (detection probability against length, from AIS-matched detections). Target tier: Remote Sensing of Environment or Fish and Fisheries.

Fit scores are judgements from 0 (not suitable) to 3 (strong) and rest on the facts in each CSV row. Read the Method section before relying on any number, because most venue facts could only be seen in search summaries.

**Short answer.** Letter: IEEE GRSL, IGARSS 2027, Remote Sensing Letters. Flagship: Remote Sensing of Environment, Fish and Fisheries, ICES Journal of Marine Science. Only the OpenAlex fields (publisher, ISSN, open access flags, APC, h-index) and the retraction counts are verified. SJR, quartiles, review times, page limits, AI policies and both integrity screens are not, because the hosts that hold them are blocked here. `scripts/journals_build.py` closes most of that gap once the network opens.

## Method

### Sources and their state

| Source | Used for | State |
|---|---|---|
| OpenAlex sources snapshot dated 2026-09-23, read on 2026-10-02 from `openalex.s3.amazonaws.com` (196 part files, 256,981 source records, 23 kept) | Publisher, ISSNs, fully-open-access flag, DOAJ flag, APC with price year, OpenAlex h-index, 2-year mean citedness | VERIFIED. I read the data. Cache: `data/journals/openalex_sources.csv` |
| Retraction Watch Database, Crossref copy on GitLab, dataset generated 2026-10-01 (72,831 rows) | Retraction counts per venue | VERIFIED. I read the file. Cache: `data/journals/retractions_by_venue.csv` |
| WebSearch result summaries and result links | SJR and quartiles, SCImago h-index, review times, article types and page limits, current APCs, AI policies, integrity searches | **UNVERIFIED (search snippet)**. The search tool returns a generated summary and a list of links. I did not open any of those pages. Summaries can be wrong. |

Every UNVERIFIED claim is tied to the result link that matches it, in the `sources` column of `data/journals.csv`. Where two searches returned the same value, the cell says so.

The WebSearch tool allowed 200 calls in this session and all 200 were used. Venues added later from the bibliometric scan therefore start as skeleton rows that carry no researched facts (see Open items).

### What was blocked

The egress proxy answered HTTP 403 to the CONNECT request for every host below when I tried it with curl or Python requests (WebFetch returned EGRESS_BLOCKED for `scimagojr.com`): `scimagojr.com`, `elsevier.com`, `sciencedirect.com`, `scopus.com`, `ieeexplore.ieee.org`, `ieee.org`, `journals.ieeeauthorcenter.ieee.org`, `grss-ieee.org`, `ieee-grss.org`, `2026.ieeeigarss.org`, `mdpi.com`, `onlinelibrary.wiley.com`, `link.springer.com`, `springernature.com`, `nature.com`, `science.org`, `frontiersin.org`, `tandfonline.com`, `academic.oup.com`, `ices.dk`, `retractionwatch.com`, `docs.google.com`, `openalex.org`, `docs.openalex.org`, `doaj.org`, `api.crossref.org`, `wikidata.org`, `archive.org`, `zenodo.org`, `wikipedia.org`, `kscien.org`, `beallslist.net`, and the third-party metric sites `journalmetrics.org`, `letpub.com` and `peeref.com`. The brief also names `doi.org` and `api.openalex.org` as blocked; I did not test those two. Reachable: `openalex.s3.amazonaws.com`, `gitlab.com`, `raw.githubusercontent.com`, `pypi.org`, and `api.github.com` for repository-scoped paths only. Consequences:

- No official SCImago file, so SJR, quartiles per category and the SCImago h-index are search snippets.
- No Scopus discontinued-sources list and no Hijacked Journal Checker data, so both integrity screens are NOT CHECKED.
- No publisher page was opened, so review times, article types, page limits, current APCs and AI policies are search snippets.

### How cells read

Most cells in `data/journals.csv` have the form `value | provenance`. The provenance names the source and the check state. Keys point into the `sources` column: `[oa]` OpenAlex snapshot, `[sc]` SCImago page, `[p1]` to `[p4]` publisher or society pages, `[x1]` to `[x4]` other pages, `[ai]` the publisher AI policy page.

`verification_status` is computed, never typed. A row is VERIFIED when no fact column carries the tags UNVERIFIED, NOT CHECKED or NOT RETRIEVED. It is PARTIAL when the publisher and ISSNs come from OpenAlex but another fact column still carries a tag. It is UNVERIFIED when even the publisher is a snippet. Today 21 rows are PARTIAL and the IGARSS row is UNVERIFIED, because OpenAlex has no publisher for IGARSS.

Three limits of the OpenAlex fields:

- OpenAlex does not label ISSNs as print or online. The labels in the CSV follow publisher convention (the ISSN-L is the print ISSN, a single ISSN on an open access title is online). They are not verified. Matching ignores the labels.
- I read the OpenAlex field `is_oa` as the fully-open-access flag; the OpenAlex documentation could not be opened. Venues with `is_oa` false and an APC listed are labelled hybrid by inference. Seed cells that say hybrid without a publisher page are tagged as inferred.
- The OpenAlex h-index is computed by OpenAlex and differs from the SCImago h-index. Both are shown, labelled.

### How to refresh

```
python scripts/journals_openalex.py      # OpenAlex rows (works now: 157 MB, 18 s here; 5 s with --cache-dir once the parts are cached)
python scripts/journals_retractions.py   # Retraction Watch counts (works now, 67 MB)
python scripts/journals_build.py         # SCImago, Scopus discontinued, Hijacked Journal Checker, rebuilds the CSV and this table
python scripts/journals_top_venues.py    # compares data/biblio/top_venues.csv with the shortlist, --add appends skeleton rows
```

`journals_build.py` downloads the SCImago CSV (`journalrank.php?out=xls`), finds the discontinued-sources workbook on the Scopus content policy page, finds the public Google Sheet on the Retraction Watch checker page, and matches every venue by ISSN (print, online and ISSN-L), then by normalised title. A title-only match is reported as a possible match. Each download is independent. A blocked host is reported with the host name and the option for a local file, the other sources still run, results of earlier successful runs are reused, and the exit code is 2. With no network, use `--offline`. To work from files downloaded elsewhere: `--scimago-csv`, `--scopus-xlsx`, `--hijacked-csv`.

To upgrade a cell that is still a snippet: open the cited page, confirm the value, and replace `UNVERIFIED (search snippet)` by `VERIFIED (page opened YYYY-MM-DD)` in `data/journals/seed.csv`, then run `journals_build.py`. Do not edit `data/journals.csv` by hand, it is rebuilt.

## Venue table

`*` after a quartile means SJR and quartile come from a search snippet (UNVERIFIED). Status: P is PARTIAL, U is UNVERIFIED, V is VERIFIED. The APC is the OpenAlex value in USD for price year 2025; for hybrid venues it is paid only if open access is chosen. `n/r` means not retrieved. Snippets showed higher prices than OpenAlex for eight venues (JAG USD 3610 against 3550, Science of Remote Sensing 2510 against 2180, ICES JMS GBP 2935 against 2795, Science Advances 5450 against 4500, and the 2026 IEEE list: GRSL and TGRS 2800 against 2645, JSTARS 2160 against 1800, IEEE Access 2160 against 2075), and one snippet was lower (RSE, USD 4230 against 4450; 4230 equals the OpenAlex value for 2024), so treat every APC as approximate until the 2026 price pages are read.

<!-- journals-table:begin -->
| Venue | Publisher | SJR (year), best quartile | OA model, APC (USD) | Letters or short type | Fit letter | Fit flagship | Status |
|---|---|---|---|---|---|---|---|
| Remote Sensing of Environment | Elsevier | 4.266 (SJR 2025), Q1* | hybrid, 4450 | yes: Short Communication, limit 5,000 words in one search | 0 | 3 | P |
| Fish and Fisheries | Wiley | 1.808 (SJR 2025), Q1* | hybrid, 5000 | no: no letter or short research type found | 0 | 2 | P |
| IEEE Geoscience and Remote Sensing Letters | IEEE | 1.296 (SJR 2025), Q1* | hybrid, 2645 | yes: Letters, max 5 pages | 3 | 0 | P |
| IEEE Transactions on Geoscience and Remote Sensing | IEEE | 2.649 (SJR 2025), Q1* | hybrid, 2645 | no: regular papers only | 1 | 2 | P |
| IEEE Journal of Selected Topics in Applied Earth Observations and Remote Sensing | IEEE | 1.452 (SJR 2025), Q1* | gold, 1800 | no letter type found | 1 | 2 | P |
| Remote Sensing (MDPI) | MDPI | 1.011 (SJR 2025), Q1* | gold, 3258 | yes: Technical Note (under 18 pages) and Letter | 2 | 2 | P |
| IGARSS proceedings (IEEE International Geoscience and Remote Sensing Symposium) | IEEE | 0.263 (SJR 2024), n/r* | n/a, none | yes: 4-page full paper | 3 | 0 | U |
| ISPRS Journal of Photogrammetry and Remote Sensing | Elsevier | 3.694 (SJR 2025), Q1* | hybrid, 3310 | partly: Perspective Papers, 4,000 words | 0 | 2 | P |
| International Journal of Applied Earth Observation and Geoinformation | Elsevier | 2.177 (SJR 2025), Q1* | gold, 3550 | not retrieved | 1 | 2 | P |
| Remote Sensing Letters | T&F | 0.434 (SJR 2025), Q2* | hybrid, n/r | yes: Letter, up to 10 printed pages | 2 | 0 | P |
| International Journal of Remote Sensing | T&F | 0.604 (SJR 2025), Q2* | hybrid, n/r | no: original research articles and review papers | 1 | 1 | P |
| Science of Remote Sensing | Elsevier | 1.897 (SJR 2025), Q1* | gold, 2180 | not retrieved | 1 | 2 | P |
| Marine Policy | Elsevier | 1.279 (SJR 2025), Q1* | hybrid, 4260 | yes: Short Communication, 1,500 to 2,500 words | 1 | 2 | P |
| Frontiers in Marine Science | Frontiers | 0.959 (SJR 2025), Q1* | gold, 3801 | yes: Brief Research Report, 4,000 words | 1 | 2 | P |
| ICES Journal of Marine Science | OUP | 1.072 (SJR 2025), Q1* | gold, 3686 | no: no research letter type | 0 | 2 | P |
| Fisheries Research | Elsevier | 0.752 (SJR 2025), Q1* | hybrid, 3680 | yes: Short Communication, max 6 printed pages | 1 | 2 | P |
| Ocean and Coastal Management | Elsevier | 1.332 (SJR 2025), Q1* | hybrid, 4350 | yes: Short Communication, about 2,000 words | 1 | 2 | P |
| Science Advances | AAAS | 4.534 (year not stated), Q1* | gold, 4500 | not retrieved | 0 | 1 | P |
| Nature Communications | Nature Portfolio | 4.904 (SJR 2025), Q1* | gold, 6990 | not retrieved | 0 | 1 | P |
| IEEE Access | IEEE | 0.884 (year not stated), Q1* | gold, 2075 | not retrieved | 1 | 1 | P |
| Sensors | MDPI | 0.802 (SJR 2025), Q1* | gold, 3137 | yes: Communication, limit not retrieved | 1 | 1 | P |
| Journal of Marine Science and Engineering | MDPI | 0.605 (SJR 2025), Q2* | gold, 3137 | yes: Communication, limit not retrieved | 1 | 2 | P |
| elib (German Aerospace Center) | Deutsches Zentrum für Luft- und Raumfahrt e. V. (DLR) | NOT RETRIEVED, n/r | not applicable, n/r | NOT RETRIEVED | 0 | 0 | P |
| IET conference proceedings. | Institution of Engineering and Technology | NOT RETRIEVED, n/r | NOT RETRIEVED, n/r | NOT RETRIEVED | ? | ? | P |
| DOAJ (DOAJ: Directory of Open Access Journals) | NOT RETRIEVED | NOT RETRIEVED, n/r | not applicable, n/r | NOT RETRIEVED | 0 | 0 | U |
| Zenodo (CERN European Organization for Nuclear Research) | European Organization for Nuclear Research | NOT RETRIEVED, n/r | not applicable, n/r | NOT RETRIEVED | 0 | 0 | P |
| arXiv (Cornell University) | Cornell University | NOT RETRIEVED, n/r | not applicable, n/r | NOT RETRIEVED | 0 | 0 | P |
| IEEE Transactions on Aerospace and Electronic Systems | IEEE | NOT RETRIEVED, n/r | NOT RETRIEVED, 2645 | NOT RETRIEVED | ? | ? | P |
<!-- journals-table:end -->

## Integrity screening

### What was and was not checked

| Check | Result | State |
|---|---|---|
| Scopus discontinued sources | Not run. `elsevier.com` is blocked. | NOT CHECKED for all 22 venues |
| Retraction Watch Hijacked Journal Checker | Not run. `retractionwatch.com` and `docs.google.com` are blocked. | NOT CHECKED for all 22 venues |
| Web searches for hijack, clone or fake-website reports naming each exact title (21 journals, plus IGARSS separately) | No report about any of these titles. Results were general articles only. | UNVERIFIED (search snippet). A search is not a list check. |
| Indirect Scopus signal: current Scopus-based data in the snippet | Present for 20 of 21 journals (for Remote Sensing Letters it is a Scopus CiteScore quoted by the publisher page) and for IGARSS (2,593 documents in 2024); none retrieved for Science Advances | UNVERIFIED (search snippet). Weak evidence. |
| ISSN uniqueness in OpenAlex | Each of the 35 ISSNs of the 21 journals maps to exactly one OpenAlex source. The two IGARSS ISSNs are not in OpenAlex at all. | VERIFIED |

The script performs both list checks as soon as the hosts are reachable. Until then, no venue is cleared and none is flagged.

### Why the screens match by ISSN and not by title

OpenAlex lists a second source called "Remote Sensing" from PiscoMed Publishing with ISSN 2315-4632 (source S4210172057), which is not the MDPI journal (ISSN 2072-4292). VERIFIED against the snapshot. Hijacked journals copy the title and the ISSN of the genuine journal (Retraction Watch description, UNVERIFIED (search snippet), https://retractionwatch.com/the-retraction-watch-hijacked-journal-checker/), so an ISSN hit in the hijack list means a clone exists, not that the genuine journal is bad, and an ISSN match alone never proves that a website is genuine.

### Look-alike sites to watch

- **MDPI titles (Remote Sensing, Sensors, JMSE).** MDPI states that fraudulent sites such as imdpi.com imitate its brand, and lists look-alike domains imdpi.com, mdpi.org and mdpi.net. The official site is https://www.mdpi.com, official email addresses end in @mdpi.com, and every genuine journal is listed at https://www.mdpi.com/about/journals. Announcement of 22 October 2025, https://www.mdpi.com/about/announcements/13625 (UNVERIFIED (search snippet)).
- **Elsevier titles (RSE, ISPRS, JAG, Science of Remote Sensing, Marine Policy, Fisheries Research, Ocean and Coastal Management).** Retraction Watch reported on 25 November 2024 a ring ("Springer Global Publication") that cloned journal sites of Elsevier, Springer Nature and others. The summary did not name my venues. https://retractionwatch.com/2024/11/25/exclusive-new-hijacking-scam-targets-elsevier-springer-nature-and-other-major-publishers/ (UNVERIFIED (search snippet)). Reach the submission page from the ScienceDirect journal page, never from an email or an advertisement.
- **IEEE titles and IGARSS.** No case naming these titles was found. IEEE societies warn that fake conference sites copy official conference domains (https://signalprocessingsociety.org/newsletter/2026/04/guide-recognizing-and-reporting-contact-fraud, UNVERIFIED (search snippet)). The IGARSS 2026 site was `2026.ieeeigarss.org` (seen in search text). Open the 2027 site from the GRSS event page, https://www.grss-ieee.org/event/2027-ieee-international-geoscience-and-remote-sensing-symposium/ (UNVERIFIED (search snippet)).
- **Scopus and MDPI.** A search summary says Scopus paused indexing of Sustainability (MDPI) and dropped Axioms (MDPI) after re-evaluation (UNVERIFIED (search snippet), https://retractionwatch.com/page/68/?ms=SIX). The real discontinued-list check therefore matters most for Remote Sensing, Sensors and JMSE.

### Retraction counts (supplementary, VERIFIED against the file)

Source: Retraction Watch Database, Crossref copy, generated 2026-10-01, matched by normalised exact journal name (IGARSS by pattern). This is context and not a ranking. Retraction Watch does not find every retraction, large journals have more notices than small ones, and a notice shows that the venue acted. A zero means no notice under that exact name in this file, not that none exists.

| Venue | Notices | Retractions | Retractions dated 2023-01-01 or later |
|---|---|---|---|
| Remote Sensing of Environment | 2 | 2 | 0 |
| ISPRS Journal of Photogrammetry and Remote Sensing | 4 | 4 | 0 |
| Remote Sensing Letters | 1 | 1 | 0 |
| International Journal of Remote Sensing | 1 | 1 | 0 |
| Remote Sensing (MDPI) | 9 | 8 | 4 |
| Marine Policy | 4 | 4 | 0 |
| Frontiers in Marine Science | 1 | 1 | 0 |
| ICES Journal of Marine Science | 1 | 1 | 0 |
| IEEE Access | 31 | 31 | 0 |
| Sensors | 12 | 12 | 3 |
| Science Advances | 15 | 10 | 3 |
| Nature Communications | 54 | 44 | 19 |

No notice found for Fish and Fisheries, GRSL, TGRS, JSTARS, JAG, Science of Remote Sensing, Fisheries Research, Ocean and Coastal Management, JMSE and IGARSS. Counts are small against each journal's output (OpenAlex works: 43,493 for Remote Sensing, 81,639 for Sensors, 92,847 for Nature Communications).

## Recommendations

Facts marked UNVERIFIED are search snippets; the URLs are in `data/journals.csv`.

### Letter (Sentinel-1A to 1C and 1D detector transfer)

1. **IEEE Geoscience and Remote Sensing Letters.** Built for this format: Letters of at most 5 pages on new ideas and timely results (UNVERIFIED), Q1 with SJR 1.296 (UNVERIFIED), hybrid, so an APC (USD 2800 on the 2026 IEEE list, USD 2645 in OpenAlex for 2025) is optional (UNVERIFIED), an average turnaround of about 30 days according to GRSS pages (UNVERIFIED), and an IGARSS paper can be extended later if at least 50% of the content is new (UNVERIFIED).
2. **IGARSS 2027 proceedings (Reykjavik, July 2027).** A 4-page full paper in IEEE Xplore suits a detector-transfer note and adds a talk (2026 rules, UNVERIFIED); the dates are not settled (11 to 16 July in two searches limited to grss-ieee.org and hi.is, 4 to 9 July in two earlier searches), the 2027 deadline is not known (2026 was 10 January 2026), SJR is only 0.263 (UNVERIFIED), and presenting first means a later GRSL version needs at least 50% new material.
3. **Remote Sensing Letters.** The other letters journal on the list besides GRSL (Letters of up to 10 printed pages, UNVERIFIED) and hybrid, so a paper can appear without an APC (the standard APC was not found); it is the fallback after GRSL and weaker on every metric: SJR 0.434, quartile Q2, 59 days to first decision, 38% acceptance (UNVERIFIED).

### Flagship (how many vessels SAR misses in Southeast Asia, by length bin)

1. **Remote Sensing of Environment.** Best scope fit: the remote sensing component must be the main contribution and single study sites are accepted if representative, and the journal has published SAR ship work (a 2023 Sentinel-1 ship velocity paper) (UNVERIFIED); Q1 with SJR 4.266 (UNVERIFIED); 15,000 word limit; slow, 193 days to acceptance, with desk decisions in about 4 days (UNVERIFIED); hybrid, APC USD 4450 only if open access is chosen (OpenAlex, price year 2025).
2. **Fish and Fisheries.** The fisheries-audience tier named in the brief: Q1 in four SCImago categories, SJR 1.808, median 10 days to first decision, 14% acceptance (UNVERIFIED); the journal requires broad geographic or taxonomic scope and generic value, so present Southeast Asia as the case study of a general SAR detection-probability method; the journal has published satellite vessel tracking papers, including a 2026 review (Raynor et al.) that cites the SAR finding that 72 to 76% of industrial fishing vessels are not publicly trackable by AIS, so read that review first (UNVERIFIED); hybrid, APC USD 5000 only if open access is chosen (OpenAlex and the Wiley page agree).
3. **ICES Journal of Marine Science.** The fisheries and marine science fallback if RSE judges the paper out of scope: fully open access since 2023 with an APC of GBP 2935, average 51 days to first decision in 2024, Original Article of 5 to 12 pages, Q1 with SJR 1.072, and it has published remote sensing and AIS fishing-activity papers in 2025 (all UNVERIFIED); no scope text was retrieved, so the fit is a judgement.

## Generative-AI disclosure rules

The author will use AI assistance. These rules come from search summaries (UNVERIFIED (search snippet)); read the live page at submission. All four publishers require disclosure beyond light language editing. MDPI, Taylor and Francis, OUP, Frontiers, AAAS and Springer Nature state that an AI system cannot be an author; the snippets for IEEE, Elsevier and Wiley did not say so. The Elsevier template statement and the MDPI policy put full responsibility for the content on the authors.

| Publisher (venues here) | Where and what to disclose | Light editing | Images and figures | Policy page |
|---|---|---|---|---|
| IEEE (GRSL, TGRS, JSTARS, IEEE Access, IGARSS) | Acknowledgments section. Name the AI system, say which sections contain AI-generated content (text, figures, images, code) and at what level. | Editing and grammar help is outside the policy; disclosure is recommended. | Images are covered by the same rule. | https://journals.ieeeauthorcenter.ieee.org/become-an-ieee-journal-author/publishing-ethics/guidelines-and-policies/submission-and-peer-review-policies/ |
| Elsevier (RSE, ISPRS, JAG, Science of Remote Sensing, Marine Policy, Fisheries Research, Ocean and Coastal Management) | Separate statement at submission, titled "Declaration of generative AI and AI-assisted technologies in the manuscript preparation process", printed in the article. Name the tool, the purpose and the oversight. | Basic grammar, spelling and punctuation checks need no declaration. Substantive changes to sentences or structure do. | No AI creation or alteration of images, including graphical abstracts. Allowed: concept images (disclose in the caption and the statement) and data plots made reproducibly from the data. | https://www.elsevier.com/about/policies-and-standards/generative-ai-policies-for-journals |
| Wiley (Fish and Fisheries) | Methods section, or a disclosure statement, or Acknowledgements, described transparently and in detail. Keep records of purpose, influence on key arguments and how output was checked. | Copyediting needs no disclosure. | Not retrieved. | https://www.wiley.com/en-us/publish/article/ai-guidelines/ |
| MDPI (Remote Sensing, Sensors, JMSE) | Cover letter at submission and the Acknowledgements, with tool details and use. | Formatting, refining and editing need no declaration. | No generative AI to create or enhance research results, images or visualisations of data such as graphs. Concept figures are discouraged. | https://www.mdpi.com/ethics |

Working rules for this project:

1. Keep an AI-use log from now on: tool, version, date, purpose, file or section. Every policy above asks for these details.
2. IEEE counts code as AI-generated content. If an assistant wrote analysis code that produced reported results, say so in the IEEE acknowledgments. The snippets do not say whether Elsevier, Wiley or MDPI want the same for code; state it anyway.
3. Make every figure from scripts. Elsevier allows data plots made reproducibly from data; the MDPI sentence on data visualisations is broad, so state that plots come from scripts and not from an image model. Do not use a generative tool on SAR chips or detection images.
4. IEEE tells reviewers not to process manuscript content through public AI platforms. Apply the same rule to any manuscript or review material you handle.
5. Rules for the provisional venues (Taylor and Francis, OUP, Frontiers, AAAS, Springer Nature) are summarised in the `ai_disclosure_policy` column of `data/journals.csv`. Science journals forbid AI-generated images without editor permission.

## Open items

1. Run `scripts/journals_build.py` with open network access, or pass the three files by hand. This replaces the SJR, quartile and SCImago h-index snippets with the official values and runs both integrity screens. Quartile lists per category are incomplete for most venues until then.
2. Open the cited pages for the six recommended venues first and upgrade their seed cells: review times, article types and limits, 2026 APCs, AI policies.
3. IGARSS 2027: the dates conflict between searches (11 to 16 July or 4 to 9 July 2027, Reykjavik), and the call for papers, the full-paper deadline, the paper kit and the registration fee were not found. The 2026 deadline was 10 January 2026. Check whether the J-STARS special issue for IGARSS authors (seen for IGARSS 2025, with papers expected to be 2 to 3 times longer than the conference paper, UNVERIFIED, https://www.grss-ieee.org/publications/call-for-papers/jstars-special-issues/) will run again.
4. The GRSL turnaround of about 30 days is a society statement without a year or a definition (UNVERIFIED). GRSL takes part in IEEE Transparent Peer Review (UNVERIFIED): find out what is published with an accepted paper before submitting. TGRS: the GRSS page gives an acceptance rate below 30% and a blog gives 50 to 60%, so the blog figure is not used.
5. TGRS and JSTARS page limits need confirmation. The JSTARS APC was USD 1800 for submissions from 1 January 2025 and is USD 2160 on the 2026 IEEE list, with subsidised pricing that moves towards the standard fee each year (UNVERIFIED). The TGRS mandatory overlength charge of USD 230 per page applies to submissions after 1 January 2026 above 10 pages (UNVERIFIED).
6. Remote Sensing Letters: the standard Open Select APC was not found. Letter limits for MDPI Remote Sensing were not found.
7. Science Advances SJR and h-index are weakly supported: the search results did not include the SCImago page of that journal.
8. The IGARSS row has no OpenAlex publisher or APC data because OpenAlex splits the series into fragmentary records (S4306419991, S4363604196).
9. SCImago year labels are missing for Science Advances and IEEE Access; the IGARSS SJR is labelled 2024 and its h-index is given as 87 or 94 by two parts of one summary.
10. A search on SAR ship detection and maritime monitoring special issues returned MDPI pages in Remote Sensing, JMSE and Sensors, with deadlines that the summary gave as already past for the Remote Sensing ones (UNVERIFIED; topic and deadline of each page not checked): https://www.mdpi.com/journal/remotesensing/special_issues/SAR_SeaSurface, https://www.mdpi.com/journal/jmse/special_issues/9LHKI2WSW6, https://www.mdpi.com/journal/sensors/special_issues/RS_VDN. Look for current ones if an MDPI route is chosen.
11. The bibliometric scan of the project had not written `data/biblio/top_venues.csv` when this file was finished. When it does, run `python scripts/journals_top_venues.py` to list which of its top 15 venues are missing here, and `--add` to append skeleton rows. A skeleton row holds identity only and marks every other fact NOT RETRIEVED until someone researches it.
12. Novelty of the Letter is not checked here. One search found no paper on detector transfer from Sentinel-1A to 1C or 1D, only general cross-sensor SAR ship detection domain-adaptation work (UNVERIFIED). Settle it with the bibliometric scan before the novelty claim is written.
13. Fit scores are judgements. The owner should overrule them where the project knows better, in particular the Fish and Fisheries scope question.
