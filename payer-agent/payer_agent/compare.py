"""
Traditional SQL joins vs NetworkX traversal on the SAME payer data.

Every query runs on both engines; the results are normalised and asserted equal, then timed (median).
Includes queries where SQL wins (pure aggregation) so the comparison is honest.
"""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict

import networkx as nx

from .data import Dataset
from .graphrag import in_by_rel, out_by_rel


def _time(fn, reps=3):
    ts, out = [], None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t) * 1000)
    return out, statistics.median(ts)


def build_cases(ds: Dataset, flagged_provider: str):
    G, db = ds.G, ds.db

    # ---------- Q1 shared-panel providers (2 hops)
    q1_sql = """SELECT b.provider_id, COUNT(DISTINCT b.member_id) AS shared
FROM claim a
JOIN claim b ON a.member_id = b.member_id
            AND b.provider_id <> a.provider_id
WHERE a.provider_id = :p
GROUP BY b.provider_id
HAVING shared >= 8"""

    def q1_nx():
        members = {next(iter(G.predecessors(c))) for c in in_by_rel(G, flagged_provider, "BILLED_BY")}
        cnt = Counter()
        for m in members:
            provs = {out_by_rel(G, c, "BILLED_BY")[0] for c in out_by_rel(G, m, "FILED")}
            for p in provs - {flagged_provider}:
                cnt[p] += 1
        return {p: n for p, n in cnt.items() if n >= 8}

    # ---------- Q2 opioid doctor shopping
    q2_sql = """SELECT r.member_id
FROM rx_claim r
JOIN drug d ON d.id = r.drug_id
WHERE d.opioid = 1
GROUP BY r.member_id
HAVING COUNT(DISTINCT r.prescriber_id) >= 3
   AND COUNT(DISTINCT r.pharmacy_id)   >= 3"""

    def q2_nx():
        out = set()
        opioids = [d for d, a in G.nodes(data=True) if a["type"] == "Drug" and a["opioid"]]
        per = defaultdict(lambda: (set(), set()))
        for d in opioids:
            for r in in_by_rel(G, d, "OF_DRUG"):
                m = next(iter(G.predecessors(r)))
                pres, phs = per[m]
                pres.update(out_by_rel(G, r, "PRESCRIBED_BY"))
                phs.update(out_by_rel(G, r, "DISPENSED_AT"))
        for m, (pres, phs) in per.items():
            if len(pres) >= 3 and len(phs) >= 3:
                out.add(m)
        return out

    # ---------- Q3 prior-auth process gap
    q3_sql = """SELECT DISTINCT c.id
FROM claim c
JOIN prior_auth pa ON pa.provider_id = c.provider_id
                  AND pa.cpt        = c.cpt
                  AND pa.status     = 'APPROVED'
                  AND pa.member_id <> c.member_id
WHERE c.denial_code = 'CO-197'"""

    def q3_nx():
        out = set()
        for c in in_by_rel(G, "CARC:CO-197", "DENIED_FOR"):
            p = out_by_rel(G, c, "BILLED_BY")[0]
            cpt = [x for x in out_by_rel(G, c, "FOR_PROCEDURE")][0]
            m = next(iter(G.predecessors(c)))
            for pa in out_by_rel(G, p, "REQUESTED"):
                if (G.nodes[pa]["status"] == "APPROVED" and G.has_edge(pa, cpt)
                        and out_by_rel(G, pa, "FOR_MEMBER")[0] != m):
                    out.add(c)
                    break
        return out

    # ---------- Q4 out-of-network leakage by PCP
    q4_sql = """SELECT m.pcp_id,
       COUNT(*) AS specialist_claims,
       SUM(NOT EXISTS (SELECT 1 FROM provider_network pn
                       WHERE pn.provider_id = c.provider_id
                         AND pn.plan_id     = m.plan_id)) AS oon
FROM claim c
JOIN member m ON m.id = c.member_id
WHERE m.pcp_id IS NOT NULL
  AND c.provider_id <> m.pcp_id
GROUP BY m.pcp_id
HAVING specialist_claims >= 20"""

    def q4_nx():
        stats = defaultdict(lambda: [0, 0])
        for m in (n for n, t in G.nodes(data="type") if t == "Member"):
            pcp = out_by_rel(G, m, "HAS_PCP")
            if not pcp:
                continue
            plan = out_by_rel(G, m, "ENROLLED_IN")[0]
            for c in out_by_rel(G, m, "FILED"):
                p = out_by_rel(G, c, "BILLED_BY")[0]
                if p == pcp[0]:
                    continue
                s = stats[pcp[0]]
                s[0] += 1
                s[1] += not G.has_edge(p, plan)
        return {p: (a, b) for p, (a, b) in stats.items() if a >= 20}

    # ---------- Q5 synthetic-identity clusters (variable depth)
    q5_sql = """WITH RECURSIVE link(a, b) AS (
  SELECT m1.id, m2.id FROM member m1
  JOIN member m2 ON m1.phone = m2.phone AND m1.id <> m2.id
  UNION
  SELECT m1.id, m2.id FROM member m1
  JOIN member m2 ON m1.address = m2.address AND m1.id <> m2.id
),
reach(root, id) AS (
  SELECT id, id FROM member
  UNION
  SELECT r.root, l.b FROM reach r JOIN link l ON l.a = r.id
),
comp AS (SELECT root AS member_id, MIN(id) AS cluster FROM reach GROUP BY root)
SELECT cluster, COUNT(*) AS n FROM comp
GROUP BY cluster HAVING n >= 5"""

    def q5_nx():
        H = nx.Graph()
        for u, v, r in G.edges(data="rel"):
            if r in ("HAS_PHONE", "LIVES_AT"):
                H.add_edge(u, v)
        out = {}
        for comp in nx.connected_components(H):
            ms = [n for n in comp if G.nodes[n]["type"] == "Member"]
            if len(ms) >= 5:
                out[min(ms)] = len(ms)
        return out

    # ---------- Q6 shortest referral path between two providers
    R = nx.Graph()
    R.add_edges_from((u, v) for u, v, r in G.edges(data="rel") if r == "REFERRED_TO")
    lengths = nx.single_source_shortest_path_length(R, flagged_provider) if flagged_provider in R else {}
    far = sorted(lengths.items(), key=lambda x: -x[1])
    src = flagged_provider if far else next(iter(R))
    if not far:
        far = sorted(nx.single_source_shortest_path_length(R, src).items(), key=lambda x: -x[1])
    dst = far[0][0]
    q6_sql = """WITH RECURSIVE edge(a, b) AS (
  SELECT src, dst FROM referral UNION SELECT dst, src FROM referral
),
bfs(node, depth) AS (
  SELECT :src, 0
  UNION
  SELECT e.b, bfs.depth + 1 FROM bfs JOIN edge e ON e.a = bfs.node
  WHERE bfs.depth < 8
)
SELECT MIN(depth) FROM bfs WHERE node = :dst"""

    def q6_nx():
        return nx.shortest_path_length(R, src, dst)

    # ---------- Q7 denial rate by plan (pure aggregation - SQL's home turf)
    q7_sql = """SELECT m.plan_id,
       COUNT(*) AS claims,
       ROUND(AVG(c.status = 'DENIED'), 3) AS denial_rate
FROM claim c
JOIN member m ON m.id = c.member_id
GROUP BY m.plan_id"""

    def q7_nx():
        agg = defaultdict(lambda: [0, 0])
        for c, t in G.nodes(data="type"):
            if t != "Claim":
                continue
            m = next(iter(G.predecessors(c)))
            plan = out_by_rel(G, m, "ENROLLED_IN")[0]
            a = agg[plan]
            a[0] += 1
            a[1] += G.nodes[c]["status"] == "DENIED"
        return {p: (n, round(d / n, 3)) for p, (n, d) in agg.items()}

    # ---------- Q8 member 360 (GraphRAG local context)
    member = ds.truth["doctor_shoppers"][0]
    q8_sql = """SELECT 'claim', c.id, p.specialty, pr.name, dr.description
FROM claim c
JOIN provider p        ON p.id = c.provider_id
JOIN procedure pr      ON pr.cpt = c.cpt
LEFT JOIN denial_reason dr ON dr.code = c.denial_code
WHERE c.member_id = :m
UNION ALL
SELECT 'rx', r.id, p.specialty, d.name, ph.name
FROM rx_claim r
JOIN drug d      ON d.id = r.drug_id
JOIN provider p  ON p.id = r.prescriber_id
JOIN pharmacy ph ON ph.id = r.pharmacy_id
WHERE r.member_id = :m"""

    def q8_nx():
        rows = set()
        for x in G.successors(member):
            if G[member][x]["rel"] in ("FILED", "FILLED"):
                rows.add(x)
        return rows

    # ---------- Q9 PageRank provider influence (iterative algorithm)
    def q9_nx():
        D = nx.DiGraph()
        D.add_edges_from((u, v, d) for u, v, d in G.edges(data=True) if d["rel"] == "REFERRED_TO")
        return sorted(nx.pagerank(D, weight="weight").items(), key=lambda x: -x[1])[:5]

    return [
        dict(id="Q1", title="Providers sharing patients with a flagged provider", category="Fraud / SIU", depth="2 hops",
             sql=q1_sql, params={"p": flagged_provider}, nx_fn=q1_nx,
             nx_code="members = {pred(c) for c in in_by_rel(G, P, 'BILLED_BY')}\nfor m in members: count providers of m's claims",
             norm_sql=lambda r: {a: b for a, b in r}, norm_nx=lambda r: r),
        dict(id="Q2", title="Opioid doctor shopping (≥3 prescribers & ≥3 pharmacies)", category="Pharmacy / FWA", depth="2 hops",
             sql=q2_sql, params={}, nx_fn=q2_nx,
             nx_code="for opioid in drugs: for rx in in_by_rel(opioid, 'OF_DRUG'):\n    collect prescribers + pharmacies per member",
             norm_sql=lambda r: {x[0] for x in r}, norm_nx=lambda r: r),
        dict(id="Q3", title="CO-197 denials where the provider holds an approved PA for the same procedure", category="Prior auth / UM", depth="3 hops",
             sql=q3_sql, params={}, nx_fn=q3_nx,
             nx_code="for claim in in_by_rel('CARC:CO-197'):\n    provider -> REQUESTED -> PA (APPROVED, same CPT, other member)",
             norm_sql=lambda r: {x[0] for x in r}, norm_nx=lambda r: r),
        dict(id="Q4", title="Out-of-network leakage by attributed PCP", category="Network management", depth="3 hops",
             sql=q4_sql, params={}, nx_fn=q4_nx,
             nx_code="member -HAS_PCP-> pcp; member -FILED-> claim -BILLED_BY-> provider\noon = not G.has_edge(provider, plan)",
             norm_sql=lambda r: {a: (b, c) for a, b, c in r}, norm_nx=lambda r: r),
        dict(id="Q5", title="Synthetic-identity clusters via shared phone/address", category="Enrollment integrity", depth="Variable",
             sql=q5_sql, params={}, nx_fn=q5_nx,
             nx_code="H = edges where rel in (HAS_PHONE, LIVES_AT)\nnx.connected_components(H)",
             norm_sql=lambda r: {a: b for a, b in r}, norm_nx=lambda r: r),
        dict(id="Q6", title=f"Shortest referral path {src} → {dst}", category="Care coordination", depth="Variable",
             sql=q6_sql, params={"src": src, "dst": dst}, nx_fn=q6_nx,
             nx_code="nx.shortest_path_length(R, src, dst)",
             norm_sql=lambda r: r[0][0], norm_nx=lambda r: r),
        dict(id="Q7", title="Denial rate by plan", category="Reporting", depth="1 join",
             sql=q7_sql, params={}, nx_fn=q7_nx,
             nx_code="for claim in claims: plan = member(claim) -> ENROLLED_IN\n    aggregate",
             norm_sql=lambda r: {a: (b, c) for a, b, c in r}, norm_nx=lambda r: r),
        dict(id="Q8", title=f"Member 360 context for {member} (claims + Rx)", category="GraphRAG retrieval", depth="1-2 hops",
             sql=q8_sql, params={"m": member}, nx_fn=q8_nx,
             nx_code="ego = G.successors(member) where rel in (FILED, FILLED)\n(+ neighbours carry provider, drug, pharmacy)",
             norm_sql=lambda r: {x[1] for x in r}, norm_nx=lambda r: r),
        dict(id="Q9", title="Most influential providers in the referral network (PageRank)", category="Network analytics", depth="Whole graph",
             sql=None, params={}, nx_fn=q9_nx,
             nx_code="nx.pagerank(referral_graph, weight='weight')",
             norm_sql=None, norm_nx=lambda r: r),
    ]


