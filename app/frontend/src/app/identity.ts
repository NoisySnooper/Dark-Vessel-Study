// Identification rules shared by the Contact page, the Pass page and the lead card (board D4.7 and D6.2).
// A match is shown as an identification only when it is high or medium quality and its hand check (review_note) is not
// doubtful; any other match keeps its row, its MMSI and its quality, and carries LOW_QUALITY_LABEL. An ambiguous contact
// (the pairing could not tell which of two or more AIS vessels it is) lists its candidates and is never a dark lead.
import type { Contact, Lead } from "../adapters/types";
import { AISSTREAM_LABEL } from "./text";

export type ReviewGrade = "confirmed" | "plausible" | "doubtful";

/** Grade of the hand check: the record's `review_grade`, else the `<grade>: <reason>` prefix of `review_note`. */
export function reviewGrade(c: Pick<Contact, "review_note" | "review_grade">): ReviewGrade | null {
  const g = typeof c.review_grade === "string" ? c.review_grade : /^\s*([a-z]+)\s*:/i.exec(String(c.review_note ?? ""))?.[1];
  const v = (g || "").toLowerCase();
  return v === "confirmed" || v === "plausible" || v === "doubtful" ? v : null;
}

/** The reason part of a hand-check note, without its grade. */
export function reviewReason(c: Pick<Contact, "review_note">): string | null {
  const n = typeof c.review_note === "string" ? c.review_note.trim() : "";
  if (!n) return null;
  const m = /^\s*[a-z]+\s*:\s*(.*)$/is.exec(n);
  return m ? m[1] : n;
}

/** Board D6.2: a matched contact whose pairing is low quality or graded doubtful by the hand check. */
export function lowQualityPairing(c: Pick<Contact, "ais_status" | "match_quality" | "review_note" | "review_grade">): boolean {
  if (c.ais_status !== "matched") return false;
  return c.match_quality === "low" || reviewGrade(c) === "doubtful";
}

/** Candidate MMSIs of an ambiguous contact (`ambiguous_mmsi`: one MMSI or several joined by ';' or ','). */
export function ambiguousCandidates(c: Pick<Contact, "match_ambiguous" | "ambiguous_mmsi">): string[] {
  const raw = c.ambiguous_mmsi;
  const list = Array.isArray(raw) ? raw.map(String) : typeof raw === "string" ? raw.split(/[;,\s]+/) : raw === null || raw === undefined ? [] : [String(raw)];
  return [...new Set(list.map((s) => s.replace(/\.0$/, "").trim()).filter((s) => /^\d{9}$/.test(s)))];
}

export function isAmbiguous(c: Pick<Contact, "match_ambiguous" | "ambiguous_mmsi">): boolean {
  return c.match_ambiguous === true || ambiguousCandidates(c).length > 0;
}

/** Why an L1 lead no longer stands against its primary contact's current record, or null when it stands. An L1 lead is
 * formed only from an unmatched, unambiguous contact; a leads file older than the live pass file's rematch can still cite
 * a contact that is now ambiguous, matched or without AIS coverage. Such a lead is stale and is never shown as a lead. */
export function staleLeadReason(lead: Pick<Lead, "lead_type" | "primary_type">, c: Contact | null | undefined): string | null {
  if (!c || lead.lead_type !== "L1" || lead.primary_type !== "contact") return null;
  if (c.ais_status === "unmatched" && isAmbiguous(c)) {
    const cand = ambiguousCandidates(c);
    return `its primary contact is now ambiguous between AIS vessels${cand.length ? ` (${cand.join(", ")})` : ""}, and an ambiguous contact never forms a lead`;
  }
  if (c.ais_status === "matched") return "its primary contact is now matched to an AIS vessel";
  if (c.ais_status === "no_coverage") return "its primary contact now has no AIS coverage, which never forms a lead";
  return null;
}

/** AIS ship type as displayed: type 0 is "not available" under ITU-R M.1371 (the producer writes it as "code 0"). */
export function shipTypeText(t: string | null | undefined): string | null {
  if (t === null || t === undefined || t === "") return null;
  return /^code 0+$/.test(String(t).trim()) ? "not reported (AIS type 0)" : String(t);
}

/** Board D4.7: the label every aisstream-derived identity carries (the record's own label when the API sends one). */
export function identityLabel(c: Pick<Contact, "ais_source" | "identity_label">): string | null {
  if (typeof c.identity_label === "string" && c.identity_label) return c.identity_label;
  return c.ais_source === "aisstream" ? AISSTREAM_LABEL : null;
}
