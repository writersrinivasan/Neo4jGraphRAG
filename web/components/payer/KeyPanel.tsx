"use client";
import { useEffect, useState } from "react";
import { listModels } from "@/lib/agentApi";

const STORE = "groq-byok";

export default function KeyPanel({ onChange }: { onChange: (k: { key: string; model: string } | null) => void }) {
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<"idle" | "checking" | "ok" | "error">("idle");
  const [err, setErr] = useState("");

  async function connect(k = key) {
    setStatus("checking");
    setErr("");
    try {
      const ms = await listModels(k.trim());
      setModels(ms);
      setModel(ms[0] || "");
      setStatus("ok");
      onChange({ key: k.trim(), model: ms[0] || "" });
      try { if (remember) sessionStorage.setItem(STORE, k.trim()); } catch {}
    } catch (e) {
      setStatus("error");
      setErr((e as Error).message);
      onChange(null);
    }
  }

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(STORE);
      if (saved) { setKey(saved); setRemember(true); connect(saved); }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function clear() {
    setKey(""); setModels([]); setModel(""); setStatus("idle"); onChange(null);
    try { sessionStorage.removeItem(STORE); } catch {}
  }

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-bold">🔑 Bring your own key: Groq</h3>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${status === "ok" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-surface-2 text-muted"}`}>
          {status === "ok" ? "● Connected" : status === "checking" ? "Checking…" : "Retrieval-only mode"}
        </span>
      </div>
      <p className="mt-1 text-sm text-ink-2">The key goes from your browser to your local agent server and then to Groq, one request at a time. It is never written to disk, logged or stored on the server.</p>
      <div className="mt-3 flex gap-2">
        <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="gsk_…" autoComplete="off" spellCheck={false}
          className="min-w-0 flex-1 rounded-xl border border-line bg-surface-2 px-3 py-2 font-mono text-sm" />
        {status === "ok"
          ? <button onClick={clear} className="rounded-xl border border-line px-3 py-2 text-sm">Forget</button>
          : <button onClick={() => connect()} disabled={key.trim().length < 10 || status === "checking"}
              className="rounded-xl bg-ink px-4 py-2 text-sm font-semibold text-bg disabled:opacity-40">Connect</button>}
      </div>
      {status === "error" && <p className="mt-2 text-sm text-red-500">⚠ {err}</p>}
      {models.length > 0 && (
        <label className="mt-3 block text-sm">
          <span className="text-ink-2">Model (from your Groq account)</span>
          <select value={model} onChange={(e) => { setModel(e.target.value); onChange({ key: key.trim(), model: e.target.value }); }}
            className="mt-1 w-full rounded-xl border border-line bg-surface-2 px-3 py-2">
            {models.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
      )}
      <label className="mt-3 flex items-center gap-2 text-xs text-muted">
        <input type="checkbox" checked={remember} onChange={(e) => { setRemember(e.target.checked); if (!e.target.checked) try { sessionStorage.removeItem(STORE); } catch {} }} />
        Remember in this browser tab only (sessionStorage; cleared when the tab closes)
      </label>
      <p className="mt-2 text-xs text-muted">No key? The agent still runs: it plans with keyword rules and shows the retrieved graph evidence instead of an AI-written answer. Get a free key at console.groq.com.</p>
    </div>
  );
}
