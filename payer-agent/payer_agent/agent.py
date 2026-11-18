"""
LangGraph agent: payer GraphRAG copilot.

    link_entities -> plan -> run_tools -> reflect --(need more?)--> run_tools
                                               \\--> answer -> verify -> END

BYOK: the Groq chat model is built per request from the caller's key and passed in
`config["configurable"]["llm"]`, so the key never enters graph state, logs or storage.
Without a key the agent still runs (heuristic planner + retrieval-only answer).
"""
from __future__ import annotations

import json
import operator
import re
import time
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from .graphrag import GraphRAG

MAX_CONTEXT_CHARS = 11000  # keeps prompts inside Groq free-tier TPM limits

TOOLS = {
    "local_search": "Neighbourhood facts for ONE entity id (member M#####, provider P####, claim CLM######, drug DRG##, plan PL##, CARC:CO-###, CPT:#####). args: {\"entity\": id}",
    "global_search": "Network-wide themes from GraphRAG community reports. Use for summaries, 'biggest risks', 'across the network'. args: {}",
    "fraud_rings": "Detect colluding provider rings via abnormal patient-panel overlap. args: {}",
    "doctor_shopping": "Members filling opioids from >=3 prescribers and >=3 pharmacies. args: {}",
    "pa_gaps": "Providers with abnormal prior-authorization-absent (CO-197) denial rates. args: {}",
    "referral_leakage": "HMO/EPO PCPs whose attributed members leak to out-of-network specialists (CO-242). args: {}",
    "identity_clusters": "Members linked by shared phone/address across plans (synthetic identity / enrollment fraud). args: {}",
    "provider_influence": "PageRank of providers in the referral network. args: {}",
    "denial_summary": "Denial rate and top CARC reasons by plan. args: {}",
    "path_between": "Shortest relationship path between two entities. args: {\"a\": id, \"b\": id}",
    "text2cypher": "(Neo4j only) Exact counts, sums, averages or lists: an LLM-written read-only Cypher query over the claims graph. args: {}",
}

HEURISTICS = [
    (r"fraud|ring|collu|kickback|scheme", "fraud_rings"),
    (r"opioid|doctor.?shop|pharmac|prescri|\brx\b|drug abuse", "doctor_shopping"),
    (r"prior.?auth|authori[sz]|co-?197|precert|\bum\b|utili[sz]ation", "pa_gaps"),
    (r"leak|out.of.network|\boon\b|co-?242|network adequacy", "referral_leakage"),
    (r"identity|synthetic|shared (phone|address)|enrol+ment fraud|household", "identity_clusters"),
    (r"influen|hub|pagerank|central|key provider", "provider_influence"),
    (r"denial rate|by plan|which plan|denials? (by|per)|carc", "denial_summary"),
    (r"summar|overall|across|biggest|top risk|network.wide|overview|where should|priorit", "global_search"),
    (r"how many|count of|number of|total|sum of|average|\blist (all|the)\b|top \d+", "text2cypher"),
]

SYSTEM = """You are a payer-side GraphRAG copilot for a health plan's Special Investigations (SIU), Utilization Management and Network teams.
Answer ONLY from the CONTEXT, which was retrieved from a claims knowledge graph (NetworkX) by graph tools.
Rules:
- Cite every entity you mention with its id in square brackets, e.g. [P0379], [M00333], [CARC:CO-197], [C4] for communities.
- Quote numbers exactly as they appear in the context. Never invent ids, numbers or regulations.
- If the context is insufficient, say what is missing and which question would resolve it.
- Data is synthetic. Do not present findings as accusations: use "flagged for review".
Format: a 1-2 sentence direct answer, then "Evidence" bullets, then "Recommended payer actions" (2-4 bullets: e.g. SIU referral, prepayment review, PA education, network contracting)."""


class AgentState(TypedDict, total=False):
    question: str
    history: list
    entities: list
    plan: list
    executed: int
    contexts: Annotated[list, operator.add]
    iterations: int
    answer: str
    verification: dict
    trace: Annotated[list, operator.add]
    usage: Annotated[list, operator.add]


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _usage(msg) -> dict:
    u = getattr(msg, "usage_metadata", None) or {}
    return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}


