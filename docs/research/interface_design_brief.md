# SCS Vessel Watch: analyst interface design brief
Research only, no code. Date 2026-10-08. Every point carries the URL that was fetched in this session. Tags: [verified] = page fetched and read in this session; [snippet] = seen only in web-search result text; UNVERIFIED = not confirmed. Palantir product names appear below only as research references; none of them may appear in the product (owner's rule).

## 0. Access notes (read first)
- www.palantir.com/docs is blocked for the WebFetch tool but reachable with curl through the proxy. All Palantir docs pages below were downloaded with curl and read as text. blueprintjs.com is blocked both ways; Blueprint facts come from the GitHub source (raw.githubusercontent.com/palantir/blueprint, branch develop) and the compiled CSS of @blueprintjs/core 6.21.0 on cdn.jsdelivr.net. dni.gov, patents.google.com, wikipedia, blog.palantir.com/medium and web.archive.org are blocked; ICD 203 was read from the irp.fas.org mirror. ICD 206 could not be fetched (irp.fas.org returned a bot challenge): its content is UNVERIFIED here and is cited only through ICD 203's text.
- The UK G-Cloud 14 service definition for Palantir Gotham (May 2024) is a public government procurement document, but Palantir marks it "proprietary and confidential" on every page. Use it to learn patterns; do not quote it in product text or docs beyond short attributed phrases.

## 1. Toolkit decision: Blueprint
- Licence: "This project is made available under the Apache 2.0 License." [verified] https://github.com/palantir/blueprint ; licence text https://raw.githubusercontent.com/palantir/blueprint/develop/LICENSE
- Purpose statement to quote in the spec: "optimized for building complex, data-dense web interfaces for desktop applications which run in modern browsers. This is not a mobile-first UI toolkit." [verified] https://github.com/palantir/blueprint
- Packages: @blueprintjs/colors, core, datetime, icons (500+ icons), select, table. [verified] same URL.
- Version to pin: @blueprintjs/core 6.21.0 is dist-tag latest; license field Apache-2.0; peer react "18 || 19". [verified] https://registry.npmjs.org/@blueprintjs/core and https://cdn.jsdelivr.net/npm/@blueprintjs/core@6.21.0/package.json . Publish date of 6.21.0 was not read (registry document too large); the owner's "two weeks old" rule must be checked with `npm view @blueprintjs/core time` before pinning. UNVERIFIED age.
- CSS namespace is bp6 (NS = "bp6"), dark theme class is `bp6-dark`; constants ELEVATION_0..4, INTENT_PRIMARY/SUCCESS/WARNING/DANGER, TEXT_MUTED, MONOSPACE_TEXT, HEADING, RUNNING_TEXT, COMPACT, SMALL, LARGE. [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/classes.ts
- Dark theme is applied by adding `bp6-dark` to a container; "the container is responsible for setting a dark background". The `.bp6-dark` rule itself only sets `color-scheme: dark` (dark scrollbars) plus text colour #f6f7f9 in the compiled CSS. [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/typography.mdx , https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/_dark-theme.scss , compiled CSS line 3553.
- Design tokens (experimental, Style Dictionary, DTCG source) are exposed as CSS custom properties with prefix `--bp-`: palette, intent, surface, typography, iconography, emphasis; dark overrides under `.bp6-dark` (e.g. `--bp-surface-border-color-default:#ffffff33`). Some tokens use CSS relative colour syntax (Chrome 122+, Safari 18+). [verified] https://github.com/palantir/blueprint/tree/develop/packages/core/src/design-tokens and compiled CSS lines 122 to 243.

## 2. Visual language: exact tokens
Colour (hex from the colors package, "Modernized colors with more accessible contrast ratios") [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/colors/src/_colors.scss
- Gray scale: black #111418; dark-gray1 #1c2127, dark-gray2 #252a31, dark-gray3 #2f343c, dark-gray4 #383e47, dark-gray5 #404854; gray1 #5f6b7c, gray2 #738091, gray3 #8f99a8, gray4 #abb3bf, gray5 #c5cbd3; light-gray1 #d3d8de, light-gray2 #dce0e5, light-gray3 #e5e8eb, light-gray4 #edeff2, light-gray5 #f6f7f9; white #ffffff.
- Core: blue1 #184a90, blue2 #215db0, blue3 #2d72d2, blue4 #4c90f0, blue5 #8abbff; green1 #165a36, green2 #1c6e42, green3 #238551, green4 #32a467, green5 #72ca9b; orange1 #77450d, orange2 #935610, orange3 #c87619, orange4 #ec9a3c, orange5 #fbb360; red1 #8e292c, red2 #ac2f33, red3 #cd4246, red4 #e76a6e, red5 #fa999c.
- Extended (data viz only): vermilion3 #d33d17, rose3 #db2c6f, violet3 #9d3f9d, indigo3 #7961db, cerulean3 #147eb3, turquoise3 #00a396, forest3 #29a634, lime3 #8eb125, gold3 #d1980b, sepia3 #946638 (each has shades 1 to 5 in the file).
Semantic aliases [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_color-aliases.scss
- Intents: primary = blue3, success = green3, warning = orange3, danger = red3.
- Dark app background = dark-gray1 #1c2127; dark secondary background = black #111418; dark elevated background = dark-gray2 #252a31. Light app background = light-gray5.
- Dark text #f6f7f9 (light-gray5); dark muted text gray4 #abb3bf; dark disabled rgba(gray4, 0.6); dark link blue5 #8abbff; dark icon = muted text, icon selected = blue3; dark dividers rgba(white, 0.2) and muted rgba(white, 0.1); focus ring dark rgba(blue5, 0.752); text selection rgba(125,188,255,0.6).
- Compiled checks: `.bp6-dark .bp6-navbar` background #252a31; dark heading colour #f6f7f9; dark muted #abb3bf; dark `kbd` background #2f343c; selected tab in dark: `inset 0 -3px 0 #8abbff` indicator and text #8abbff; selected tree node in dark: background #2d72d2 with white icon. [verified] https://cdn.jsdelivr.net/npm/@blueprintjs/core@6.21.0/lib/css/blueprint.css
Colour rules [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/colors.mdx
- "The gray scale should be used for the main UI frame: containers, headers, sections, boxes, etc." Core colours call attention to elements by intent; they "adhere to WCAG 2.0 standards". Extended colours are "reserved mainly for data visualizations" and "should therefore not be used for typical user interface design". Palette types: sequential (ordered values), diverging (mid and extremes), qualitative (difference in kind).
- Contrast targets to test against: text 4.5:1, large text 3:1 (WCAG 2.1 SC 1.4.3), non-text 3:1 (SC 1.4.11). [verified] https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html
Typography [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_variables.scss , https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/_typography.scss , docs/typography.mdx , compiled CSS body rule
- "Blueprint does not include any fonts of its own; it will use the default sans-serif operating system font." Stack: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Open Sans", "Helvetica Neue", icons16, sans-serif. Monospace = `monospace`.
- Base 14px, line-height 1.28581, weight 400; small 12px, large 16px ($pt-spacing 4px x 3, 3.5, 4). Headings (font-size/line-height): h1 36/40, h2 28/32, h3 22/25, h4 18/21, h5 16/19, h6 14/16; heading weight 600; heading margin 0 0 12px. Running text: p margin 0 0 12px, heading margins 40px top 20px bottom, hr 1px divider.
- Palantir's own app builder defaults to "Source Sans Pro" ("the default Foundry font") and recommends system fonts over custom typefaces because custom fonts cannot be uploaded. [verified] https://www.palantir.com/docs/foundry/workshop/application-design-best-practices/ . Recommendation: system stack via Blueprint; optionally self-host Source Sans 3 (OFL) for the Palantir look. Licence of Source Sans 3 not checked here: UNVERIFIED.
Spacing and shape [verified] _variables.scss, docs/variables.mdx, _navbar.scss, compiled CSS
- $pt-spacing 4px is the base unit ("The legacy $pt-grid-size variable (10px) is deprecated. Please use $pt-spacing (4px)"). Border radius 4px. Button heights: 24px small, 30px default, 40px large (spacing x 6, 7.5, 10); inputs same. Navbar 50px tall, padding 0 16px, heading font 16px, divider 1px rgba(black,0.15), fixedToTop requires body top padding equal to navbar height. Card padding 20px (spacing x 5); five elevations 0 to 4 with explicit light and dark shadow stacks (dark ones use `inset 0 0 0 1px rgba(white, 20%)` plus black drop shadows). Tag: font 12px, line-height 16px, min-height 20px, padding 2px 6px, radius 4px. Transitions 100ms, ease cubic-bezier(0.4, 1, 0.75, 0.9). z-index: content 10, overlay 20, dialog header 30.
- Palantir app-builder spacing presets to mirror: Compact padding 16px; Regular 24px top/bottom, 48px left/right; Large 40px and 62px. Five preset background shades for light and dark mode plus Blueprint colours; widgets auto-switch light/dark by background brightness. Border styles: Bordered, Outer drop shadow, Inner shadow, Borderless. Section header styles: Block, Contained, Floating. [verified] https://www.palantir.com/docs/foundry/workshop/concepts-layouts/

## 3. Layout: the analyst map console
Palantir's map application interface, verbatim inventory [verified] https://www.palantir.com/docs/foundry/map/map-overview/ and https://www.palantir.com/docs/foundry/map/getting-started/
- Left panels: "Layers: Add, manage, and style object and overlay layers; set the base layer." "Find: Find objects and locations; navigate to specific geospatial coordinates." "Histogram: Analyze and filter objects based on property and time series values." "Info: Display an overall summary of the map."
- Right panels: "Selection: Analyze details about and take actions on the selected items." "Time Selection: Set the time range and current timestamp..." (now deprecated into the timeline).
- Bottom: Series panel / timeline for "temporal analysis of time series and event data".
- Top toolbar: Select, Search Around ("Explore object relations"), Draw (polygons, circles, rectangles, lines, points), Capture (screenshot), Measure, Annotate, Delete.
- Filter bar: active filters "are visible in a bar above the main application toolbar", removable by x or Clear filters; filters fade non-matching objects and make them uninteractive. [verified] https://www.palantir.com/docs/foundry/map/histogram/
- Settings gear top right: units Metric/Imperial/Nautical (per user), GeoJSON panel toggle, polling interval, time series buckets, time zone Local/UTC, time format 12h/24h (UTC forces 24h), Theme Light/Dark for the UI only ("does not change the base map"), experimental label de-overlap. [verified] https://www.palantir.com/docs/foundry/map/settings/
- Zoom buttons bottom left; Timeline toggle lower left; legend panel with compact or full size and collapsed default; "Enable transition to full Map application" from an embedded widget. [verified] https://www.palantir.com/docs/foundry/map/navigation/ , https://www.palantir.com/docs/foundry/workshop/widgets-map/
Platform chrome [verified] https://www.palantir.com/docs/foundry/getting-started/orientation-and-nav/
- A collapsible left workspace sidebar (toggle Cmd/Ctrl+O) with Home, Search (Quicksearch), Notifications, What's New, Recent (last 20), Files, Applications, Favourites, Support, Account. Notifications: newest first, "small, yellow badge" on the bell when unread, brief pop-up in the lower left when one arrives.
App-builder layout vocabulary [verified] https://www.palantir.com/docs/foundry/workshop/concepts-layouts/
- Header (persistent toolbar; horizontal top or vertical left, collapsible with icon-only state), Pages, Sections (layouts: Columns, Rows, Tabs, Flow, Toolbar, Loop), Overlays of two types: Drawers (left or right, width) and Modals (centre, size); backdrop dim on by default. Blueprint Drawer sizes: SMALL 360px, STANDARD 50%, LARGE 90%. [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/drawer/drawer.mdx
Design rules with numbers [verified] https://www.palantir.com/docs/foundry/workshop/application-design-best-practices/
- No more than five primary actions in top-level navigation; whitespace 30 to 40 percent of the screen; at most ten visible components per view; F-shaped hierarchy, filters anchored left, content flows right, metric cards for key figures; one shaded or drop-shadow emphasis per page; avoid horizontal scrolling; nav bars and tabs live in the primary header so only content scrolls; touch targets at least 30px; overlays for temporary or secondary interactions, never for analytical views that need several layers at once; validate by squinting at the screen.
Two canonical patterns [verified] https://www.palantir.com/docs/foundry/workshop/overview/ and https://www.palantir.com/docs/foundry/workshop/example-applications/
- "Inbox Alert and Task Management": triage, prioritise, complete. Core components: "A clear set of alerts to review or tasks to complete, and the ability to easily filter and prioritize among them", "A detailed view of each individual alert", "Configured action types that enable decision-making". Widgets: Prominent Terms Filter, Filter List, Object Table (ordered by risk score), Button Group; saved views shared between users.
- "Common Operational Pictures (COPs)": "on-the-wall", big-screen view with a map, statistics, charts, filters, drill-down and links to other workflows. Widgets: Metric Card, Map, Chart XY, Object Table.
Proposed SCS Vessel Watch shell (derived): 50px navbar (product name left, build badge "open"/"research", Omnibar search centre, time zone and units, user right); left rail 320px with tabs Layers / Find / Histogram / Info; map centre; right inspector 360px (Blueprint Drawer SMALL width) with tabs Properties / Series / Events / Links; bottom timeline 160px collapsible; filter bar under the navbar; caveat banner above the navbar and below the timeline (see section 10).

## 4. Object-centric model (what every entity is)
Definitions, verbatim [verified] https://www.palantir.com/docs/foundry/ontology/core-concepts/
- "An object type defines an entity or event in an organization." "A property defines the object type's characteristics." "A link type defines the relationship between two object types." "An action type defines how an object type can be modified." "An object set refers to a collection of multiple object instances." Dataset analogy: Dataset = Object type, Row = Object, Column = Property, Field = Property value, Join = Link type. Roles are the permission model; Functions take objects and object sets as input; Interfaces give polymorphism.
- Ontology as "digital twin" containing "semantic elements (objects, properties, links) and kinetic elements (actions, functions, dynamic security)". [verified] https://www.palantir.com/docs/foundry/ontology/overview/
- Design rules worth copying: "Model reality, not systems"; "Separate identity from observation: If a row represents a measurement or event about an entity, the entity and the observation are likely different object types"; "Name things for humans"; mark technical types hidden. The page even uses "a Vessel" as its example of a real-world entity. [verified] https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/
- Object View: "a central hub for all information and workflows related to a particular object", with "biographical data", "360-degree view" of linked objects, metrics, embedded analyses. Standard view surfaces prominent properties in a card-style block above a table; time series properties render as charts; geospatial properties render on a map; "hidden properties are not visible". Linked objects component: grouped by link type, inline property preview, open subset in new tab, preview in side panel. Two form factors: Full and Panel (panel = "the most critical data for a specific workflow"). Tabs carry a badge with the count of linked objects when the tab is a link. Comments on objects with mentions and attachments. [verified] https://www.palantir.com/docs/foundry/ontology/applications/ , https://www.palantir.com/docs/foundry/object-views/standard-object-views/ , https://www.palantir.com/docs/foundry/object-views/overview/ , https://www.palantir.com/docs/foundry/object-views/config-tabs/ , https://www.palantir.com/docs/foundry/object-views/comment-on-objects/
- Property display controls: visibility Prominent / Normal / Hidden; render hints Long text and Keywords (keywords render as tags); conditional and numeric formatting defined once in the ontology and reused by every widget; "Show security markings" renders "property security markings ... as a condensed gray pill with an expanded window view on selection". [verified] https://www.palantir.com/docs/foundry/object-views/widgets-properties-links/ , https://www.palantir.com/docs/foundry/workshop/widgets-object-list/
- Events: object types with start and end timestamps; they drive opacity-by-time on the map and bars on the timeline. Tracks: objects whose position changes over time; Palantir's own FAQ example is "GPS coordinates for ships or vehicles" stored as lat/lon time series and shown as track objects; geotemporal series add a measurement (speed, altitude) to each position. [verified] https://www.palantir.com/docs/foundry/map/time-overview/ , https://www.palantir.com/docs/foundry/geospatial/faq/ , https://www.palantir.com/docs/foundry/geospatial/geotemporal-series-overview/
Gotham (defence product) uses the same model: data becomes "objects and associated properties that represent real concepts (such as people, organisations, places, documents, and events) and the relationships that connect them"; "Every piece of data is tethered to its original data source". [verified] UK G-Cloud 14 Gotham service definition PDF.
SCS mapping (derived, for the spec): object types Radar contact, Fixed structure, Night light, Recurring light site, Scene (one Sentinel-1 product), Pass, Weather sample, AIS vessel (research build only until a feed exists), Track, Lead, Alert, Label, Reporting box, EEZ polygon (as published by Marine Regions, off by default). Links: contact in scene; contact near structure; contact matched/unmatched to AIS vessel; light same-night co-located with contact; lead cites contacts, lights, weather, scene. Actions: label (vessel/structure/clutter/unsure), lead decision, export. Every object carries source, product ID, acquisition time, processing version and licence as properties (section 11).

## 5. Search, filtering, drill-down
Global search [verified] https://www.palantir.com/docs/foundry/compass/quicksearch/
- Cmd/Ctrl+J opens Quicksearch. Two modes: "Jump-to mode" (title-only, personalised, keyboard navigation) and "Full results view" (tabs Apps / Objects / Datasets / Files, filters, metadata such as path, view count, last updated, "key security metadata"). Respects permissions; discoverable-but-not-accessible content shows "Request access".
Object search syntax [verified] https://www.palantir.com/docs/foundry/object-explorer/search-syntax/
- Words are OR'd by default; quotes for phrases; AND, OR, NOT with parentheses; `?` single char, `*` zero or more (leading wildcard needs a render hint; `*term*` unsupported); `~` fuzzy.
Filtering UI [verified] https://www.palantir.com/docs/foundry/object-explorer/filter-results/
- A search bar is "the central hub for filtering"; filters render as pills; typed values offer "where Destination City Name is Los Angeles"-style suggestions; term modifiers as toggles: "Is not", "Starts with", "Exact"; AND/OR tags that flip on click and nest; link filters: "Has Link", filter by a linked object's property, filter by specific linked objects.
Charts as filters [verified] https://www.palantir.com/docs/foundry/object-explorer/explore-charts/
- "Charts are the main point of interaction for users filtering." Types: Listogram (categorical counts or aggregates, keep/exclude), Pie, Histogram (auto-bucketed, click a bucket or brush a range, editable endpoints), Grid plot (two properties, colour scale), Single statistic, Statistics table, Cluster map (scaled bubbles, click to filter geospatially), Choropleth. Two-column layout, drag to reorder and resize, saved personal and global default layouts, undo/redo of the last five exploration states, a right preview panel of up to 20 results opening an Object View.
- Map histogram panel specifics: object-type counts first, then one section per property across all types; top five values with "Show more" in fives; sort by Value or Count or Selected count; date binning Year / Year and month / Quarter / Month / Day; numeric binning None / Equal size / Logarithmic; click row selects, Shift range, Ctrl/Cmd add; "Filter to" / "Filter out"; double-click a row to filter to it; filters are not saved with the map. [verified] https://www.palantir.com/docs/foundry/map/histogram/
Results table [verified] https://www.palantir.com/docs/foundry/object-explorer/view-results/
- Multi-column sort with numbered sort badges; drag handle reorder; "Freeze X columns"; hide columns; configure-columns dialog; no-truncate option; click title opens object; checkbox opens a right "Selection Preview" panel; up to twenty previews as cards; "Compare objects" for two; time series columns show latest value plus a sparkline; inline edits with a pen icon.
- Blueprint Table package: "fixed column headers and row index columns", resizable columns and rows with double-click auto-size, "viewport-only rendering", region selection, right-click copy, inline editing. [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/table/README.md
Pivot, compare, save [verified] https://www.palantir.com/docs/foundry/object-explorer/pivot-linked/ , .../compare-object-sets/ , .../save-lists/ , .../overview/
- Pivot the exploration to a linked type (e.g. Airports filtered, then pivot to Departing Flights) and chain pivots. Comparison mode shows every chart side by side for two sets with a colour per set. Explorations are live saved queries; Lists are static snapshots; both can be Private or Public; sharing a list does not share the underlying objects.

## 6. Map interactions (exact behaviours to copy)
Selection [verified] https://www.palantir.com/docs/foundry/map/selection/
- Click selects (all items under cursor); Ctrl/Cmd+click adds; Shift+drag rectangle; click empty map clears; Select all (Ctrl/Cmd+A); Select all of "Type"; Invert selection (Ctrl/Cmd+I); Select intersecting objects; Select intersecting a shape; lock a layer so its objects cannot be clicked but can still be selected from panels. Selection panel: list when several, details when one; actions icon; "..." menu with centre map, remove from map, open in other apps. Tabs: Properties (pin properties to the top via right-click), Series, Events (active vs inactive in the time window), Object view. Delete key removes with "A toast ... with an Undo option".
Layers [verified] https://www.palantir.com/docs/foundry/map/layer-management/ , https://www.palantir.com/docs/foundry/map/core-concepts/
- Visibility toggle, rename by clicking the name, drag to reorder (higher renders on top), move selected objects into a new layer to style them separately. Layer kinds: Base (light, dark, satellite), Object, Link (after a Search Around), Overlay (reusable, built once), Annotation.
Styling [verified] https://www.palantir.com/docs/foundry/map/visualize-objects/ , .../visualize-points/ , .../visualize-tracks/ , .../visualize-clusters/
- A layer holds several "displays" (icons, circles, lines, polygons, track lines, breadcrumbs, heatmaps, clusters). Value-based styling: sources Fixed / Property / Function / Measure; numeric values through a gradient editor with inferred or manual min and max; strings through Manual / Automatic / None (value used as hex); numeric attributes through a linear "Scaled" mapping (e.g. width 1px at value 20, 10px at 500). Icons: object default, media image, fixed, or by property; rotation "Automatic" follows track heading (their example is vessel objects); marker shape Circle / Pin / None. Tracks: interpolation Linear or Last known; "Max time gap" breaks the line; breadcrumbs outside the viewport are faded; breadcrumbs also appear on the timeline. Clusters for large point sets, sized or coloured by count or an aggregate.
Scale [verified] https://www.palantir.com/docs/foundry/map/objects-loading-methods/ , https://www.palantir.com/docs/foundry/map/timeline/
- Loading method Auto / Tile / Object. Sets over 1,000 objects switch to tile loading; tile mode drops time-based styling, functions, labels, timeline geometries, search-arounds, histogram and time filtering. Lesson for 78,615 contacts: tiles or clusters for the regional view, full objects only for the selection and the Ca Mau scene.
Shapes, geosearch, annotations, actions [verified] https://www.palantir.com/docs/foundry/map/shapes/ , .../annotations/ , .../actions/ , .../getting-started/
- Press D or pick a drawing mode; create a shape from the current selection; modify with Edit points / Buffer (grow or shrink by distance) / Translate / Replace; operations: Select intersecting, Search within (objects with geo properties intersecting the shape), Actions (ontology actions that take shapes), Delete. Radius search example: circle of 200 km, then "Add all". Annotations: text, line, polygon, rectangle, circle with title and notes in tooltip; measurements per segment or total, polygon area; dash patterns; fill toggle. Right-click on the map offers Actions on points.
Navigation [verified] https://www.palantir.com/docs/foundry/map/navigation/
- Drag or arrow keys pan; wheel, buttons or + and - zoom; Ctrl/Cmd+drag rotates and tilts; 0 centres on the selection (or all objects); Find panel tabs "Objects on map", "Locations" (needs a geocoder key), coordinates input with "Show coordinates" adding a text annotation.
Projection and basemaps [verified] https://www.palantir.com/docs/foundry/map/overview/ , .../layer-editor/ , .../control-panel/
- Renders in Web Mercator (EPSG:3857), expects WGS 84 degrees (EPSG:4326). Custom layers are vector (GeoJSON file, dataset, URL or MVT tiles) or raster tiles (opacity, Linear or Nearest sampling, min and max zoom) or a Mapbox GL style document ("only the sources and layers properties are supported"). Organisation defaults: default viewport, default time selection, unit system, polling intervals, object search and search-around limits, custom base map, watermark overlay on the base map. For SCS the open equivalent is MapLibre GL with a dark basemap; CARTO Dark Matter is free up to 1 million requests a month for commercial use (5 million non-commercial), raster for Leaflet and vector style JSON for MapLibre, with and without labels. [verified] https://carto.com/basemaps/
Search Around on the map [verified] https://www.palantir.com/docs/foundry/map/integrate-searcharounds/
- Right-click an object, Search Around, pick a linked type; results add a layer and link arcs. Two-step "link merged" search-arounds draw an arc through an intermediary object (e.g. contract), and when the intermediary is an event the map shows "circles traveling along the arc" positioned by event time against the selected time. For SCS: contact to scene to pass; contact to candidate AIS vessel via a match event.

## 7. Time: one timeline, not three panels
[verified] https://www.palantir.com/docs/foundry/map/timeline/ , .../time-overview/ , .../time-selection/ , .../time-series/ , .../events/ , .../visualize-timeline/
- Every map has a selected time (cursor) inside a time window (range); "All temporal data shown on a map respects the current selected time and time range" even when the timeline is collapsed. Palantir retired its separate "Time Selection" and "Series" panels into the timeline; copy the end state, not the history.
- Controls: toggle lower left; cursor by double-click, right-click, drag, or typed date-time; "View latest" live mode polls at a configured interval; time range by wheel zoom, Cmd/Ctrl+wheel pan, typed range, "Zoom to fit"; time filter by Shift+drag or Time filter button, also shown at the top of the canvas; non-matching objects fade; playback with presets "1x, 2x, 5x, 10x, 100x" looping over the window or filter; "Expand" shows each object type on its own row; a legend lists layers with per-layer style actions. Lines for instants, bars for ranges, breadcrumbs for tracks, series plots for time series, colour-by-property shapes (their example: diamonds for flights).
- Events tab on a selected object: Active events (cursor inside start and end) vs inactive; actions: set the window to the event, open the event elsewhere; label counts of active events.
- Timeline widget config vocabulary: layers per object type, event title from object title or property, prominent properties on the event, static or conditional colour, vertical or horizontal orientation, newest or oldest first, interactive legend, "time between events" tooltip. [verified] https://www.palantir.com/docs/foundry/workshop/widgets-timeline/
- Vertex graph timeline uses the same conventions (Shift+drag filter, faded nodes, expand per type, playback presets). [verified] https://www.palantir.com/docs/foundry/vertex/timeline/
SCS use: window = one 12-day Sentinel-1 cycle by default; rows = radar contacts per scene, VIIRS nights, passes, wind events; "View latest" tied to the AIS recorder and next-pass file when present.

## 8. Graph and link analysis
[verified] https://www.palantir.com/docs/foundry/vertex/explore-object-relationships/ , .../graphs-display-options/ , .../explore-time-series/ ; Gotham Graph from the G-Cloud PDF
- Blank canvas, "+ add objects" via search; click a node opens a selection panel of properties with derived property functions pinned at the top; right-click gives selection, layout and exploration options; Search Around lists linked types with counts; a filter icon opens a multi-step Search Around panel (filters per hop, "Add link" chains hops, parameters, save and load as a resource); histogram filters on selected nodes; layouts Auto, Circular, Hierarchy (L to R or T to B, root choice), Grid, Row/Column ordered by a property, Radial (central node, density 1 to 5), Cluster by property, Cartesian by two properties; group nodes by type or property; "link merging" collapses transactional objects (their example: Flights between airports) into styled edges; time series values can be added to node labels or free "readouts".
- Gotham Graph "helpers": Histogram, Selection ("a miniature version of the Browser application"), History ("how the content and styling of a graph has changed over time, as well as which user performed the change"), Table (exportable), Search Around (multi-hop), Timeline. Graphs export to interactive HTML. [verified] G-Cloud PDF.
SCS use (keep small): a graph tab inside a Lead showing contact, scene, pass, co-located light, weather sample, candidate AIS vessels, with hop counts; export as GeoJSON and an HTML snapshot.

## 9. Alerting and triage
Alerting pattern [verified] https://www.palantir.com/docs/foundry/use-case-patterns/alerting-workflow/
- "the user is presented with prioritized alerts and relevant data associated with the alerts, which they will review to make a decision. Their decision is recorded using buttons." Alerts come from "a data pipeline, a machine learning model, Foundry Rules, or any combination". The alert is its own object linked to the trigger object. Decision action fields: user ID, decision timestamp, alert object, the decision, optional explanation ("helpful for reviewing in the tracker"). An "Impact Tracker" shows alerts resolved, common decisions, time to decision.
- Rules: "Each rule defined creates a type of alert." [verified] https://www.palantir.com/docs/foundry/foundry-rules/overview/
- Automation: conditions are time-based, object-data based ("trigger when a new Alert object with priority high is added") or both; effects: submit actions, run functions, "Send platform and email notifications with attachments"; "Watched searches" notify when a saved exploration gets new results or an aggregate crosses a threshold. Planning guidance to copy into docs: document "purpose, data scope, execution identity, and potential impact of each effect", stage consequential changes for human review, include a recovery path. [verified] https://www.palantir.com/docs/foundry/automate/overview/
- Gotham Inbox (defence product): "centralises results, notifications, and alerts in an inbox-style interactive operational interface"; subscription based ("Notifications are only generated when a user subscribes"); stays until archived; alert types include search feeds, object watch feeds, "Geofence alert: A user-defined alert, where a user defines a 'fence' on a map ... to be alerted if an object enters or exits the area", sharing alerts; grouped into channels; alert shows what to do and a path to the app. [verified] G-Cloud PDF.
- Severity vocabulary used by Palantir's monitoring views: low, medium, high. [snippet] https://palantir.com/docs/foundry/monitoring-views
Maritime vendors (public marketing, for workflow vocabulary only) [verified]
- Starboard: "detecting anomalous behaviour, scoring risks, and routing alerts directly into secure workflows"; "Set protection zones and be alerted in real-time when high risk activity, like loitering, is detected"; "Know which vessels to focus on and why"; "key events highlighted in the track history timeline and available from the vessel report page"; layering "from wide, cost-effective data sources to narrow, expensive and high-resolution data sources"; "audit-ready reports". https://www.starboardintelligence.com/ and https://www.starboardintelligence.com/platform
- Windward: "explainable, unclassified and shareable mission-grade intelligence"; fuses "AIS, dark vessel signals, EO, SAR, RF". https://windward.ai/
- Kpler: "Detect AIS gaps and AIS spoofing that could be linked to deceptive shipping practices"; AIS network of "over 13,000 AIS receivers", latency "approximately 5 seconds". https://www.kpler.com/ and https://www.kpler.com/product/maritime/kplerais
- Unseenlabs: RF geolocation of "uncooperative ships ... also known as 'dark vessels'". https://unseenlabs.com/en/
- Global Fishing Watch map: "Detailed events along vessel tracks, including fishing activity and potential encounters"; data "through three days prior to present time". https://globalfishingwatch.org/our-map/ (CC BY-NC data: research build only).
- HawkEye 360 pages are JavaScript rendered; no text extracted. UNVERIFIED.
SCS lead inbox (derived): left Filter List (lead type, score bin, region box, scene, night, status), centre Object Table ordered by score with "Pending review" default filter, right Lead panel with evidence (chip, VV/VH, CNN score and threshold, coverage, weather, lights, nearest structure, AIS status), decision Button Group: "Needs AIS check", "Lawful non-carrier likely", "Escalate", "Dismiss", each requiring an explanation; every lead card and every notification carries the sentence "Dark does not mean illegal" (rule 3). Severity words: low, medium, high. Geofence alerts only on reporting boxes, never on boundary lines.

## 10. Provenance, markings, audit and the caveat banner
Markings as UI [verified] https://www.palantir.com/docs/foundry/security/markings/ , https://www.palantir.com/docs/foundry/workshop/widgets-object-list/
- Markings are "a mandatory control" (binary, conjunctive AND); they "travel with the data" along file hierarchy and data dependencies; a user can see that a derived dataset exists and its metadata yet not its data. On screen a marking is a condensed gray pill that expands on click.
Classification controls [verified] https://www.palantir.com/docs/foundry/security/classification-based-access-controls/
- Hierarchical (Secret or below, Top Secret or below), disjunctive releasability categories ("RELEASE TO" GBR OR CAN), ubiquity (every project and dataset must carry a classification), file classification (to discover) vs data classification (to read; inherited and "always at least as strict" as upstream), project maximum classification, warnings on violations. Configured per institution, not by default.
Security principles to state in docs [verified] https://www.palantir.com/docs/foundry/security/overview/
- "Transparency: Enables users to reason about who has access to what resource and why." Mandatory controls propagate "via Palantir's provenance and lineage capabilities"; row and column filters "do not extend to downstream outputs or exports". Audit logging, SSO and MFA are not paywalled.
Gotham audit [verified] G-Cloud PDF: "All user and administrator interactions with the system and the use of information to which they have access are recorded in audit logs. These audit logs can additionally be configured to be tamper-evident." Graph History shows who changed what.
Lineage [verified] https://www.palantir.com/docs/foundry/data-lineage/overview/ : expand ancestors and descendants, colour out-of-date tables, "Drill into details about your data such as its schema, when it was last built, and the code that generated the data itself", shareable snapshots. Quiver keeps a version history plus an autosaved state URL. [verified] https://www.palantir.com/docs/foundry/quiver/core-concepts/
Marking placement rules from US regulation (pattern only; SCS is unclassified) [verified] https://www.law.cornell.edu/cfr/text/32/2001.21 and https://www.archives.gov/files/isoo/training/marking-booklet-revision.pdf
- "Conspicuously place the overall classification at the top and bottom" of the first page and "Each interior page ... shall be marked at the top and bottom"; portion marks precede each paragraph, graphic, table, chart; dissemination control and handling markings supplement the banner. ISOO booklet: "The map itself is considered a portion and is marked top and bottom".
- UK scheme for a non-US reader: three tiers OFFICIAL, SECRET, TOP SECRET with additional markings. [verified] https://www.gov.uk/government/publications/government-security-classifications (titles only; body text not parsed).
SCS banner (derived): a full-width strip at the very top and very bottom of every view, Blueprint Callout minimal style, text "OPEN BUILD. Public sources only. No AIS matched. Dark does not mean illegal." (research build: "RESEARCH BUILD. Contains Global Fishing Watch data, CC BY-NC 4.0, noncommercial."). Never print a real classification word (UNCLASSIFIED, OFFICIAL) because the product is not a government record; call it a handling banner in docs. Each panel and export carries the same line as a portion-style prefix.

## 11. What makes analysts trust a field or a score
ICD 203 Analytic Standards (ODNI, read from the FAS mirror) [verified] https://irp.fas.org/dni/icd/icd-203.pdf
- Tradecraft standard 1: "Properly describes quality and credibility of underlying sources, data, and methodologies", with "source descriptors in accordance with ICD 206" covering "accuracy and completeness, possible denial and deception, age and continued currency of information, and technical elements of collection". Standard 2: "Properly expresses and explains uncertainties", with one row of likelihood terms: almost no chance (01-05%), very unlikely (05-20%), unlikely (20-45%), roughly even chance (45-55%), likely (55-80%), very likely (80-95%), almost certain(ly) (95-99%); confidence levels must not share a sentence with likelihood terms. Standard 3: "Properly distinguishes between underlying intelligence information and analysts' assumptions and judgments" and "identify indicators that, if detected, would alter judgments". Standard 9: visual information "when information or concepts (e.g., spatial or temporal relationships) can be conveyed better in graphic form". ICD 206 itself: UNVERIFIED (not fetched).
NIST IR 8312, Four Principles of Explainable AI [verified] https://nvlpubs.nist.gov/nistpubs/ir/2021/NIST.IR.8312.pdf
- "Explanation: A system delivers or contains accompanying evidence or reason(s) for outputs and/or processes." "Meaningful: ... understandable to the intended consumer(s)." "Explanation Accuracy: An explanation correctly reflects the reason for generating the output." "Knowledge Limits: A system only operates under conditions for which it was designed and when it reaches sufficient confidence in its output."
Palantir practice that supports trust [verified]: every property can carry a security marking pill; functions and derived properties shown with their origin in the selection panel (Vertex); lineage down to "the code that generated the data"; decision actions record user, time and explanation; "Transparency ... who has access to what resource and why".
SCS rules (derived): every property in the inspector shows a source chip (sensor and product ID, acquisition time UTC, processing script and git hash, licence) on hover and in exports; the CNN score shows the chip, the score, the threshold, the held-out precision 0.77 and recall 0.75 on 1A/1B, and a Knowledge Limits flag "trained on 1A/1B labels, 1C/1D transfer not yet scored, no labels under 15 m" (from docs/STATUS.md); likelihood words only from the ICD 203 row above, never mixed with "confidence"; "Information" (measured) and "Judgment" (model or analyst) are separated typographically (Blueprint muted text vs normal); each lead lists the indicator that would change it (AIS match, second look, optical view).

## 12. Keyboard shortcuts
Blueprint hotkeys [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/hooks/hotkeys/use-hotkeys.mdx , .../components/hotkeys/hotkeys-target.mdx , https://raw.githubusercontent.com/palantir/blueprint/develop/packages/docs-app/src/examples/select-examples/omnibarExample.tsx , https://raw.githubusercontent.com/palantir/blueprint/develop/packages/select/src/components/omnibar/omnibar.mdx
- `useHotkeys` with HotkeysProvider at the root; "Hotkeys must define a group, or be marked as global"; the `?` key opens a generated hotkeys dialog; combo = zero or more modifiers plus one key; modifiers alt, ctrl, shift, meta/cmd, and `mod` (cmd on macOS, ctrl elsewhere); named keys plus, minus, tab, enter, esc, space, pageup, left, up, del; examples "cmd+plus", "shift+1", "alt + shift + x", "ctrl + left". Omnibar is "a macOS Spotlight-style typeahead component"; the docs example opens it with "shift + o" (global).
Palantir platform shortcuts [verified] https://www.palantir.com/docs/foundry/getting-started/orientation-and-nav/ , https://www.palantir.com/docs/foundry/map/selection/ , .../navigation/ , .../shapes/ , .../timeline/
- Cmd/Ctrl+J search; Cmd/Ctrl+O sidebar; Cmd/Ctrl+U assistant; map: + and - zoom, arrows pan, 0 centre on selection, D draw, Delete remove (with Undo toast), Ctrl/Cmd+A select all, Ctrl/Cmd+I invert, Shift+drag rectangle select, Ctrl/Cmd+click add, Ctrl/Cmd+drag rotate and tilt, Shift+drag on the timeline for a time filter, Cmd/Ctrl+wheel pans the timeline.
SCS table (derived, keep the existing N and 1 to 4 labelling keys from STATUS.md): mod+K or shift+O Omnibar; ? shortcuts dialog; L layers, F find, H histogram, I inspector, T timeline; + - 0 arrows as above; D draw; Esc clear selection; Delete remove from map with Undo; N next contact in queue, 1 to 4 label; [ and ] step scenes; Space play/pause timeline; mod+E export.

## 13. Component mapping (Blueprint 6 names, all present in packages/core/src/components) [verified] https://github.com/palantir/blueprint/tree/develop/packages/core/src/components
- Shell: Navbar + NavbarGroup + NavbarHeading + NavbarDivider (fixedToTop, 50px); Tabs (controlled with selectedTabId, TabsExpander to right-align, tagContent for counts); Drawer (right, SMALL 360px) for the full object view; Dialog for confirmations; Toast for Undo; Breadcrumbs for scene > contact.
- Panels: Section + SectionCard ("contain, structure, and create hierarchy", collapsible) for inspector groups; Card with elevation 0 to 4, compact, interactive, selected; CardList; PanelStack for drill-in rails; Tree for the layers panel (icon, label, secondaryLabel, caret, selected state #2d72d2 in dark); Callout (intent, icon, title, compact, minimal) for the caveat banner and model limits; NonIdealState for empty, loading and error states with an action; Skeleton and Spinner for loading; ProgressBar for pipeline runs.
- Data: Table (@blueprintjs/table) for results; HTMLTable for small property lists; Tag and CompoundTag for classes, markings, licences (minimal + intent); EntityTitle ("title, icon, subtitle and tag") for object headers; SegmentedControl for view switches (regional / scene); Slider and RangeSlider (time window), DateRangePicker (@blueprintjs/datetime); Omnibar, Select, MultiSelect (@blueprintjs/select) for search and filters; Menu and ContextMenu for right-click; Hotkeys; OverflowList for filter pills; Tooltip and Popover for source chips.
- Icons available in @blueprintjs/icons: globe, map, map-marker, satellite, layers, search, filter, filter-list, flag, warning-sign, pin, path-search, graph, time, history, locate, geolocation, geosearch, heatmap, polygon-filter, timeline-events, timeline-line-chart, timeline-bar-chart, series-search, shield, lock, eye-open, eye-off, pulse, inbox, cell-tower, compass, airplane, send-to-map, map-create, key-command, export, database. Missing (supply custom SVG): ship, anchor, radar, vessel, geofence, target, crosshair. [verified] https://raw.githubusercontent.com/palantir/blueprint/develop/packages/icons/icons.json (first 100k chars read; the rest UNVERIFIED)

## 14. Do not copy
- Palantir names and product names (Gotham, Foundry, Gaia, Graph, Dossier, Browser, Object Explorer, Workshop, Quiver, Vertex, AIP, Inbox as a product name) stay out of product text, docs and comments; use "object view", "search around", "inbox", "timeline", "histogram" as generic terms. No screenshots, figures or icon art from palantir.com or the G-Cloud PDF. Blueprint code and CSS are fine under Apache-2.0 with the NOTICE kept.
- Do not reproduce Mapbox-hosted base maps or geocoding without a key (Palantir needs a Mapbox key for Find Locations); use CARTO or self-hosted tiles with attribution.

## 15. Gaps and UNVERIFIED items
- ICD 206 source descriptor elements (dni.gov and odni.gov blocked, fas.org challenged).
- Palantir Gotham product page body (JavaScript; only the title "Gotham | Palantir" and meta description "The Operating System for Defense Decision Making" were read).
- Palantir design blog posts, Wikipedia, patents, YouTube demos: blocked, not used.
- Publish date of @blueprintjs/core 6.21.0 (check with npm view before pinning).
- Esri Dark Gray Canvas basemap description (item page not parsed).
- HawkEye 360 product details (JavaScript page).
- Third-party claims seen only as snippets (Gotham Horizon in-memory store, monitoring severity levels) are not relied on.


SOURCES
https://github.com/palantir/blueprint
https://raw.githubusercontent.com/palantir/blueprint/develop/LICENSE
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/colors/src/_colors.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_variables.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_color-aliases.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_typography-colors.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/_variables-extended.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/common/classes.ts
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/_typography.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/_dark-theme.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/colors.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/typography.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/variables.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/docs/index.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/navbar/navbar.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/navbar/_navbar.scss
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/tree/tree.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/tabs/tabs.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/drawer/drawer.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/tag/tag.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/card/card.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/section/section.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/entity-title/entity-title.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/non-ideal-state/non-ideal-state.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/callout/callout.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/html/html.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/components/hotkeys/hotkeys-target.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/core/src/hooks/hotkeys/use-hotkeys.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/select/src/components/omnibar/omnibar.mdx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/docs-app/src/examples/select-examples/omnibarExample.tsx
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/table/README.md
https://raw.githubusercontent.com/palantir/blueprint/develop/packages/icons/icons.json
https://github.com/palantir/blueprint/tree/develop/packages/core/src
https://github.com/palantir/blueprint/tree/develop/packages/core/src/components
https://github.com/palantir/blueprint/tree/develop/packages/core/src/design-tokens
https://cdn.jsdelivr.net/npm/@blueprintjs/core@6.21.0/lib/css/blueprint.css
https://cdn.jsdelivr.net/npm/@blueprintjs/core@6.21.0/package.json
https://registry.npmjs.org/@blueprintjs/core
https://registry.npmjs.org/@blueprintjs/core/6.21.0
https://www.palantir.com/docs/foundry/map/overview/
https://www.palantir.com/docs/foundry/map/getting-started/
https://www.palantir.com/docs/foundry/map/core-concepts/
https://www.palantir.com/docs/foundry/map/map-overview/
https://www.palantir.com/docs/foundry/map/selection/
https://www.palantir.com/docs/foundry/map/layer-management/
https://www.palantir.com/docs/foundry/map/navigation/
https://www.palantir.com/docs/foundry/map/shapes/
https://www.palantir.com/docs/foundry/map/histogram/
https://www.palantir.com/docs/foundry/map/timeline/
https://www.palantir.com/docs/foundry/map/time-overview/
https://www.palantir.com/docs/foundry/map/time-selection/
https://www.palantir.com/docs/foundry/map/time-series/
https://www.palantir.com/docs/foundry/map/visualize-tracks/
https://www.palantir.com/docs/foundry/map/visualize-timeline/
https://www.palantir.com/docs/foundry/map/visualize-clusters/
https://www.palantir.com/docs/foundry/map/visualize-objects/
https://www.palantir.com/docs/foundry/map/visualize-points/
https://www.palantir.com/docs/foundry/map/events/
https://www.palantir.com/docs/foundry/map/annotations/
https://www.palantir.com/docs/foundry/map/objects-loading-methods/
https://www.palantir.com/docs/foundry/map/settings/
https://www.palantir.com/docs/foundry/map/control-panel/
https://www.palantir.com/docs/foundry/map/actions/
https://www.palantir.com/docs/foundry/map/integrate-searcharounds/
https://www.palantir.com/docs/foundry/map/layer-editor/
https://www.palantir.com/docs/foundry/workshop/widgets-map/
https://www.palantir.com/docs/foundry/object-explorer/overview/
https://www.palantir.com/docs/foundry/object-explorer/getting-started/
https://www.palantir.com/docs/foundry/object-explorer/search-objects/
https://www.palantir.com/docs/foundry/object-explorer/search-syntax/
https://www.palantir.com/docs/foundry/object-explorer/filter-results/
https://www.palantir.com/docs/foundry/object-explorer/explore-charts/
https://www.palantir.com/docs/foundry/object-explorer/view-results/
https://www.palantir.com/docs/foundry/object-explorer/pivot-linked/
https://www.palantir.com/docs/foundry/object-explorer/compare-object-sets/
https://www.palantir.com/docs/foundry/object-explorer/save-lists/
https://www.palantir.com/docs/foundry/ontology/overview/
https://www.palantir.com/docs/foundry/ontology/core-concepts/
https://www.palantir.com/docs/foundry/ontology/applications/
https://www.palantir.com/docs/foundry/ontology/ontology-best-practices/
https://www.palantir.com/docs/foundry/object-views/overview/
https://www.palantir.com/docs/foundry/object-views/standard-object-views/
https://www.palantir.com/docs/foundry/object-views/config-tabs/
https://www.palantir.com/docs/foundry/object-views/config-panel-views/
https://www.palantir.com/docs/foundry/object-views/widgets-properties-links/
https://www.palantir.com/docs/foundry/object-views/comment-on-objects/
https://www.palantir.com/docs/foundry/vertex/overview/
https://www.palantir.com/docs/foundry/vertex/explore-object-relationships/
https://www.palantir.com/docs/foundry/vertex/graphs-display-options/
https://www.palantir.com/docs/foundry/vertex/timeline/
https://www.palantir.com/docs/foundry/vertex/events-overview/
https://www.palantir.com/docs/foundry/vertex/explore-time-series/
https://www.palantir.com/docs/foundry/quiver/overview/
https://www.palantir.com/docs/foundry/quiver/core-concepts/
https://www.palantir.com/docs/foundry/workshop/overview/
https://www.palantir.com/docs/foundry/workshop/concepts-layouts/
https://www.palantir.com/docs/foundry/workshop/getting-started/
https://www.palantir.com/docs/foundry/workshop/application-design-best-practices/
https://www.palantir.com/docs/foundry/workshop/example-applications/
https://www.palantir.com/docs/foundry/workshop/widgets-object-list/
https://www.palantir.com/docs/foundry/workshop/widgets-object-view/
https://www.palantir.com/docs/foundry/workshop/widgets-object-table/
https://www.palantir.com/docs/foundry/workshop/widgets-metric-card/
https://www.palantir.com/docs/foundry/workshop/widgets-filter-list/
https://www.palantir.com/docs/foundry/workshop/widgets-timeline/
https://www.palantir.com/docs/foundry/use-case-patterns/alerting-workflow/
https://www.palantir.com/docs/foundry/foundry-rules/overview/
https://www.palantir.com/docs/foundry/automate/overview/
https://www.palantir.com/docs/foundry/security/overview/
https://www.palantir.com/docs/foundry/security/markings/
https://www.palantir.com/docs/foundry/security/classification-based-access-controls/
https://www.palantir.com/docs/foundry/security/restricted-views/
https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies/
https://www.palantir.com/docs/foundry/data-lineage/overview/
https://www.palantir.com/docs/foundry/getting-started/orientation-and-nav/
https://www.palantir.com/docs/foundry/compass/quicksearch/
https://www.palantir.com/docs/foundry/analytics/types-of-analysis/
https://www.palantir.com/docs/foundry/geospatial/geotemporal-series-overview/
https://www.palantir.com/docs/foundry/geospatial/faq/
https://www.palantir.com/docs/foundry/notepad/overview/
https://www.palantir.com/docs/gotham/api/
https://www.palantir.com/platforms/gotham/
https://assets.applytosupply.digitalmarketplace.service.gov.uk/g-cloud-14/documents/92736/801146272055049-service-definition-document-2024-05-02-1537.pdf
https://irp.fas.org/dni/icd/icd-203.pdf
https://nvlpubs.nist.gov/nistpubs/ir/2021/NIST.IR.8312.pdf
https://www.law.cornell.edu/cfr/text/32/2001.21
https://www.archives.gov/files/isoo/training/marking-booklet-revision.pdf
https://www.gov.uk/government/publications/government-security-classifications
https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html
https://carto.com/basemaps/
https://windward.ai/
https://www.starboardintelligence.com/
https://www.starboardintelligence.com/platform
https://unseenlabs.com/en/
https://www.kpler.com/
https://www.kpler.com/product/maritime/kplerais
https://globalfishingwatch.org/our-map/
https://palantir.com/docs/foundry/monitoring-views