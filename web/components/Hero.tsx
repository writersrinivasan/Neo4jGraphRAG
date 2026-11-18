import Link from "next/link";
import data from "@/data/analytics.json";
import { maxSpeed } from "@/lib/stats";

const NAV = [
  ["options", "Graph DB options"],
  ["languages", "Query languages"],
  ["budget", "Budget / TCO"],
  ["analytics", "Analytics"],
  ["roadmap", "Roadmap"],
];

export default function Hero() {
  const d = data.dataset;
  return (
    <>
      <nav className="sticky top-0 z-20 border-b border-line bg-surface/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 overflow-x-auto px-4 py-3 sm:px-6">
          <span className="shrink-0 font-extrabold"><span className="text-grad">Graph</span>RAG · Health</span>
          <div className="flex gap-1">
            {NAV.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="shrink-0 rounded-full px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2 hover:text-ink">{label}</a>
            ))}
          </div>
          <Link href="/payer" className="ml-auto shrink-0 rounded-full bg-[#6d5dfc] px-3 py-1.5 text-sm font-semibold text-white">Payer Copilot →</Link>
        </div>
      </nav>
      <header className="hero-grad text-white">
        <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">
          <p className="inline-block rounded-full bg-white/20 px-3 py-1 text-xs font-semibold tracking-wide">Health insurance · Legacy MySQL modernisation</p>
          <h1 className="mt-4 max-w-3xl text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl">
            Turn claims tables into a knowledge graph your AI can reason over
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-white/90">
            GraphRAG connects members, providers, claims and diagnoses so investigators and LLMs can follow relationships
            that SQL joins hide. The infrastructure stays in-house and is built on open-source components.
          </p>
          <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              [d.claims.toLocaleString(), "claims analysed"],
              [`up to ${maxSpeed.toFixed(1)}×`, "faster multi-hop queries"],
              [`${Math.round(data.fraud.recall * 100)}%`, "planted fraud rings found"],
              [`${(data.graphrag.sqlDumpTokens / data.graphrag.graphContextTokens).toFixed(1)}×`, "fewer LLM context tokens"],
            ].map(([v, l]) => (
              <div key={l} className="rounded-2xl bg-white/15 p-4 backdrop-blur">
                <p className="text-2xl font-extrabold tabular-nums sm:text-3xl">{v}</p>
                <p className="text-sm text-white/85">{l}</p>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs text-white/75">All figures were computed by <code>analysis/run_analysis.py</code> on a synthetic dataset with no PHI ({d.members.toLocaleString()} members, {d.providers.toLocaleString()} providers).</p>
        </div>
      </header>
    </>
  );
}
