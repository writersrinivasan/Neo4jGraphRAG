"use client";
import { useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import data from "@/data/analytics.json";
import { usePalette } from "@/lib/palette";
import { usd } from "@/lib/tco";
import { ChartCard, Section, Stat } from "./ui";
import RingGraph from "./RingGraph";
import { speedRange } from "@/lib/stats";

const fmt = (n: number) => n.toLocaleString();
const tip = { cursor: { fill: "var(--surface-2)" } };

export default function AnalyticsSection() {
  const pal = usePalette();
  const [scaleM, setScaleM] = useState(100);
  const f = data.fraud;
  const g = data.graphrag;
  const bench = data.benchmark.map((b) => ({ ...b, label: `${b.hops} hop${b.hops > 1 ? "s" : ""}`, speedup: `${(b.sqlMs / b.graphMs).toFixed(1)}×` }));
  const nxGb = (scaleM * 1e6 * data.memory.bytesPerElement) / 1e9;
  const tokens = [
    { name: "Raw SQL rows dumped", tokens: g.sqlDumpTokens },
    { name: "GraphRAG local (2-hop subgraph)", tokens: g.graphContextTokens },
    { name: "GraphRAG global (community summary)", tokens: g.communitySummaryTokens },
  ];

  return (
    <Section id="analytics" kicker="Question 3 · continued" title="Analytics on a health-insurance claims graph"
      intro={<>A synthetic dataset with the shape of a payer&apos;s data ({fmt(data.dataset.members)} members, {fmt(data.dataset.providers)} providers, {fmt(data.dataset.claims)} claims and {data.dataset.plantedRings} hidden fraud rings) was loaded twice: into relational tables (SQLite, standing in for MySQL) and into a graph (NetworkX). Every number below was computed by <code>analysis/run_analysis.py</code>, and both engines were checked to return identical answers.</>}>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat accent={pal[0]} label="Graph size" value={fmt(data.dataset.graphNodes + data.dataset.graphEdges)} sub={`${fmt(data.dataset.graphNodes)} nodes · ${fmt(data.dataset.graphEdges)} edges`} />
        <Stat accent={pal[1]} label="Fraud exposure surfaced" value={usd(f.ringExposureUSD)} sub={`${f.candidateRings} rings · precision ${f.precision * 100}% · recall ${f.recall * 100}%`} />
        <Stat accent={pal[2]} label="Suspicious identity clusters" value={data.entity.suspiciousClusters} sub="5+ members sharing a phone/address" />
        <Stat accent={pal[3]} label="NetworkX memory (this graph)" value={`${data.memory.networkxMB} MB`} sub={`${data.memory.bytesPerElement} bytes per node+edge`} />
      </div>

      {/* 1. Benchmark */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Multi-hop query latency: SQL recursive CTE vs graph traversal (ms, median)"
          takeaway={`Graph traversal was ${speedRange} faster at every depth tested (${bench.map((b) => b.speedup).join(", ")} for 1-4 hops). Fraud, referral and household questions are all multi-hop.`}>
          <div style={{ height: 280 }}>
            <ResponsiveContainer>
              <BarChart data={bench} barGap={2}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="label" axisLine={false} tickLine={false} />
                <YAxis axisLine={false} tickLine={false} unit=" ms" width={60} />
                <Tooltip {...tip} formatter={(v: number) => `${v} ms`} />
                <Legend formatter={(v) => <span style={{ color: "var(--text-2)" }}>{v}</span>} />
                <Bar isAnimationActive={false} dataKey="sqlMs" name="SQL (relational)" fill={pal[1]} radius={[4, 4, 0, 0]} />
                <Bar isAnimationActive={false} dataKey="graphMs" name="Graph traversal" fill={pal[0]} radius={[4, 4, 0, 0]}>
                  <LabelList dataKey="speedup" position="top" fill="var(--text-2)" fontSize={12} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">Providers reached: {bench.map((b) => `${b.hops}h=${fmt(b.reached)}`).join(" · ")}. Small dataset; at production scale the gap is typically larger.</p>
        </ChartCard>

        <ChartCard title="Shared-patient counts between provider pairs" takeaway={`${fmt(f.providerPairsScanned)} provider pairs share patients. Normal pairs share 1-3; fraud-ring pairs share ${f.threshold}+, which makes them easy to spot.`}>
          <div style={{ height: 280 }}>
            <ResponsiveContainer>
              <BarChart data={f.sharedMemberHistogram}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="shared" axisLine={false} tickLine={false} />
                <YAxis scale="log" domain={[1, "auto"]} allowDataOverflow axisLine={false} tickLine={false} width={60} tickFormatter={fmt} />
                <Tooltip {...tip} labelFormatter={(l) => `${l} shared patients`} formatter={(v: number) => [fmt(v), "provider pairs"]} />
                <Bar isAnimationActive={false} dataKey="pairs" radius={[4, 4, 0, 0]}>
                  {f.sharedMemberHistogram.map((d) => <Cell key={d.shared} fill={d.shared >= f.threshold ? pal[7] : pal[0]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-xs text-muted">Log scale. Red bars are at or above the {f.threshold}-patient threshold. The equivalent SQL self-join took {f.sqlPairJoinMs} ms.</p>
        </ChartCard>
      </div>

      {/* 2. Fraud ring */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <ChartCard title="The highest-risk ring found by the graph" takeaway="Five same-region providers bill the same patients for high-cost codes, and those patients share three phone numbers. In SQL this pattern is spread across five tables.">
          <RingGraph />
        </ChartCard>
        <ChartCard title="Detected rings, ranked by risk score" takeaway="Risk score = core members + shared-phone ratio + billing z-score.">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted"><tr><th className="py-2">Ring</th><th className="text-right">Core mbrs</th><th className="text-right">Shared ph.</th><th className="text-right">Billed</th><th className="text-right">Risk</th></tr></thead>
              <tbody>
                {f.rings.map((r, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-2 pr-2 text-xs text-ink-2">{r.providers.slice(0, 2).join(", ")} +{r.providers.length - 2}</td>
                    <td className="text-right tabular-nums">{r.coreMembers}</td>
                    <td className="text-right tabular-nums">{Math.round(r.sharedPhoneRatio * 100)}%</td>
                    <td className="text-right tabular-nums">{usd(r.billedUSD)}</td>
                    <td className="text-right"><span className="rounded-full px-2 py-0.5 text-xs font-bold text-white" style={{ background: pal[7] }}>⚠ {r.risk}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-muted">Louvain clustering also found {f.louvainCommunities} provider communities, which match the {f.louvainCommunities} regions in the data. This shows community detection recovers real care networks without labels.</p>
        </ChartCard>
      </div>

      {/* 3. Influence + comorbidity */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Most influential providers in the referral network (PageRank)" takeaway="These hub specialists receive most referrals. They are candidates for network contracting and for checking referral concentration.">
          <div style={{ height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={data.influence} layout="vertical" margin={{ left: 8 }}>
                <CartesianGrid horizontal={false} />
                <XAxis type="number" axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="provider" width={70} axisLine={false} tickLine={false} />
                <Tooltip {...tip} formatter={(v: number, _n, p) => [`${v} (‰) · ${p.payload.inReferrals} referrals in`, p.payload.specialty]} />
                <Bar isAnimationActive={false} dataKey="pagerank" name="PageRank ‰" fill={pal[6]} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="Comorbidity network: condition pairs by lift" takeaway="Lift > 1 means two conditions co-occur more often than chance would predict. This drives care-management and risk-adjustment cohorts.">
          <div style={{ height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={data.comorbidity.slice(0, 8).map((c) => ({ ...c, pair: `${c.a.split(" ")[0]} + ${c.b.split(" ")[0]}` }))} layout="vertical" margin={{ left: 8 }}>
                <CartesianGrid horizontal={false} />
                <XAxis type="number" axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="pair" width={90} axisLine={false} tickLine={false} />
                <Tooltip {...tip} formatter={(v: number, _n, p) => [`lift ${v} · ${fmt(p.payload.members)} members`, `${p.payload.a} + ${p.payload.b}`]} />
                <Bar isAnimationActive={false} dataKey="lift" fill={pal[2]} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>

      {/* 4. Classic analytics (still SQL's job) */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Monthly claims spend" takeaway="Aggregate reporting like this is still a job for SQL, so keep MySQL as the system of record.">
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <AreaChart data={data.trend}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="month" axisLine={false} tickLine={false} minTickGap={30} />
                <YAxis tickFormatter={usd} axisLine={false} tickLine={false} width={60} />
                <Tooltip formatter={(v: number, n) => [n === "spendUSD" ? usd(v) : fmt(v), n === "spendUSD" ? "Spend" : "Claims"]} />
                <Area isAnimationActive={false} dataKey="spendUSD" stroke={pal[0]} strokeWidth={2} fill={pal[0]} fillOpacity={0.15} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="Spend by provider specialty" takeaway="Hover a bar to see claim count and denial rate.">
          <div style={{ height: 260 }}>
            <ResponsiveContainer>
              <BarChart data={data.spendBySpecialty} layout="vertical" margin={{ left: 8 }}>
                <CartesianGrid horizontal={false} />
                <XAxis type="number" tickFormatter={usd} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="specialty" width={140} axisLine={false} tickLine={false} />
                <Tooltip {...tip} formatter={(v: number, _n, p) => [`${usd(v)} · ${fmt(p.payload.claims)} claims · ${p.payload.denialPct}% denied`, "Spend"]} />
                <Bar isAnimationActive={false} dataKey="spendUSD" fill={pal[0]} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>

      {/* 5. Entity resolution + degree */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="Entity resolution: members linked by shared phone or address" takeaway="Households of 2-4 are normal. Clusters of 5 or more are a synthetic-identity or ring signal that the graph finds by following shared phones and addresses.">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={data.entity.buckets}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="bucket" axisLine={false} tickLine={false} />
                <YAxis scale="log" domain={[1, "auto"]} allowDataOverflow axisLine={false} tickLine={false} tickFormatter={fmt} width={60} />
                <Tooltip {...tip} formatter={(v: number) => [fmt(v), "clusters"]} labelFormatter={(l) => `${l} members per cluster`} />
                <Bar isAnimationActive={false} dataKey="clusters" radius={[4, 4, 0, 0]}>
                  {data.entity.buckets.map((b, i) => <Cell key={b.bucket} fill={i >= 2 ? pal[7] : pal[2]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard title="Care fragmentation: providers seen per member" takeaway="Members who see many providers drive coordination cost and duplicate tests. The graph degree gives this metric directly.">
          <div style={{ height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={data.degreeDist}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="providers" axisLine={false} tickLine={false} />
                <YAxis axisLine={false} tickLine={false} tickFormatter={fmt} width={60} />
                <Tooltip {...tip} formatter={(v: number) => [fmt(v), "members"]} labelFormatter={(l) => `${l} distinct providers`} />
                <Bar isAnimationActive={false} dataKey="members" fill={pal[6]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>

      {/* 6. GraphRAG context + scale */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <ChartCard title="LLM context needed to explain a flagged provider (tokens)"
          takeaway={`Retrieving the 2-hop subgraph (${fmt(g.egoNodes)} nodes) instead of raw claim rows uses ${(g.sqlDumpTokens / g.graphContextTokens).toFixed(1)}× fewer tokens and keeps the relationships the LLM needs. Fewer tokens means lower LLM cost per question.`}>
          <div style={{ height: 200 }}>
            <ResponsiveContainer>
              <BarChart data={tokens} layout="vertical" margin={{ left: 8, right: 40 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={200} axisLine={false} tickLine={false} />
                <Tooltip {...tip} formatter={(v: number) => [fmt(v), "tokens"]} />
                <Bar isAnimationActive={false} dataKey="tokens" radius={[0, 4, 4, 0]}>
                  {tokens.map((t, i) => <Cell key={t.name} fill={[pal[1], pal[0], pal[2]][i]} />)}
                  <LabelList dataKey="tokens" position="right" formatter={fmt} fill="var(--text-2)" fontSize={12} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mb-1 mt-2 text-xs font-semibold text-muted">What the LLM receives (sample triples):</p>
          <pre className="code text-xs">{g.sampleTriples.join("\n")}</pre>
        </ChartCard>
        <ChartCard title="When does NetworkX stop being enough?" takeaway="Measured memory use extrapolated to the client's graph size. Above roughly 64 GB, a disk-backed graph database is the cheaper choice.">
          <label className="block">
            <div className="flex justify-between text-sm"><span className="text-ink-2">Client graph size (nodes + edges)</span><b className="tabular-nums">{scaleM >= 1000 ? `${scaleM / 1000}B` : `${scaleM}M`}</b></div>
            <input type="range" min={10} max={2000} step={10} value={scaleM} onChange={(e) => setScaleM(Number(e.target.value))} />
          </label>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-surface-2 p-4">
              <p className="text-sm text-muted">NetworkX RAM needed</p>
              <p className="text-3xl font-extrabold tabular-nums">{nxGb.toFixed(0)} GB</p>
            </div>
            <div className="rounded-xl bg-surface-2 p-4">
              <p className="text-sm text-muted">Verdict</p>
              <p className="text-lg font-bold">{nxGb < 16 ? "✅ Fine on a laptop/VM" : nxGb < 64 ? "🟡 Needs a big-RAM server" : "🔴 Use a graph DB"}</p>
            </div>
          </div>
          <p className="mt-4 text-sm text-ink-2">For scale: a mid-size payer with 2M members, 50k providers and 5 years of claims typically ends up with <b>several hundred million</b> nodes and edges, well past what NetworkX can hold comfortably.</p>
        </ChartCard>
      </div>
    </Section>
  );
}
