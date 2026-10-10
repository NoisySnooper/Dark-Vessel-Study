// App shell (spec sections 2 and 3): caveat banner top and bottom on every view, navbar with the product name, build tag,
// Omnibar trigger, time zone, units, theme toggle and the shortcuts dialog; hash routes; phone bottom tab bar via Console.
import { useMemo } from "react";
import { Button, ButtonGroup, HotkeysProvider, Navbar, NavbarDivider, NavbarGroup, NavbarHeading, Popover, Tag, useHotkeys, type HotkeyConfig } from "@blueprintjs/core";
import { useApp } from "./state";
import { useRoute, navigate, isObjectRoute } from "./router";
import { BUILD_LINE, BUILD_TAG, DARK_CAVEAT_SHORT, DATA_CREDIT, PRODUCT_NAME } from "./text";
import { Console } from "../views/Console";
import { AboutPage } from "../views/AboutPage";
import { CellPage, ContactPage, EventPage, LeadPage, LightPage, PassPage, VesselPage } from "../views/ObjectPages";
import { OmnibarSearch } from "../search/OmnibarSearch";

function Banner({ position }: { position: "top" | "bottom" }) {
  const { meta, phone } = useApp();
  const research = meta.build === "research";
  // The research label, attribution and its link come from the build's meta (API envelope or bundle meta part), so the
  // shell shared by both builds names no research source itself (spec section 2.2).
  const line = meta.build_label || (research ? meta.research_label || "" : BUILD_LINE.open);
  const attribution = research ? meta.attribution || null : null;
  const attrSource = meta.sources.find((s) => s.research_only && s.url);
  let attrHref: string | null = null;
  try {
    attrHref = attrSource?.url ? new URL(attrSource.url).origin : null;
  } catch {
    attrHref = null;
  }
  const full = (
    <div className="scs-prov-pop" style={{ maxWidth: 420 }}>
      <strong>Dark does not mean illegal.</strong>
      <p style={{ margin: "6px 0 0" }}>{meta.caveat}</p>
    </div>
  );
  return (
    <div className={"scs-banner scs-banner-" + position} role="note" aria-label="Handling banner" data-banner={position}>
      {research && <Tag className="scs-research-tag" minimal data-research-label="1">{meta.research_label || "Research build"}</Tag>}
      <span className="scs-banner-short" data-caveat="short">{DARK_CAVEAT_SHORT}</span>
      <Popover content={full} interactionKind="click" placement={position === "top" ? "bottom" : "top"} fill={false}>
        <button type="button" aria-label="Read the full caveat" data-caveat-link="1">full caveat</button>
      </Popover>
      {!phone && <span className="scs-build-line">{(attribution ? line.replace(attribution, "") : line).replace(` ${DARK_CAVEAT_SHORT}`, "").trim()}</span>}
      {attribution && (attrHref ? <a href={attrHref} target="_blank" rel="noreferrer" data-attribution="1">{attribution}</a> : <span data-attribution="1">{attribution}</span>)}
      {position === "bottom" && <span className="scs-muted" style={{ color: "inherit", opacity: 0.85 }}>{DATA_CREDIT}</span>}
    </div>
  );
}

