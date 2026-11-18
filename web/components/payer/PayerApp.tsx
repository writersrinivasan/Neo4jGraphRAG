"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getJson, type Neo4jStatus } from "@/lib/agentApi";
import { usePalette } from "@/lib/palette";
import { Section, Stat } from "../ui";
import Chat from "./Chat";
import Comparison from "./Comparison";
import KeyPanel from "./KeyPanel";
import Neo4jInspector from "./Neo4jInspector";
import SubgraphView from "./SubgraphView";

interface Overview {
  stats: { nodes: number; edges: number; nodeTypes: Record<string, number>; edgeTypes: Record<string, number>; communities: number; flaggedEntities: number };
  communities: { id: string; members: number; providers: number; claims: number; paid: number; denial_rate: number; risk_flags: number }[];
  suggestions: string[];
  startupSec: number;
}
interface Eval { pattern: string; found: number; planted: number; truePositives: number; precision: number | null; recall: number; note?: string }

const FLOW = [
  ["link_entities", "🔗", "Regex and dictionary match IDs, drugs, plans and CARC codes in the question (falls back to chat history for follow-ups)."],
  ["plan", "🧭", "The Groq LLM picks 1-4 graph tools as JSON. Without a key, keyword rules choose them."],
  ["run_tools", "🛠️", "Runs NetworkX tools: local search, global (community) search, fraud rings, PA gaps, leakage, doctor shopping, paths."],
  ["reflect", "🤔", "If the evidence is thin, adds global search and loops back (a conditional edge)."],
  ["answer", "✍️", "Groq writes a grounded answer with [ID] citations and payer actions, capped at 11k chars of context."],
  ["verify", "✅", "Every cited ID is checked against the graph, and invented IDs are flagged."],
];

