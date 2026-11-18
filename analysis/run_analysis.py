"""
GraphRAG for Health Insurance - analytics engine.

1. Generates a synthetic (but realistic-shaped) health-insurance dataset:
   members, providers, claims, diagnoses, referrals, shared identifiers,
   with planted fraud rings.
2. Stores it RELATIONALLY in SQLite (stand-in for the legacy MySQL system).
3. Builds the SAME data as a property graph in NetworkX.
4. Runs: multi-hop benchmark (SQL recursive CTE vs graph traversal),
   fraud-ring detection, provider influence (PageRank), entity resolution,
   comorbidity network, denial/cost analytics, GraphRAG context sizing,
   NetworkX memory footprint -> scale extrapolation.
5. Writes ../web/data/analytics.json for the Next.js app.

Run:  .venv/bin/python run_analysis.py
All numbers are computed on synthetic data - no PHI involved.
"""
import json
import math
import os
import random
import sqlite3
import statistics
import time
import tracemalloc
from collections import Counter, defaultdict
from itertools import combinations

import networkx as nx

SEED = 42
rng = random.Random(SEED)

N_REGIONS = 30
N_PROVIDERS = 1500
N_MEMBERS = 20000
N_RINGS = 6
RING_PROVIDERS = 5
RING_MEMBERS = 30

OUT = os.path.join(os.path.dirname(__file__), "..", "web", "data", "analytics.json")

SPECIALTIES = [
    ("Primary Care", 0.45), ("Cardiology", 0.08), ("Endocrinology", 0.05),
    ("Orthopedics", 0.08), ("Physical Therapy", 0.08), ("Radiology", 0.06),
    ("Nephrology", 0.04), ("Psychiatry", 0.06), ("Pulmonology", 0.05),
    ("Durable Medical Equip.", 0.05),
]
CONDITIONS = {
    "E11 Type 2 diabetes": 0.10, "I10 Hypertension": 0.22, "N18 Chronic kidney disease": 0.03,
    "J45 Asthma": 0.08, "J44 COPD": 0.04, "F32 Depression": 0.09, "F41 Anxiety": 0.08,
    "M54 Low back pain": 0.12, "E78 Hyperlipidemia": 0.15, "I25 Coronary artery disease": 0.04,
    "M17 Knee osteoarthritis": 0.06, "G47 Sleep apnea": 0.05,
}
# conditional boosts -> realistic comorbidity structure
BOOST = {
    "E11 Type 2 diabetes": {"I10 Hypertension": 0.55, "E78 Hyperlipidemia": 0.45, "N18 Chronic kidney disease": 0.15},
    "I10 Hypertension": {"I25 Coronary artery disease": 0.10, "E78 Hyperlipidemia": 0.30},
    "F32 Depression": {"F41 Anxiety": 0.50},
    "J44 COPD": {"G47 Sleep apnea": 0.20},
    "J45 Asthma": {"J44 COPD": 0.08},
    "M17 Knee osteoarthritis": {"M54 Low back pain": 0.30},
}
CPT = {  # code: (desc, mean cost, specialty affinity)
    "99213": ("Office visit, est. patient", 110, "Primary Care"),
    "99214": ("Office visit, moderate", 165, "Primary Care"),
    "93000": ("ECG", 60, "Cardiology"),
    "93306": ("Echocardiogram", 650, "Cardiology"),
    "83036": ("HbA1c test", 25, "Endocrinology"),
    "27447": ("Total knee replacement", 18500, "Orthopedics"),
    "97110": ("Therapeutic exercise", 95, "Physical Therapy"),
    "72148": ("MRI lumbar spine", 1200, "Radiology"),
    "90935": ("Hemodialysis", 900, "Nephrology"),
    "90837": ("Psychotherapy 60 min", 180, "Psychiatry"),
    "94010": ("Spirometry", 70, "Pulmonology"),
    "E0601": ("CPAP device", 1100, "Durable Medical Equip."),
}
SPEC_CPT = defaultdict(list)
for code, (_, _, sp) in CPT.items():
    SPEC_CPT[sp].append(code)


def pick_specialty():
    r, acc = rng.random(), 0
    for s, p in SPECIALTIES:
        acc += p
        if r <= acc:
            return s
    return SPECIALTIES[-1][0]


