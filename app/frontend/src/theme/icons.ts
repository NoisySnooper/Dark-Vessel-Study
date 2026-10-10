// Icons: only the toolkit icons this product names are bundled (40 of 2,121, 16 px and 20 px paths), through the
// custom loader the icons package documents (`Icons.setLoaderOptions({ loader })`, @blueprintjs/icons 6.14.1
// lib/esm/iconLoader.d.ts). The package's default loaders import every icon's paths, about 0.6 MB in the single-file
// page; vite.config.ts replaces those two loader modules with a stub so they are not bundled at all.
// The list below is the glob of both sizes. scripts/check_build.mjs fails the build when the source names an icon
// that is not in it; at run time a missing icon draws blank and logs "icon not bundled" (the smoke check fails on it).
import { Icons, IconSize, type IconName } from "@blueprintjs/icons";

const P16 = import.meta.glob<string[]>(
  "/node_modules/@blueprintjs/icons/lib/esm/generated/16px/paths/{arrow-left,arrow-right,build,cell-tower,chevron-down,chevron-up,clipboard,cross,document,document-open,download,drive-time,error,export,eye-open,filter-remove,flash,grid,help,history,inbox,info-sign,layers,locate,map,media,menu,moon,panel-stats,satellite,search,search-around,square,step-backward,step-forward,tick,time,timeline-events,undo,warning-sign}.js",
  { eager: true, import: "default" },
);
const P20 = import.meta.glob<string[]>(
  "/node_modules/@blueprintjs/icons/lib/esm/generated/20px/paths/{arrow-left,arrow-right,build,cell-tower,chevron-down,chevron-up,clipboard,cross,document,document-open,download,drive-time,error,export,eye-open,filter-remove,flash,grid,help,history,inbox,info-sign,layers,locate,map,media,menu,moon,panel-stats,satellite,search,search-around,square,step-backward,step-forward,tick,time,timeline-events,undo,warning-sign}.js",
  { eager: true, import: "default" },
);

function byName(glob: Record<string, string[]>): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const [path, paths] of Object.entries(glob)) m.set(path.replace(/^.*\/([a-z0-9-]+)\.js$/, "$1"), paths);
  return m;
}
const T16 = byName(P16);
const T20 = byName(P20);
export const BUNDLED_ICONS = [...T16.keys()].sort();

const warned = new Set<string>();
Icons.setLoaderOptions({
  loader: async (name: IconName, size: IconSize) => {
    const paths = (size >= IconSize.LARGE ? T20 : T16).get(name);
    if (paths) return paths;
    if (!warned.has(name)) {
      warned.add(name);
      console.warn(`[scs] icon not bundled: ${name} (add it to src/theme/icons.ts)`);
    }
    return [];
  },
});

/** Load every bundled icon at both sizes before the first render, so no icon draws blank for a frame. */
export async function preloadIcons(): Promise<void> {
  const names = BUNDLED_ICONS as IconName[];
  await Promise.all([Icons.load(names, IconSize.STANDARD), Icons.load(names, IconSize.LARGE)]);
}
