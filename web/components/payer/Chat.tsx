"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { streamChat, type Final, type Step } from "@/lib/agentApi";

interface Msg { role: "user" | "assistant"; content: string; steps?: Step[]; final?: Final; error?: string; pending?: boolean }

const NODE_ICON: Record<string, string> = { link_entities: "🔗", plan: "🧭", run_tools: "🛠️", reflect: "🤔", answer: "✍️", verify: "✅" };
const ID_RE = /\[((?:CARC:)?(?:M\d{5}|P\d{4}|CLM\d{6}|PA\d{5}|RX\d{6}|PL\d{2}|RXP\d{2}|DRG\d{2}|CO-\d{2,3}|C\d{1,3}))\]/g;

export default function Chat({ byok, suggestions, onEntity, backend, setBackend, neo4jReady }: {
  byok: { key: string; model: string } | null; suggestions: string[]; onEntity: (id: string) => void;
  backend: "networkx" | "neo4j"; setBackend: (b: "networkx" | "neo4j") => void; neo4jReady: boolean;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [msgs]);

  async function ask(q: string) {
    if (!q.trim() || busy) return;
    const history = msgs.filter((m) => !m.pending && !m.error).map((m) => ({ role: m.role, content: m.content }));
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", steps: [], pending: true }]);
    setInput("");
    setBusy(true);
    const patch = (f: (m: Msg) => Msg) => setMsgs((all) => [...all.slice(0, -1), f(all[all.length - 1])]);
    try {
      const final = await streamChat({ question: q, history, key: byok?.key, model: byok?.model, backend },
        (s) => patch((m) => ({ ...m, steps: [...(m.steps || []), s] })));
      patch((m) => ({ ...m, content: final.answer, final, pending: false }));
      const first = final.verification.valid?.find((x) => !x.startsWith("C") || x.startsWith("CARC")) || final.entities[0] || final.contexts.find((c) => c.refs?.length)?.refs[0];
      if (first) onEntity(first);
    } catch (e) {
      patch((m) => ({ ...m, pending: false, error: (e as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  const linkify = (t: string) => t.replace(ID_RE, (_, id) => `[${id}](#entity:${id})`);
  // retrieval-only evidence lists bare ids ('P0129'), so link those too
  const linkifyBare = (t: string) => t.replace(/'?\b(M\d{5}|P\d{4}|RXP\d{2}|PL\d{2})\b'?/g, (_, id) => `[${id}](#entity:${id})`);

  return (
    <div className="card flex flex-col p-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-bold">💬 Payer GraphRAG Copilot</h3>
        <span className="text-xs text-muted">{byok ? `Groq · ${byok.model}` : "No key: retrieval only"}</span>
      </div>
      <div className="mt-3 inline-flex self-start rounded-full border border-line bg-surface-2 p-1 text-xs">
        {([["networkx", "🐍 NetworkX (in-memory)"], ["neo4j", "🟢 Neo4j Aura (Cypher)"]] as const).map(([b, l]) => (
          <button key={b} disabled={b === "neo4j" && !neo4jReady} onClick={() => setBackend(b)}
            className={`rounded-full px-3 py-1 disabled:opacity-40 ${backend === b ? "bg-ink text-bg" : "text-ink-2"}`}>{l}</button>
        ))}
      </div>

      <div className="mt-4 max-h-[640px] min-h-[260px] space-y-4 overflow-y-auto pr-1">
        {msgs.length === 0 && (
          <div>
            <p className="text-sm text-ink-2">Try a question. These use real IDs from the simulated graph:</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button key={s} onClick={() => ask(s)} className="rounded-full border border-line bg-surface-2 px-3 py-1.5 text-left text-sm hover:border-[#6d5dfc]">{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => m.role === "user" ? (
          <div key={i} className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-[#6d5dfc] px-4 py-2.5 text-white">{m.content}</div>
        ) : (
          <div key={i} className="max-w-full rounded-2xl rounded-bl-sm border border-line bg-surface-2 p-4">
            {/* agent trace */}
            <ol className="space-y-1 text-xs">
              {(m.steps || []).map((s, j) => (
                <li key={j} className="flex gap-2 text-ink-2">
                  <span>{NODE_ICON[s.node] || "•"}</span>
                  <span><b className="font-mono text-ink">{s.node}</b> {s.detail} <span className="text-muted">· {s.ms} ms</span></span>
                </li>
              ))}
              {m.pending && <li className="animate-pulse text-muted">⏳ agent working…</li>}
            </ol>
            {m.error && <p className="mt-3 text-sm text-red-500">⚠ {m.error}. Is the agent server running on port 8000?</p>}
            {m.final && (
              <>
                <div className="prose-sm mt-3 border-t border-line pt-3 text-sm leading-relaxed [&_h3]:mt-3 [&_h3]:font-bold [&_li]:ml-4 [&_li]:list-disc [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:text-xs [&_strong]:font-semibold">
                  <ReactMarkdown components={{
                    a: ({ href, children }) => href?.startsWith("#entity:")
                      ? <button onClick={() => onEntity(href.slice(8))} className="rounded bg-[#6d5dfc]/15 px-1 font-mono text-[0.85em] text-[#6d5dfc] hover:bg-[#6d5dfc]/25">{children}</button>
                      : <a href={href} className="underline">{children}</a>,
                  }}>{m.final.mode === "llm" ? linkify(m.content) : linkifyBare(m.content)}</ReactMarkdown>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  {m.final.mode === "llm" && (
                    <span className={`rounded-full px-2 py-0.5 ${m.final.verification.unknown?.length ? "bg-amber-500/15 text-amber-600" : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"}`}>
                      {m.final.verification.unknown?.length ? `⚠ ${m.final.verification.unknown.length} unverified id(s): ${m.final.verification.unknown.join(", ")}` : `✓ ${m.final.verification.valid?.length ?? 0} citations verified in graph`}
                    </span>
                  )}
                  <span className="rounded-full bg-surface px-2 py-0.5 text-muted">{m.final.totalMs.toLocaleString()} ms</span>
                  {m.final.tokens.calls > 0 && <span className="rounded-full bg-surface px-2 py-0.5 text-muted">{m.final.tokens.input.toLocaleString()} in / {m.final.tokens.output.toLocaleString()} out tokens · {m.final.tokens.calls} LLM calls</span>}
                </div>
                {(m.final.mode === "llm" || m.final.contexts.some((c) => c.cypher)) && (
                  <details className="mt-3 text-xs">
                    <summary className="cursor-pointer text-muted">Show the {m.final.backend === "neo4j" ? "Cypher queries and " : ""}graph context the LLM received ({m.final.contexts.length} tool results)</summary>
                    {m.final.contexts.map((c, j) => (
                      <div key={j} className="mt-2">
                        <p className="font-mono font-semibold">{c.tool} {Object.values(c.args).join(", ")} · {c.backend} · {c.ms} ms</p>
                        {c.cypher && <pre className="code mt-1 max-h-60 overflow-auto whitespace-pre-wrap text-[#8fd18f]">{c.cypher}</pre>}
                        <pre className="code mt-1 max-h-60 overflow-auto whitespace-pre-wrap">{c.text}</pre>
                      </div>
                    ))}
                  </details>
                )}
              </>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="mt-4 flex gap-2">
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about providers, members, denials, prior auth, opioids, network…"
          className="min-w-0 flex-1 rounded-xl border border-line bg-surface-2 px-3 py-2.5 text-sm" />
        <button disabled={busy || !input.trim()} className="rounded-xl bg-[#6d5dfc] px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Ask</button>
      </form>
      {msgs.length > 0 && <button onClick={() => setMsgs([])} className="mt-2 self-start text-xs text-muted underline">New conversation</button>}
    </div>
  );
}
