"use client";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { computeTco, DEFAULT_INPUTS, PROFILES, usd, type TcoInputs } from "@/lib/tco";
import { ChartCard, Section, Stat } from "./ui";
import { usePalette } from "@/lib/palette";

const SIZES = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000];
const PARTS = [["infra", "Infrastructure"], ["license", "License"], ["ops", "Operations"], ["dev", "Graph feature dev"], ["migration", "Migration"]] as const;

function Slider({ label, value, display, min, max, step, onChange }: { label: string; value: number; display: string; min: number; max: number; step: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <div className="flex justify-between text-sm"><span className="text-ink-2">{label}</span><b className="tabular-nums">{display}</b></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

export default function BudgetSection() {
  const pal = usePalette();
  const [inp, setInp] = useState<TcoInputs>(DEFAULT_INPUTS);
  const set = (k: keyof TcoInputs) => (v: number | boolean) => setInp((s) => ({ ...s, [k]: v }));
  const results = useMemo(() => PROFILES.map((p) => computeTco(p, inp)), [inp]);
  const sorted = [...results].sort((a, b) => a.total - b.total);
  const best = sorted.find((r) => r.feasible);
  const status = results.find((r) => r.profile.id === "mysql")!;
  const ent = results.find((r) => r.profile.id === "neo4je")!;
  const chartData = sorted.map((r) => ({ name: r.profile.name + (r.feasible ? "" : " ⚠"), infra: r.infra, license: r.license, ops: r.ops, dev: r.dev, migration: r.migration }));

  return (
    <Section id="budget" kicker="Question 3" title="Which option saves the most money?"
      intro={<>This is a transparent {inp.years}-year total cost of ownership (TCO) model. Licenses are often not the largest cost. <b>Engineering effort</b> usually is, and it is where native graph query languages save money compared with hand-written SQL. Move the sliders to match the client&apos;s estate. Options marked ⚠ do not meet the requirements you set.</>}>
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="card space-y-5 p-5">
          <Slider label="Graph size (nodes + edges)" value={SIZES.indexOf(inp.elementsM)} min={0} max={SIZES.length - 1} step={1}
            display={`${inp.elementsM >= 1000 ? inp.elementsM / 1000 + "B" : inp.elementsM + "M"}`} onChange={(i) => set("elementsM")(SIZES[i])} />
          <Slider label="Horizon (years)" value={inp.years} min={1} max={5} step={1} display={`${inp.years}`} onChange={set("years")} />
          <Slider label="Engineer cost / year" value={inp.fteCost} min={40000} max={250000} step={5000} display={usd(inp.fteCost)} onChange={set("fteCost")} />
          <Slider label="Graph feature dev effort" value={inp.devFte} min={0.25} max={4} step={0.25} display={`${inp.devFte} FTE/yr`} onChange={set("devFte")} />
          <Slider label="Infra cost per GB RAM / month" value={inp.ramGbMonth} min={1} max={12} step={0.5} display={`$${inp.ramGbMonth}`} onChange={set("ramGbMonth")} />
          <label className="flex items-center justify-between rounded-xl bg-surface-2 px-3 py-2 text-sm">
            <span>High availability required (PHI production)</span>
            <input type="checkbox" checked={inp.needHA} onChange={(e) => set("needHA")(e.target.checked)} />
          </label>
          <button onClick={() => setInp(DEFAULT_INPUTS)} className="text-sm text-muted underline">Reset assumptions</button>
        </div>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat accent={pal[2]} label="Lowest-cost option that meets requirements" value={best ? usd(best.total) : "n/a"} sub={best?.profile.name} />
            <Stat accent={pal[0]} label="Saving vs MySQL-only" value={best ? usd(status.total - best.total) : "n/a"}
              sub={best ? `${Math.round((1 - best.total / status.total) * 100)}% lower over ${inp.years} yrs` : ""} />
            <Stat accent={pal[1]} label="Saving vs Neo4j Enterprise" value={best ? usd(ent.total - best.total) : "n/a"}
              sub={best ? `${Math.round((1 - best.total / ent.total) * 100)}% lower (assumed license)` : ""} />
          </div>
          <ChartCard title={`${inp.years}-year TCO by option (USD)`} takeaway="Bars are sorted cheapest first. Colours show where the money goes.">
            <div style={{ height: 380 }}>
              <ResponsiveContainer>
                <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 16 }} barCategoryGap={8}>
                  <CartesianGrid horizontal={false} />
                  <XAxis type="number" tickFormatter={usd} axisLine={false} tickLine={false} />
                  <YAxis type="category" dataKey="name" width={200} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(v: number) => usd(v)} cursor={{ fill: "var(--surface-2)" }} />
                  <Legend formatter={(v) => <span style={{ color: "var(--text-2)" }}>{v}</span>} />
                  {PARTS.map(([k, l], i) => (
                    <Bar isAnimationActive={false} key={k} dataKey={k} name={l} stackId="a" fill={pal[i]} stroke="var(--surface)" strokeWidth={2}
                      radius={i === PARTS.length - 1 ? [0, 4, 4, 0] : 0} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>
      </div>

      <div className="card mt-4 overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
            <tr><th className="px-4 py-3">Option</th><th className="px-4 py-3 text-right">Servers × RAM</th><th className="px-4 py-3 text-right">Total</th><th className="px-4 py-3">Fit</th><th className="px-4 py-3">Assumption</th></tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.profile.id} className="border-t border-line">
                <td className="px-4 py-3 font-semibold">{r.profile.name}</td>
                <td className="px-4 py-3 text-right tabular-nums">{r.servers} × {r.ramGbPerServer} GB</td>
                <td className="px-4 py-3 text-right font-semibold tabular-nums">{usd(r.total)}</td>
                <td className="px-4 py-3">{r.feasible ? <span>✅ Meets needs</span> : <span className="text-ink-2">⚠ {r.reasons.join("; ")}</span>}</td>
                <td className="px-4 py-3 text-ink-2">{r.profile.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">This is an illustrative model, not a quote. RAM per element: NetworkX was measured in this repo; the other figures are planning estimates. RAM is sized with 50% headroom and a 16 GB minimum. Replace the license and infrastructure rates with the client&apos;s actual figures before deciding.</p>
    </Section>
  );
}
