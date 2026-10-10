// Entry: theme before React, adapter choice (embedded bundle or /api/v1), meta load, render.
import "normalize.css/normalize.css";
import "@blueprintjs/core/lib/css/blueprint.css";
import "@blueprintjs/select/lib/css/blueprint-select.css";
import "@blueprintjs/table/lib/css/table.css";
import "leaflet/dist/leaflet.css";
import "./theme/tokens.css";
import "./app/app.css";
import React from "react";
import { createRoot } from "react-dom/client";
import { chooseAdapter } from "./adapters";
import type { Meta } from "./adapters/types";
import { AppProvider } from "./app/state";
import { App } from "./app/App";
import { initTheme } from "./theme/theme";
import { preloadIcons } from "./theme/icons";
import { DARK_CAVEAT_SHORT, PRODUCT_CAVEAT } from "./app/text";

initTheme();
const root = createRoot(document.getElementById("root")!);
const adapter = chooseAdapter();

function Failed({ message }: { message: string }) {
  return (
    <div style={{ padding: 16, fontFamily: "sans-serif" }}>
      <div style={{ background: "#3e3224", color: "#fbb360", padding: "4px 16px" }}>{DARK_CAVEAT_SHORT}</div>
      <h2>SCS Vessel Watch</h2>
      <p>Could not load the data: {message}</p>
      <p>{adapter.kind === "http" ? "Start the local backend with make serve, or open the single-file page." : "The embedded bundle is missing or damaged."}</p>
      <p style={{ color: "#abb3bf" }}>{PRODUCT_CAVEAT}</p>
    </div>
  );
}

Promise.all([adapter.meta(), preloadIcons()]).then(
  ([meta]: [Meta, void]) => meta,
).then(
  (meta: Meta) => {
    root.render(
      <React.StrictMode>
        <AppProvider adapter={adapter} meta={meta}>
          <App />
        </AppProvider>
      </React.StrictMode>,
    );
  },
  (e: unknown) => {
    root.render(<Failed message={String((e as Error)?.message || e)} />);
  },
);
