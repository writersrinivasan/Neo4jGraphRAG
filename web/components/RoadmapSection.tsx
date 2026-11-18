"use client";
import { usePalette } from "@/lib/palette";
import { Section } from "./ui";
import { speedRange, tokenRatio } from "@/lib/stats";

const FLOW = [
  ["🐬", "Legacy MySQL", "Stays the system of record for claims, members, providers and policies. Nothing is ripped out."],
  ["🔄", "CDC / ETL", "Debezium or nightly jobs map tables to nodes and foreign keys to edges."],
  ["🐘", "Graph + vector layer", "PostgreSQL with Apache AGE (Cypher) and pgvector, or Neo4j. Role-based access, audit logs, PHI stays in-house."],
  ["🧠", "GraphRAG", "Entities extracted from clinical notes, community summaries, and hybrid graph + vector retrieval."],
  ["💬", "AI apps", "Fraud investigator copilot, prior-auth assistant, member-service Q&A."],
];

const PHASES = [
  { t: "Phase 0 · 2-4 weeks", h: "Prove it with NetworkX", b: "Export one region or line of business. Run this repo's analytics on real data inside the client network. Cost: no licenses, about 1 engineer." },
  { t: "Phase 1 · 2-3 months", h: "Pilot graph DB + GraphRAG", b: "PostgreSQL + AGE + pgvector (or Neo4j Community) on one server. Text-to-Cypher and the investigator copilot on fraud SIU cases." },
  { t: "Phase 2 · 3-6 months", h: "Production with HA", b: "Patroni HA for Postgres, or Neo4j/Memgraph Enterprise if a vendor SLA is required. RBAC, HIPAA audit trail, CDC from MySQL." },
  { t: "Phase 3 · ongoing", h: "Scale and extend", b: "Real-time claim-intake scoring, SNOMED/ICD ontology layer (RDF), and a distributed engine only once the graph passes ~1B edges." },
];

export default function RoadmapSection() {
  const pal = usePalette();
  return (
    <Section id="roadmap" kicker="Recommendation" title="An incremental path the client can say yes to"
      intro="The recommendation is to keep MySQL and add a graph layer alongside it. Start free and in-house, and pay for an enterprise license only when the client needs an HA cluster with a vendor SLA.">
      <div className="grid gap-3 md:grid-cols-5">
        {FLOW.map(([e, h, b], i) => (
          <div key={h} className="card relative p-4">
            <span className="absolute inset-x-0 top-0 h-1 rounded-t-[18px]" style={{ background: pal[i] }} />
            <p className="text-2xl">{e}</p>
            <p className="mt-1 font-bold">{h}</p>
            <p className="mt-1 text-sm text-ink-2">{b}</p>
            {i < FLOW.length - 1 && <span className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-xl text-muted md:block">→</span>}
          </div>
        ))}
      </div>
      <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {PHASES.map((p, i) => (
          <div key={p.h} className="card p-5">
            <p className="text-xs font-bold uppercase tracking-wide" style={{ color: pal[i] }}>{p.t}</p>
            <p className="mt-1 text-lg font-bold">{p.h}</p>
            <p className="mt-2 text-sm text-ink-2">{p.b}</p>
          </div>
        ))}
      </div>
      <div className="hero-grad mt-8 rounded-3xl p-6 text-white sm:p-8">
        <p className="text-xl font-extrabold sm:text-2xl">Summary for the client</p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-white/95">
          <li>Graph queries answered the multi-hop fraud and referral questions <b>{speedRange} faster</b> than SQL in our measurements, and the queries are much shorter.</li>
          <li>An <b>open-source, in-house</b> stack (PostgreSQL + AGE + pgvector) avoids license fees and keeps PHI inside the client&apos;s firewall.</li>
          <li>GraphRAG gives the LLM <b>{tokenRatio}× smaller, relationship-rich context</b>, so answers are cheaper and easier to trace and explain.</li>
          <li>Learning <b>Cypher, which the ISO GQL standard is based on</b>, protects the team&apos;s skills and queries from vendor lock-in.</li>
        </ul>
      </div>
      <p className="mt-10 text-center text-xs text-muted">Synthetic data only · TCO figures are illustrative · Regenerate the numbers with <code>npm run analyze</code></p>
    </Section>
  );
}
