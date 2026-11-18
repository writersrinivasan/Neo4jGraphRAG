"""
GraphRAG retrieval on Neo4j, as explicit, inspectable stages.

  1 exact entity match      (index-backed MATCH on :Label {id})
  2 full-text search        (Lucene BM25 over node descriptions)
  3 vector search           (BGE-small embedding of the question -> cosine kNN index)
  4 hybrid fusion           (reciprocal-rank fusion of 1-3 -> seed nodes)
  5 graph expansion         (label-specific Cypher retrieval queries around each seed)
  6 community search        (vector kNN over :Community GraphRAG reports = "global" context)
  7 text2cypher (optional)  (LLM writes read-only Cypher from the schema; validated, executed read-only)
  8 context assembly        (budgeted text the LLM receives)

Also exposes Cypher versions of the payer detectors so the agent can run on Neo4j instead of NetworkX.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import networkx as nx
from langchain_core.messages import HumanMessage
from neo4j import READ_ACCESS, unit_of_work

from .neo4j_store import embed, get_driver

PREFIX_LABEL = [(r"^M\d{5}$", "Member"), (r"^P\d{4}$", "Provider"), (r"^CLM\d{6}$", "Claim"), (r"^PA\d{5}$", "PriorAuth"),
                (r"^RX\d{6}$", "RxClaim"), (r"^RXP\d{2}$", "Pharmacy"), (r"^PL\d{2}$", "Plan"), (r"^DRG\d{2}$", "Drug"),
                (r"^CARC:", "DenialReason"), (r"^CPT:", "Procedure"), (r"^ICD:", "Diagnosis"), (r"^C\d{1,2}$", "Community")]
LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')
WRITE_CLAUSES = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|CALL\s+dbms|CALL\s+db\.(?!index\.(fulltext|vector)\.query)|apoc\.)", re.I)

SCHEMA = """Node labels and key properties:
(:Member {id, age, region, flags})            (:Provider {id, name, specialty, region, panel_size, flags})
(:Plan {id, name, product, lob})              (:Claim {id, month 'YYYY-MM', billed, allowed, paid, status 'PAID'|'DENIED', in_network})
(:PriorAuth {id, status 'APPROVED'|'DENIED', month})  (:RxClaim {id, month, cost})
(:Drug {id, name, drug_class, opioid, pa_required})   (:Pharmacy {id, name, region})
(:Procedure {id 'CPT:#####', code, name, pa_required})  (:Diagnosis {id 'ICD:...', code, name})
(:DenialReason {id 'CARC:CO-###', code, name})  (:Appeal {id, outcome 'OVERTURNED'|'UPHELD'})
(:Employer {id, name}) (:Phone {id}) (:Address {id}) (:Community {id 'C#', report, members, providers, claims, billed, paid, denial_rate, oon_rate, opioid_share, risk_flags})
Relationships:
(Member)-[:ENROLLED_IN]->(Plan) (Member)-[:HAS_PCP]->(Provider) (Member)-[:EMPLOYED_BY]->(Employer)
(Member)-[:HAS_PHONE]->(Phone) (Member)-[:LIVES_AT]->(Address) (Member)-[:HAS_CONDITION]->(Diagnosis)
(Member)-[:FILED]->(Claim) (Claim)-[:BILLED_BY]->(Provider) (Claim)-[:FOR_PROCEDURE]->(Procedure) (Claim)-[:HAS_DIAGNOSIS]->(Diagnosis)
(Claim)-[:DENIED_FOR]->(DenialReason) (Claim)-[:AUTHORIZED_BY]->(PriorAuth) (Claim)-[:APPEALED]->(Appeal)
(Provider)-[:REQUESTED]->(PriorAuth) (PriorAuth)-[:FOR_MEMBER]->(Member) (PriorAuth)-[:FOR_PROCEDURE]->(Procedure)
(Member)-[:FILLED]->(RxClaim) (RxClaim)-[:OF_DRUG]->(Drug) (RxClaim)-[:PRESCRIBED_BY]->(Provider) (RxClaim)-[:DISPENSED_AT]->(Pharmacy)
(Provider)-[:IN_NETWORK]->(Plan) (Provider)-[:REFERRED_TO {weight}]->(Provider) (Member|Provider)-[:IN_COMMUNITY]->(Community)"""

FEW_SHOT = """Q: How many claims were denied for missing prior authorization?
MATCH (c:Claim)-[:DENIED_FOR]->(:DenialReason {code:'CO-197'}) RETURN count(c) AS denied
Q: Top 5 providers by paid amount for Medicare Advantage members
MATCH (m:Member)-[:ENROLLED_IN]->(pl:Plan) WHERE pl.lob = 'Medicare'
MATCH (m)-[:FILED]->(c:Claim)-[:BILLED_BY]->(p:Provider)
RETURN p.id, p.specialty, round(sum(c.paid)) AS paid ORDER BY paid DESC LIMIT 5"""

# label-specific retrieval queries used for graph expansion (stage 5)
EXPANSION = {
    "Provider": """MATCH (p:Provider {id: $id})