def _count_joins(sql: str | None):
    if not sql:
        return None
    s = sql.upper()
    return s.count("JOIN") + s.count("EXISTS")


def run_comparison(ds: Dataset, flagged_provider: str):
    rows = []
    for case in build_cases(ds, flagged_provider):
        nx_out, nx_ms = _time(case["nx_fn"])
        row = {k: case[k] for k in ("id", "title", "category", "depth", "sql", "nx_code")}
        row.update(nx_ms=round(nx_ms, 2), joins=_count_joins(case["sql"]),
                   sql_lines=len(case["sql"].splitlines()) if case["sql"] else None,
                   nx_lines=len(case["nx_code"].splitlines()))
        if case["sql"]:
            sql_out, sql_ms = _time(lambda: ds.db.execute(case["sql"], case["params"]).fetchall(), reps=1 if case["id"] == "Q5" else 3)
            a, b = case["norm_sql"](sql_out), case["norm_nx"](nx_out)
            row.update(sql_ms=round(sql_ms, 2), match=a == b, result_size=len(b) if hasattr(b, "__len__") else b,
                       winner="NetworkX" if nx_ms < sql_ms else "SQL", speedup=round(max(sql_ms, nx_ms) / max(min(sql_ms, nx_ms), 1e-3), 1))
        else:
            row.update(sql_ms=None, match=None, result_size=len(nx_out), winner="NetworkX (SQL impractical)", speedup=None)
        rows.append(row)
    return rows


