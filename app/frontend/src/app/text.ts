// UI strings. Plain English. No em or en dashes. Never a vendor name (docs/product_design.md section 17).
import type { AisStatus, Confidence, LeadState } from "../adapters/types";

export const PRODUCT_NAME = "SCS Vessel Watch";
export const DARK_CAVEAT_SHORT = "Dark = no AIS match. Not evidence of illegal activity.";
export const PRODUCT_CAVEAT =
  "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not " +
  "required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite " +
  "AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every " +
  "unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent.";
export const GAP_NOTE = "An AIS gap is not proof of intent.";
export const OCEAN_NOTE = "Ocean and weather layers describe the sea, not what any vessel does.";
// darkvessel.ocean.grid.OCEAN_CAVEAT, and the anomaly sentence data/expected_activity.json adds to it.
export const OCEAN_CAVEAT =
  OCEAN_NOTE + " Expected activity says where lit boats or radar candidates usually are, given the sea and the weather; a cell " +
  "above or below it is a lead for review, not evidence. " + DARK_CAVEAT_SHORT;
export const ANOMALY_CAVEAT =
  "An activity anomaly is a difference between a count of detections in a cell and a model's expectation for that cell, night or " +
  "pass. It is not a count of vessels and not evidence of wrongdoing: model error, weather, cloud, moonlight, fleet movements and " +
  "the sensors' limits (unlit boats for VIIRS, small boats for radar) all produce it. A lead for review only.";
export const NO_CONTEXT = "No ocean context for this object yet";
export const DATA_CREDIT = "Contains modified Copernicus Sentinel data 2026";
// The research build's label and attribution are read from the build's meta (meta.build_label, research_label,
// attribution), so the shell shared by both builds carries no research source name.
export const BUILD_LINE = {
  open: "Open build. Open-licensed sources and live AIS relayed by aisstream.io.",
};
export const BUILD_TAG = { open: "OPEN BUILD", research: "RESEARCH BUILD" };
export const IDENTITY_NOTE = "All identity fields are self-reported by the transponder or published by the data source; they can be wrong, reused or spoofed.";
export const AISSTREAM_NOTE = "Live AIS relayed by aisstream.io; terms UNVERIFIED.";
/** Board D4.7: the label on every aisstream-derived identity in the open build. */
export const AISSTREAM_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED";
/** Board D6.2: a low-quality or doubtful pairing stays in the files and is shown with this label, never as an identification. */
export const LOW_QUALITY_LABEL = "low-quality pairing, identity not confirmed";
export const AMBIGUOUS_NOTE =
  "Ambiguous: the pairing could not tell which of these AIS vessels this return is (for example two ships alongside each other " +
  "give one return and two MMSIs). It is very likely one of them, so it names neither and is never a lead.";
/** A lead whose primary contact changed status after the lead was built (the leads file predates a rematch). */
export const STALE_LEAD_LABEL = "stale: not a lead";
export const STALE_LEAD_NOTE =
  "This lead was built before its contact was matched again and no longer stands, so it is not shown as a lead and takes no " +
  "decision. The next lead rebuild drops it.";
export const AIS_ONLY_NOTE =
  "AIS vessels placed inside the footprint at the scene time that no radar contact matched. Most lie in the 1 km shore buffer " +
  "or on water the detector does not test, are held back as ambiguous, or were dropped as oversized returns; it is a recall " +
  "check of the radar, not a finding about any vessel.";
export const EEZ_LAYER_NAME = "Maritime boundaries as published by Marine Regions";
export const EEZ_STATEMENT =
  "Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY 4.0, doi:10.14284/632. " +
  "In this sea many zones overlap or are disputed; the source marks them. This product takes no position on any boundary or claim.";
export const EEZ_DISCLAIMER =
  "VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning its delimitation, " +
  "frontier or borders. The data has no legal value whatsoever.";
export const EEZ_DISCLAIMER_URL = "https://www.marineregions.org/disclaimer.php";
export const REPORTING_BOX_NOTE = "reporting box: for statistics only, not a boundary";
export const STORAGE_WARNING = "Decisions are not kept in this browser. Use Export decisions before closing the page.";
export const LENGTH_NOTE = "pixel extent: crude and biased upward, a 2-pixel object reads 20 m";
export const CNN_LIMITS = "trained on Sentinel-1A/1B labels; 1C/1D transfer not yet scored; no training label under 15 m";
export const CNN_HELD_OUT = "held-out precision 0.77 and recall 0.75 on Sentinel-1A/1B labels (50 m rule)";
export const UNCALIBRATED = "uncalibrated";