OPTIONAL MATCH (p)<-[:BILLED_BY]-(c:Claim)
WITH p, count(c) AS claims, round(sum(c.billed)) AS billed, round(sum(c.paid)) AS paid,
     sum(CASE c.status WHEN 'DENIED' THEN 1 ELSE 0 END) AS denied
OPTIONAL MATCH (p)<-[:BILLED_BY]-(:Claim)-[:DENIED_FOR]->(d:DenialReason)
WITH p, claims, billed, paid, denied, d.code AS code, count(*) AS n ORDER BY n DESC
WITH p, claims, billed, paid, denied, collect(code + ' x' + n)[..3] AS top_denials
OPTIONAL MATCH (p)<-[:BILLED_BY]-(:Claim)<-[:FILED]-(m:Member)-[:FILED]->(:Claim)-[:BILLED_BY]->(q:Provider)
WHERE q <> p
WITH p, claims, billed, paid, denied, top_denials, q, count(DISTINCT m) AS shared ORDER BY shared DESC
WITH p, claims, billed, paid, denied, top_denials, collect(q.id + ' (' + q.specialty + ', ' + shared + ' shared)')[..4] AS co_billers
OPTIONAL MATCH (p)-[:REQUESTED]->(pa:PriorAuth)
WITH p, claims, billed, paid, denied, top_denials, co_billers, count(pa) AS prior_auths
OPTIONAL MATCH (p)-[:IN_COMMUNITY]->(com:Community)
RETURN p.id AS id, p.specialty AS specialty, p.flags AS flags, claims, billed, paid, denied, top_denials,
       co_billers, prior_auths, com.id AS community""",
    "Member": """MATCH (m:Member {id: $id})-[:ENROLLED_IN]->(pl:Plan)
OPTIONAL MATCH (m)-[:HAS_PCP]->(pcp:Provider)
OPTIONAL MATCH (m)-[:HAS_CONDITION]->(dx:Diagnosis)
WITH m, pl, pcp, collect(dx.name) AS conditions
OPTIONAL MATCH (m)-[:FILED]->(c:Claim)-[:BILLED_BY]->(p:Provider)
OPTIONAL MATCH (c)-[:DENIED_FOR]->(d:DenialReason)
WITH m, pl, pcp, conditions, count(c) AS claims,
     collect(CASE WHEN d IS NULL THEN null ELSE c.id + ' ' + d.code + ' by ' + p.id END)[..5] AS denials
OPTIONAL MATCH (m)-[:FILLED]->(r:RxClaim)-[:OF_DRUG]->(dr:Drug)
OPTIONAL MATCH (r)-[:PRESCRIBED_BY]->(pr:Provider)
OPTIONAL MATCH (r)-[:DISPENSED_AT]->(ph:Pharmacy)
WITH m, pl, pcp, conditions, claims, denials, count(r) AS fills, collect(DISTINCT dr.name) AS drugs,
     collect(DISTINCT pr.id) AS prescribers, collect(DISTINCT ph.id) AS pharmacies
