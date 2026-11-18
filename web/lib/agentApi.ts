export const AGENT_URL = process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8000";

export interface Step { node: string; detail: string; ms: number; tools?: { tool: string; args: Record<string, string>; why: string }[] }
export interface Final {
  answer: string;
  entities: string[];
  contexts: { tool: string; args: Record<string, string>; text: string; ms: number; refs: string[]; cypher?: string | null; backend?: string }[];
  backend?: string;
  verification: { cited?: number; valid?: string[]; unknown?: string[]; grounded?: number };
  mode: "llm" | "retrieval-only";
  model: string | null;
  tokens: { input: number; output: number; calls: number };
  totalMs: number;
}

export async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(`${AGENT_URL}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export async function listModels(key: string): Promise<string[]> {
  const r = await fetch(`${AGENT_URL}/api/models`, { method: "POST", headers: { "X-Groq-Api-Key": key } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body.models;
}

/** POST a question and stream LangGraph steps back as server-sent events. */
export async function streamChat(
  args: { question: string; model?: string; history: { role: string; content: string }[]; key?: string; backend?: string },
  onStep: (s: Step) => void,
): Promise<Final> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (args.key) headers["X-Groq-Api-Key"] = args.key; // BYOK: sent per request, never persisted server-side
  const r = await fetch(`${AGENT_URL}/api/chat`, {
    method: "POST", headers, body: JSON.stringify({ question: args.question, model: args.model, history: args.history, backend: args.backend || "networkx" }),
  });
  if (!r.ok || !r.body) throw new Error(`Agent server error ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i).trim();
      buf = buf.slice(i + 2);
      if (!chunk.startsWith("data:")) continue;
      const ev = JSON.parse(chunk.slice(5));
      if (ev.type === "step") onStep(ev);
      else if (ev.type === "final") return ev as Final;
      else if (ev.type === "error") throw new Error(ev.message);
    }
  }
  throw new Error("Stream ended without a final answer");
}

export interface Stage { stage: string; title: string; cypher: string; explain: string; ms: number; results: Record<string, unknown>[]; error: string | null }
export interface Retrieval { stages: Stage[]; context: string; seeds: string[]; totalMs: number; text2cypher: boolean }
export interface Neo4jStatus { connected: boolean; error?: string; nodes?: number; relationships?: number; labels?: Record<string, number>; indexes?: { name: string; type: string; state: string }[] }

export async function retrieveNeo4j(question: string, byok?: { key: string; model: string } | null): Promise<Retrieval> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (byok?.key) headers["X-Groq-Api-Key"] = byok.key;
  const r = await fetch(`${AGENT_URL}/api/neo4j/retrieve`, { method: "POST", headers, body: JSON.stringify({ question, model: byok?.model }) });
  const body = await r.json();
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}
