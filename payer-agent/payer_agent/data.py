"""
Simulated health-insurance PAYER data.

Generates one dataset and loads it twice:
  * relational  -> SQLite (stand-in for the legacy MySQL claims system)
  * graph       -> NetworkX DiGraph (the GraphRAG knowledge graph)

Planted patterns (ground truth the agent should be able to find):
  fraud_ring        5 colluding providers billing the same members with shared phones
  pa_abuser         provider that routinely skips prior authorization (CO-197 denials)
  doctor_shoppers   members filling opioids from many prescribers + pharmacies
  leaky_pcps        HMO primary-care providers whose members leak out of network (CO-242)
  identity_cluster  members on different plans sharing phone + addresses
"""
from __future__ import annotations

import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import networkx as nx

SEED = 7
N_MEMBERS = 6000
N_PROVIDERS = 450
N_PHARMACIES = 40
N_EMPLOYERS = 25
N_REGIONS = 10
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 10)]

PLANS = [  # id, name, product, line of business
    ("PL01", "Commercial HMO Silver", "HMO", "Commercial"),
    ("PL02", "Commercial PPO Gold", "PPO", "Commercial"),
    ("PL03", "Medicare Advantage HMO", "HMO", "Medicare"),
    ("PL04", "Medicare Advantage PPO", "PPO", "Medicare"),
    ("PL05", "Medicaid Managed Care", "HMO", "Medicaid"),
    ("PL06", "ACA Exchange Bronze", "EPO", "Exchange"),
]
CLOSED_NETWORK = {"HMO", "EPO"}  # out-of-network claims are denied

SPECIALTIES = [
    ("Primary Care", 0.34), ("Cardiology", 0.07), ("Orthopedics", 0.08), ("Radiology", 0.07),
    ("Physical Therapy", 0.08), ("Behavioral Health", 0.08), ("Sleep Medicine", 0.04),
    ("DME Supplier", 0.06), ("Laboratory", 0.06), ("Endocrinology", 0.05), ("Pain Management", 0.07),
]
PROCEDURES = {  # cpt: (name, base cost, specialties, PA required)
    "99213": ("Office visit, established", 110, ["Primary Care"], False),
    "99214": ("Office visit, moderate", 165, ["Primary Care", "Endocrinology", "Cardiology", "Pain Management"], False),
    "99204": ("New patient visit", 210, ["Cardiology", "Orthopedics", "Endocrinology", "Pain Management", "Sleep Medicine"], False),
    "72148": ("MRI lumbar spine", 1200, ["Radiology"], True),
    "73721": ("MRI knee", 1100, ["Radiology"], True),
    "27447": ("Total knee replacement", 18500, ["Orthopedics"], True),
    "29881": ("Knee arthroscopy", 5200, ["Orthopedics"], True),
    "95810": ("Sleep study", 2400, ["Sleep Medicine"], True),
    "E0601": ("CPAP device", 1100, ["DME Supplier"], True),
    "E0260": ("Hospital bed", 1600, ["DME Supplier"], True),
    "97110": ("Therapeutic exercise", 95, ["Physical Therapy"], False),
    "93306": ("Echocardiogram", 650, ["Cardiology"], False),
    "90837": ("Psychotherapy 60 min", 180, ["Behavioral Health"], False),
    "80053": ("Metabolic panel", 35, ["Laboratory"], False),
    "64483": ("Epidural steroid injection", 1450, ["Pain Management"], True),
}
# PPO Gold waives PA for imaging - plan-level benefit rules matter
PA_EXEMPT = {"PL02": {"72148", "73721"}}