OPTIONAL MATCH (m)-[:HAS_PHONE]->(:Phone)<-[:HAS_PHONE]-(o:Member)
RETURN m.id AS id, m.age AS age, m.flags AS flags, pl.name AS plan, pcp.id AS pcp, conditions, claims, denials,
       fills, drugs, prescribers, pharmacies, collect(DISTINCT o.id)[..8] AS shares_phone_with""",
    "Community": """MATCH (c:Community {id: $id}) RETURN c.id AS id, c.report AS report""",
    "Procedure": """MATCH (x:Procedure {id: $id})<-[:FOR_PROCEDURE]-(c:Claim)-[:BILLED_BY]->(p:Provider)
OPTIONAL MATCH (c)-[:DENIED_FOR]->(d:DenialReason)
WITH x, p, count(c) AS n, sum(CASE WHEN d.code = 'CO-197' THEN 1 ELSE 0 END) AS co197 ORDER BY n DESC
RETURN x.id AS id, x.name AS name, x.pa_required AS pa_required, sum(n) AS claims,
       collect(p.id + ' x' + n + ' (CO-197 ' + co197 + ')')[..5] AS top_billers""",
    "Drug": """MATCH (d:Drug {id: $id})<-[:OF_DRUG]-(r:RxClaim)-[:PRESCRIBED_BY]->(p:Provider)
WITH d, p, count(r) AS n ORDER BY n DESC
RETURN d.id AS id, d.name AS name, d.drug_class AS class, d.opioid AS opioid, sum(n) AS fills, collect(p.id + ' x' + n)[..5] AS top_prescribers""",
    "DenialReason": """MATCH (d:DenialReason {id: $id})<-[:DENIED_FOR]-(c:Claim)-[:BILLED_BY]->(p:Provider)
OPTIONAL MATCH (c)-[:APPEALED]->(a:Appeal)
WITH d, p, count(c) AS n, sum(CASE a.outcome WHEN 'OVERTURNED' THEN 1 ELSE 0 END) AS overturned ORDER BY n DESC
RETURN d.code AS code, d.name AS name, sum(n) AS denied_claims, sum(overturned) AS appeals_overturned, collect(p.id + ' x' + n)[..5] AS top_providers""",
    "Plan": """MATCH (pl:Plan {id: $id})<-[:ENROLLED_IN]-(m:Member)
OPTIONAL MATCH (m)-[:FILED]->(c:Claim)
RETURN pl.id AS id, pl.name AS name, count(DISTINCT m) AS members, count(c) AS claims,
       round(avg(CASE c.status WHEN 'DENIED' THEN 1.0 ELSE 0.0 END), 3) AS denial_rate""",
    "Diagnosis": """MATCH (dx:Diagnosis {id: $id})<-[:HAS_CONDITION]-(m:Member) RETURN dx.id AS id, dx.name AS name, count(m) AS members""",
    "Pharmacy": """MATCH (ph:Pharmacy {id: $id})<-[:DISPENSED_AT]-(r:RxClaim)-[:OF_DRUG]->(d:Drug)
RETURN ph.id AS id, count(r) AS fills, sum(CASE WHEN d.opioid THEN 1 ELSE 0 END) AS opioid_fills""",
}

DETECTORS = {
    "fraud_rings": """// provider pairs whose patient panels overlap abnormally (overlap = shared / smaller panel)
