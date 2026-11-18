"use client";
import { useState } from "react";
import data from "@/data/analytics.json";
import { usePalette } from "@/lib/palette";

const W = 640, H = 420, PAD = 30;

export default function RingGraph() {
  const pal = usePalette();
  const [hover, setHover] = useState<string | null>(null);
  const { nodes, edges } = data.ringViz;
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const px = (x: number) => PAD + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * PAD);
  const py = (y: number) => PAD + ((y - y0) / (y1 - y0 || 1)) * (H - 2 * PAD);
  const pos = Object.fromEntries(nodes.map((n) => [n.id, { x: px(n.x), y: py(n.y) }]));
  const color: Record<string, string> = { Provider: pal[1], Member: pal[0], Phone: pal[3] };
  const radius: Record<string, number> = { Provider: 13, Member: 6, Phone: 9 };
  const linked = new Set(hover ? edges.filter((e) => e.s === hover || e.t === hover).flatMap((e) => [e.s, e.t]) : []);
  const hn = nodes.find((n) => n.id === hover);

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-4 text-sm text-ink-2">
        {Object.entries(color).map(([k, c]) => (
          <span key={k} className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full" style={{ background: c }} />{k === "Phone" ? "Shared phone number" : k}</span>
        ))}
        <span className="ml-auto text-muted">{hn ? `${hn.label}: ${linked.size - 1} connections` : "Hover a node"}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-xl bg-surface-2" role="img" aria-label="Detected fraud ring: providers, members and shared phone numbers">
        {edges.map((e, i) => {
          const on = hover && (e.s === hover || e.t === hover);
          return <line key={i} x1={pos[e.s].x} y1={pos[e.s].y} x2={pos[e.t].x} y2={pos[e.t].y}
            stroke={e.rel === "HAS_PHONE" ? pal[3] : "var(--muted)"} strokeOpacity={hover ? (on ? 0.9 : 0.08) : 0.3} strokeWidth={on ? 2 : 1} />;
        })}
        {nodes.map((n) => (
          <g key={n.id} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} style={{ cursor: "pointer" }}>
            <circle cx={pos[n.id].x} cy={pos[n.id].y} r={radius[n.kind] + 8} fill="transparent" />
            <circle cx={pos[n.id].x} cy={pos[n.id].y} r={radius[n.kind]} fill={color[n.kind]} stroke="var(--surface)" strokeWidth={2}
              opacity={hover && !linked.has(n.id) ? 0.25 : 1} />
            {n.kind === "Provider" && <text x={pos[n.id].x} y={pos[n.id].y - 18} textAnchor="middle" fontSize={11} fill="var(--text)" fontWeight={600}>{n.id}</text>}
          </g>
        ))}
      </svg>
    </div>
  );
}
