# Owner actions

Updated: 2026-10-03 (UTC). Only you can do these. Everything else runs without you. Do them in the order below; times are your effort, not waiting time.

> Never paste a key, token or password into the chat. Keys go into the cloud environment settings as environment variables; a new session picks them up.

## At a glance

| # | Action | Your time | What it unlocks | Status |
|---|---|---|---|---|
| 1 | Open network access | 10 min | Verification of about 100 UNVERIFIED facts; STAC search; GFW, NASA and EOG data | Open |
| 2 | Apply for accounts (GFW, Copernicus, OpenAlex, NASA Earthdata, EOG) | 45 min, then waiting | API keys for items 3 and 6 | Open |
| 3 | Add the keys as environment variables | 10 min | AIS (dark labels), Copernicus S3, OpenAlex API | Waiting on 2 |
| 4 | Label the Ca Mau queue on the demo page | 3 h | First Sentinel-1D truth; scores the CNN for paper 1 | Open |
| 5 | Make four decisions | 1 h plus reading | AIS source, letter venue, map naming, commit author | Open |
| 6 | Label the regional queue | 1 h | Sentinel-1C truth for paper 1 | Later week |
| 7 | Share the demo page, if wanted | 2 min | Reviewers can open it | Optional |

## 1. Open network access (10 min)

Most research and data hosts are blocked from this cloud environment (`docs/STATUS.md`, Blocked).

Steps: open the cloud environment menu in the session's title bar, then Edit, then Network access. Choose "Full", or "Custom" and add the hosts below under Allowed domains, keeping the default package-manager list. Instructions: https://code.claude.com/docs/en/cloud-environments#network-access

Hosts, grouped by what they unlock:
- Literature and venue checks: `api.openalex.org`, `openalex.org`, `doi.org`, `api.crossref.org`, `www.scimagojr.com`, `www.scopus.com`, `retractionwatch.com`, `ieeexplore.ieee.org`, `www.sciencedirect.com`, `www.mdpi.com`, `onlinelibrary.wiley.com`, `link.springer.com`, `www.nature.com`
- Copernicus (official Sentinel archive and STAC): `dataspace.copernicus.eu`, `catalogue.dataspace.copernicus.eu`, `stac.dataspace.copernicus.eu`, `identity.dataspace.copernicus.eu`, `eodata.dataspace.copernicus.eu`, `sentiwiki.copernicus.eu`
- AIS: `gateway.api.globalfishingwatch.org`, `globalfishingwatch.org`
- Night lights and NASA data: `eogdata.mines.edu`, `payneinstitute.mines.edu`, `urs.earthdata.nasa.gov`, `ladsweb.modaps.eosdis.nasa.gov`, `search.asf.nasa.gov`, `datapool.asf.alaska.edu`
- Other: `planetarycomputer.microsoft.com`, `earth-search.aws.element84.com`, `huggingface.co`, `zenodo.org`, `www.ncei.noaa.gov`

Hosts for optional extra sources (Black Marble, SDGSAT-1, scatterometer winds, other SAR programmes, Vietnamese portals) are listed in `docs/data_additions.md`, section "Hosts the owner would need to allow". None of them is needed for the core pipeline.

## 2. Apply for accounts (45 min, then waiting)

Start these now: approval can take days.

| Account | Where | Cost | Gives | Licence note |
|---|---|---|---|---|
| Global Fishing Watch API | globalfishingwatch.org, API access page | Free | AIS positions and GFW vessel data | Noncommercial (CC BY-NC 4.0, per `docs/data_landscape.md`): fine for the papers, not for anything Viettel-facing |
| Copernicus Data Space | dataspace.copernicus.eu | Free | Official Sentinel archive, S3 keys, STAC | Copernicus terms allow commercial use |
| OpenAlex | openalex.org | Free | API key (the API reportedly needs a key since 2026; UNVERIFIED, `docs/data_landscape.md` F02) | Open data |
| NASA Earthdata Login | urs.earthdata.nasa.gov | Free (UNVERIFIED) | Black Marble night lights, NISAR, ASF | Check the terms when you sign up |
| Earth Observation Group | eogdata.mines.edu | Free registration (UNVERIFIED) | Ready-made VIIRS Boat Detection (VBD) | Licence per subset, partly CC BY 4.0 (UNVERIFIED) |

## 3. Add the keys as environment variables (10 min)

Steps: cloud environment menu, then Edit. Add each key under API credentials where offered, otherwise as an environment variable with exactly these names:

| Variable | From |
|---|---|
| `GFW_API_TOKEN` | Global Fishing Watch |
| `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY` | Copernicus Data Space (S3 credentials) |
| `OPENALEX_API_KEY` | OpenAlex |
| `EARTHDATA_TOKEN` | NASA Earthdata Login (user token) |
| `EOG_USER`, `EOG_PASSWORD` | Earth Observation Group, if you want their VBD product |

Then start a new session or tell me, and I will:
- pull AIS for the regional window and label dark candidates (with the caveat on every row);
- cross-check the scene list against Copernicus;
- verify the SJR, quartile and integrity items in `docs/journals.md`;
- compare the project's own VIIRS lights with EOG's VBD product.

## 4. Label the Ca Mau queue (3 h)

1. Open the demo page: https://claude.ai/artifact/5VbpXaoghkKx8nwGoaEeUs
2. Click the tab "Scene detail: Ca Mau".
3. Press N to step through the queue: 384 contacts, each with a radar chip. That includes all 149 the CNN accepts, so its precision on Sentinel-1D is measured on all of them.
4. Press 1 to 4 for vessel, structure, clutter or unsure, about 30 s each. "Unsure" is fine; do not guess.
5. Press "Copy labels as CSV" and save the text as `data/labels/owner_2026-10.csv`, or send it to me.

Labels live only in your browser until you copy them. The sample rule is in `docs/paper1_design.md`, section 4; `scripts/12_score_labels.py` scores them.

## 5. Make four decisions (1 h plus reading)

Read `docs/STATUS.md`, `docs/scs_regional.md`, the gap analysis at the end of `docs/bibliometrics.md` and the short answer in `docs/journals.md`. Then decide:

1. **AIS source.** GFW (noncommercial) for the papers, and whether to request a commercial quote (Spire or exactEarth) for anything Viettel-facing.
2. **Letter venue.** IGARSS 2027 or IEEE GRSL. The IGARSS 2027 deadline is not published; the 2026 deadline was 10 January 2026 (UNVERIFIED), so plan for early January 2027.
3. **Map naming.** "South China Sea" (current) or "East Sea (Bien Dong)" for a Vietnamese audience.
4. **Commit author.** Commits use the container default author. Give a name and email if you want them under yours.

## 6. Label the regional queue (1 h, a later week)

Same page, tab "Regional". The queue holds 700 contacts from both satellites; about 100 is enough for a first Sentinel-1C estimate.

## 7. Share the demo page (optional, 2 min)

The page is private. Use its Share menu to give reviewers access.

## What does not need you

Everything else continues without you: regional runs, VIIRS night lights, weather context, the Sentinel-2 optical check, the CNN, figures, docs and the demo page. Progress is in `docs/STATUS.md`. To rerun anything yourself: `make test`, `make regional`, `make context`, `make demo OUT=page.html` (see the `Makefile`).

Nothing new is needed from you after the 2026-10-03 additions (VIIRS, Sentinel-2, weather, look probability): all of them use open buckets that this environment already reaches.
