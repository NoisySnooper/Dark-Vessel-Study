// Layer registry for the Layers tab (spec section 6.2): name, default, legend, licence source key and notes.
import type { LayerId } from "../app/state";
import { EEZ_LAYER_NAME, REPORTING_BOX_NOTE } from "../app/text";

export interface LayerInfo {
  id: LayerId;
  name: string;
  source: string; // source registry key for the provenance chip
  legend: string;
  note?: string;
}

export const LAYER_INFO: LayerInfo[] = [
  { id: "land", name: "Land", source: "natural_earth", legend: "Natural Earth 10 m land, public domain" },
  { id: "aoi", name: "AOI outline", source: "natural_earth", legend: "South China Sea, Gulf of Tonkin and Gulf of Thailand (Natural Earth marine areas)" },
  { id: "contacts", name: "Radar contacts", source: "det_live", legend: "circle = both channels, ring = one channel, square = fixed; colour = AIS status; 50 % opacity when the CNN rejects" },
  { id: "leads", name: "Leads", source: "app", legend: "2 px ring around the primary object" },
  { id: "vessels", name: "AIS vessels, last position", source: "aisstream", legend: "class B drawn smaller; live AIS relayed by aisstream.io, terms UNVERIFIED" },
  { id: "footprints", name: "Scene footprints", source: "s1_grd", legend: "dashed outline of the processed scenes" },
  { id: "structures", name: "Fixed structures", source: "det_regional", legend: "square markers; persistent bright returns" },
  { id: "lights", name: "VIIRS lights", source: "viirs_dnb", legend: "gold dots; 50 % opacity under cloud; a light is not a vessel identity" },
  { id: "tracks", name: "AIS tracks", source: "aisstream", legend: "simplified recorded tracks; an AIS gap is not proof of intent" },
  { id: "next_passes", name: "Next planned passes", source: "esa_acq_plan", legend: "dashed footprints from the ESA acquisition plan; a repeat prediction is not ESA's plan" },
  { id: "reporting_boxes", name: "Reporting boxes", source: "app", legend: REPORTING_BOX_NOTE },
  { id: "eez_boundaries", name: EEZ_LAYER_NAME, source: "marineregions_v12", legend: "thin neutral lines, no fill, no labels; as published by Marine Regions, no position taken", note: "off by default" },
];