export const AIS_STATUS_LABEL: Record<AisStatus, string> = {
  matched: "AIS matched",
  unmatched: "No AIS match (AIS heard nearby)",
  no_coverage: "No AIS coverage here",
  not_checked: "AIS not checked",
};
export const AIS_STATUS_SHORT: Record<AisStatus, string> = {
  matched: "matched",
  unmatched: "no AIS match",
  no_coverage: "no AIS coverage",
  not_checked: "not checked",
};
export const AIS_STATUS_MEANING: Record<AisStatus, string> = {
  matched: "paired one-to-one with an AIS vessel under the stated gate",
  unmatched: "no pair, but AIS was heard near the contact during the window; the only state that can become a dark lead",
  no_coverage: "no pair and nothing heard near the contact during the window; says nothing about the contact",
  not_checked: "no AIS source was applied to this run in this build",
};
export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "both channels",
  medium: "one channel",
  fixed: "fixed structure",
  low: "low",
};
export const STATE_LABEL: Record<LeadState, string> = {
  new: "new",
  reviewing: "reviewing",
  closed_explained: "closed, explained",
  closed_unexplained: "closed, unexplained",
  closed_false_alarm: "closed, false alarm",
};
export const EXPLAINED_REASONS = [
  "no carriage requirement (size or type)",
  "VMS fleet",
  "outside AIS reach",
  "weather or sea clutter",
  "fixed structure",
  "fishing lights",
  "pilot or supply transfer",
  "other (note required)",
];
export const FALSE_ALARM_REASONS = ["sea clutter", "rain cell", "fixed structure", "ambiguity or sidelobe", "duplicate", "other (note required)"];
export const LEAD_TYPE_NAME: Record<string, string> = {
  L1: "Unmatched radar contact in AIS reach",
  L2: "AIS silence at a radar look",
  L3: "Possible meeting with an unidentified contact",
  L4: "Encounter between two AIS vessels",
  L5: "Identity or position anomaly",
  L6: "Activity anomaly cell",
  L7: "Lit activity where radar does not look",
  L8: "Area entry",
};
export const LEAD_LAWFUL: Record<string, string[]> = {
  L1: ["no carriage requirement", "VMS fleet", "class B out of range", "detector false positive (precision 0.77 on Sentinel-1A/1B)"],
  L2: ["the contact may be another vessel", "the silence may be lawful"],
  L3: ["fishing, fuel and supply transfers are lawful"],
  L4: ["lawful transshipment and bunkering"],
  L5: ["GNSS and installation faults", "re-registration"],
  L6: ["model error", "weather", "fleet migration; counts, not vessels"],
  L7: ["mostly lawful fishing", "a coverage statement for tasking, not a vessel lead"],
  L8: ["EEZ crossings alone never make a lead"],
};
export const LIGHT_NOTE =
  "A light is not a vessel identity. Lit vessels are mostly fishing (squid fishing uses bright lights at night). Known false sources: " +
  "auroral and moonlit clouds, image artefacts, near-shore lights, lightning and gas flares; platforms show as recurring lights.";
export const NOT_BUILT = "not built yet";

// Lead factor names and the explanation and indicator codes the lead builder writes (codes and sentences from
// src/darkvessel/leads/priority.py and rules.py, documented in docs/leads.md). Unknown codes are shown humanised.
export const FACTOR_LABEL: Record<string, string> = {
  evidence_quality: "Evidence quality",
  corroboration: "Corroboration",
  ais_reach: "AIS reach quality at the spot",
  persistence: "Persistence across passes",
  area_weight: "Area weight",
  "weather unknown": "Weather unknown (lead kept, 0 weather points)",
};
export const LAWFUL_TEXT: Record<string, string> = {
  no_carriage_requirement:
    "No carriage requirement: the IMO AIS rule covers ships of 300 gross tonnage and upwards on international voyages, cargo ships of 500 " +
    "gross tonnage and upwards and passenger ships; most fishing boats and small craft on domestic voyages are outside it.",
  vms_fleet: "VMS fleet: in Vietnam fishing vessels of 15 m and over report by VMS, which this product does not see (UNVERIFIED).",
  class_b_out_of_range:
    "Class B out of range: class B transponders transmit at 5 W (2 W for carrier-sense units) against 12.5 W for class A (USCG NAVCEN " +
    "class comparison), so receivers hear them over a shorter range; satellite AIS misses messages in busy coastal waters.",
  lawful_switch_off:
    "Lawful switch-off: IMO Resolution A.1106(29) paragraph 22 lets the master switch AIS off where its operation might compromise the " +
    "safety or security of the ship.",
  detector_false_positive:
    "Detector false positive: the CNN verifier's held-out precision is 0.77 on Sentinel-1A/1B labels; sea clutter, rain cells, fixed " +
    "structures, sidelobes and ambiguities remain possible.",
  lawful_fishing_lights: "Lit activity at night is mostly lawful fishing; bright lights are used to attract squid and other species.",
  recurring_light_not_vessel: "Recurring lights can be platforms, flares, islands, navigation lights or anchorages, not vessels.",
  dnb_false_sources: "Moonlit clouds, lightning, auroral light and image artefacts are known false sources of Day/Night Band spikes.",
  coverage_statement_only: "This lead states a radar coverage gap for tasking. It is not a vessel lead and it names no vessel.",
};
export const CHANGE_TEXT: Record<string, string> = {
  late_ais_match: "An AIS match on the late re-check of recorded AIS (positions that arrived after the pass).",
  next_radar_look: "The next Sentinel-1 look at this spot: the contact is there again, or gone.",
  optical_view: "An optical view (Sentinel-2) of the spot within the same days.",
  chip_false_alarm: "A fixed-structure or clutter explanation on the radar chip (false alarm).",
  owner_label: "An owner label (vessel, structure, clutter, unsure), which also calibrates the weights.",
  radar_acquisition: "A Sentinel-1 acquisition whose footprint covers the cell turns the coverage gap into radar evidence.",
  more_clear_nights: "More clear nights with lights at the same spots strengthen the activity statement; clear nights without lights weaken it.",
  ais_reach_improves: "AIS reach improving in the cell (a receiver that hears it) allows light-to-AIS checks.",
};

/** Display text for a code: the map entry, the text itself when it is already a sentence, else the code humanised. */
export function codeText(code: string, map: Record<string, string>): string {
  if (map[code]) return map[code];
  if (/^[a-z0-9]+(_[a-z0-9]+)+$/.test(code)) return code.replace(/_/g, " ");
  return code;
}
export const CHIP_CAPTION = "VV (left) and VH (right), 64 x 64 px at the 10 m GRD pixel spacing (640 m). Radar geometry, not north-up.";