CONCEPTUAL = [
    ("Data model", "Tables, rows and foreign keys; relationships are implicit until joined",
     "Nodes and edges are first-class; relationships are stored and typed (FILED, BILLED_BY, REQUESTED)"),
    ("Relationship lookup", "JOIN on indexed keys at query time, O(log n) per lookup",
     "Adjacency lookup, O(1) per neighbour (index-free adjacency in memory)"),
    ("Multi-hop questions", "One JOIN per hop; query size and planner cost grow with depth",
     "Traversal loop or BFS; cost grows with the neighbourhood actually visited"),
    ("Variable depth / paths", "Recursive CTEs: verbose, easy to explode, no native path type",
     "shortest_path, all_simple_paths, connected_components built in"),
    ("Graph algorithms", "Not available; PageRank or Louvain need procedural loops or an export",
     "500+ algorithms: PageRank, Louvain, betweenness, k-core, link prediction"),
    ("Aggregation and reporting", "Excellent: GROUP BY, window functions, optimizer",
     "Manual Python loops; slower for flat roll-ups (see Q7)"),
    ("Schema change", "ALTER TABLE plus migration scripts",
     "Add a node or edge type at any time; no schema"),
    ("Persistence and ACID", "Durable, transactional, mature backup and recovery",
     "In-memory only; persist via pickle or GraphML; no transactions"),
    ("Concurrency and security", "Many users, roles, row-level security, audit (HIPAA-ready)",
     "Single Python process; no access control. Wrap behind an API (as this app does)"),
    ("Scale", "Billions of rows on disk",
     "Bounded by RAM (~hundreds of bytes per node or edge); fine to roughly 10⁷–10⁸ elements"),
    ("Query language", "SQL (ANSI standard, universal skill)",
     "Python API (no declarative language); Cypher or GQL arrive with a graph database"),
    ("GraphRAG fit", "LLM gets flat rows: verbose, relationships lost, high token cost",
     "LLM gets typed triples, subgraphs and community summaries: compact and explainable"),
    ("Best role for a payer", "System of record: claims adjudication, finance, regulatory reports",
     "Analytics and GraphRAG layer: FWA, prior-auth, network, member-360 context for AI agents"),
]
