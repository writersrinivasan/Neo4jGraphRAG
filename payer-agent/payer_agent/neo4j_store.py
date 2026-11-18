"""
Neo4j Aura store for the payer knowledge graph.

* Credentials come from payer-agent/.env (never from the browser, never logged).
* TLS is fully verified against the certifi CA bundle (uv's Python has no system CA store).
* load_graph() mirrors the NetworkX graph into Neo4j, adds :Community nodes with GraphRAG reports,
  detector flags, local embeddings (BGE-small via fastembed) and vector + full-text indexes.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

import certifi
from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase, TrustCustomCAs

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ~70 MB ONNX, runs on CPU
EMBED_DIM = 384
SEARCHABLE = {"Provider", "Procedure", "Drug", "DenialReason", "Diagnosis", "Plan", "Pharmacy"}
BATCH = 5000


def neo4j_config():
    load_dotenv(ENV_PATH)
    keys = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    if not all(os.environ.get(k) for k in keys):
        return None
    return {k: os.environ[k] for k in keys} | {"NEO4J_DATABASE": os.environ.get("NEO4J_DATABASE") or None}


def get_driver() -> tuple[Driver, str | None]:
    cfg = neo4j_config()
    if not cfg:
        raise RuntimeError(f"Neo4j credentials missing in {ENV_PATH}")
    # neo4j+s:// would use Python's default store, which lacks the SSL.com root on uv builds.
    uri = cfg["NEO4J_URI"].replace("neo4j+s://", "neo4j://").replace("bolt+s://", "bolt://")
    drv = GraphDatabase.driver(uri, auth=(cfg["NEO4J_USERNAME"], cfg["NEO4J_PASSWORD"]),
                               encrypted=True, trusted_certificates=TrustCustomCAs(certifi.where()),
                               max_connection_lifetime=300, connection_timeout=20)
    drv.verify_connectivity()
    return drv, cfg["NEO4J_DATABASE"]


_embedder = None


def embed(texts: list[str]) -> list[list[float]]:
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return [v.tolist() for v in _embedder.embed(texts)]


def _props(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "type" and isinstance(v, (str, int, float, bool))}


def load_graph(rag, reset: bool = True, log=print):
    """Mirror rag.G (+ communities, flags, embeddings) into Neo4j. Idempotent (MERGE)."""
    G = rag.G
    drv, db = get_driver()
    t0 = time.perf_counter()
    labels = sorted({d["type"] for _, d in G.nodes(data=True)}) + ["Community"]
    with drv.session(database=db) as s:
        if reset:
            for lbl in labels:  # only our labels; batched delete keeps Aura Free memory happy
                while s.run(f"MATCH (n:`{lbl}`) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS c").single()["c"]:
                    pass
            log(f"reset done ({time.perf_counter() - t0:.0f}s)")
        for lbl in labels:
            s.run(f"CREATE CONSTRAINT {lbl.lower()}_id IF NOT EXISTS FOR (n:`{lbl}`) REQUIRE n.id IS UNIQUE")

        # ---------- nodes (with detector flags + panel size used by Cypher detectors)
        panel = {p: len(ms) for p, ms in _panels(rag).items()}
        by_label = defaultdict(list)
        for n, d in G.nodes(data=True):
            row = {"id": n, **_props(d)}
            if n in rag._flags:
                row["flags"] = sorted(set(rag._flags[n]))
            if d["type"] == "Provider":
                row["panel_size"] = panel.get(n, 0)
            by_label[d["type"]].append(row)
        for lbl, rows in by_label.items():
            for i in range(0, len(rows), BATCH):
                s.run(f"UNWIND $rows AS r MERGE (n:`{lbl}` {{id: r.id}}) SET n += r", rows=rows[i:i + BATCH])
        log(f"nodes loaded: {G.number_of_nodes():,} ({time.perf_counter() - t0:.0f}s)")

        # ---------- relationships, grouped by (type, src label, dst label) so MATCH uses the id constraint
        groups = defaultdict(list)
        for u, v, d in G.edges(data=True):
            groups[(d["rel"], G.nodes[u]["type"], G.nodes[v]["type"])].append({"s": u, "t": v, "p": {k: x for k, x in d.items() if k != "rel"}})
        done = 0
        for (rel, a, b), rows in groups.items():
            for i in range(0, len(rows), BATCH):
                s.run(f"UNWIND $rows AS r MATCH (a:`{a}` {{id: r.s}}) MATCH (b:`{b}` {{id: r.t}}) "
                      f"MERGE (a)-[x:`{rel}`]->(b) SET x += r.p", rows=rows[i:i + BATCH])
                done += len(rows[i:i + BATCH])
        log(f"relationships loaded: {done:,} ({time.perf_counter() - t0:.0f}s)")

        # ---------- GraphRAG communities
        comm_rows, member_rows = [], []
        for c in rag.communities:
            comm_rows.append({"id": f"C{c.id}", "report": c.report, **c.metrics})
            member_rows += [{"n": n, "c": f"C{c.id}"} for n in c.members + c.providers]
        s.run("UNWIND $rows AS r MERGE (c:Community {id: r.id}) SET c += r", rows=comm_rows)
        for i in range(0, len(member_rows), BATCH):
            s.run("UNWIND $rows AS r MATCH (n {id: r.n}) WHERE n:Member OR n:Provider "
                  "MATCH (c:Community {id: r.c}) MERGE (n)-[:IN_COMMUNITY]->(c)", rows=member_rows[i:i + BATCH])
        log(f"communities loaded: {len(comm_rows)} ({time.perf_counter() - t0:.0f}s)")

    load_embeddings(rag, drv, db, log, t0)
    with drv.session(database=db) as s:
        counts = s.run("MATCH (n) RETURN count(n) AS n").single()["n"], s.run("MATCH ()-[r]->() RETURN count(r) AS r").single()["r"]
    drv.close()
    log(f"done: {counts[0]:,} nodes, {counts[1]:,} relationships in Neo4j ({time.perf_counter() - t0:.0f}s)")
    return counts


def load_embeddings(rag, drv=None, db=None, log=print, t0=None):
    """Descriptions + local embeddings + vector/full-text indexes (re-runnable on its own)."""
    G = rag.G
    own = drv is None
    if own:
        drv, db = get_driver()
    t0 = t0 or time.perf_counter()
    texts = [(d["type"], n, _describe(rag, n)) for n, d in G.nodes(data=True) if d["type"] in SEARCHABLE]
    texts += [("Community", f"C{c.id}", c.report) for c in rag.communities]
    vecs = embed([t for _, _, t in texts])
    rows = defaultdict(list)
    for (lbl, n, t), v in zip(texts, vecs):
        rows[lbl].append({"id": n, "text": t, "embedding": v})
    with drv.session(database=db) as s:
        for lbl, rs in rows.items():
            set_label = "" if lbl == "Community" else "SET n:Searchable "
            s.run(f"UNWIND $rows AS r MATCH (n:`{lbl}` {{id: r.id}}) {set_label}SET n.text = r.text "
                  f"WITH n, r CALL db.create.setNodeVectorProperty(n, 'embedding', r.embedding)", rows=rs)
        log(f"embedded {len(texts)} descriptions with {EMBED_MODEL} ({time.perf_counter() - t0:.0f}s)")
        for name, lbl in (("entity_embedding", "Searchable"), ("community_embedding", "Community")):
            s.run(f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{lbl}) ON (n.embedding) "
                  f"OPTIONS {{indexConfig: {{`vector.dimensions`: {EMBED_DIM}, `vector.similarity_function`: 'cosine'}}}}")
        s.run("CREATE FULLTEXT INDEX entity_text IF NOT EXISTS FOR (n:Searchable|Community) ON EACH [n.text, n.name, n.id]")
        s.run("CALL db.awaitIndexes(300)")
        log(f"vector + full-text indexes online ({time.perf_counter() - t0:.0f}s)")
    if own:
        drv.close()


def _panels(rag):
    return {p: [m for m in rag.det.member_providers if p in rag.det.member_providers[m]]
            for p, t in rag.G.nodes(data="type") if t == "Provider"}


def _describe(rag, n: str) -> str:
    """Short natural-language description used for full-text + vector entry points."""
    G = rag.G
    d = G.nodes[n]
    t = d["type"]
    if t == "Provider":
        ctx = rag.local_context(n, max_lines=8).replace("\n", ". ")
        return ctx[:900]
    if t == "Procedure":
        return f"Procedure CPT {d['code']} {d['name']}. Prior authorization required: {d['pa_required']}."
    if t == "Drug":
        return f"Drug {d['name']} ({d['drug_class']}). Opioid: {d['opioid']}. Prior authorization required: {d['pa_required']}."
    if t == "DenialReason":
        return f"Claim denial reason CARC {d['code']}: {d['name']}."
    if t == "Diagnosis":
        return f"Diagnosis ICD-10 {d['code']}: {d['name']}."
    if t == "Pharmacy":
        fills = [f for f in rag.det.rx if f["pharmacy"] == n]
        opi = [f for f in fills if G.nodes[f["drug"]]["opioid"]]
        members = {f["member"] for f in opi}
        flagged = sorted(m for m in members if m in rag._flags)
        return (f"Retail pharmacy {d['name']} in region {d['region']}: {len(fills)} prescription fills, {len(opi)} opioid fills "
                f"({len(opi) / max(1, len(fills)):.0%}) for {len(members)} members"
                + (f"; dispenses opioids to members flagged for doctor shopping: {', '.join(flagged[:6])}" if flagged else "") + ".")
    if t == "Plan":
        return f"Health plan {n} {d['name']}: {d['product']} product, {d['lob']} line of business."
    return f"{t} {d.get('name', n)}"
