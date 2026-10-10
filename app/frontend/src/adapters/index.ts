// Adapter choice at start (spec section 13): embedded when scs-part-meta exists, else HTTP.
import { EmbeddedAdapter, hasEmbeddedBundle } from "./embedded";
import { HttpAdapter } from "./http";
import type { DataAdapter } from "./types";

export function chooseAdapter(): DataAdapter {
  return hasEmbeddedBundle() ? new EmbeddedAdapter() : new HttpAdapter();
}

export type { DataAdapter } from "./types";