export default function PayerApp() {
  const pal = usePalette();
  const [ov, setOv] = useState<Overview | null>(null);
  const [ev, setEv] = useState<Eval[]>([]);
  const [down, setDown] = useState(false);
  const [byok, setByok] = useState<{ key: string; model: string } | null>(null);
  const [entity, setEntity] = useState<string | null>(null);
  const [backend, setBackend] = useState<"networkx" | "neo4j">("networkx");
  const [n4, setN4] = useState<Neo4jStatus | null>(null);

  useEffect(() => {
    getJson<Overview>("/api/overview").then(setOv).catch(() => setDown(true));
    getJson<Eval[]>("/api/evaluation").then(setEv).catch(() => {});
    getJson<Neo4jStatus>("/api/neo4j/status").then((s) => { setN4(s); if (s.connected) setBackend("neo4j"); }).catch(() => setN4({ connected: false, error: "agent server unreachable" }));
  }, []);

  return (
    <main>
      <nav className="sticky top-0 z-20 border-b border-line bg-surface/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 overflow-x-auto px-4 py-3 sm:px-6">
          <Link href="/" className="shrink-0 text-sm text-ink-2 hover:text-ink">← Strategy</Link>
          <span className="shrink-0 font-extrabold"><span className="text-grad">Payer</span> GraphRAG Copilot</span>
          {[["copilot", "Copilot"], ["neo4j", "Neo4j retrieval"], ["agent", "LangGraph agent"], ["evaluation", "Detector accuracy"], ["compare", "SQL vs NetworkX"]].map(([id, l]) => (
            <a key={id} href={`#${id}`} className="shrink-0 rounded-full px-3 py-1.5 text-sm text-ink-2 hover:bg-surface-2">{l}</a>
          ))}
        </div>
      </nav>

      <header className="hero-grad text-white">
        <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
          <p className="inline-block rounded-full bg-white/20 px-3 py-1 text-xs font-semibold">Health plan (payer) · NetworkX · LangGraph · Groq (BYOK)</p>
          <h1 className="mt-3 max-w-3xl text-3xl font-extrabold leading-tight sm:text-4xl">An AI copilot for SIU, utilization management and network teams</h1>
          <p className="mt-3 max-w-2xl text-white/90">A LangGraph agent answers questions about claims, prior authorizations, pharmacy fills and provider networks by running GraphRAG over a NetworkX knowledge graph. Every answer cites graph entities, and every citation is verified.</p>
          {ov && (
            <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[[ov.stats.nodes.toLocaleString(), "graph nodes"], [ov.stats.edges.toLocaleString(), "graph edges"], [ov.stats.communities, "GraphRAG communities"], [ov.stats.flaggedEntities, "entities flagged by detectors"]].map(([v, l]) => (
                <div key={String(l)} className="rounded-2xl bg-white/15 p-4"><p className="text-2xl font-extrabold tabular-nums">{v}</p><p className="text-sm text-white/85">{l}</p></div>
              ))}
            </div>
          )}
        </div>
      </header>

      {down && (
        <div className="mx-auto mt-6 max-w-6xl px-4 sm:px-6">
          <div className="card border-amber-500/50 p-4 text-sm">⚠ The agent server isn&apos;t reachable at port 8000. Start it with <code className="rounded bg-surface-2 px-1">cd payer-agent && .venv/bin/uvicorn server:app --port 8000</code>, then reload.</div>
        </div>
      )}

      <Section id="copilot" kicker="Ask the graph" title="Payer copilot"
        intro="Add your Groq key for AI-written answers, or ask without one to see the raw graph evidence. Click any [ID] in an answer to explore its neighbourhood.">
        <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
          <Chat byok={byok} suggestions={ov?.suggestions ?? []} onEntity={setEntity} backend={backend} setBackend={setBackend} neo4jReady={!!n4?.connected} />
          <div className="space-y-4">
            <KeyPanel onChange={setByok} />
            <div className="card p-5">
              <h3 className="font-bold">🕸️ Subgraph {entity && <span className="font-mono text-[#6d5dfc]">{entity}</span>}</h3>
              <div className="mt-3"><SubgraphView entity={entity} onSelect={setEntity} /></div>
            </div>
          </div>
        </div>
      </Section>

      <Section id="neo4j" kicker="Neo4j Aura" title="How GraphRAG retrieval works, step by step"
        intro={<>The same payer graph lives in Neo4j. Retrieval runs as a pipeline: find entry points in three ways (exact ID, BM25 full-text, vector similarity), fuse them, <b>expand along relationships with Cypher</b>, add community summaries, optionally let the LLM write Cypher, then assemble the context. Each stage below shows the Cypher it ran and what it returned.</>}>
        <Neo4jInspector status={n4} byok={byok} />
      </Section>

      <Section id="agent" kicker="Agentic AI" title="How the LangGraph agent works"
        intro={<>A <code>StateGraph</code> with six nodes and one conditional loop. The Groq model is built for each request from your key and passed in <code>config[&quot;configurable&quot;]</code>, so the key never enters graph state.</>}>
        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
          {FLOW.map(([n, icon, d], i) => (
            <div key={n} className="card relative p-4">
              <span className="absolute inset-x-0 top-0 h-1 rounded-t-[18px]" style={{ background: pal[i] }} />
              <p className="text-xl">{icon}</p>
              <p className="mt-1 font-mono text-sm font-bold">{n}</p>
              <p className="mt-1 text-xs text-ink-2">{d}</p>
            </div>
          ))}
        </div>
        <pre className="code mt-4 text-xs">{`START → link_entities → plan → run_tools → reflect ─┬─(evidence thin)→ run_tools
                                                    └─(enough)───────→ answer → verify → END`}</pre>
        {ov && (
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className="card p-5">
              <h3 className="font-bold">Knowledge graph schema</h3>
              <p className="mt-1 text-xs text-muted">Node types (count)</p>
              <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(ov.stats.nodeTypes).map(([t, n]) => <span key={t} className="rounded-full bg-surface-2 px-2.5 py-1 text-xs"><b>{t}</b> {n.toLocaleString()}</span>)}</div>
              <p className="mt-3 text-xs text-muted">Edge types (count)</p>
              <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(ov.stats.edgeTypes).map(([t, n]) => <span key={t} className="rounded-full bg-surface-2 px-2.5 py-1 font-mono text-xs">{t} {n.toLocaleString()}</span>)}</div>
            </div>
            <div className="card overflow-x-auto p-5">
              <h3 className="font-bold">GraphRAG communities (Louvain)</h3>
              <p className="mt-1 text-xs text-muted">Each community gets a statistical report that global search ranks against the question.</p>
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted"><tr><th className="py-1">ID</th><th className="text-right">Members</th><th className="text-right">Providers</th><th className="text-right">Paid</th><th className="text-right">Denial</th><th className="text-right">Flags</th></tr></thead>
                <tbody>{ov.communities.map((c) => (
                  <tr key={c.id} className="border-t border-line"><td className="py-1.5 font-mono">{c.id}</td><td className="text-right tabular-nums">{c.members}</td><td className="text-right tabular-nums">{c.providers}</td>
                    <td className="text-right tabular-nums">${(c.paid / 1000).toFixed(0)}k</td><td className="text-right tabular-nums">{(c.denial_rate * 100).toFixed(1)}%</td><td className="text-right tabular-nums">{c.risk_flags}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        )}
      </Section>

      <Section id="evaluation" kicker="Trust but verify" title="Do the graph detectors find what was planted?"
        intro="The simulator hides five payer schemes. The detectors (pure NetworkX, no LLM) never see the answer key. The comparison below is only possible because the data is simulated.">
        <div className="grid gap-4 sm:grid-cols-3">
          {ev.slice(0, 3).map((e, i) => <Stat key={e.pattern} accent={pal[i]} label={e.pattern} value={`${Math.round(e.recall * 100)}% recall`} sub={`${e.truePositives}/${e.planted} planted found · precision ${e.precision != null ? Math.round(e.precision * 100) + "%" : "n/a"}`} />)}
        </div>
        <div className="card mt-4 overflow-x-auto">
          <table className="w-full min-w-[700px] text-sm">
            <thead className="bg-surface-2 text-left text-xs uppercase text-muted"><tr><th className="px-4 py-3">Pattern</th><th className="px-3 py-3 text-right">Planted</th><th className="px-3 py-3 text-right">Flagged</th><th className="px-3 py-3 text-right">Precision</th><th className="px-3 py-3 text-right">Recall</th><th className="px-4 py-3">Why</th></tr></thead>
            <tbody>{ev.map((e) => (
              <tr key={e.pattern} className="border-t border-line align-top"><td className="px-4 py-3 font-semibold">{e.pattern}</td><td className="px-3 py-3 text-right">{e.planted}</td><td className="px-3 py-3 text-right">{e.found}</td>
                <td className="px-3 py-3 text-right">{e.precision != null ? `${Math.round(e.precision * 100)}%` : "-"}</td><td className="px-3 py-3 text-right">{Math.round(e.recall * 100)}%</td><td className="px-4 py-3 text-xs text-ink-2">{e.note ?? ""}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section id="compare" kicker="Comparison" title="Traditional SQL joins vs NetworkX nodes & edges"
        intro="The concepts first, then nine real payer questions run on both engines against the same simulated data. Every pair of answers is checked for equality before timing.">
        <Comparison />
      </Section>
      <p className="pb-10 text-center text-xs text-muted">Simulated payer data only · no PHI · the backend started in {ov?.startupSec ?? "?"} s</p>
    </main>
  );
}