DIAGNOSES = {
    "E11": ("Type 2 diabetes", 0.11), "I10": ("Hypertension", 0.24), "M54.5": ("Low back pain", 0.14),
    "M17": ("Knee osteoarthritis", 0.07), "G47.33": ("Obstructive sleep apnea", 0.06), "F32": ("Depression", 0.09),
    "J45": ("Asthma", 0.08), "E66": ("Obesity", 0.12), "I25": ("Coronary artery disease", 0.05),
}
SPEC_DX = {
    "Cardiology": ["I10", "I25"], "Orthopedics": ["M17", "M54.5"], "Radiology": ["M54.5", "M17"],
    "Physical Therapy": ["M54.5", "M17"], "Behavioral Health": ["F32"], "Sleep Medicine": ["G47.33"],
    "DME Supplier": ["G47.33", "M17"], "Endocrinology": ["E11", "E66"], "Pain Management": ["M54.5"],
}
DENIALS = {  # CARC code: (description, appeal overturn probability)
    "CO-197": ("Precertification/authorization absent", 0.35),
    "CO-242": ("Services not provided by network provider", 0.15),
    "CO-16": ("Claim lacks information needed for adjudication", 0.60),
    "CO-50": ("Not medically necessary", 0.30),
    "CO-97": ("Bundled into another service", 0.20),
    "CO-29": ("Timely filing limit expired", 0.05),
}
DRUGS = {  # id: (name, class, monthly cost, PA required, opioid, treats)
    "DRG01": ("Oxycodone", "Opioid analgesic", 45, False, True, "M54.5"),
    "DRG02": ("Hydrocodone", "Opioid analgesic", 30, False, True, "M54.5"),
    "DRG03": ("Tramadol", "Opioid analgesic", 20, False, True, "M17"),
    "DRG04": ("Metformin", "Antidiabetic", 10, False, False, "E11"),
    "DRG05": ("Semaglutide", "GLP-1 agonist", 950, True, False, "E66"),
    "DRG06": ("Insulin glargine", "Insulin", 320, False, False, "E11"),
    "DRG07": ("Lisinopril", "ACE inhibitor", 8, False, False, "I10"),
    "DRG08": ("Atorvastatin", "Statin", 12, False, False, "I25"),
    "DRG09": ("Sertraline", "SSRI antidepressant", 15, False, False, "F32"),
    "DRG10": ("Albuterol", "Bronchodilator", 55, False, False, "J45"),
    "DRG11": ("Adalimumab", "Biologic (specialty)", 6800, True, False, "M17"),
    "DRG12": ("Apixaban", "Anticoagulant", 520, False, False, "I25"),
}
PRESCRIBER_SPECS = {"Primary Care", "Pain Management", "Endocrinology", "Cardiology", "Behavioral Health", "Orthopedics"}


@dataclass
class Dataset:
    db: sqlite3.Connection
    G: nx.DiGraph
    truth: dict = field(default_factory=dict)


def _pick(rng, weighted):
    r, acc = rng.random(), 0.0
    for v, w in weighted:
        acc += w
        if r <= acc:
            return v
    return weighted[-1][0]


