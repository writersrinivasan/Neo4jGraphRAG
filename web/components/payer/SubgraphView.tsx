"use client";
import { useEffect, useState } from "react";
import { getJson } from "@/lib/agentApi";
import { usePalette } from "@/lib/palette";

interface N { id: string; type: string; x: number; y: number; flag: boolean; center: boolean }
interface E { s: string; t: string; rel: string }
const W = 560, H = 380, PAD = 34;

export default function SubgraphView({ entity, onSelect }: { entity: string | null; onSelect: (id: string) => void }) {
  const pal = usePalette();
  const [g, setG] = useState<{ nodes: N[]; edges: E[] } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    if (!entity) return;
    setG(null);
    getJson<{ nodes: N[]; edges: E[] }>(`/api/subgraph/${encodeURIComponent(entity)}`).then(setG).catch(() => setG({ nodes: [], edges: [] }));
  }, [entity]);

  const color: Record<string, string> = { Member: pal[0], Provider: pal[1], Pharmacy: pal[2], Phone: pal[3], Address: pal[3], Plan: pal[6], Drug: pal[4] };
  const c = (t: string) => color[t] || pal[6];

  if (!entity) return <div className="grid h-[380px] place-items-center rounded-xl bg-surface-2 text-sm text-muted">Ask a question or click an entity id to explore its neighbourhood</div>;
  if (!g) return <div className="grid h-[380px] place-items-center rounded-xl bg-surface-2 text-sm text-muted">Loading {entity}…</div>;
  if (!g.nodes.length) return <div className="grid h-[380px] place-items-center rounded-xl bg-surface-2 text-sm text-muted">No subgraph for {entity}</div>;

  const xs = g.nodes.map((n) => n.x), ys = g.nodes.map((n) => n.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const P = Object.fromEntries(g.nodes.map((n) => [n.id, { x: PAD + ((n.x - x0) / (x1 - x0 || 1)) * (W - 2 * PAD), y: PAD + ((n.y - y0) / (y1 - y0 || 1)) * (H - 2 * PAD) }]));
  const types = [...new Set(g.nodes.map((n) => n.type))];

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-3 text-xs text-ink-2">
        {types.map((t) => <span key={t} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c(t) }} />{t}</span>)}
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: pal[7] }} />flagged by a detector</span>
        <span className="ml-auto text-muted">{hover ?? "Click a node to explore it"}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-xl bg-surface-2" role="img" aria-label={`Neighbourhood of ${entity}`}>
        {g.edges.map((e, i) => (
          <line key={i} x1={P[e.s].x} y1={P[e.s].y} x2={P[e.t].x} y2={P[e.t].y}
            stroke={e.rel.includes("DENIED") ? pal[7] : "var(--muted)"} strokeOpacity={hover && e.s !== hover && e.t !== hover ? 0.1 : 0.45}
            strokeDasharray={e.rel.startsWith("RX") ? "3 3" : undefined} />
        ))}
        {g.nodes.map((n) => (
          <g key={n.id} onClick={() => onSelect(n.id)} onMouseEnter={() => setHover(`${n.id} · ${n.type}`)} onMouseLeave={() => setHover(null)} style={{ cursor: "pointer" }}>
            <circle cx={P[n.id].x} cy={P[n.id].y} r={16} fill="transparent" />
            {n.flag && <circle cx={P[n.id].x} cy={P[n.id].y} r={n.center ? 15 : 10} fill="none" stroke={pal[7]} strokeWidth={2.5} />}
            <circle cx={P[n.id].x} cy={P[n.id].y} r={n.center ? 11 : 6} fill={c(n.type)} stroke="var(--surface)" strokeWidth={2} />
            {(n.center || n.type === "Provider" || n.type === "Pharmacy") && (
              <text x={P[n.id].x} y={P[n.id].y - (n.center ? 17 : 11)} textAnchor="middle" fontSize={n.center ? 12 : 10} fontWeight={n.center ? 700 : 500} fill="var(--text)">{n.id}</text>
            )}
          </g>
        ))}
      </svg>
      <p className="mt-2 text-xs text-muted">Red lines are denied claims; dashed lines are pharmacy fills. Live data from the NetworkX graph.</p>
    </div>
  );
}
