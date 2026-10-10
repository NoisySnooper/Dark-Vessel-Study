// Hash router (spec section 3.2). Works in the single-file page without a server.
import { useEffect, useState } from "react";

export type RouteName = "leads" | "map" | "contact" | "vessel" | "light" | "event" | "lead" | "pass" | "cell" | "about";

export interface Route {
  name: RouteName;
  id: string | null;
  query: URLSearchParams;
  raw: string;
}

const OBJECT_ROUTES = new Set<RouteName>(["contact", "vessel", "light", "event", "lead", "pass", "cell"]);

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const segs = pathPart.split("/").filter(Boolean);
  const query = new URLSearchParams(queryPart);
  const head = (segs[0] || "leads") as RouteName;
  if (OBJECT_ROUTES.has(head)) {
    return { name: head, id: decodeURIComponent(segs.slice(1).join("/")) || null, query, raw };
  }
  if (head === "map" || head === "about") return { name: head, id: null, query, raw };
  return { name: "leads", id: null, query, raw };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function hrefFor(name: RouteName, id?: string | null): string {
  return id ? `#/${name}/${encodeURIComponent(id)}` : `#/${name}`;
}

export function navigate(name: RouteName, id?: string | null, query?: Record<string, string>): void {
  let h = hrefFor(name, id);
  if (query && Object.keys(query).length) h += "?" + new URLSearchParams(query).toString();
  if (window.location.hash !== h) window.location.hash = h;
}

export function isObjectRoute(name: RouteName): boolean {
  return OBJECT_ROUTES.has(name);
}