# ------------------------------------------------------------------ data gen
def generate():
    providers = []
    for i in range(N_PROVIDERS):
        providers.append({
            "id": f"P{i:05d}", "region": i % N_REGIONS, "specialty": pick_specialty(),
            "addr": f"PADDR{i:05d}",
        })
    by_region = defaultdict(list)
    for p in providers:
        by_region[p["region"]].append(p)

    members = []
    for i in range(N_MEMBERS):
        conds = [c for c, p in CONDITIONS.items() if rng.random() < p]
        for c in list(conds):
            for c2, p2 in BOOST.get(c, {}).items():
                if c2 not in conds and rng.random() < p2:
                    conds.append(c2)
        members.append({
            "id": f"M{i:06d}", "region": rng.randrange(N_REGIONS), "age": rng.randint(1, 89),
            "plan": rng.choice(["HMO Silver", "PPO Gold", "PPO Platinum", "Medicare Adv.", "Medicaid MCO"]),
            "phone": f"PH{i:06d}", "addr": f"ADDR{i:06d}", "conds": conds,
        })
    # legit families sharing address (2-4 members)
    i = 0
    while i < N_MEMBERS - 4:
        if rng.random() < 0.25:
            k = rng.randint(2, 4)
            for j in range(1, k):
                members[i + j]["addr"] = members[i]["addr"]
                members[i + j]["region"] = members[i]["region"]
            i += k
        else:
            i += 1

    # plant fraud rings: providers in same region, colluding members share a few phones/addresses
    rings = []
    used = set()
    ring_member_ids = set()
    for r in range(N_RINGS):
        region = rng.randrange(N_REGIONS)
        cands = [p for p in by_region[region] if p["id"] not in used]
        ring_p = rng.sample(cands, RING_PROVIDERS)
        for p in ring_p:
            used.add(p["id"])
        pool = [m for m in members if m["id"] not in ring_member_ids]
        ring_m = rng.sample(pool, RING_MEMBERS)
        phones = [f"RINGPH{r}_{k}" for k in range(3)]
        addrs = [f"RINGADDR{r}_{k}" for k in range(2)]
        for m in ring_m:
            ring_member_ids.add(m["id"])
            m["phone"] = rng.choice(phones)
            m["addr"] = rng.choice(addrs)
        rings.append({"providers": [p["id"] for p in ring_p], "members": [m["id"] for m in ring_m]})
    ring_provider_set = {p for r in rings for p in r["providers"]}

    # claims
    claims = []
    cid = 0
    start = 2024 * 12  # Jan 2024 .. Jun 2026 (30 months)
    for m in members:
        local = by_region[m["region"]]
        n_prov = rng.choices([1, 2, 3, 4, 5], [30, 32, 20, 12, 6])[0]
        # specialty matching conditions
        provs = rng.sample(local, n_prov)
        for p in provs:
            for _ in range(rng.randint(1, 5)):
                codes = SPEC_CPT.get(p["specialty"]) or ["99213"]
                code = rng.choice(codes)
                base = CPT[code][1]
                amt = round(max(15, rng.gauss(base, base * 0.25)), 2)
                mon = start + rng.randrange(30)
                denied = rng.random() < 0.08
                dx = rng.choice(m["conds"]) if m["conds"] else "Z00 Routine exam"
                claims.append((f"C{cid:07d}", m["id"], p["id"], code, dx, amt, f"{mon//12}-{mon%12+1:02d}", int(denied)))
                cid += 1
    for r in rings:  # every ring member billed by every ring provider, inflated high-cost codes
        for mid in r["members"]:
            for pid in r["providers"]:
                for _ in range(rng.randint(2, 4)):
                    code = rng.choice(["72148", "E0601", "93306", "97110"])
                    base = CPT[code][1]
                    amt = round(rng.gauss(base * 1.6, base * 0.2), 2)
                    mon = start + rng.randrange(30)
                    claims.append((f"C{cid:07d}", mid, pid, code, "M54 Low back pain", amt,
                                   f"{mon//12}-{mon%12+1:02d}", int(rng.random() < 0.03)))
                    cid += 1

    # referrals: PCP -> specialists in region; a few regional hub specialists
    referrals = Counter()
    hubs = {reg: rng.sample([p for p in by_region[reg] if p["specialty"] != "Primary Care"], 2) for reg in by_region}
    for p in providers:
        if p["specialty"] != "Primary Care":
            continue
        specs = [q for q in by_region[p["region"]] if q["specialty"] != "Primary Care"]
        for q in rng.sample(specs, min(len(specs), rng.randint(2, 6))):
            referrals[(p["id"], q["id"])] += rng.randint(1, 10)
        for h in hubs[p["region"]]:
            referrals[(p["id"], h["id"])] += rng.randint(10, 40)
    return providers, members, claims, referrals, rings, ring_provider_set, ring_member_ids