MATCH (a:Provider)<-[:BILLED_BY]-(:Claim)<-[:FILED]-(m:Member)-[:FILED]->(:Claim)-[:BILLED_BY]->(b:Provider)
WHERE a.id < b.id
WITH a, b, count(DISTINCT m) AS shared
WHERE shared >= 8 AND toFloat(shared) / CASE WHEN a.panel_size < b.panel_size THEN a.panel_size ELSE b.panel_size END >= 0.5
RETURN a.id AS a, a.specialty AS a_spec, b.id AS b, b.specialty AS b_spec, shared ORDER BY shared DESC""",
    "doctor_shopping": """MATCH (m:Member)-[:FILLED]->(r:RxClaim)-[:OF_DRUG]->(d:Drug {opioid: true})
MATCH (r)-[:PRESCRIBED_BY]->(p:Provider), (r)-[:DISPENSED_AT]->(ph:Pharmacy)
WITH m, count(DISTINCT r) AS fills, count(DISTINCT p) AS prescribers, count(DISTINCT ph) AS pharmacies, collect(DISTINCT d.name) AS drugs
WHERE prescribers >= 3 AND pharmacies >= 3
RETURN m.id AS member, fills, prescribers, pharmacies, drugs ORDER BY prescribers + pharmacies DESC""",
    "pa_gaps": """MATCH (c:Claim)-[:FOR_PROCEDURE]->(:Procedure {pa_required: true}), (c)-[:BILLED_BY]->(p:Provider)
WITH p, count(c) AS pa_claims,
     sum(CASE WHEN EXISTS { (c)-[:DENIED_FOR]->(:DenialReason {code: 'CO-197'}) } THEN 1 ELSE 0 END) AS co197
WHERE pa_claims >= 20
RETURN p.id AS provider, p.specialty AS specialty, pa_claims, co197, round(toFloat(co197) / pa_claims, 3) AS co197_rate
ORDER BY co197_rate DESC LIMIT 8""",
    "referral_leakage": """MATCH (m:Member)-[:HAS_PCP]->(pcp:Provider)
MATCH (m)-[:FILED]->(c:Claim)-[:BILLED_BY]->(p:Provider) WHERE p <> pcp
WITH pcp, count(c) AS specialist_claims, sum(CASE WHEN c.in_network THEN 0 ELSE 1 END) AS oon
WHERE specialist_claims >= 20
RETURN pcp.id AS pcp, specialist_claims, oon, round(toFloat(oon) / specialist_claims, 3) AS leakage_rate
ORDER BY leakage_rate DESC LIMIT 8""",
    "identity_clusters": """// phones/addresses shared by >=3 members spread across >=3 plans (1-hop; full components need GDS)
MATCH (x)<-[:HAS_PHONE|LIVES_AT]-(m:Member)-[:ENROLLED_IN]->(pl:Plan)
WITH x, collect(DISTINCT m.id) AS members, count(DISTINCT pl) AS plans
WHERE size(members) >= 3 AND plans >= 3
RETURN x.id AS identifier, labels(x)[0] AS kind, size(members) AS n, plans, members[..10] AS members ORDER BY plans DESC, n DESC""",
    "denial_summary": """MATCH (m:Member)-[:ENROLLED_IN]->(pl:Plan), (m)-[:FILED]->(c:Claim)
OPTIONAL MATCH (c)-[:DENIED_FOR]->(d:DenialReason)
WITH pl, count(c) AS claims, sum(CASE c.status WHEN 'DENIED' THEN 1 ELSE 0 END) AS denied, collect(d.code) AS codes
RETURN pl.id AS plan, pl.name AS name, claims, round(toFloat(denied) / claims, 3) AS denial_rate ORDER BY denial_rate DESC""",
    "path_between": """MATCH (a {id: $a}), (b {id: $b})
MATCH p = shortestPath((a)-[:FILED|BILLED_BY|HAS_PCP|REFERRED_TO|HAS_PHONE|LIVES_AT|FILLED|PRESCRIBED_BY|REQUESTED|FOR_MEMBER*..10]-(b))
RETURN [n IN nodes(p) | n.id + ':' + labels(n)[0]] AS nodes, [r IN relationships(p) | type(r)] AS rels""",
    "provider_influence": """// PageRank needs Graph Data Science (not on Aura Free): pull the referral edges, rank in NetworkX
