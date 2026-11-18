"use client";
import { useState } from "react";
import { retrieveNeo4j, type Neo4jStatus, type Retrieval, type Stage } from "@/lib/agentApi";
import { usePalette } from "@/lib/palette";

const PRESETS = [
  "Why are claims from P0379 being denied so often?",
  "Which pharmacies dispense opioids to doctor-shopping members?",
  "sleep apnea CPAP billing patterns",
  "How many claims were denied for each denial reason?",
];

function Results({ s }: { s: Stage }) {
  if (s.error) return <p className="text-sm text-red-500">⚠ {s.error}</p>;
  if (s.stage === "context") {
    const c = s.results[0] as { chars: number; approx_tokens: number; text: string } | undefined;
    return c ? (
      <div>
        <p className="text-sm text-ink-2">{c.chars.toLocaleString()} characters ≈ <b>{c.approx_tokens.toLocaleString()} tokens</b> sent to the LLM</p>
        <pre className="code mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs">{c.text}</pre>
      </div>
    ) : null;
  }
  if (!s.results.length) return <p className="text-sm text-muted">No results at this stage.</p>;
  if (s.stage === "expansion" || s.stage === "community" || s.stage === "text2cypher") {
    return <pre className="code max-h-72 overflow-auto whitespace-pre-wrap text-xs">{s.results.map((r) => JSON.stringify(r, null, 1)).join("\n\n")}</pre>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {s.results.map((r, i) => (
        <span key={i} className="rounded-full border border-line bg-surface-2 px-2.5 py-1 font-mono text-xs">
          {String(r.id)} <span className="text-muted">{String(r.label ?? "")}</span> <b>{String(r.score ?? r.rrf ?? "")}</b>
        </span>
      ))}
    </div>
  );
}

export default function Neo4jInspector({ status, byok }: { status: Neo4jStatus | null; byok: { key: string; model: string } | null }) {
  const pal = usePalette();
  const [q, setQ] = useState(PRESETS[0]);
  const [res, setRes] = useState<Retrieval | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function run(question = q) {
    setQ(question); setBusy(true); setErr("");
    try { setRes(await retrieveNeo4j(question, byok)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  if (!status) return <p className="text-sm text-muted">Checking Neo4j…</p>;
  if (!status.connected) return <div className="card p-5 text-sm">⚠ Neo4j is not connected: {status.error}. Add credentials to <code>payer-agent/.env</code> and run <code>load_neo4j.py</code>.</div>;
  const maxMs = Math.max(1, ...(res?.stages.map((s) => s.ms) ?? [1]));

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-[1fr_2fr]">
        <div className="card p-5">
          <h3 className="font-bold">🟢 Neo4j Aura connected</h3>
          <p className="mt-1 text-sm text-ink-2">{status.nodes?.toLocaleString()} nodes · {status.relationships?.toLocaleString()} relationships</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {status.indexes?.map((i) => <span key={i.name} className="rounded-full bg-surface-2 px-2.5 py-1 font-mono text-xs">{i.type === "VECTOR" ? "🧭" : "🔤"} {i.name} · {i.state}</span>)}
          </div>
          <p className="mt-3 text-xs text-muted">Credentials stay in <code>payer-agent/.env</code> on the server. TLS is verified. Queries run in read-only transactions.</p>
        </div>
        <div className="card p-5">
          <h3 className="font-bold">🔎 Run the retrieval pipeline</h3>
          <form onSubmit={(e) => { e.preventDefault(); run(); }} className="mt-3 flex gap-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} className="min-w-0 flex-1 rounded-xl border border-line bg-surface-2 px-3 py-2 text-sm" />
            <button disabled={busy} className="rounded-xl bg-[#6d5dfc] px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">{busy ? "Retrieving…" : "Retrieve"}</button>
          </form>
          <div className="mt-3 flex flex-wrap gap-2">
            {PRESETS.map((p) => <button key={p} onClick={() => run(p)} className="rounded-full border border-line bg-surface-2 px-3 py-1 text-xs hover:border-[#6d5dfc]">{p}</button>)}
          </div>
          <p className="mt-3 text-xs text-muted">{byok ? "Your Groq key is connected, so stage 7 (text-to-Cypher) will also run." : "Add a Groq key above to include stage 7, text-to-Cypher."}</p>
          {err && <p className="mt-2 text-sm text-red-500">⚠ {err}</p>}
        </div>
      </div>

      {res && (
        <div className="space-y-3">
          <p className="text-sm text-ink-2">Total retrieval time <b>{res.totalMs.toLocaleString()} ms</b> (each Aura round trip from this machine costs about 0.3 s). Seed entities: <span className="font-mono">{res.seeds.join(", ")}</span></p>
          {res.stages.map((s, i) => (
            <div key={s.stage} className="card overflow-hidden">
              <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
                <span className="h-3 w-3 rounded-full" style={{ background: pal[i % 8] }} />
                <h4 className="font-bold">{s.title}</h4>
                <div className="ml-auto flex items-center gap-2 text-xs text-muted">
                  <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full" style={{ width: `${(s.ms / maxMs) * 100}%`, background: pal[i % 8] }} /></div>
                  <span className="tabular-nums">{s.ms} ms</span>
                </div>
              </div>
              <div className="grid gap-4 p-5 lg:grid-cols-2">
                <div>
                  <p className="text-sm text-ink-2">{s.explain}</p>
                  <details className="mt-2" open={s.stage === "text2cypher"}>
                    <summary className="cursor-pointer text-xs text-muted">Cypher executed</summary>
                    <pre className="code mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-[#8fd18f]">{s.cypher}</pre>
                  </details>
                </div>
                <div className="min-w-0"><Results s={s} /></div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