# ------------------------------------------------------------------ relational store
def build_sqlite(providers, members, claims, referrals):
    db = sqlite3.connect(":memory:")
    c = db.cursor()
    c.executescript("""
    CREATE TABLE provider(id TEXT PRIMARY KEY, region INT, specialty TEXT, addr TEXT);
    CREATE TABLE member(id TEXT PRIMARY KEY, region INT, age INT, plan TEXT, phone TEXT, addr TEXT);
    CREATE TABLE member_condition(member_id TEXT, icd TEXT);
    CREATE TABLE claim(id TEXT PRIMARY KEY, member_id TEXT, provider_id TEXT, cpt TEXT, icd TEXT,
                       amount REAL, month TEXT, denied INT);
    CREATE TABLE referral(src TEXT, dst TEXT, cnt INT);
    """)
    c.executemany("INSERT INTO provider VALUES(?,?,?,?)", [(p["id"], p["region"], p["specialty"], p["addr"]) for p in providers])
    c.executemany("INSERT INTO member VALUES(?,?,?,?,?,?)", [(m["id"], m["region"], m["age"], m["plan"], m["phone"], m["addr"]) for m in members])
    c.executemany("INSERT INTO member_condition VALUES(?,?)", [(m["id"], x) for m in members for x in m["conds"]])
    c.executemany("INSERT INTO claim VALUES(?,?,?,?,?,?,?,?)", claims)
    c.executemany("INSERT INTO referral VALUES(?,?,?)", [(a, b, n) for (a, b), n in referrals.items()])
    c.executescript("""
    CREATE INDEX ix_claim_m ON claim(member_id);
    CREATE INDEX ix_claim_p ON claim(provider_id);
    CREATE TABLE pm AS SELECT DISTINCT provider_id, member_id FROM claim;   -- edge table
    CREATE INDEX ix_pm_p ON pm(provider_id, member_id);
    CREATE INDEX ix_pm_m ON pm(member_id, provider_id);
    """)
    db.commit()
    return db


# ------------------------------------------------------------------ graph store
def build_graph(providers, members, claims, referrals):
    G = nx.Graph()
    for p in providers:
        G.add_node(p["id"], label="Provider", specialty=p["specialty"], region=p["region"])
    for m in members:
        G.add_node(m["id"], label="Member", plan=m["plan"], age=m["age"])
        G.add_node(m["phone"], label="Phone")
        G.add_node(m["addr"], label="Address")
        G.add_edge(m["id"], m["phone"], rel="HAS_PHONE")
        G.add_edge(m["id"], m["addr"], rel="LIVES_AT")
        for x in m["conds"]:
            G.add_node(x, label="Diagnosis")
            G.add_edge(m["id"], x, rel="DIAGNOSED_WITH")
    for (cid, mid, pid, code, dx, amt, mon, den) in claims:
        if G.has_edge(mid, pid):
            e = G[mid][pid]
            e["claims"] += 1
            e["amount"] += amt
        else:
            G.add_edge(mid, pid, rel="TREATED_BY", claims=1, amount=amt)
    D = nx.DiGraph()
    for (a, b), n in referrals.items():
        D.add_edge(a, b, weight=n)
    return G, D


def timeit(fn, reps):
    ts = []
    out = None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts), out


