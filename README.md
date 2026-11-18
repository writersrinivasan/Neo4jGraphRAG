# GraphRAG for Health Insurance: client showcase

A Next.js app that makes the case for adding a graph and GraphRAG layer to a legacy MySQL health-insurance estate. It covers four questions:

1. **Graph DB options**: 17 in-house and managed options (NetworkX, Kùzu, DuckPGQ, PostgreSQL + Apache AGE, Neo4j, Memgraph, FalkorDB, NebulaGraph, RDF stores, and others). They can be filtered by category and by whether they run in-house.
2. **Query languages**: the same fraud question written in Cypher, ISO GQL, AGE, SQL/PGQ, Gremlin, SPARQL, AQL, nGQL, T-SQL MATCH, MySQL and NetworkX, shown side by side with a comparison table.
3. **Budget**: an interactive 1–5 year TCO model covering infrastructure, licenses, operations, development effort and migration, with an HA requirement toggle.
4. **Analytics**: real results computed on a synthetic payer dataset. They include an SQL vs graph latency benchmark, fraud-ring detection, PageRank on the referral network, entity resolution, a comorbidity network, LLM context-token sizing for GraphRAG, and NetworkX memory extrapolation.

## Payer GraphRAG Copilot (`/payer`)

A second page with a LangGraph agent over a NetworkX payer knowledge graph, using a Groq key you supply (BYOK). It also has a SQL vs NetworkX comparison. Setup is in [payer-agent/README.md](payer-agent/README.md). It needs the Python agent server running on port 8000.

## Run

```bash
# 1. (optional) regenerate analytics: writes web/data/analytics.json
cd analysis
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_analysis.py

# 2. web app
cd ../web
npm install
npm run dev        # http://localhost:3000
```

`npm run analyze` (from `web/`) reruns step 1 once the venv exists. The JSON is bundled at build time, so restart `dev` or rebuild after regenerating it.

## Honesty notes for the client meeting

- All data is **synthetic**: there is no PHI. The fraud rings are planted, so the 100% precision and recall show that the method works, not how it will perform on real claims.
- SQLite stands in for MySQL in the benchmark. Both engines are asserted to return identical results. Timings vary a little between runs, and the page text is generated from the data so it always matches.
- TCO license figures are **assumptions** (Neo4j Enterprise is quote-based). Replace them with real quotes and the client's infrastructure rates.
- The NetworkX memory figure (~379 bytes per node+edge) was measured with `tracemalloc`. The RAM figures for the other engines are planning estimates.