def _step(node, detail, t0, **extra):
    return {"node": node, "detail": detail, "ms": round((time.perf_counter() - t0) * 1000, 1), **extra}


def build_agent(rag: GraphRAG, n4=None):
    """n4: optional Neo4jGraphRAG. When config['configurable']['backend'] == 'neo4j' tools run as Cypher on Neo4j Aura."""
    G, det = rag.G, rag.det
    if n4 is not None:
        from .neo4j_retrieval import DETECTORS, EXPANSION, _label_for

    def fmt_neo4j(tool: str, args: dict, question: str, llm):
        if tool == "local_search":
            e = args.get("entity", "")
            q = EXPANSION.get(_label_for(e) or "")
            if not q:
                return f"No Neo4j expansion query for {e}", [], None
            rows = n4.run(q, id=e)
            return (f"Neo4j expansion for {e}:\n{json.dumps(rows[0], default=str)}" if rows else f"{e} not found in Neo4j"), [e], q
        if tool == "global_search":
            out = n4.retrieve(question, link=rag.link_entities)
            return out["context"], out["seeds"], "// hybrid: exact id + full-text + vector -> RRF fusion -> Cypher expansion -> community vector search"
        if tool == "text2cypher":
            if llm is None:
                return "text2cypher needs a Groq key.", [], None
            cy, rows = n4.text2cypher(question, llm)
            return f"Cypher:\n{cy}\nRows ({len(rows)}):\n" + "\n".join(json.dumps(r, default=str) for r in rows), [], cy
        if tool == "path_between":
            return n4.detector("path_between", a=args.get("a"), b=args.get("b"))
        if tool in DETECTORS:
            return n4.detector(tool)
        return f"Unknown tool {tool}", [], None

    # ------------------------------------------------------------ tool executors
    def fmt(tool: str, args: dict) -> tuple[str, list]:
        if tool == "local_search":
            e = args.get("entity", "")
            if e not in G:
                return f"Entity {e} not found in graph.", []
            return rag.local_context(e), [e]
        if tool == "global_search":
            return rag.global_context(args.get("question", ""))
        if tool == "fraud_rings":
            r = det.fraud_rings()
            lines = [f"Fraud-ring detector: scanned {r['pairs_scanned']:,} provider pairs; rule {r['threshold']}."]
            for i, ring in enumerate(r["rings"], 1):
                lines.append(f"Ring {i}: providers {ring['providers']} ({', '.join(ring['specialties'])}); "
                             f"{len(ring['core_members'])} core members seen by >=3 ring providers (e.g. {ring['core_members'][:6]}); "
                             f"shared-phone ratio {ring['shared_phone_ratio']:.0%}; billed ${ring['billed']:,} on core members.")
            return "\n".join(lines if r["rings"] else lines + ["No rings found."]), [p for x in r["rings"] for p in x["providers"]]
        if tool == "doctor_shopping":
            r = det.doctor_shopping()
            lines = [f"Doctor-shopping detector: {r['opioid_members']} members have opioid fills; {len(r['flagged'])} meet >=3 prescribers AND >=3 pharmacies."]
            for h in r["flagged"][:12]:
                lines.append(f"{h['member']}: {h['opioid_fills']} opioid fills ({', '.join(h['drugs'])}); prescribers {h['prescribers']}; pharmacies {h['pharmacies']}")
            return "\n".join(lines), [h["member"] for h in r["flagged"]]
        if tool == "pa_gaps":
            r = det.pa_gaps()
            lines = [f"Prior-auth gap detector: network CO-197 rate on PA-required claims = {r['network_co197_rate']:.1%}. Providers ranked by CO-197 rate:"]
            for x in r["providers"][:8]:
                lines.append(f"{x['provider']} ({x['specialty']}): {x['co197_denials']}/{x['pa_required_claims']} PA-required claims denied CO-197 "
                             f"({x['co197_rate']:.0%}); ${x['denied_billed']:,} billed denied")
            return "\n".join(lines), [x["provider"] for x in r["providers"][:8]]
        if tool == "referral_leakage":
            r = det.referral_leakage()
            lines = [f"Leakage detector: network out-of-network rate for HMO/EPO specialist claims = {r['network_leakage_rate']:.1%}. PCPs ranked:"]
            for x in r["pcps"][:8]:
                lines.append(f"{x['pcp']}: {x['out_of_network']}/{x['specialist_claims']} specialist claims out of network ({x['leakage_rate']:.0%}); {x['co242_denials']} CO-242 denials")
            return "\n".join(lines), [x["pcp"] for x in r["pcps"][:8]]
        if tool == "identity_clusters":
            r = det.identity_clusters()
            lines = [f"Identity detector: {len(r['clusters'])} clusters of >=5 members linked by shared phone/address."]
            for c in r["clusters"][:5]:
                lines.append(f"Cluster of {len(c['members'])} members {c['members'][:10]} across {len(c['plans'])} plans {c['plans']}; identifiers {c['identifiers'][:5]}")
            return "\n".join(lines), [m for c in r["clusters"][:5] for m in c["members"]]
        if tool == "provider_influence":
            r = det.provider_influence()
            lines = ["Referral-network PageRank (top providers):"] + [
                f"{x['provider']} ({x['specialty']}): pagerank {x['pagerank']}, {x['referrals_in']} referrals in" for x in r["providers"]]
            return "\n".join(lines), [x["provider"] for x in r["providers"]]
        if tool == "denial_summary":
            r = det.denial_summary()
            lines = ["Denials by plan:"] + [f"{x['plan']} {x['name']}: {x['claims']} claims, denial rate {x['denial_rate']:.1%}, top CARC {x['top_reasons']}" for x in r["plans"]]
            return "\n".join(lines), [x["plan"] for x in r["plans"]]
        if tool == "path_between":
            r = det.path_between(args.get("a", ""), args.get("b", ""))
            if not r["path"]:
                return f"No path between {args.get('a')} and {args.get('b')} (excluding hub nodes).", []
            return f"Shortest path ({len(r['path']) - 1} hops):\n" + "\n".join(r["hops"]), r["path"]
        return f"Unknown tool {tool}", []

    def heuristic_plan(q: str, entities: list) -> list:
        ql = q.lower()
        plan = [{"tool": t, "args": {}, "why": f"keyword match /{pat}/"} for pat, t in HEURISTICS if re.search(pat, ql)]
        if len(entities) >= 2 and re.search(r"path|connect|link|relat|between", ql):
            plan.append({"tool": "path_between", "args": {"a": entities[0], "b": entities[1]}, "why": "two entities + relationship question"})
        for e in entities[:3]:
            plan.append({"tool": "local_search", "args": {"entity": e}, "why": f"entity {e} mentioned"})
        return plan or [{"tool": "global_search", "args": {}, "why": "no specific signal; use community reports"}]

    # ------------------------------------------------------------ nodes
    def link_entities(state: AgentState) -> dict:
        t0 = time.perf_counter()
        ents = rag.link_entities(state["question"])
        source = "question"
        if not ents:  # follow-up like "tell me more about that provider"
            for msg in reversed(state.get("history", [])[-4:]):
                ents = rag.link_entities(msg.get("content", ""))[:2]
                if ents:
                    source = "conversation history"
                    break
        return {"entities": ents, "iterations": 0, "executed": 0,
                "trace": [_step("link_entities", f"{len(ents)} entities from {source}: {', '.join(ents) or 'none'}", t0)]}

    def plan(state: AgentState, config: RunnableConfig) -> dict:
        t0 = time.perf_counter()
        llm = config.get("configurable", {}).get("llm")
        neo = config.get("configurable", {}).get("backend") == "neo4j" and n4 is not None
        allowed = {k: v for k, v in TOOLS.items() if k != "text2cypher" or (neo and llm is not None)}
        q, ents = state["question"], state.get("entities", [])
        steps, how, usage = None, "heuristic (no API key)", []
        if llm is not None:
            prompt = (
                "Choose 1-4 graph tools to answer a health-plan analyst's question.\n"
                f"TOOLS:\n" + "\n".join(f"- {k}: {v}" for k, v in allowed.items()) +
                f"\n\nLinked entities: {ents or 'none'}\nQuestion: {q}\n\n"
                'Reply with JSON only: {"tools": [{"tool": "<name>", "args": {...}, "why": "<short reason>"}]}'
            )
            try:
                try:
                    msg = llm.bind(response_format={"type": "json_object"}).invoke([HumanMessage(prompt)])
                except Exception:
                    msg = llm.invoke([HumanMessage(prompt)])
                usage.append(_usage(msg))
                raw = _strip_think(msg.content)
                data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
                steps = [s for s in data.get("tools", []) if s.get("tool") in allowed][:4]
                how = "LLM planner"
            except Exception as e:  # malformed JSON, rate limit, ... -> safe fallback
                steps, how = None, f"heuristic (LLM planner failed: {type(e).__name__})"
        if not steps:
            steps = [s for s in heuristic_plan(q, ents) if s["tool"] in allowed] or [{"tool": "global_search", "args": {}, "why": "fallback"}]
        # always ground mentioned entities with local search
        have = {s["args"].get("entity") for s in steps if s["tool"] == "local_search"}
        for e in ents[:3]:
            if e not in have:
                steps.append({"tool": "local_search", "args": {"entity": e}, "why": f"ground entity {e}"})
        steps = steps[:6]
        return {"plan": steps, "usage": usage,
                "trace": [_step("plan", f"{how}: " + ", ".join(s["tool"] + (f"({list(s['args'].values())[0]})" if s["args"] else "") for s in steps), t0,
                                tools=[{"tool": s["tool"], "args": s["args"], "why": s.get("why", "")} for s in steps])]}

    def run_tools(state: AgentState, config: RunnableConfig) -> dict:
        t0 = time.perf_counter()
        cfg = config.get("configurable", {})
        backend = "neo4j" if cfg.get("backend") == "neo4j" and n4 is not None else "networkx"
        start = state.get("executed", 0)
        new, notes = [], []
        for s in state["plan"][start:]:
            t1 = time.perf_counter()
            args = dict(s["args"])
            if s["tool"] == "global_search":
                args["question"] = state["question"]
            cypher = None
            try:
                if backend == "neo4j":
                    text, refs, cypher = fmt_neo4j(s["tool"], args, state["question"], cfg.get("llm"))
                else:
                    text, refs = fmt(s["tool"], args)
            except Exception as e:
                text, refs = f"Tool error: {type(e).__name__}: {str(e)[:300]}", []
            new.append({"tool": s["tool"], "args": s["args"], "text": text, "refs": refs[:40], "cypher": cypher, "backend": backend,
                        "ms": round((time.perf_counter() - t1) * 1000, 1)})
            notes.append(f"{s['tool']} → {len(text):,} chars")
        return {"contexts": new, "executed": len(state["plan"]),
                "trace": [_step("run_tools", f"[{'Neo4j Aura · Cypher' if backend == 'neo4j' else 'NetworkX · in-memory'}] " + "; ".join(notes), t0)]}

    def reflect(state: AgentState) -> dict:
        t0 = time.perf_counter()
        ctx = state.get("contexts", [])
        useful = [c for c in ctx if c["refs"] or len(c["text"]) > 200]
        it = state.get("iterations", 0) + 1
        if not useful and it < 2 and not any(c["tool"] == "global_search" for c in ctx):
            return {"iterations": it, "plan": state["plan"] + [{"tool": "global_search", "args": {}, "why": "evidence too thin; broaden"}],
                    "trace": [_step("reflect", "evidence too thin → adding global_search and looping", t0)]}
        return {"iterations": it, "trace": [_step("reflect", f"{len(useful)}/{len(ctx)} tool results carry evidence → answer", t0)]}

    def route_after_reflect(state: AgentState) -> str:
        return "run_tools" if state.get("executed", 0) < len(state["plan"]) else "answer"

    def answer(state: AgentState, config: RunnableConfig) -> dict:
        t0 = time.perf_counter()
        llm = config.get("configurable", {}).get("llm")
        ctx_text, used = "", 0
        for c in state.get("contexts", []):
            block = f"### TOOL {c['tool']} {json.dumps(c['args']) if c['args'] else ''}\n{c['text']}\n\n"
            if used + len(block) > MAX_CONTEXT_CHARS:
                block = block[: max(0, MAX_CONTEXT_CHARS - used)] + "\n[truncated]\n"
            ctx_text += block
            used += len(block)
            if used >= MAX_CONTEXT_CHARS:
                break
        if llm is None:
            text = ("**Retrieval-only mode** (add a Groq API key for an AI-written answer). "
                    "Here is the evidence the agent retrieved from the graph:\n\n" + ctx_text)
            return {"answer": text, "trace": [_step("answer", "no LLM: returned retrieved evidence", t0)]}
        msgs = [SystemMessage(SYSTEM)]
        for h in state.get("history", [])[-4:]:
            msgs.append(HumanMessage(h["content"]) if h["role"] == "user" else AIMessage(h["content"][:1500]))
        msgs.append(HumanMessage(f"CONTEXT:\n{ctx_text}\nQUESTION: {state['question']}"))
        try:
            msg = llm.invoke(msgs)
            text = _strip_think(msg.content)
            return {"answer": text, "usage": [_usage(msg)],
                    "trace": [_step("answer", f"LLM answer from {len(ctx_text):,} chars of graph context", t0)]}
        except Exception as e:
            text = f"⚠️ The Groq call failed ({type(e).__name__}: {str(e)[:200]}). Evidence retrieved:\n\n{ctx_text}"
            return {"answer": text, "trace": [_step("answer", f"LLM error {type(e).__name__}", t0)]}

    def verify(state: AgentState) -> dict:
        t0 = time.perf_counter()
        cited = set(re.findall(r"\[([A-Z]{1,4}:?[A-Z0-9-]+)\]", state.get("answer", "")))
        comm_ids = {f"C{c.id}" for c in rag.communities}
        valid = sorted(c for c in cited if c in G or c in comm_ids)
        unknown = sorted(c for c in cited if c not in G and c not in comm_ids)
        retrieved = {r for c in state.get("contexts", []) for r in c["refs"]} | set(state.get("entities", []))
        grounded = sorted(c for c in valid if c in retrieved or c in comm_ids or c in "".join(x["text"] for x in state.get("contexts", [])))
        v = {"cited": len(cited), "valid": valid, "unknown": unknown, "grounded": len(grounded)}
        return {"verification": v,
                "trace": [_step("verify", f"{len(cited)} citations: {len(valid)} exist in graph, {len(grounded)} appear in retrieved context, {len(unknown)} unknown", t0)]}

    g = StateGraph(AgentState)
    g.add_node("link_entities", link_entities)
    g.add_node("plan", plan)
    g.add_node("run_tools", run_tools)
    g.add_node("reflect", reflect)
    g.add_node("answer", answer)
    g.add_node("verify", verify)
    g.add_edge(START, "link_entities")
    g.add_edge("link_entities", "plan")
    g.add_edge("plan", "run_tools")
    g.add_edge("run_tools", "reflect")
    g.add_conditional_edges("reflect", route_after_reflect, {"run_tools": "run_tools", "answer": "answer"})
    g.add_edge("answer", "verify")
    g.add_edge("verify", END)
    return g.compile()


def make_groq_llm(api_key: str, model: str):
    from langchain_groq import ChatGroq

    return ChatGroq(api_key=api_key, model=model, temperature=0.1, max_tokens=1200, max_retries=1, timeout=45)
