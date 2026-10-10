// Entry for check_bundle.mjs: the frontend's own embedded adapter, column decoder and identity rules, bundled in memory
// with rolldown and added to the checked page as an inline script, so the spot check reads records exactly as the page.
import { EmbeddedAdapter } from "../frontend/src/adapters/embedded";
import { decodePart } from "../frontend/src/adapters/columns";
import { identityLabel, reviewGrade } from "../frontend/src/app/identity";

(window as unknown as { __scsCheck: unknown }).__scsCheck = { EmbeddedAdapter, decodePart, identityLabel, reviewGrade };