function TopNav() {
  const { meta, theme, setTheme, tz, setTz, units, setUnits, setOmnibarOpen, phone, timelineOpen, setTimelineOpen, inspectorOpen, setInspectorOpen } = useApp();
  const research = meta.build === "research";
  return (
    <Navbar className="scs-navbar" aria-label="Main">
      <NavbarGroup align="start">
        <NavbarHeading><a href="#/leads" style={{ color: "inherit", textDecoration: "none" }}>{PRODUCT_NAME}</a></NavbarHeading>
        <Tag className={"scs-build-tag" + (research ? " scs-research-tag" : "")} minimal={!research} data-build-tag={meta.build}>{BUILD_TAG[meta.build]}</Tag>
        {meta.fixture && <Tag intent={meta.fixture.synthetic ? "danger" : "warning"} minimal style={{ marginLeft: 6 }} data-fixture-tag="1">FIXTURE</Tag>}
        {!phone && <NavbarDivider />}
        {!phone && <ButtonGroup minimal>
          <Button className="scs-navbtn" icon="inbox" text="Leads" aria-label="Leads" onClick={() => navigate("leads")} />
          <Button className="scs-navbtn" icon="map" text="Map" aria-label="Map" onClick={() => navigate("map")} />
          <Button className="scs-navbtn" icon="info-sign" text="About" aria-label="About" onClick={() => navigate("about")} />
        </ButtonGroup>}
      </NavbarGroup>
      <NavbarGroup align="end">
        <Button className="scs-omnibar-trigger" icon="search" minimal text={phone ? undefined : "Search: MMSI, id, name, coordinates"} aria-label="Open search" onClick={() => setOmnibarOpen(true)} rightIcon={phone ? undefined : <kbd className="bp6-key">mod K</kbd>} data-omnibar-trigger="1" />
        {!phone && <NavbarDivider />}
        <Button minimal small text={tz} aria-label={`Time zone ${tz}, click to switch`} onClick={() => setTz(tz === "UTC" ? "ICT" : "UTC")} title="UTC or ICT (UTC+7)" />
        <Button minimal small text={units} aria-label={`Units ${units}, click to switch`} onClick={() => setUnits(units === "km" ? "nm" : "km")} title="kilometres or nautical miles" />
        <Button minimal small icon={theme === "dark" ? "flash" : "moon"} aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"} onClick={() => setTheme(theme === "dark" ? "light" : "dark")} data-theme-toggle="1" />
        {!phone && <Button minimal small icon="panel-stats" aria-label="Toggle the inspector (I)" active={inspectorOpen} onClick={() => setInspectorOpen(!inspectorOpen)} />}
        {!phone && <Button minimal small icon="timeline-events" aria-label="Toggle the timeline (T)" active={timelineOpen} onClick={() => setTimelineOpen(!timelineOpen)} />}
        <Button minimal small icon="help" aria-label="Keyboard shortcuts (?)" onClick={() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "?", shiftKey: true, bubbles: true }))} />
      </NavbarGroup>
    </Navbar>
  );
}

function Routes() {
  const route = useRoute();
  const id = route.id || "";
  switch (route.name) {
    case "about": return <AboutPage />;
    case "contact": return <ContactPage id={id} />;
    case "vessel": return <VesselPage id={id} />;
    case "light": return <LightPage id={id} />;
    case "event": return <EventPage id={id} />;
    case "lead": return <LeadPage id={id} />;
    case "pass": return <PassPage id={id} />;
    case "cell": return <CellPage id={id} />;
    case "map": return <Console focusMapTab />;
    default: return <Console focusMapTab={false} />;
  }
}

function GlobalHotkeys() {
  const { setOmnibarOpen, setRailTab, setInspectorOpen, inspectorOpen, setTimelineOpen, timelineOpen, setSelection } = useApp();
  const hotkeys = useMemo<HotkeyConfig[]>(() => [
    { combo: "mod+k", global: true, label: "Open the Omnibar", onKeyDown: () => setOmnibarOpen(true), preventDefault: true },
    { combo: "/", global: true, label: "Open the Omnibar", onKeyDown: () => setOmnibarOpen(true), preventDefault: true },
    { combo: "shift+o", global: true, label: "Open the Omnibar", onKeyDown: () => setOmnibarOpen(true) },
    { combo: "q", global: true, group: "Rail", label: "Leads tab (queue)", onKeyDown: () => { setRailTab("leads"); navigate("leads"); } },
    { combo: "l", global: true, group: "Rail", label: "Layers tab", onKeyDown: () => { setRailTab("layers"); navigate("leads"); } },
    { combo: "f", global: true, group: "Rail", label: "Find tab", onKeyDown: () => { setRailTab("find"); navigate("leads"); } },
    { combo: "h", global: true, group: "Rail", label: "Histogram tab (not built yet; opens Info)", onKeyDown: () => { setRailTab("info"); navigate("leads"); } },
    { combo: "i", global: true, group: "Panels", label: "Toggle the inspector", onKeyDown: () => setInspectorOpen(!inspectorOpen) },
    { combo: "t", global: true, group: "Panels", label: "Toggle the timeline", onKeyDown: () => setTimelineOpen(!timelineOpen) },
    { combo: "esc", global: true, group: "Panels", label: "Clear the selection", onKeyDown: () => setSelection(null) },
  ], [setOmnibarOpen, setRailTab, setInspectorOpen, inspectorOpen, setTimelineOpen, timelineOpen, setSelection]);
  useHotkeys(hotkeys);
  return null;
}

export function App() {
  const route = useRoute();
  const { phone } = useApp();
  const objectPage = isObjectRoute(route.name) || route.name === "about";
  return (
    <HotkeysProvider>
      <div className="scs-app" data-route={route.name} data-phone={phone ? "1" : "0"}>
        <Banner position="top" />
        <TopNav />
        <main className="scs-main" data-object-page={objectPage ? "1" : "0"}>
          <Routes />
        </main>
        <Banner position="bottom" />
        <GlobalHotkeys />
        <OmnibarSearch />
      </div>
    </HotkeysProvider>
  );
}