def generate(seed: int = SEED) -> Dataset:
    rng = random.Random(seed)

    # ---------------- providers + network contracts
    providers = []
    for i in range(1, N_PROVIDERS + 1):
        spec = _pick(rng, SPECIALTIES)
        nets = {p[0] for p in PLANS if rng.random() < 0.72}
        providers.append({"id": f"P{i:04d}", "name": f"Dr. Provider {i:04d}" if spec != "DME Supplier" else f"DME Supply Co {i:04d}",
                          "specialty": spec, "region": rng.randrange(N_REGIONS), "networks": nets})
    by_region_spec = defaultdict(list)
    for p in providers:
        by_region_spec[(p["region"], p["specialty"])].append(p)
    prov = {p["id"]: p for p in providers}

    pcps = [p for p in providers if p["specialty"] == "Primary Care"]
    leaky_pcps = rng.sample(pcps, 6)
    leaky_ids = {p["id"] for p in leaky_pcps}
    pa_abuser = rng.choice([p for p in providers if p["specialty"] == "Radiology"])

    pharmacies = [{"id": f"RXP{i:02d}", "name": f"Pharmacy #{i:02d}", "region": i % N_REGIONS} for i in range(1, N_PHARMACIES + 1)]
    pharm_by_region = defaultdict(list)
    for ph in pharmacies:
        pharm_by_region[ph["region"]].append(ph)

    employers = [{"id": f"EMP{i:02d}", "name": f"Employer Group {i:02d}"} for i in range(1, N_EMPLOYERS + 1)]

    # ---------------- members
    members = []
    plan_weights = [("PL01", 0.22), ("PL02", 0.20), ("PL03", 0.15), ("PL04", 0.10), ("PL05", 0.23), ("PL06", 0.10)]
    for i in range(1, N_MEMBERS + 1):
        plan = _pick(rng, plan_weights)
        lob = next(p[3] for p in PLANS if p[0] == plan)
        age = rng.randint(65, 92) if lob == "Medicare" else rng.randint(0, 64) if lob == "Medicaid" else rng.randint(19, 64)
        conds = [c for c, (_, p) in DIAGNOSES.items() if rng.random() < p * (1.6 if age > 60 else 1)]
        region = rng.randrange(N_REGIONS)
        product = next(p[2] for p in PLANS if p[0] == plan)
        local_pcps = [p for p in by_region_spec[(region, "Primary Care")] if plan in p["networks"]] or pcps
        members.append({
            "id": f"M{i:05d}", "plan": plan, "age": age, "region": region, "conds": conds,
            "employer": rng.choice(employers)["id"] if lob == "Commercial" else None,
            "pcp": rng.choice(local_pcps)["id"] if product in CLOSED_NETWORK else None,
            "phone": f"TEL-{i:05d}", "address": f"ADDR-{i:05d}",
        })
    # legitimate households share an address
    i = 0
    while i < N_MEMBERS - 4:
        if rng.random() < 0.2:
            k = rng.randint(2, 4)
            for j in range(1, k):
                members[i + j]["address"] = members[i]["address"]
                members[i + j]["region"] = members[i]["region"]
            i += k
        else:
            i += 1
    # give some members of leaky PCPs to guarantee signal
    hmo_members = [m for m in members if m["pcp"]]
    for m in rng.sample(hmo_members, 120):
        m["pcp"] = rng.choice(leaky_pcps)["id"]
    mem = {m["id"]: m for m in members}

    # synthetic identity cluster: 8 members on different plans sharing 1 phone, 2 addresses
    identity = rng.sample([m for m in members if m["pcp"] is None or True], 8)
    for k, m in enumerate(identity):
        m["phone"] = "TEL-SHARED-01"
        m["address"] = f"ADDR-SHARED-0{k % 2 + 1}"
        m["plan"] = PLANS[k % len(PLANS)][0]

    # fraud ring: 5 providers in one region, 25 members, 3 shared phones
    ring_region = rng.randrange(N_REGIONS)
    ring_specs = ["DME Supplier", "DME Supplier", "Physical Therapy", "Physical Therapy", "Sleep Medicine"]
    ring = []
    for s in ring_specs:
        cands = [p for p in providers if p["specialty"] == s and p["id"] not in {r["id"] for r in ring} and p["id"] != pa_abuser["id"]]
        local = [p for p in cands if p["region"] == ring_region] or cands
        pick = rng.choice(local)
        pick["region"] = ring_region
        pick["networks"] = {p[0] for p in PLANS}
        ring.append(pick)
    ring_members = rng.sample([m for m in members if m not in identity], 25)
    for m in ring_members:
        m["phone"] = f"TEL-RING-{rng.randint(1, 3)}"

    shoppers = rng.sample([m for m in members if m not in identity and m not in ring_members], 12)
    for m in shoppers:
        if "M54.5" not in m["conds"]:
            m["conds"].append("M54.5")

    # ---------------- claims, prior auths, appeals
    claims, pas, appeals = [], [], []
    referrals = Counter()
    cid = paid_ = 0

    def add_claim(m, p, cpt, month, billed_mult=1.0, force_pa=None):
        nonlocal cid
        cid += 1
        claim_id = f"CLM{cid:06d}"
        name, base, _, pa_req = PROCEDURES[cpt]
        pa_req = pa_req and cpt not in PA_EXEMPT.get(m["plan"], set())
        spec_dx = [d for d in SPEC_DX.get(p["specialty"], []) if d in m["conds"]] or SPEC_DX.get(p["specialty"], [])
        dx = rng.choice(spec_dx or m["conds"] or ["I10"])
        billed = round(max(20, rng.gauss(base, base * 0.2)) * billed_mult, 2)
        product = next(x[2] for x in PLANS if x[0] == m["plan"])
        in_net = m["plan"] in p["networks"]
        pa_id, status, denial = None, "PAID", None
        if pa_req:
            req_prob = 0.12 if p["id"] == pa_abuser["id"] else 0.9
            if force_pa is not None:
                req_prob = force_pa
            if rng.random() < req_prob:
                pa_id = f"PA{len(pas) + 1:05d}"
                approved = rng.random() < (0.97 if force_pa else 0.84)
                pas.append((pa_id, m["id"], p["id"], cpt, "APPROVED" if approved else "DENIED", month))
                if not approved:
                    status, denial = "DENIED", "CO-50"
            else:
                status, denial = "DENIED", "CO-197"
        if status == "PAID" and not in_net and product in CLOSED_NETWORK and rng.random() < 0.9:
            status, denial = "DENIED", "CO-242"
        if status == "PAID":
            r = rng.random()
            for code, pr in (("CO-16", 0.04), ("CO-97", 0.02), ("CO-50", 0.015), ("CO-29", 0.01)):
                if r < pr:
                    status, denial = "DENIED", code
                    break
                r -= pr
        allowed = round(billed * rng.uniform(0.45, 0.75), 2)
        paid = round(allowed * 0.85, 2) if status == "PAID" else 0.0
        claims.append((claim_id, m["id"], p["id"], cpt, dx, month, billed, allowed, paid, status, denial, pa_id, int(in_net)))
        if status == "DENIED" and rng.random() < 0.15:
            overturned = rng.random() < DENIALS[denial][1]
            appeals.append((f"AP{len(appeals) + 1:04d}", claim_id, "OVERTURNED" if overturned else "UPHELD"))
        if m["pcp"] and p["id"] != m["pcp"]:
            referrals[(m["pcp"], p["id"])] += 1
        return claim_id

    specialists = [s for s, _ in SPECIALTIES if s != "Primary Care"]
    for m in members:
        product = next(x[2] for x in PLANS if x[0] == m["plan"])
        for _ in range(rng.choices([0, 1, 2, 3, 4, 6, 8], [6, 14, 20, 20, 18, 14, 8])[0]):
            month = rng.choice(MONTHS)
            if rng.random() < 0.45:
                p = prov[m["pcp"]] if m["pcp"] else rng.choice(by_region_spec[(m["region"], "Primary Care")] or pcps)
            else:
                rel = [SPEC for d in m["conds"] for SPEC, dl in SPEC_DX.items() if d in dl] or specialists
                spec = rng.choice(rel)
                pool = by_region_spec[(m["region"], spec)] or [p for p in providers if p["specialty"] == spec]
                if product in CLOSED_NETWORK:
                    leak = 0.45 if m["pcp"] in leaky_ids else 0.06
                    innet = [p for p in pool if m["plan"] in p["networks"]]
                    outnet = [p for p in pool if m["plan"] not in p["networks"]]
                    pool = outnet if (rng.random() < leak and outnet) else (innet or pool)
                p = rng.choice(pool)
            cpt = rng.choice([c for c, v in PROCEDURES.items() if p["specialty"] in v[2]] or ["99213"])
            add_claim(m, p, cpt, month)
    # pa abuser gets extra imaging volume
    for m in rng.sample(members, 140):
        add_claim(m, pa_abuser, rng.choice(["72148", "73721"]), rng.choice(MONTHS))
    # fraud ring billing: every ring member billed by every ring provider, PAs approved (sophisticated)
    ring_codes = {"DME Supplier": ["E0601", "E0260"], "Physical Therapy": ["97110"], "Sleep Medicine": ["95810"]}
    for m in ring_members:
        for p in ring:
            for _ in range(rng.randint(2, 3)):
                add_claim(m, p, rng.choice(ring_codes[p["specialty"]]), rng.choice(MONTHS), billed_mult=1.55, force_pa=1.0)

    # ---------------- pharmacy claims
    rx = []
    presc_pool = [p for p in providers if p["specialty"] in PRESCRIBER_SPECS]
    for m in members:
        drugs = [d for d, v in DRUGS.items() if v[5] in m["conds"] and rng.random() < 0.65]
        if not drugs:
            continue
        home = rng.choice(pharm_by_region[m["region"]])
        prescriber = prov[m["pcp"]] if m["pcp"] else rng.choice([p for p in presc_pool if p["region"] == m["region"]] or presc_pool)
        for d in drugs:
            for _ in range(rng.randint(1, 5)):
                ph = home if rng.random() < 0.88 else rng.choice(pharmacies)
                rx.append((f"RX{len(rx) + 1:06d}", m["id"], d, prescriber["id"], ph["id"], rng.choice(MONTHS), DRUGS[d][2]))
    for m in shoppers:
        docs = rng.sample(presc_pool, rng.randint(3, 5))
        phs = rng.sample(pharmacies, rng.randint(3, 5))
        for _ in range(rng.randint(7, 11)):
            d = rng.choice(["DRG01", "DRG02", "DRG03"])
            rx.append((f"RX{len(rx) + 1:06d}", m["id"], d, rng.choice(docs)["id"], rng.choice(phs)["id"], rng.choice(MONTHS), DRUGS[d][2]))

    truth = {
        "fraud_ring": sorted(p["id"] for p in ring),
        "fraud_ring_members": sorted(m["id"] for m in ring_members),
        "pa_abuser": pa_abuser["id"],
        "doctor_shoppers": sorted(m["id"] for m in shoppers),
        "leaky_pcps": sorted(leaky_ids),
        "identity_cluster": sorted(m["id"] for m in identity),
    }
    db = _to_sqlite(providers, members, pharmacies, employers, claims, pas, appeals, rx, referrals)
    G = _to_graph(providers, members, pharmacies, employers, claims, pas, appeals, rx, referrals)
    return Dataset(db=db, G=G, truth=truth)


