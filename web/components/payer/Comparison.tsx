"use client";
import { Fragment, useEffect, useState } from "react";
import { getJson } from "@/lib/agentApi";
import { usePalette } from "@/lib/palette";

interface Row {
  id: string; title: string; category: string; depth: string; sql: string | null; nx_code: string;
  sql_ms: number | null; nx_ms: number; joins: number | null; sql_lines: number | null; nx_lines: number;
  match: boolean | null; result_size: number; winner: string; speedup: number | null;
}
interface Conceptual { aspect: string; sql: string; networkx: string }

export default function Comparison() {
  const pal = usePalette();
  const [data, setData] = useState<{ rows: Row[]; conceptual: Conceptual[] } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { getJson<{ rows: Row[]; conceptual: Conceptual[] }>("/api/compare").then(setData).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="text-sm text-red-500">Could not load the comparison ({err}). Start the agent server.</p>;
  if (!data) return <p className="text-sm text-muted">Running benchmarks…</p>;
  const max = Math.max(...data.rows.flatMap((r) => [r.sql_ms ?? 0, r.nx_ms]));
  const bar = (v: number | null, c: string) => v == null ? <span className="text-xs text-muted">n/a</span> : (
    <div className="flex items-center gap-2">
      <div className="h-2 rounded-full" style={{ width: `${Math.max(3, (Math.log10(v + 1) / Math.log10(max + 1)) * 90)}px`, background: c }} />
      <span className="tabular-nums">{v < 1 ? v.toFixed(2) : v.toFixed(1)}</span>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
            <tr><th className="px-4 py-3 w-48">Aspect</th><th className="px-4 py-3">Traditional SQL joins (MySQL / SQLite)</th><th className="px-4 py-3">NetworkX nodes & edges</th></tr>
          </thead>
          <tbody>
            {data.conceptual.map((c) => (
              <tr key={c.aspect} className="border-t border-line align-top">
                <td className="px-4 py-3 font-semibold">{c.aspect}</td>
                <td className="px-4 py-3 text-ink-2">{c.sql}</td>
                <td className="px-4 py-3 text-ink-2">{c.networkx}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card overflow-x-auto">
        <div className="flex flex-wrap items-center gap-4 px-4 pt-4 text-xs text-ink-2">
          <span className="font-semibold text-ink">Live benchmark: same data, same answer, measured on this machine (median ms, log-scaled bars)</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-full" style={{ background: pal[1] }} />SQL (SQLite)</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-full" style={{ background: pal[0] }} />NetworkX</span>
        </div>
        <table className="mt-2 w-full min-w-[920px] text-sm">
          <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-4 py-3">Payer question</th><th className="px-3 py-3">Depth</th><th className="px-3 py-3 text-right">SQL joins</th>
              <th className="px-3 py-3">SQL ms</th><th className="px-3 py-3">NetworkX ms</th><th className="px-3 py-3">Faster</th><th className="px-3 py-3">Same result</th><th />
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <Fragment key={r.id}>
                <tr className="border-t border-line">
                  <td className="px-4 py-3"><b>{r.id}</b> {r.title}<div className="text-xs text-muted">{r.category} · {r.result_size} results</div></td>
                  <td className="px-3 py-3 text-ink-2">{r.depth}</td>
                  <td className="px-3 py-3 text-right tabular-nums">{r.joins ?? "-"}</td>
                  <td className="px-3 py-3">{bar(r.sql_ms, pal[1])}</td>
                  <td className="px-3 py-3">{bar(r.nx_ms, pal[0])}</td>
                  <td className="px-3 py-3 font-semibold">{r.winner}{r.speedup ? <span className="font-normal text-muted"> ×{r.speedup}</span> : ""}</td>
                  <td className="px-3 py-3">{r.match === null ? "n/a" : r.match ? "✅" : "❌"}</td>
                  <td className="px-3 py-3"><button onClick={() => setOpen(open === r.id ? null : r.id)} className="text-xs text-[#6d5dfc] underline">{open === r.id ? "hide" : "code"}</button></td>
                </tr>
                {open === r.id && (
                  <tr className="bg-surface-2/60">
                    <td colSpan={8} className="px-4 pb-4">
                      <div className="grid gap-3 md:grid-cols-2">
                        <div><p className="mb-1 text-xs font-semibold text-muted">SQL ({r.sql_lines ?? 0} lines)</p><pre className="code">{r.sql ?? "-- Not practical in SQL: PageRank is iterative.\n-- Requires a procedural loop or exporting the data."}</pre></div>
                        <div><p className="mb-1 text-xs font-semibold text-muted">NetworkX (sketch)</p><pre className="code">{r.nx_code}</pre></div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        <p className="px-4 py-3 text-xs text-muted">
          How to read this: pure-Python NetworkX loses to SQLite&apos;s C engine on flat scans and aggregations (Q2, Q4, Q7). It wins on traversal, paths and point
          context (Q3, Q6, Q8), and Q9 is practical only on the graph. GraphRAG mostly runs Q8-style lookups. Keep SQL for reporting and use the graph for relationships.
        </p>
      </div>
    </div>
  );
}
