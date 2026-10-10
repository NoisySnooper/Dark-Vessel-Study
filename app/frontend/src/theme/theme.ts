// Theme: data-theme on <html> and Blueprint's bp6-dark on <body> in step (spec section 10). Dark is the default.
import { readItem, writeItem } from "../storage";

export type Theme = "dark" | "light";

export function currentTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  document.body.classList.toggle("bp6-dark", theme === "dark");
  document.body.style.background = theme === "dark" ? "#1c2127" : "#f6f7f9";
  writeItem("theme", theme);
}

export function initTheme(): Theme {
  const stored = readItem("theme");
  const theme: Theme = stored === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  document.body.classList.toggle("bp6-dark", theme === "dark");
  document.body.style.background = theme === "dark" ? "#1c2127" : "#f6f7f9";
  return theme;
}

export function cssToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