def _to_sqlite(providers, members, pharmacies, employers, claims, pas, appeals, rx, referrals) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("""
    CREATE TABLE plan(id TEXT PRIMARY KEY, name TEXT, product TEXT, lob TEXT);
    CREATE TABLE employer(id TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE member(id TEXT PRIMARY KEY, plan_id TEXT, employer_id TEXT, pcp_id TEXT, age INT, region INT, phone TEXT, address TEXT);
    CREATE TABLE member_condition(member_id TEXT, icd TEXT);
    CREATE TABLE provider(id TEXT PRIMARY KEY, name TEXT, specialty TEXT, region INT);
    CREATE TABLE provider_network(provider_id TEXT, plan_id TEXT);
    CREATE TABLE procedure(cpt TEXT PRIMARY KEY, name TEXT, base_cost REAL, pa_required INT);
    CREATE TABLE diagnosis(icd TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE denial_reason(code TEXT PRIMARY KEY, description TEXT);
    CREATE TABLE prior_auth(id TEXT PRIMARY KEY, member_id TEXT, provider_id TEXT, cpt TEXT, status TEXT, month TEXT);
    CREATE TABLE claim(id TEXT PRIMARY KEY, member_id TEXT, provider_id TEXT, cpt TEXT, icd TEXT, month TEXT,
                       billed REAL, allowed REAL, paid REAL, status TEXT, denial_code TEXT, pa_id TEXT, in_network INT);
    CREATE TABLE appeal(id TEXT PRIMARY KEY, claim_id TEXT, outcome TEXT);
    CREATE TABLE drug(id TEXT PRIMARY KEY, name TEXT, drug_class TEXT, cost REAL, pa_required INT, opioid INT);
    CREATE TABLE pharmacy(id TEXT PRIMARY KEY, name TEXT, region INT);
    CREATE TABLE rx_claim(id TEXT PRIMARY KEY, member_id TEXT, drug_id TEXT, prescriber_id TEXT, pharmacy_id TEXT, month TEXT, cost REAL);
    CREATE TABLE referral(src TEXT, dst TEXT, cnt INT);
    """)
    db.executemany("INSERT INTO plan VALUES(?,?,?,?)", PLANS)
    db.executemany("INSERT INTO employer VALUES(?,?)", [(e["id"], e["name"]) for e in employers])
    db.executemany("INSERT INTO member VALUES(?,?,?,?,?,?,?,?)",
                   [(m["id"], m["plan"], m["employer"], m["pcp"], m["age"], m["region"], m["phone"], m["address"]) for m in members])
    db.executemany("INSERT INTO member_condition VALUES(?,?)", [(m["id"], c) for m in members for c in m["conds"]])
    db.executemany("INSERT INTO provider VALUES(?,?,?,?)", [(p["id"], p["name"], p["specialty"], p["region"]) for p in providers])
    db.executemany("INSERT INTO provider_network VALUES(?,?)", [(p["id"], n) for p in providers for n in sorted(p["networks"])])
    db.executemany("INSERT INTO procedure VALUES(?,?,?,?)", [(c, v[0], v[1], int(v[3])) for c, v in PROCEDURES.items()])
    db.executemany("INSERT INTO diagnosis VALUES(?,?)", [(c, v[0]) for c, v in DIAGNOSES.items()])
    db.executemany("INSERT INTO denial_reason VALUES(?,?)", [(c, v[0]) for c, v in DENIALS.items()])
    db.executemany("INSERT INTO prior_auth VALUES(?,?,?,?,?,?)", pas)
    db.executemany("INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", claims)
    db.executemany("INSERT INTO appeal VALUES(?,?,?)", appeals)
    db.executemany("INSERT INTO drug VALUES(?,?,?,?,?,?)", [(d, v[0], v[1], v[2], int(v[3]), int(v[4])) for d, v in DRUGS.items()])
    db.executemany("INSERT INTO pharmacy VALUES(?,?,?)", [(p["id"], p["name"], p["region"]) for p in pharmacies])
    db.executemany("INSERT INTO rx_claim VALUES(?,?,?,?,?,?,?)", rx)
    db.executemany("INSERT INTO referral VALUES(?,?,?)", [(a, b, n) for (a, b), n in referrals.items()])
    db.executescript("""
    CREATE INDEX ix_claim_member ON claim(member_id);
    CREATE INDEX ix_claim_provider ON claim(provider_id);
    CREATE INDEX ix_rx_member ON rx_claim(member_id);
    CREATE INDEX ix_net ON provider_network(provider_id, plan_id);
    CREATE INDEX ix_member_phone ON member(phone);
    CREATE INDEX ix_member_addr ON member(address);
    CREATE INDEX ix_ref ON referral(src, dst);
    """)
    db.commit()
    return db


