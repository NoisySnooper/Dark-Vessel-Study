// Guarded browser storage (docs/product_design.md section 13). Every read and write is wrapped; a failure never breaks a view.
// Decisions and labels live in page memory first; storage is a convenience copy.

const PREFIX = "scs.";

let tested: boolean | null = null;

export function storageWorks(): boolean {
  if (tested !== null) return tested;
  try {
    const k = PREFIX + "probe";
    window.localStorage.setItem(k, "1");
    const ok = window.localStorage.getItem(k) === "1";
    window.localStorage.removeItem(k);
    tested = ok;
  } catch {
    tested = false;
  }
  return tested;
}

export function readItem(key: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

export function writeItem(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(PREFIX + key, value);
    return true;
  } catch {
    return false;
  }
}

export function readJson<T>(key: string, fallback: T): T {
  const raw = readItem(key);
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): boolean {
  try {
    return writeItem(key, JSON.stringify(value));
  } catch {
    return false;
  }
}
