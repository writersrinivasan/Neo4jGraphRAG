import type { ReactNode } from "react";

export function Section({ id, kicker, title, intro, children }: { id: string; kicker: string; title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
      <p className="text-xs font-bold uppercase tracking-[0.18em] text-grad">{kicker}</p>
      <h2 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">{title}</h2>
      {intro && <div className="mt-3 max-w-3xl text-ink-2">{intro}</div>}
      <div className="mt-8">{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent: string }) {
  return (
    <div className="card relative overflow-hidden p-5">
      <span className="absolute inset-x-0 top-0 h-1" style={{ background: accent }} />
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-3xl font-extrabold tracking-tight tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-sm text-ink-2">{sub}</p>}
    </div>
  );
}

export function ChartCard({ title, takeaway, children, className = "" }: { title: string; takeaway?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`card p-5 ${className}`}>
      <h3 className="font-bold">{title}</h3>
      {takeaway && <p className="mt-1 text-sm text-ink-2">{takeaway}</p>}
      <div className="mt-4">{children}</div>
    </div>
  );
}

export function Pill({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-ink-2">
      {color && <span className="h-2 w-2 rounded-full" style={{ background: color }} />}
      {children}
    </span>
  );
}

export function Dots({ n, max = 5, color }: { n: number; max?: number; color: string }) {
  return (
    <span className="inline-flex gap-1" aria-label={`${n} of ${max}`}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} className="h-2.5 w-2.5 rounded-full" style={{ background: i < n ? color : "var(--border)" }} />
      ))}
    </span>
  );
}