def _to_graph(providers, members, pharmacies, employers, claims, pas, appeals, rx, referrals) -> nx.DiGraph:
    G = nx.DiGraph()
    add = G.add_node
    for pid, name, product, lob in PLANS:
        add(pid, type="Plan", name=name, product=product, lob=lob)
    for e in employers:
        add(e["id"], type="Employer", name=e["name"])
    for c, v in PROCEDURES.items():
        add(f"CPT:{c}", type="Procedure", name=v[0], code=c, pa_required=v[3])
    for c, v in DIAGNOSES.items():
        add(f"ICD:{c}", type="Diagnosis", name=v[0], code=c)
    for c, v in DENIALS.items():
        add(f"CARC:{c}", type="DenialReason", name=v[0], code=c)
    for d, v in DRUGS.items():
        add(d, type="Drug", name=v[0], drug_class=v[1], opioid=v[4], pa_required=v[3])
    for ph in pharmacies:
        add(ph["id"], type="Pharmacy", name=ph["name"], region=ph["region"])
    for p in providers:
        add(p["id"], type="Provider", name=p["name"], specialty=p["specialty"], region=p["region"])
        for n in p["networks"]:
            G.add_edge(p["id"], n, rel="IN_NETWORK")
    for m in members:
        add(m["id"], type="Member", name=m["id"], age=m["age"], region=m["region"])
        G.add_edge(m["id"], m["plan"], rel="ENROLLED_IN")
        if m["employer"]:
            G.add_edge(m["id"], m["employer"], rel="EMPLOYED_BY")
        if m["pcp"]:
            G.add_edge(m["id"], m["pcp"], rel="HAS_PCP")
        for key, typ, rel in (("phone", "Phone", "HAS_PHONE"), ("address", "Address", "LIVES_AT")):
            if m[key] not in G:
                add(m[key], type=typ, name=m[key])
            G.add_edge(m["id"], m[key], rel=rel)
        for c in m["conds"]:
            G.add_edge(m["id"], f"ICD:{c}", rel="HAS_CONDITION")
    for pa_id, mid, pid, cpt, status, month in pas:
        add(pa_id, type="PriorAuth", name=pa_id, status=status, month=month)
        G.add_edge(pid, pa_id, rel="REQUESTED")
        G.add_edge(pa_id, mid, rel="FOR_MEMBER")
        G.add_edge(pa_id, f"CPT:{cpt}", rel="FOR_PROCEDURE")
    for (cid, mid, pid, cpt, icd, month, billed, allowed, paid, status, denial, pa_id, in_net) in claims:
        add(cid, type="Claim", name=cid, month=month, billed=billed, allowed=allowed, paid=paid, status=status, in_network=bool(in_net))
        G.add_edge(mid, cid, rel="FILED")
        G.add_edge(cid, pid, rel="BILLED_BY")
        G.add_edge(cid, f"CPT:{cpt}", rel="FOR_PROCEDURE")
        G.add_edge(cid, f"ICD:{icd}", rel="HAS_DIAGNOSIS")
        if denial:
            G.add_edge(cid, f"CARC:{denial}", rel="DENIED_FOR")
        if pa_id:
            G.add_edge(cid, pa_id, rel="AUTHORIZED_BY")
    for aid, cid, outcome in appeals:
        add(aid, type="Appeal", name=aid, outcome=outcome)
        G.add_edge(cid, aid, rel="APPEALED")
    for rid, mid, d, presc, ph, month, cost in rx:
        add(rid, type="RxClaim", name=rid, month=month, cost=cost)
        G.add_edge(mid, rid, rel="FILLED")
        G.add_edge(rid, d, rel="OF_DRUG")
        G.add_edge(rid, presc, rel="PRESCRIBED_BY")
        G.add_edge(rid, ph, rel="DISPENSED_AT")
    for (a, b), n in referrals.items():
        G.add_edge(a, b, rel="REFERRED_TO", weight=n)
    return G