MATCH (a:Provider)-[r:REFERRED_TO]->(b:Provider) RETURN a.id AS a, b.id AS b, r.weight AS w""",
}


def _label_for(node_id: str):
    return next((lbl for pat, lbl in PREFIX_LABEL if re.match(pat, node_id)), None)


class Neo4jGraphRAG:
    def __init__(self):
        self.driver, self.db = get_driver()

    def close(self):
        self.driver.close()

    def run(self, cypher: str, timeout: float = 20, **params):
        @unit_of_work(timeout=timeout)  # server-side transaction timeout
        def work(tx):
            return tx.run(cypher, **params).data()

        with self.driver.session(database=self.db, default_access_mode=READ_ACCESS) as s:
            return s.execute_read(work)  # read-only transaction: writes are rejected by the server

    def status(self):
        rows = self.run("""MATCH (n) WITH labels(n)[0] AS label, count(*) AS c RETURN label, c ORDER BY c DESC""")
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
        idx = self.run("SHOW INDEXES YIELD name, type, state WHERE type IN ['VECTOR','FULLTEXT'] RETURN name, type, state")
        return {"labels": {r["label"]: r["c"] for r in rows}, "nodes": sum(r["c"] for r in rows), "relationships": rels, "indexes": idx}

    # ------------------------------------------------------------------ retrieval stages
    def retrieve(self, question: str, k: int = 5, llm=None, link=None, budget: int = 9000):
        stages = []

        def stage(name, title, cypher, fn, explain):
            t = time.perf_counter()
            try:
                out = fn()
                err = None
            except Exception as e:  # keep the pipeline alive and show the failure
                out, err = [], f"{type(e).__name__}: {str(e)[:200]}"
            stages.append({"stage": name, "title": title, "cypher": cypher, "explain": explain,
                           "ms": round((time.perf_counter() - t) * 1000, 1), "results": out, "error": err})
            return out

        # 1 exact ids
        ids = link(question) if link else []
        q1 = "MATCH (n:`<Label>` {id: $id}) RETURN n.id, labels(n)   // label chosen from the id prefix; uses the uniqueness index"
        exact = stage("exact", "1 · Exact entity match", q1,
                      lambda: [{"id": i, "label": _label_for(i), "score": 1.0} for i in ids
                               if _label_for(i) and self.run(f"MATCH (n:`{_label_for(i)}` {{id: $id}}) RETURN n.id AS id", id=i)],
                      "IDs in the question (P0379, M00333, CO-197…) are looked up directly. These are the most precise entry points.")
        # 2 full-text
        lucene = LUCENE_SPECIAL.sub(r"\\\1", question)
        q2 = "CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score\nRETURN node.id, labels(node), score LIMIT $k"
        ft = stage("fulltext", "2 · Full-text search (BM25)", q2,
                   lambda: [{"id": r["id"], "label": r["label"], "score": round(r["score"], 3)} for r in self.run(
                       "CALL db.index.fulltext.queryNodes('entity_text', $q) YIELD node, score "
                       "RETURN node.id AS id, labels(node)[0] AS label, score LIMIT $k", q=lucene, k=k)],
                   "Keyword match over each entity's description, e.g. 'MRI', 'sleep apnea', 'oxycodone'.")
        # 3 vector
        qvec = []
        q3 = "CALL db.index.vector.queryNodes('entity_embedding', $k, $embedding) YIELD node, score\nRETURN node.id, labels(node), score"

        def _vec():
            qvec.append(embed([question])[0])
            return [{"id": r["id"], "label": r["label"], "score": round(r["score"], 3)} for r in self.run(
                "CALL db.index.vector.queryNodes('entity_embedding', $k, $v) YIELD node, score "
                "RETURN node.id AS id, labels(node)[0] AS label, score", k=k, v=qvec[0])]
        vec = stage("vector", "3 · Vector search (semantic)", q3, _vec,
                    "The question is embedded locally (BGE-small, 384 dimensions) and matched by cosine similarity, so 'doctor shopping' can find opioid drugs even without shared keywords.")
        # 4 fusion
        def _fuse():
            score = defaultdict(float)
            label = {}
            for weight, lst in ((3.0, exact), (1.0, ft), (1.0, vec)):
                for rank, r in enumerate(lst):
                    score[r["id"]] += weight / (rank + 1)
                    label[r["id"]] = r["label"]
            return [{"id": i, "label": label[i], "rrf": round(s, 3)} for i, s in sorted(score.items(), key=lambda x: -x[1])[:k]]
        seeds = stage("fusion", "4 · Hybrid fusion (reciprocal rank)", "-- Python: score(id) = Σ weight / (rank + 1); exact matches weighted 3×", _fuse,
                      "The three lists are merged into one ranked set of seed nodes. Entities found by several methods rise to the top.")
        # 5 expansion
        def _expand():
            todo = [s_ for s_ in seeds if s_["label"] in EXPANSION]
            with ThreadPoolExecutor(max_workers=6) as pool:  # one Aura round trip per seed, in parallel
                results = list(pool.map(lambda s_: self.run(EXPANSION[s_["label"]], id=s_["id"]), todo))
            return [{"seed": s_["id"], "label": s_["label"], "facts": rows[0]} for s_, rows in zip(todo, results) if rows]
        expanded = stage("expansion", "5 · Graph expansion (Cypher retrieval queries)",
                         "\n\n".join(f"// {lbl}\n{q}" for lbl, q in EXPANSION.items() if lbl in {s_['label'] for s_ in seeds}) or "-- no seeds",
                         _expand, "Each seed is expanded along typed relationships: claims, denials, co-billing providers, prior auths, prescribers and its community. This is the step that makes it GraphRAG rather than plain RAG.")
        # 6 community (global)
        q6 = "CALL db.index.vector.queryNodes('community_embedding', 2, $embedding) YIELD node, score\nRETURN node.id, node.report, score"
        comms = stage("community", "6 · Community reports (global context)", q6,
                      lambda: [{"id": r["id"], "score": round(r["score"], 3), "report": r["report"]} for r in self.run(
                          "CALL db.index.vector.queryNodes('community_embedding', 2, $v) YIELD node, score "
                          "RETURN node.id AS id, node.report AS report, score", v=qvec[0] if qvec else embed([question])[0])],
                      "The two most relevant pre-computed Louvain community summaries add network-wide context: totals, denial mix and detector flags.")
        # 7 text2cypher
        t2c = {"cypher": None}
        if llm is not None:
            def _t2c():
                cy, rows = self.text2cypher(question, llm, sink=t2c)
                return rows
            stage("text2cypher", "7 · Text2Cypher (LLM-generated query)", "(generated at run time; shown in results)", _t2c,
                  "The LLM reads the graph schema and writes a Cypher query for exact counts, sums and lists. The query is refused if it contains write clauses, gets a LIMIT, runs in a read-only transaction and has a 10 s timeout.")
            stages[-1]["cypher"] = t2c["cypher"] or stages[-1]["cypher"]
        # 8 context assembly
        def _assemble():
            parts = []
            for e in expanded:
                parts.append(f"[{e['seed']}:{e['label']}] " + json.dumps(e["facts"], default=str))
            for c in comms:
                parts.append(f"[{c['id']}:Community] {c['report']}")
            if llm is not None and stages[-1]["stage"] == "text2cypher" and stages[-1]["results"]:
                parts.append(f"[text2cypher] {t2c['cypher']}\n=> " + json.dumps(stages[-1]["results"][:15], default=str))
            text = "\n\n".join(parts)
            return [{"chars": len(text[:budget]), "approx_tokens": len(text[:budget]) // 4, "text": text[:budget]}]
        ctx = stage("context", "8 · Context assembly", "-- Python: concatenate expansion facts + community reports (+ text2cypher rows), cap at the token budget", _assemble,
                    "The context the LLM actually receives. Every fact carries its node id, so the answer can cite it and the verify step can check it.")
        return {"stages": stages, "context": ctx[0]["text"] if ctx else "", "seeds": [s_["id"] for s_ in seeds]}

    def text2cypher(self, question: str, llm, sink: dict | None = None):
        """LLM writes Cypher from the schema; refused if it writes; LIMIT enforced; read-only tx with timeout."""
        prompt = (f"Write ONE read-only Cypher query (Neo4j 5) that answers the question. Use only this schema.\n{SCHEMA}\n\n"
                  f"Examples:\n{FEW_SHOT}\n\nRules: no write clauses, always end with LIMIT 25 or less, return named columns.\n"
                  f"Question: {question}\nReply with the Cypher only, no explanation, no code fences.")
        msg = llm.invoke([HumanMessage(prompt)])
        cy = re.sub(r"<think>.*?</think>|```(cypher)?", "", msg.content, flags=re.S | re.I).strip().rstrip(";")
        if sink is not None:
            sink["cypher"] = cy
        if WRITE_CLAUSES.search(cy):
            raise ValueError("generated Cypher contains a write/admin clause; refused")
        if not re.search(r"\bLIMIT\s+\d+\s*$", cy, re.I):
            cy += "\nLIMIT 25"
            if sink is not None:
                sink["cypher"] = cy
        return cy, self.run(cy, timeout=10)[:25]

    # ------------------------------------------------------------------ Cypher detectors for the agent
    def detector(self, name: str, **params) -> tuple[str, list, str]:
        q = DETECTORS[name]
        rows = self.run(q, timeout=60, **params)
        if name == "fraud_rings":
            g = nx.Graph()
            g.add_edges_from((r["a"], r["b"]) for r in rows)
            rings = [sorted(c) for c in nx.connected_components(g) if len(c) >= 3]
            in_ring = {p for r in rings for p in r}
            ring_pairs = [r for r in rows if r["a"] in in_ring and r["b"] in in_ring]
            lone = [r for r in rows if not (r["a"] in in_ring and r["b"] in in_ring)]
            pair = lambda r: f"{r['a']}({r['a_spec']})-{r['b']}({r['b_spec']}) shared {r['shared']}"
            text = (f"Cypher fraud-ring query returned {len(rows)} abnormal provider pairs. "
                    f"{len(ring_pairs)} of them connect into {len(rings)} ring(s) of 3+ providers: " + "; ".join(str(r) for r in rings) +
                    "\nRing pairs: " + "; ".join(pair(r) for r in ring_pairs[:12]) +
                    (f"\n{len(lone)} isolated 2-provider pair(s), not a ring (lower priority, e.g. a shared practice): " + "; ".join(pair(r) for r in lone[:6]) if lone else ""))
            return text, [p for r in rings for p in r], q
        if name == "provider_influence":
            D = nx.DiGraph()
            D.add_weighted_edges_from((r["a"], r["b"], r["w"]) for r in rows)
            pr = sorted(nx.pagerank(D).items(), key=lambda x: -x[1])[:10]
            return "PageRank on referral edges pulled from Neo4j (GDS unavailable on Aura Free):\n" + \
                   "\n".join(f"{p}: {v:.5f}" for p, v in pr), [p for p, _ in pr], q
        refs = [str(r.get("member") or r.get("provider") or r.get("pcp") or r.get("plan") or r.get("identifier") or "") for r in rows]
        if name == "identity_clusters":
            refs = [m for r in rows for m in r["members"]]
        if name == "path_between" and rows:
            refs = [n.split(":")[0] for n in rows[0]["nodes"]]
        text = f"Cypher detector {name} returned {len(rows)} rows:\n" + "\n".join(json.dumps(r, default=str) for r in rows[:15])
        return text, [r for r in refs if r][:40], q
