"use client";
import { useState } from "react";
import { CATEGORIES, OPTIONS, type Category } from "@/lib/options";
import { Dots, Pill, Section } from "./ui";
import { usePalette } from "@/lib/palette";

const COST = ["Free", "Low", "Usage-based", "Enterprise license"];

export default function OptionsSection() {
  const pal = usePalette();
  const [cat, setCat] = useState<Category | "All">("All");
  const [inHouseOnly, setInHouseOnly] = useState(false);
  const list = OPTIONS.filter((o) => (cat === "All" || o.category === cat) && (!inHouseOnly || o.inHouse));
  const catColor = (c: Category) => pal[CATEGORIES.indexOf(c)];

  return (
    <Section id="options" kicker="Question 1" title="Graph storage options beyond NetworkX"
      intro={<>NetworkX is a good place to start, but it is a library, not a database. The options below range from embedded libraries to clustered servers, and <b>all but the last can run in-house</b>. Two points for the client: (1) GraphRAG as a method does not require a graph database, since Microsoft&apos;s reference implementation stores the graph in files; (2) a graph store becomes worthwhile once investigators and the LLM need live, multi-hop questions answered across the data.</>}>
      <div className="mb-6 flex flex-wrap items-center gap-2">
        {(["All", ...CATEGORIES] as const).map((c) => (
          <button key={c} onClick={() => setCat(c)}
            className={`rounded-full border px-3 py-1.5 text-sm transition ${cat === c ? "border-transparent bg-ink text-bg" : "border-line bg-surface text-ink-2 hover:bg-surface-2"}`}>
            {c}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-sm text-ink-2">
          <input type="checkbox" checked={inHouseOnly} onChange={(e) => setInHouseOnly(e.target.checked)} /> In-house only
        </label>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {list.map((o) => (
          <article key={o.name} className="card flex flex-col p-5">
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-lg font-bold leading-snug"><span className="mr-1.5">{o.emoji}</span>{o.name}</h3>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Pill color={catColor(o.category)}>{o.category}</Pill>
              {o.languages.map((l) => <Pill key={l}>{l}</Pill>)}
            </div>
            <p className="mt-3 text-sm text-ink-2"><b className="text-ink">Best for:</b> {o.bestFor}</p>
            <p className="mt-2 text-sm text-ink-2"><b className="text-ink">Watch out:</b> {o.watchOut}</p>
            <div className="mt-auto grid grid-cols-2 gap-2 pt-4 text-xs text-muted">
              <div>License<br /><span className="text-sm font-semibold text-ink">{o.license}</span></div>
              <div>Cost tier<br /><span className="text-sm font-semibold text-ink">{COST[o.costTier]}</span></div>
              <div className="col-span-2 flex items-center justify-between border-t border-line pt-2">
                <span>GraphRAG fit</span><Dots n={o.graphragFit} color={pal[0]} />
              </div>
            </div>
          </article>
        ))}
      </div>
    </Section>
  );
}
