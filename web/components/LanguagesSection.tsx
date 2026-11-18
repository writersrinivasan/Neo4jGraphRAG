"use client";
import { useState } from "react";
import { LANGS } from "@/lib/queries";
import { Dots, Section } from "./ui";
import { usePalette } from "@/lib/palette";

export default function LanguagesSection() {
  const pal = usePalette();
  const [sel, setSel] = useState("cypher");
  const [mode, setMode] = useState<"twoHop" | "kHop">("twoHop");
  const [cmp, setCmp] = useState("mysql");
  const a = LANGS.find((l) => l.id === sel)!;
  const b = LANGS.find((l) => l.id === cmp)!;

  return (
    <Section id="languages" kicker="Question 2" title="Query languages, compared on one fraud question"
      intro={<>Each language below answers the same question from the claims data: <b>&ldquo;Which providers share 8 or more patients with flagged provider P00227?&rdquo;</b> The variable-depth version asks for every provider within N shared-patient hops. Pick two languages to compare them side by side.</>}>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-full border border-line bg-surface p-1">
          {([["twoHop", "Fixed 2-hop"], ["kHop", "Variable depth"]] as const).map(([k, l]) => (
            <button key={k} onClick={() => setMode(k)} className={`rounded-full px-3 py-1 text-sm ${mode === k ? "bg-ink text-bg" : "text-ink-2"}`}>{l}</button>
          ))}
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {[{ v: sel, set: setSel, l: a }, { v: cmp, set: setCmp, l: b }].map(({ v, set, l }, idx) => (
          <div key={idx} className="card p-4">
            <select value={v} onChange={(e) => set(e.target.value)} className="w-full rounded-xl border border-line bg-surface-2 px-3 py-2 font-semibold">
              {LANGS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <p className="mt-2 text-xs text-muted">{l.standard} · runs on {l.engines}</p>
            <pre className="code mt-3"><code>{l[mode]}</code></pre>
            <p className="mt-2 text-xs text-muted">{l[mode].split("\n").length} lines · {l[mode].length} characters</p>
          </div>
        ))}
      </div>

      <div className="card mt-6 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-surface-2 text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-4 py-3">Language</th><th className="px-4 py-3">Style</th><th className="px-4 py-3">Standard</th>
              <th className="px-4 py-3">Variable depth</th><th className="px-4 py-3">Ease of learning</th><th className="px-4 py-3">Readability for graphs</th>
            </tr>
          </thead>
          <tbody>
            {LANGS.map((l) => (
              <tr key={l.id} className={`border-t border-line ${l.id === sel || l.id === cmp ? "bg-surface-2/60" : ""}`}>
                <td className="px-4 py-3 font-semibold">{l.name}</td>
                <td className="px-4 py-3 text-ink-2">{l.style}</td>
                <td className="px-4 py-3 text-ink-2">{l.standard}</td>
                <td className="px-4 py-3 text-ink-2">{l.variableDepth}</td>
                <td className="px-4 py-3"><Dots n={6 - l.learning} color={pal[2]} /></td>
                <td className="px-4 py-3"><Dots n={l.readability} color={pal[0]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 text-sm text-ink-2">
        <b>Recommendation:</b> standardise on <b>Cypher</b>. It is the most portable choice (Neo4j, Memgraph, Apache AGE, FalkorDB, Neptune) and the basis of the new ISO <b>GQL</b> standard, so skills and queries carry over if the client changes engines. LLMs are also good at writing it (text-to-Cypher), which matters for GraphRAG. Keep SQL for reporting on the MySQL system of record.
      </p>
    </Section>
  );
}
