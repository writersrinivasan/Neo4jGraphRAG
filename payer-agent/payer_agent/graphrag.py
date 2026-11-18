"""
GraphRAG over the payer knowledge graph (NetworkX).

Indexing  : Louvain communities on a member-provider projection -> statistical community reports
Local     : entity linking -> typed neighbourhood context (facts + triples) for the LLM
Global    : rank community reports against the question (GraphRAG "global search")
Detectors : graph-native analytics the agent can call as tools. They never read ground truth.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx

from .data import Dataset

HUB_TYPES = {"Plan", "Procedure", "Diagnosis", "DenialReason", "Drug", "Employer", "Pharmacy"}


def _n(G, x, k, d=None):
    return G.nodes[x].get(k, d)


def out_by_rel(G, node, rel):
    return [v for v in G.successors(node) if G[node][v]["rel"] == rel]


def in_by_rel(G, node, rel):
    return [u for u in G.predecessors(node) if G[u][node]["rel"] == rel]


def claim_facts(G, c):
    """Flatten a claim node into a dict (provider, cpt, denial, ...)."""
    f = {"id": c, **{k: G.nodes[c][k] for k in ("month", "billed", "paid", "status")}}
    for v in G.successors(c):
        rel = G[c][v]["rel"]
        if rel == "BILLED_BY":
            f["provider"] = v
        elif rel == "FOR_PROCEDURE":
            f["cpt"] = v
        elif rel == "HAS_DIAGNOSIS":
            f["icd"] = v
        elif rel == "DENIED_FOR":
            f["denial"] = v
        elif rel == "AUTHORIZED_BY":
            f["pa"] = v
        elif rel == "APPEALED":
            f["appeal"] = G.nodes[v]["outcome"]
    f["member"] = next(iter(G.predecessors(c)))
    return f


def rx_facts(G, r):
    f = {"id": r, "month": G.nodes[r]["month"], "member": next(iter(G.predecessors(r)))}
    for v in G.successors(r):
        f[{"OF_DRUG": "drug", "PRESCRIBED_BY": "prescriber", "DISPENSED_AT": "pharmacy"}[G[r][v]["rel"]]] = v
    return f


# ======================================================================= detectors
class Detectors:
    def __init__(self, G: nx.DiGraph):
        self.G = G
        self.claims = {c: claim_facts(G, c) for c, t in G.nodes(data="type") if t == "Claim"}
        self.rx = [rx_facts(G, r) for r, t in G.nodes(data="type") if t == "RxClaim"]
        self.member_providers = defaultdict(Counter)
        for f in self.claims.values():
            self.member_providers[f["member"]][f["provider"]] += 1

    def fraud_rings(self, min_shared: int = 8, min_overlap: float = 0.5):
        """Provider pairs whose patient panels overlap far more than chance.
        overlap = shared members / smaller panel size (big PCP pairs share many members but a small fraction)."""
        shared = Counter()
        panel = Counter()
        for m, provs in self.member_providers.items():
            ps = sorted(provs)
            for p in ps:
                panel[p] += 1
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    shared[(ps[i], ps[j])] += 1
        S = nx.Graph()
        S.add_edges_from((a, b, {"shared": w}) for (a, b), w in shared.items()
                         if w >= min_shared and w / min(panel[a], panel[b]) >= min_overlap)
        threshold = f"shared>={min_shared} and overlap>={min_overlap}"
        rings = []
        for comp in nx.connected_components(S):
            if len(comp) < 3:
                continue
            core = [m for m, provs in self.member_providers.items() if sum(p in comp for p in provs) >= 3]
            phones = Counter(out_by_rel(self.G, m, "HAS_PHONE")[0] for m in core)
            shared_phone = sum(v for v in phones.values() if v > 1) / max(1, len(core))
            billed = sum(f["billed"] for f in self.claims.values() if f["provider"] in comp and f["member"] in core)
            rings.append({"providers": sorted(comp), "specialties": sorted({_n(self.G, p, "specialty") for p in comp}),
                          "core_members": sorted(core), "shared_phone_ratio": round(shared_phone, 2), "billed": round(billed)})
        rings.sort(key=lambda r: -r["billed"])
        return {"pairs_scanned": len(shared), "threshold": threshold, "rings": rings}

    def doctor_shopping(self, min_prescribers: int = 3, min_pharmacies: int = 3):
        agg = defaultdict(lambda: {"fills": 0, "prescribers": set(), "pharmacies": set(), "drugs": set()})
        for f in self.rx:
            if _n(self.G, f["drug"], "opioid"):
                a = agg[f["member"]]
                a["fills"] += 1
                a["prescribers"].add(f["prescriber"])
                a["pharmacies"].add(f["pharmacy"])
                a["drugs"].add(_n(self.G, f["drug"], "name"))
        hits = [{"member": m, "opioid_fills": a["fills"], "prescribers": sorted(a["prescribers"]),
                 "pharmacies": sorted(a["pharmacies"]), "drugs": sorted(a["drugs"])}
                for m, a in agg.items() if len(a["prescribers"]) >= min_prescribers and len(a["pharmacies"]) >= min_pharmacies]
        hits.sort(key=lambda h: -(len(h["prescribers"]) + len(h["pharmacies"])))
        return {"opioid_members": len(agg), "flagged": hits}

    def pa_gaps(self, min_claims: int = 20):
        stats = defaultdict(lambda: Counter())
        for f in self.claims.values():
            if _n(self.G, f["cpt"], "pa_required"):
                s = stats[f["provider"]]
                s["pa_claims"] += 1
                s["co197"] += f.get("denial") == "CARC:CO-197"
                s["denied_amount"] += f["billed"] if f.get("denial") == "CARC:CO-197" else 0
        rows = [{"provider": p, "specialty": _n(self.G, p, "specialty"), "pa_required_claims": s["pa_claims"],
                 "co197_denials": s["co197"], "co197_rate": round(s["co197"] / s["pa_claims"], 3), "denied_billed": round(s["denied_amount"])}
                for p, s in stats.items() if s["pa_claims"] >= min_claims]
        rows.sort(key=lambda r: -r["co197_rate"])
        base = sum(r["co197_denials"] for r in rows) / max(1, sum(r["pa_required_claims"] for r in rows))
        return {"network_co197_rate": round(base, 3), "providers": rows[:10]}

    def referral_leakage(self, min_claims: int = 20):
        G = self.G
        stats = defaultdict(Counter)
        for f in self.claims.values():
            m = f["member"]
            pcp = out_by_rel(G, m, "HAS_PCP")
            if not pcp or f["provider"] == pcp[0]:
                continue
            s = stats[pcp[0]]
            s["specialist_claims"] += 1
            s["oon"] += not G.nodes[f["id"]]["in_network"]
            s["co242"] += f.get("denial") == "CARC:CO-242"
        rows = [{"pcp": p, "specialist_claims": s["specialist_claims"], "out_of_network": s["oon"],
                 "leakage_rate": round(s["oon"] / s["specialist_claims"], 3), "co242_denials": s["co242"]}
                for p, s in stats.items() if s["specialist_claims"] >= min_claims]
        rows.sort(key=lambda r: -r["leakage_rate"])
        base = sum(r["out_of_network"] for r in rows) / max(1, sum(r["specialist_claims"] for r in rows))
        return {"network_leakage_rate": round(base, 3), "pcps": rows[:10]}

    def identity_clusters(self, min_members: int = 5):
        G = self.G
        H = nx.Graph()
        for m, t in G.nodes(data="type"):
            if t == "Member":
                for x in out_by_rel(G, m, "HAS_PHONE") + out_by_rel(G, m, "LIVES_AT"):
                    H.add_edge(m, x)
        out = []
        for comp in nx.connected_components(H):
            ms = sorted(n for n in comp if G.nodes[n]["type"] == "Member")
            if len(ms) >= min_members:
                plans = sorted({out_by_rel(G, m, "ENROLLED_IN")[0] for m in ms})
                out.append({"members": ms, "identifiers": sorted(n for n in comp if G.nodes[n]["type"] != "Member"), "plans": plans})
        out.sort(key=lambda c: -len(c["plans"]))
        return {"clusters": out}

    def provider_influence(self, top: int = 10):
        R = nx.DiGraph()
        R.add_edges_from((u, v, d) for u, v, d in self.G.edges(data=True) if d["rel"] == "REFERRED_TO")
        pr = nx.pagerank(R, weight="weight")
        rows = sorted(pr.items(), key=lambda x: -x[1])[:top]
        return {"providers": [{"provider": p, "specialty": _n(self.G, p, "specialty"), "pagerank": round(v, 5),
                               "referrals_in": int(R.in_degree(p, weight="weight"))} for p, v in rows]}

    def path_between(self, a: str, b: str):
        G = self.G
        U = nx.subgraph_view(G, filter_node=lambda n: G.nodes[n]["type"] not in HUB_TYPES).to_undirected(as_view=True)
        try:
            path = nx.shortest_path(U, a, b)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"path": None}
        hops = []
        for x, y in zip(path, path[1:]):
            rel = G[x][y]["rel"] if G.has_edge(x, y) else G[y][x]["rel"]
            hops.append(f"({x}:{G.nodes[x]['type']})-[{rel}]-({y}:{G.nodes[y]['type']})")
        return {"path": path, "hops": hops}

    def denial_summary(self):
        by = defaultdict(Counter)
        for f in self.claims.values():
            plan = out_by_rel(self.G, f["member"], "ENROLLED_IN")[0]
            by[plan]["claims"] += 1
            if f["status"] == "DENIED":
                by[plan]["denied"] += 1
                by[plan][f["denial"]] += 1
        rows = []
        for plan, c in sorted(by.items()):
            reasons = {k.split(":")[1]: v for k, v in c.items() if k.startswith("CARC:")}
            rows.append({"plan": plan, "name": _n(self.G, plan, "name"), "claims": c["claims"],
                         "denial_rate": round(c["denied"] / c["claims"], 3),
                         "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:3])})
        return {"plans": rows}


# ======================================================================= GraphRAG index
@dataclass
class Community:
    id: int
    members: list
    providers: list
    report: str
    metrics: dict


class GraphRAG:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.G = ds.G
        self.det = Detectors(self.G)
        self._flags = self._detector_flags()
        self.communities = self._build_communities()
        self.entity_names = self._entity_index()

    # ---- indexing
    def _detector_flags(self):
        fr = self.det.fraud_rings()
        flags = defaultdict(list)
        for r in fr["rings"]:
            for p in r["providers"]:
                flags[p].append("fraud-ring provider")
            for m in r["core_members"]:
                flags[m].append("fraud-ring member")
        for h in self.det.doctor_shopping()["flagged"]:
            flags[h["member"]].append("opioid doctor-shopping pattern")
        pa = self.det.pa_gaps()
        for r in pa["providers"]:
            if r["co197_rate"] > 3 * pa["network_co197_rate"]:
                flags[r["provider"]].append(f"prior-auth gap (CO-197 rate {r['co197_rate']:.0%})")
        lk = self.det.referral_leakage()
        for r in lk["pcps"]:
            if r["leakage_rate"] > 2.5 * lk["network_leakage_rate"]:
                flags[r["pcp"]].append(f"out-of-network leakage ({r['leakage_rate']:.0%})")
        for c in self.det.identity_clusters()["clusters"]:
            if len(c["plans"]) >= 3:
                for m in c["members"]:
                    flags[m].append("synthetic-identity cluster")
        return flags

    def _build_communities(self):
        G, det = self.G, self.det
        H = nx.Graph()
        for m, provs in det.member_providers.items():
            for p, n in provs.items():
                H.add_edge(m, p, weight=n)
        parts = nx.community.louvain_communities(H, weight="weight", seed=7, resolution=1.0)
        comms = []
        claims_by_member = defaultdict(list)
        for f in det.claims.values():
            claims_by_member[f["member"]].append(f)
        rx_by_member = defaultdict(list)
        for f in det.rx:
            rx_by_member[f["member"]].append(f)
        for i, part in enumerate(sorted(parts, key=len, reverse=True)):
            ms = [n for n in part if G.nodes[n]["type"] == "Member"]
            ps = [n for n in part if G.nodes[n]["type"] == "Provider"]
            cl = [f for m in ms for f in claims_by_member[m]]
            if not cl:
                continue
            denied = [f for f in cl if f["status"] == "DENIED"]
            reasons = Counter(f["denial"].split(":")[1] for f in denied)
            specs = Counter(_n(G, p, "specialty") for p in ps)
            regions = Counter(_n(G, p, "region") for p in ps)
            rx = [f for m in ms for f in rx_by_member[m]]
            opioid = sum(1 for f in rx if _n(G, f["drug"], "opioid"))
            oon = sum(1 for f in cl if not G.nodes[f["id"]]["in_network"])
            plans = Counter(out_by_rel(G, m, "ENROLLED_IN")[0] for m in ms)
            flagged = {n: self._flags[n] for n in ms + ps if n in self._flags}
            top_prov = sorted(ps, key=lambda p: -sum(f["billed"] for f in cl if f["provider"] == p))[:5]
            metrics = {
                "members": len(ms), "providers": len(ps), "claims": len(cl),
                "billed": round(sum(f["billed"] for f in cl)), "paid": round(sum(f["paid"] for f in cl)),
                "denial_rate": round(len(denied) / len(cl), 3), "co197": reasons.get("CO-197", 0),
                "oon_rate": round(oon / len(cl), 3), "opioid_share": round(opioid / max(1, len(rx)), 3),
                "risk_flags": sum(len(v) for v in flagged.values()),
            }
            flag_lines = [f"  - {n} ({G.nodes[n]['type']}): {', '.join(sorted(set(v)))}" for n, v in list(flagged.items())[:12]]
            report = "\n".join([
                f"COMMUNITY C{i}: {len(ms)} members, {len(ps)} providers, {len(cl)} claims (mainly region {regions.most_common(1)[0][0] if regions else '?'})",
                f"Specialties: {', '.join(f'{s} ({n})' for s, n in specs.most_common(4))}",
                f"Plans: {', '.join(f'{_n(G, p, 'name')} ({n})' for p, n in plans.most_common(3))}",
                f"Billed ${metrics['billed']:,} | paid ${metrics['paid']:,} | denial rate {metrics['denial_rate']:.1%} "
                f"| top denials {dict(reasons.most_common(3))} | out-of-network {metrics['oon_rate']:.1%} | opioid share of Rx {metrics['opioid_share']:.1%}",
                f"Highest-billing providers: {', '.join(f'{p} ({_n(G, p, 'specialty')})' for p in top_prov)}",
                "Risk flags from graph detectors:" if flag_lines else "Risk flags: none",
                *flag_lines,
            ])
            comms.append(Community(i, ms, ps, report, metrics))
        return comms

    def _entity_index(self):
        names = {}
        for n, d in self.G.nodes(data=True):
            if d["type"] in ("Plan", "Drug", "Procedure", "Diagnosis", "DenialReason"):
                names[d["name"].lower()] = n
                if "code" in d:
                    names[d["code"].lower()] = n
        names.update({"medicare": "PL03", "medicaid": "PL05", "ppo gold": "PL02", "hmo silver": "PL01", "exchange": "PL06", "bronze": "PL06"})
        return names

    # ---- entity linking
    ID_RE = re.compile(r"\b(M\d{5}|P\d{4}|CLM\d{6}|PA\d{5}|RX\d{6}|AP\d{4}|PL\d{2}|EMP\d{2}|RXP\d{2}|DRG\d{2}|CO-\d{2,3}|TEL-[A-Z0-9-]+|ADDR-[A-Z0-9-]+)\b", re.I)

    def link_entities(self, text: str):
        found = []
        for m in self.ID_RE.findall(text):
            key = m.upper()
            key = f"CARC:{key}" if key.startswith("CO-") else key
            if key in self.G and key not in found:
                found.append(key)
        low = text.lower()
        for name, node in sorted(self.entity_names.items(), key=lambda x: -len(x[0])):
            if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", low) and node not in found:
                found.append(node)
        return found[:6]

    # ---- local search
    def local_context(self, node: str, max_lines: int = 40) -> str:
        G = self.G
        d = G.nodes[node]
        t = d["type"]
        lines = [f"ENTITY {node} [{t}] {d.get('name', '')}"]
        flags = self._flags.get(node)
        if flags:
            lines.append(f"Detector flags: {', '.join(sorted(set(flags)))}")
        if t == "Member":
            plan = out_by_rel(G, node, "ENROLLED_IN")[0]
            pcp = out_by_rel(G, node, "HAS_PCP")
            lines.append(f"({node})-[ENROLLED_IN]->({plan}: {_n(G, plan, 'name')}); age {d['age']}; region {d['region']}")
            if pcp:
                lines.append(f"({node})-[HAS_PCP]->({pcp[0]}: {_n(G, pcp[0], 'specialty')})")
            conds = [_n(G, c, "name") for c in out_by_rel(G, node, "HAS_CONDITION")]
            lines.append(f"Conditions: {', '.join(conds) or 'none recorded'}")
            for rel in ("HAS_PHONE", "LIVES_AT"):
                x = out_by_rel(G, node, rel)[0]
                others = [m for m in in_by_rel(G, x, rel) if m != node]
                lines.append(f"({node})-[{rel}]->({x}) shared with {len(others)} other members{': ' + ', '.join(others[:8]) if others else ''}")
            cl = sorted((self.det.claims[c] for c in out_by_rel(G, node, "FILED")), key=lambda f: f["month"])
            lines.append(f"Claims: {len(cl)} | billed ${sum(f['billed'] for f in cl):,.0f} | denied {sum(f['status'] == 'DENIED' for f in cl)}")
            for f in cl[-10:]:
                lines.append(f"  {f['id']} {f['month']} {f['cpt']} by {f['provider']} ({_n(G, f['provider'], 'specialty')}) "
                             f"${f['billed']:,.0f} {f['status']}{' ' + f['denial'] if f.get('denial') else ''}{' PA ' + f['pa'] if f.get('pa') else ''}"
                             f"{' appeal ' + f['appeal'] if f.get('appeal') else ''}")
            rx = [f for f in self.det.rx if f["member"] == node]
            if rx:
                lines.append(f"Pharmacy: {len(rx)} fills, drugs {sorted({_n(G, f['drug'], 'name') for f in rx})}, "
                             f"{len({f['prescriber'] for f in rx})} prescribers {sorted({f['prescriber'] for f in rx})}, "
                             f"{len({f['pharmacy'] for f in rx})} pharmacies {sorted({f['pharmacy'] for f in rx})}")
        elif t == "Provider":
            nets = out_by_rel(G, node, "IN_NETWORK")
            lines.append(f"Specialty {d['specialty']}; region {d['region']}; in network for {', '.join(sorted(nets))}")
            cl = [self.det.claims[c] for c in in_by_rel(G, node, "BILLED_BY")]
            den = Counter(f["denial"].split(":")[1] for f in cl if f.get("denial"))
            lines.append(f"Claims billed: {len(cl)} | billed ${sum(f['billed'] for f in cl):,.0f} | paid ${sum(f['paid'] for f in cl):,.0f} "
                         f"| denial rate {sum(f['status'] == 'DENIED' for f in cl) / max(1, len(cl)):.1%} | denials {dict(den.most_common())}")
            cpts = Counter(f["cpt"] for f in cl)
            lines.append(f"Top procedures: {', '.join(f'{c} {_n(G, c, 'name')} x{n}' for c, n in cpts.most_common(4))}")
            pas = out_by_rel(G, node, "REQUESTED")
            pa_req = [f for f in cl if _n(G, f["cpt"], "pa_required")]
            lines.append(f"Prior auths requested: {len(pas)} ({Counter(G.nodes[p]['status'] for p in pas)}) for {len(pa_req)} PA-required claims")
            mems = Counter(f["member"] for f in cl)
            lines.append(f"Distinct members: {len(mems)}; top: {', '.join(m for m, _ in mems.most_common(8))}")
            shared = Counter()
            for m in mems:
                for p in self.det.member_providers[m]:
                    if p != node:
                        shared[p] += 1
            lines.append("Providers sharing most members: " + ", ".join(f"{p} ({_n(G, p, 'specialty')}, {n} shared)" for p, n in shared.most_common(5)))
            refs_in = sorted(((u, G[u][node]["weight"]) for u in in_by_rel(G, node, "REFERRED_TO")), key=lambda x: -x[1])[:5]
            refs_out = sorted(((v, G[node][v]["weight"]) for v in out_by_rel(G, node, "REFERRED_TO")), key=lambda x: -x[1])[:5]
            if refs_in:
                lines.append(f"Referred from: {refs_in}")
            if refs_out:
                lines.append(f"Refers to: {refs_out}")
            comm = next((c.id for c in self.communities if node in c.providers), None)
            if comm is not None:
                lines.append(f"Belongs to community C{comm}")
        elif t == "Claim":
            f = self.det.claims[node]
            lines.append(", ".join(f"{k}={v}" for k, v in f.items()))
            lines.append(self.local_context(f["member"], 12))
        elif t in ("Drug", "Procedure", "Diagnosis", "DenialReason", "Plan"):
            if t == "Drug":
                fills = [f for f in self.det.rx if f["drug"] == node]
                lines.append(f"{d['drug_class']}; opioid={d['opioid']}; PA required={d['pa_required']}; fills {len(fills)}; "
                             f"members {len({f['member'] for f in fills})}; top prescribers {Counter(f['prescriber'] for f in fills).most_common(5)}")
            elif t == "Procedure":
                cl = [self.det.claims[c] for c in in_by_rel(G, node, "FOR_PROCEDURE") if G.nodes[c]["type"] == "Claim"]
                lines.append(f"PA required={d['pa_required']}; claims {len(cl)}; denial reasons {Counter(f.get('denial') for f in cl if f.get('denial')).most_common(4)}; "
                             f"top billers {Counter(f['provider'] for f in cl).most_common(5)}")
            elif t == "DenialReason":
                cl = [self.det.claims[c] for c in in_by_rel(G, node, "DENIED_FOR")]
                ap = Counter(f.get("appeal") for f in cl if f.get("appeal"))
                lines.append(f"Denied claims {len(cl)}; billed ${sum(f['billed'] for f in cl):,.0f}; appeals {dict(ap)}; "
                             f"top providers {Counter(f['provider'] for f in cl).most_common(6)}; top procedures {Counter(f['cpt'] for f in cl).most_common(4)}")
            elif t == "Diagnosis":
                lines.append(f"Members with condition: {len(in_by_rel(G, node, 'HAS_CONDITION'))}")
            elif t == "Plan":
                lines.append(f"{d['product']} / {d['lob']}; members {len(in_by_rel(G, node, 'ENROLLED_IN'))}; "
                             f"in-network providers {len(in_by_rel(G, node, 'IN_NETWORK'))}")
        else:
            for v in list(G.successors(node))[:10]:
                lines.append(f"({node})-[{G[node][v]['rel']}]->({v})")
            for u in list(G.predecessors(node))[:10]:
                lines.append(f"({u})-[{G[u][node]['rel']}]->({node})")
        return "\n".join(lines[:max_lines])

    def subgraph(self, node: str, limit: int = 45):
        """Small ego graph (non-hub neighbours, claims collapsed) for the UI."""
        G = self.G
        if node not in G:
            return {"nodes": [], "edges": []}
        S = nx.Graph()
        S.add_node(node)
        t = G.nodes[node]["type"]
        if t == "Member":
            for f in (self.det.claims[c] for c in out_by_rel(G, node, "FILED")):
                S.add_edge(node, f["provider"], rel="CLAIM" + (" DENIED" if f["status"] == "DENIED" else ""))
            for rel in ("HAS_PHONE", "LIVES_AT"):
                x = out_by_rel(G, node, rel)[0]
                S.add_edge(node, x, rel=rel)
                for m in in_by_rel(G, x, rel)[:8]:
                    S.add_edge(m, x, rel=rel)
            for f in self.det.rx:
                if f["member"] == node:
                    S.add_edge(node, f["prescriber"], rel="RX_PRESCRIBER")
                    S.add_edge(node, f["pharmacy"], rel="RX_PHARMACY")
        elif t == "Provider":
            mems = Counter(self.det.claims[c]["member"] for c in in_by_rel(G, node, "BILLED_BY"))
            for m, _ in mems.most_common(14):
                S.add_edge(node, m, rel="CLAIM")
                for p, n in self.det.member_providers[m].most_common(3):
                    if p != node:
                        S.add_edge(m, p, rel="CLAIM")
        else:
            for x in list(nx.all_neighbors(G, node))[:limit]:
                S.add_edge(node, x, rel=G[node][x]["rel"] if G.has_edge(node, x) else G[x][node]["rel"])
        if S.number_of_nodes() > limit:
            keep = [node] + [n for n in S if n != node][: limit - 1]
            S = S.subgraph(keep).copy()
        pos = nx.spring_layout(S, seed=3, k=0.6)
        return {
            "nodes": [{"id": n, "type": G.nodes[n]["type"] if n in G else "?", "x": round(float(pos[n][0]), 3), "y": round(float(pos[n][1]), 3),
                       "flag": bool(self._flags.get(n)), "center": n == node} for n in S],
            "edges": [{"s": a, "t": b, "rel": d["rel"]} for a, b, d in S.edges(data=True)],
        }

    # ---- global search
    KEYWORD_METRIC = [
        (r"fraud|ring|collu|suspicious|scheme|abuse", "risk_flags"),
        (r"deni|reject", "denial_rate"),
        (r"prior.?auth|authori[sz]ation|\bpa\b|precert", "co197"),
        (r"network|leak|referr", "oon_rate"),
        (r"opioid|drug|pharmac|\brx\b|prescri", "opioid_share"),
        (r"cost|spend|paid|expens|dollar|\$", "paid"),
    ]

    def global_context(self, question: str, k: int = 5):
        q = question.lower()
        metrics = [m for pat, m in self.KEYWORD_METRIC if re.search(pat, q)] or ["paid", "risk_flags"]
        ranked = {}
        for metric in metrics:
            order = sorted(self.communities, key=lambda c: -c.metrics[metric])
            for rank, c in enumerate(order):
                ranked[c.id] = ranked.get(c.id, 0) + 1 / (rank + 1)  # reciprocal-rank fusion
        top = sorted(self.communities, key=lambda c: -ranked[c.id])[:k]
        header = (f"{len(self.communities)} communities were detected (Louvain on the member-provider claims graph). "
                  f"Ranked by: {', '.join(metrics)}. Top {len(top)} community reports:")
        return header + "\n\n" + "\n\n".join(c.report for c in top), [f"C{c.id}" for c in top]

    def stats(self):
        G = self.G
        return {
            "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
            "nodeTypes": dict(Counter(t for _, t in G.nodes(data="type")).most_common()),
            "edgeTypes": dict(Counter(r for _, _, r in G.edges(data="rel")).most_common()),
            "communities": len(self.communities),
            "flaggedEntities": len(self._flags),
        }