def main():
    t0 = time.time()
    providers, members, claims, referrals, rings, ring_p, ring_m = generate()
    db = build_sqlite(providers, members, claims, referrals)

    tracemalloc.start()
    G, D = build_graph(providers, members, claims, referrals)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    bytes_per_elem = cur / (G.number_of_nodes() + G.number_of_edges())

    # Bipartite provider-member graph for traversal benchmark
    B = nx.Graph()
    for pid, mid in db.execute("SELECT provider_id, member_id FROM pm"):
        B.add_edge(pid, mid)

    # ---------------- benchmark: providers reachable within k "shared-member" hops
    sample_p = rng.sample([p["id"] for p in providers], 15)
    bench = []
    for k in (1, 2, 3, 4):
        sql = f"""
        WITH RECURSIVE reach(pid, depth) AS (
          SELECT ?, 0
          UNION
          SELECT pm2.provider_id, reach.depth + 1
          FROM reach
          JOIN pm pm1 ON pm1.provider_id = reach.pid
          JOIN pm pm2 ON pm2.member_id = pm1.member_id
          WHERE reach.depth < {k}
        )
        SELECT COUNT(DISTINCT pid) FROM reach"""
        sql_t, graph_t, sizes = [], [], []
        for pid in sample_p:
            ts, n_sql = timeit(lambda: db.execute(sql, (pid,)).fetchone()[0], 1)
            tg, n_g = timeit(lambda: sum(1 for n in nx.single_source_shortest_path_length(B, pid, cutoff=2 * k)
                                          if n.startswith("P")), 3)
            sql_t.append(ts)
            graph_t.append(tg)
            sizes.append(n_g)
            assert n_sql == n_g, (k, pid, n_sql, n_g)  # both engines must agree
        bench.append({"hops": k, "sqlMs": round(statistics.median(sql_t), 2),
                      "graphMs": round(statistics.median(graph_t), 2),
                      "reached": int(statistics.median(sizes))})

    # ---------------- fraud ring detection on provider projection
    shared = Counter()
    for mid in (n for n in B if n.startswith("M")):
        ps = sorted(B[mid])
        for a, b in combinations(ps, 2):
            shared[(a, b)] += 1
    weights = Counter(shared.values())
    P = nx.Graph()
    P.add_nodes_from(p["id"] for p in providers)
    for (a, b), w in shared.items():
        P.add_edge(a, b, weight=w)
    communities = nx.community.louvain_communities(P, weight="weight", seed=SEED)
    strong = nx.Graph()
    THRESH = 8
    strong.add_edges_from((a, b) for (a, b), w in shared.items() if w >= THRESH)
    cand = [c for c in nx.connected_components(strong) if len(c) >= 3]

    prov_amt = defaultdict(float)
    prov_cnt = Counter()
    for (_, mid, pid, code, dx, amt, mon, den) in claims:
        prov_amt[pid] += amt
        prov_cnt[pid] += 1
    avg_claim = {p: prov_amt[p] / prov_cnt[p] for p in prov_cnt}
    overall_avg = statistics.mean(avg_claim.values())
    sd = statistics.pstdev(avg_claim.values())
    member_by_id = {m["id"]: m for m in members}

    detected = []
    for c in cand:
        mem = set().union(*(set(B[p]) for p in c))
        core = [m for m in mem if sum(1 for p in c if B.has_edge(p, m)) >= 3]
        phones = Counter(member_by_id[m]["phone"] for m in core)
        shared_phone_ratio = sum(v for v in phones.values() if v > 1) / max(1, len(core))
        z = statistics.mean((avg_claim[p] - overall_avg) / sd for p in c)
        risk = min(100, round(35 * min(1, len(core) / 25) + 35 * shared_phone_ratio + 30 * min(1, max(0, z) / 3)))
        billed = sum(G[m][p]["amount"] for p in c for m in core if G.has_edge(m, p))
        detected.append({"providers": sorted(c), "coreMembers": len(core), "sharedPhoneRatio": round(shared_phone_ratio, 2),
                         "billingZ": round(z, 2), "risk": risk, "billedUSD": round(billed)})
    detected.sort(key=lambda d: -d["risk"])
    det_p = {p for d in detected for p in d["providers"]}
    tp = len(det_p & ring_p)
    precision = tp / max(1, len(det_p))
    recall = tp / len(ring_p)

    # SQL equivalent of the ring signal (pairwise self-join) - timed for comparison
    sql_pairs = """SELECT a.provider_id, b.provider_id, COUNT(*) AS shared
                   FROM pm a JOIN pm b ON a.member_id = b.member_id AND a.provider_id < b.provider_id
                   GROUP BY 1,2 HAVING shared >= 8"""
    t_sql_pairs, rows = timeit(lambda: db.execute(sql_pairs).fetchall(), 1)

    # ring subgraph for visualisation (top ring)
    top = detected[0]
    viz = nx.Graph()
    core_members = [m for m in set().union(*(set(B[p]) for p in top["providers"]))
                    if sum(1 for p in top["providers"] if B.has_edge(p, m)) >= 3][:24]
    for p in top["providers"]:
        viz.add_node(p, kind="Provider", label=f"{p} · {G.nodes[p]['specialty']}")
    for m in core_members:
        viz.add_node(m, kind="Member", label=m)
        for p in top["providers"]:
            if B.has_edge(p, m):
                viz.add_edge(m, p, rel="TREATED_BY")
        ph = member_by_id[m]["phone"]
        viz.add_node(ph, kind="Phone", label="Shared phone")
        viz.add_edge(m, ph, rel="HAS_PHONE")
    fixed = {p: (0.32 * math.cos(2 * math.pi * i / len(top["providers"]) - math.pi / 2),
                 0.32 * math.sin(2 * math.pi * i / len(top["providers"]) - math.pi / 2))
             for i, p in enumerate(top["providers"])}  # pentagon keeps provider labels apart
    pos = nx.spring_layout(viz, seed=SEED, k=0.35, iterations=200, pos=fixed, fixed=list(fixed))
    ring_viz = {
        "nodes": [{"id": n, "kind": d["kind"], "label": d["label"],
                   "x": round(float(pos[n][0]), 3), "y": round(float(pos[n][1]), 3)} for n, d in viz.nodes(data=True)],
        "edges": [{"s": a, "t": b, "rel": d["rel"]} for a, b, d in viz.edges(data=True)],
    }

    # ---------------- provider influence (referral network)
    pr = nx.pagerank(D, weight="weight")
    top_pr = sorted(pr.items(), key=lambda x: -x[1])[:10]
    influence = [{"provider": p, "specialty": G.nodes[p]["specialty"], "region": G.nodes[p]["region"],
                  "pagerank": round(v * 1000, 2), "inReferrals": int(D.in_degree(p, weight="weight"))} for p, v in top_pr]

    # ---------------- entity resolution: members linked via shared phone/address
    E = nx.Graph()
    for m in members:
        E.add_edge(m["id"], "ph:" + m["phone"])
        E.add_edge(m["id"], "ad:" + m["addr"])
    comp_sizes = Counter()
    suspicious_clusters = 0
    for comp in nx.connected_components(E):
        k = sum(1 for n in comp if n.startswith("M"))
        bucket = "1" if k == 1 else "2-4 (households)" if k <= 4 else "5-10" if k <= 10 else "11+"
        comp_sizes[bucket] += 1
        if k > 4:
            suspicious_clusters += 1
    entity = [{"bucket": b, "clusters": comp_sizes[b]} for b in ["1", "2-4 (households)", "5-10", "11+"]]

    # ---------------- comorbidity network (lift)
    n = len(members)
    single = Counter(c for m in members for c in m["conds"])
    pair = Counter(tuple(sorted(x)) for m in members for x in combinations(m["conds"], 2))
    como = []
    for (a, b), cnt in pair.items():
        if cnt < 40:
            continue
        lift = (cnt / n) / ((single[a] / n) * (single[b] / n))
        como.append({"a": a, "b": b, "members": cnt, "lift": round(lift, 2)})
    como.sort(key=lambda x: -x["lift"])

    # ---------------- relational-style analytics (what MySQL already does well)
    by_spec = db.execute("""SELECT p.specialty, COUNT(*), ROUND(SUM(c.amount)), ROUND(AVG(c.denied)*100,1)
                            FROM claim c JOIN provider p ON p.id=c.provider_id GROUP BY 1 ORDER BY 3 DESC""").fetchall()
    spend = [{"specialty": s, "claims": n_, "spendUSD": int(a), "denialPct": d} for s, n_, a, d in by_spec]
    trend = [{"month": m_, "claims": n_, "spendUSD": int(a)} for m_, n_, a in
             db.execute("SELECT month, COUNT(*), ROUND(SUM(amount)) FROM claim GROUP BY 1 ORDER BY 1").fetchall()]
    ring_claim_total = db.execute(f"""SELECT ROUND(SUM(amount)) FROM claim WHERE provider_id IN ({','.join('?'*len(ring_p))})
                                      AND member_id IN ({','.join('?'*len(ring_m))})""",
                                  list(ring_p) + list(ring_m)).fetchone()[0]

    # degree distribution of members (providers seen)
    deg = Counter(min(B.degree(m), 6) for m in B if m.startswith("M"))
    degree_dist = [{"providers": ("6+" if k == 6 else str(k)), "members": deg[k]} for k in sorted(deg)]

    # ---------------- GraphRAG context sizing
    probe = top["providers"][0]
    ego = nx.ego_graph(G, probe, radius=2)
    triples, by_rel = [], defaultdict(list)
    for a, b, d in ego.edges(data=True):
        if G.nodes[a]["label"] != "Member":  # every relationship starts at the Member
            a, b = b, a
        t = f"({a}:{G.nodes[a]['label']})-[{d['rel']}]->({b}:{G.nodes[b]['label']})"
        triples.append(t)
        by_rel[d["rel"]].append(t)
    sample_triples = [t for rel in ("TREATED_BY", "HAS_PHONE", "LIVES_AT", "DIAGNOSED_WITH") for t in sorted(by_rel[rel])[:2]]
    graph_tokens = sum(len(t) for t in triples) // 4
    # naive SQL-RAG: dump every claim row for those members as text
    mids = [x for x in ego if G.nodes[x]["label"] == "Member"]
    sql_rows = db.execute(f"SELECT * FROM claim WHERE member_id IN ({','.join('?'*len(mids))})", mids).fetchall()
    sql_tokens = sum(len(str(r)) for r in sql_rows) // 4
    # community summary (GraphRAG "global" mode) ~ fixed cost
    summary_tokens = 350

    nodes, edges = G.number_of_nodes(), G.number_of_edges()
    out = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M"),
        "runtimeSec": round(time.time() - t0, 1),
        "dataset": {
            "members": len(members), "providers": len(providers), "claims": len(claims),
            "diagnoses": len(CONDITIONS), "referralEdges": D.number_of_edges(),
            "graphNodes": nodes, "graphEdges": edges,
            "plantedRings": N_RINGS, "sqlTables": 5,
        },
        "benchmark": bench,
        "fraud": {
            "providerPairsScanned": len(shared), "threshold": THRESH, "candidateRings": len(detected),
            "precision": round(precision, 3), "recall": round(recall, 3),
            "louvainCommunities": len(communities),
            "sqlPairJoinMs": round(t_sql_pairs, 1), "sqlPairRows": len(rows),
            "ringExposureUSD": int(ring_claim_total),
            "rings": detected[:8],
            "sharedMemberHistogram": [{"shared": k, "pairs": weights[k]} for k in sorted(weights) if k <= 40],
        },
        "ringViz": ring_viz,
        "influence": influence,
        "entity": {"buckets": entity, "suspiciousClusters": suspicious_clusters},
        "comorbidity": como[:12],
        "spendBySpecialty": spend,
        "trend": trend,
        "degreeDist": degree_dist,
        "graphrag": {
            "probeProvider": probe, "egoNodes": ego.number_of_nodes(), "egoEdges": ego.number_of_edges(),
            "graphContextTokens": graph_tokens, "sqlDumpTokens": sql_tokens,
            "communitySummaryTokens": summary_tokens, "sampleTriples": sample_triples,
        },
        "memory": {
            "networkxMB": round(cur / 1e6, 1), "bytesPerElement": round(bytes_per_elem),
            "nodesPlusEdges": nodes + edges,
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps({k: out[k] for k in ["dataset", "benchmark", "memory"]}, indent=1))
    print("fraud:", {k: v for k, v in out["fraud"].items() if k not in ("rings", "sharedMemberHistogram")})
    print("graphrag:", {k: v for k, v in out["graphrag"].items() if k != "sampleTriples"})
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
