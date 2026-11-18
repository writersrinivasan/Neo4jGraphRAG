# Payer GraphRAG Copilot (NetworkX + LangGraph + Groq BYOK)

A health-plan (payer) GraphRAG agent built on simulated data.

| Piece | File | What it does |
|---|---|---|
| Simulator | `payer_agent/data.py` | Generates 6 plans, 6k members, 450 providers, network contracts, 20k claims (CARC denials), 2.6k prior auths, appeals and 18k pharmacy fills, with 5 planted schemes. It loads the same rows into **SQLite** (standing in for the legacy DB) and a **NetworkX** DiGraph (58k nodes, 205k edges). |
| GraphRAG | `payer_agent/graphrag.py` | Louvain communities and statistical community reports (global search), entity linking and typed neighbourhood context (local search), and graph-native detectors: fraud rings, doctor shopping, PA gaps, leakage, identity clusters, PageRank, shortest paths. |
| Agent | `payer_agent/agent.py` | LangGraph `StateGraph`: `link_entities → plan → run_tools → reflect ⟲ → answer → verify`. The Groq LLM plans tools as JSON and writes cited answers. Without a key it uses heuristic planning and returns the retrieved evidence only. |
| Comparison | `payer_agent/compare.py` | 9 payer questions run on SQL joins and on NetworkX. Results are asserted equal, then timed. Also holds a 13-row conceptual comparison. |
| API | `server.py` | FastAPI: `/api/chat` (SSE stream of agent steps), `/api/models`, `/api/overview`, `/api/evaluation`, `/api/compare`, `/api/subgraph/{id}`, `/api/neo4j/status`, `/api/neo4j/retrieve` |
| Neo4j store | `payer_agent/neo4j_store.py`, `load_neo4j.py` | Mirrors the graph into Neo4j Aura: uniqueness constraints, batched `UNWIND … MERGE`, `:Community` nodes with GraphRAG reports, detector `flags` on nodes, local BGE-small embeddings, and `entity_embedding` / `community_embedding` vector indexes plus an `entity_text` full-text index. |
| Neo4j retrieval | `payer_agent/neo4j_retrieval.py` | An 8-stage GraphRAG retrieval pipeline (exact ID → BM25 → vector → RRF fusion → Cypher expansion → community vector search → text2cypher → context), Cypher versions of every detector, and guarded text2cypher (write clauses refused, `LIMIT` enforced, read-only transaction, 10 s timeout). |

UI: `../web/app/payer` (http://localhost:3000/payer). The chat has a **NetworkX / Neo4j Aura** backend toggle, and the "Neo4j retrieval" section shows each retrieval stage with the Cypher it ran.

## Neo4j Aura

```bash
# payer-agent/.env (chmod 600, git-ignored)
NEO4J_URI=neo4j+s://<id>.databases.neo4j.io
NEO4J_USERNAME=...
NEO4J_PASSWORD=...
NEO4J_DATABASE=...

.venv/bin/python load_neo4j.py               # full load, ~4 min over the internet (Aura Free fits: 58k nodes / 211k rels)
.venv/bin/python load_neo4j.py --embeddings  # re-embed descriptions + rebuild indexes only
```

- TLS is verified against `certifi`. uv's Python has no system CA store, so a plain `neo4j+s://` connection fails with "self-signed certificate in chain". Do not switch to `+ssc` to work around it.
- PageRank and connected components need Graph Data Science, which Aura Free doesn't include. Influence is computed in NetworkX on edges pulled from Neo4j, and identity clusters use a 1-hop Cypher approximation.
- When Neo4j isn't configured, the server starts in NetworkX-only mode.

## Run

```bash
# backend (Python 3.12 via uv)
cd payer-agent
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn server:app --port 8000      # builds the graph in ~6 s

# frontend
cd ../web && npm run dev                       # open http://localhost:3000/payer
```

Then paste a Groq key (from console.groq.com) into the BYOK panel. The model list comes from your own account.

## BYOK design

- The key is held in React state and sent as `X-Groq-Api-Key` on each request, directly from the browser to the local agent server (port 8000).
- The server builds a `ChatGroq` client for that request only and passes it through `config["configurable"]`. The key never enters LangGraph state, logs or disk.
- Storing the key across reloads is opt-in. It uses `sessionStorage`, which is cleared when the tab closes.
- With no key, the whole pipeline still runs in retrieval-only mode, which is useful for demos without internet access.

## Honest caveats

- The data is simulated and the schemes are planted. The detector accuracy table compares against the planted answer key, which is only possible with synthetic data.
- In the benchmark, pure-Python NetworkX **loses** to SQLite on flat scans and aggregations, and wins on traversal, paths and point context. The UI shows both outcomes.
- The Groq answer path was unit-tested with a scripted fake LLM. It needs a real key for a live run. Groq free-tier token-per-minute limits apply, and the context is capped at 11k characters.
