// About: method, sources, licences, limits (spec sections 2, 14, 17). Third-party licence texts are listed by package and
// licence; the full Apache-2.0 text is reproduced once for the toolkit packages (section 4(a)).
import { Callout, Section, SectionCard } from "@blueprintjs/core";
import { useApp } from "../app/state";
import { AISSTREAM_NOTE, CNN_HELD_OUT, CNN_LIMITS, DATA_CREDIT, EEZ_DISCLAIMER, EEZ_STATEMENT, OCEAN_NOTE, PRODUCT_NAME } from "../app/text";
import { ObjectHeader, CaveatCallout } from "./common";
import { THIRD_PARTY } from "./licences";

export function AboutPage() {
  const { meta, adapter } = useApp();
  return (
    <article className="scs-page scs-about" data-page="about">
      <ObjectHeader title={`About ${PRODUCT_NAME}`} icon="info-sign" back="leads" subtitle={`Build ${meta.build}, contract ${meta.contract_version}, generated ${meta.generated_utc}, git ${meta.git_hash}`} />
      <CaveatCallout caveat={meta.caveat} />
      {meta.fixture && <Callout intent={meta.fixture.synthetic ? "danger" : "warning"} compact style={{ marginTop: 8 }}>FIXTURE: {meta.fixture.note}</Callout>}
      <Section title="What the product answers" compact collapsible>
        <SectionCard>
          <p>For every Sentinel-1C/1D radar contact over the South China Sea AOI the product answers, in this order: <strong>Detection</strong>, is it a vessel (detector class, clutter and fixed-structure rules, persistence, CNN verifier score, optical and Satlas checks where they exist); and <strong>Identification</strong>, who is it (an AIS match to an MMSI with its self-reported identity, or, when nothing matches, a dark lead with all its evidence). Everything else is context.</p>
          <p>Status words: <em>AIS matched</em> (paired under the stated gate); <em>No AIS match (AIS heard nearby)</em> (the only state that can become a dark lead); <em>No AIS coverage here</em> (nothing heard near the contact during the window; says nothing about the contact); <em>AIS not checked</em> (no AIS source for that run in this build). The word dark appears only as "dark lead", next to the caveat. Review priority is not a risk score: it is a sum of factor points shown with the factors, uncalibrated until the owner's labels exist.</p>
        </SectionCard>
      </Section>
      <Section title="Method and limits" compact collapsible>
        <SectionCard>
          <ul>
            <li>Detector: CA-CFAR on VV and VH, PFA 1e-6, clutter-zone and near-fixed rules, persistence across earlier passes. Radar length is a pixel extent: crude and biased upward (a 2-pixel object reads 20 m).</li>
            <li>CNN verifier verifier_v0: {CNN_HELD_OUT}. Knowledge limits: {CNN_LIMITS}.</li>
            <li>AIS (open build): {AISSTREAM_NOTE} Terrestrial receivers cover only the waters within their radio range: dense near Hong Kong, the Singapore and Bangka straits and the Philippines, thin off Vietnam. Most open-sea contacts are therefore "no AIS coverage", a statement about the receivers, not the vessels.</li>
            <li>{OCEAN_NOTE}</li>
            <li>Maritime boundaries: {EEZ_STATEMENT} {EEZ_DISCLAIMER}</li>
            <li>Two forms from one frontend: the local app reads every file through the API; this {adapter.kind === "embedded" ? "single-file page holds a subset in an embedded bundle (the Info tab lists what was left out)" : "app polls the local API"}.</li>
          </ul>
        </SectionCard>
      </Section>
      <Section title="Sources and licences, as published" compact collapsible>
        <SectionCard>
          <div className="scs-table-scroll"><table className="bp6-html-table bp6-compact" style={{ width: "100%" }}>
            <thead><tr><th>key</th><th>source</th><th>method, script</th><th>licence</th><th>access date</th></tr></thead>
            <tbody>
              {meta.sources.map((s) => (
                <tr key={s.key}><td>{s.key}</td><td>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</td><td>{s.method}{s.script ? `; ${s.script}` : ""}</td><td>{s.licence}{s.licence_url ? <> (<a href={s.licence_url} target="_blank" rel="noreferrer">text</a>)</> : null}</td><td>{s.access_date}</td></tr>
              ))}
            </tbody>
          </table></div>
          <p className="scs-muted" style={{ fontSize: 12 }}>{DATA_CREDIT}. {meta.sources.filter((s) => s.credit).map((s) => s.credit).filter((c, i, a) => a.indexOf(c) === i).join(". ")}.</p>
        </SectionCard>
      </Section>
      <Section title="Third-party software" compact collapsible>
        <SectionCard>
          <div className="scs-table-scroll"><table className="bp6-html-table bp6-compact" style={{ width: "100%" }}>
            <thead><tr><th>package</th><th>version</th><th>licence</th></tr></thead>
            <tbody>{THIRD_PARTY.packages.map((p) => <tr key={p.name}><td>{p.name}</td><td>{p.version}</td><td>{p.licence}</td></tr>)}</tbody>
          </table></div>
          {THIRD_PARTY.texts.map((t) => (
            <details key={t.name} style={{ marginTop: 8 }}><summary>{t.name}</summary><pre>{t.text}</pre></details>
          ))}
        </SectionCard>
      </Section>
    </article>
  );
}
