"""
FastAPI backend for the payer GraphRAG copilot.

Run:  .venv/bin/uvicorn server:app --port 8000

BYOK: the browser sends the user's Groq key in the `X-Groq-Api-Key` header on each request.
It is used to construct a ChatGroq client for that request only: never stored, logged or returned.
"""
from __future__ import annotations

import json
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from payer_agent.agent import build_agent, make_groq_llm
from payer_agent.compare import CONCEPTUAL, run_comparison
from payer_agent.data import generate
from payer_agent.graphrag import GraphRAG

t0 = time.perf_counter()
DS = generate()
RAG = GraphRAG(DS)
try:  # Neo4j Aura is optional: credentials live in payer-agent/.env, never in the browser
    from payer_agent.neo4j_retrieval import Neo4jGraphRAG
    from payer_agent.neo4j_store import embed

    N4 = Neo4jGraphRAG()
    embed(["warm up"])  # load the embedding model once at startup
    N4_ERROR = None
except Exception as e:
    N4, N4_ERROR = None, f"{type(e).__name__}: {str(e)[:200]}"
AGENT = build_agent(RAG, N4)
FLAGGED = RAG.det.fraud_rings()["rings"][0]["providers"][0]
COMPARISON = run_comparison(DS, FLAGGED)
STARTUP_SEC = round(time.perf_counter() - t0, 1)

app = FastAPI(title="Payer GraphRAG Copilot")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Groq-Api-Key"])

PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b",
             "openai/gpt-oss-20b", "meta-llama/llama-4-maverick-17b-128e-instruct", "llama-3.1-8b-instant"]
NON_CHAT = ("whisper", "tts", "guard", "playai", "orpheus", "compound", "allam", "distil")


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    model: str | None = None
    history: list[dict] = []
    backend: str = "networkx"  # or "neo4j"


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    model: str | None = None


@app.get("/api/health")
def health():
    return {"ok": True, "startupSec": STARTUP_SEC}


@app.get("/api/overview")
def overview():
    det = RAG.det
    pa = det.pa_gaps()["providers"][0]["provider"]
    shopper = det.doctor_shopping()["flagged"][0]["member"]
    leak = det.referral_leakage()["pcps"][0]["pcp"]
    pi = det.provider_influence()["providers"][0]["provider"]
    return {
        "stats": RAG.stats(),
        "startupSec": STARTUP_SEC,
        "communities": [{"id": f"C{c.id}", **c.metrics} for c in RAG.communities],
        "suggestions": [
            f"Why are claims from {pa} being denied so often?",
            "Are there any provider fraud rings? Who is involved?",
            f"Tell me about member {shopper}. Is there an opioid risk?",
            "Summarize the biggest cost and denial risks across the network.",
            f"Which PCPs have the worst out-of-network leakage, and is {leak} one of them?",
            f"How is {FLAGGED} connected to {pi}?",
            "Do we have synthetic-identity or enrollment-fraud clusters?",
            "Which plan has the highest denial rate and why?",
        ],
    }


@app.get("/api/evaluation")
def evaluation():
    """Detector output vs the planted ground truth (only possible because the data is simulated)."""
    det, truth = RAG.det, DS.truth

    def pr(found, actual):
        found, actual = set(found), set(actual)
        tp = len(found & actual)
        return {"found": len(found), "planted": len(actual), "truePositives": tp,
                "precision": round(tp / len(found), 3) if found else None, "recall": round(tp / len(actual), 3)}

    rings = det.fraud_rings()["rings"]
    pa = det.pa_gaps()
    lk = det.referral_leakage()
    ids = [c for c in det.identity_clusters()["clusters"] if len(c["plans"]) >= 3]
    id_members = [m for c in ids for m in c["members"]]
    id_extra = set(id_members) - set(truth["identity_cluster"])
    ring_m = set(truth["fraud_ring_members"])
    return [
        {"pattern": "Provider fraud ring", **pr([p for r in rings for p in r["providers"]], truth["fraud_ring"])},
        {"pattern": "Fraud-ring members", **pr([m for r in rings for m in r["core_members"]], truth["fraud_ring_members"])},
        {"pattern": "Opioid doctor shopping", **pr([h["member"] for h in det.doctor_shopping()["flagged"]], truth["doctor_shoppers"])},
        {"pattern": "Prior-auth gap provider", **pr([x["provider"] for x in pa["providers"] if x["co197_rate"] > 3 * pa["network_co197_rate"]], [truth["pa_abuser"]])},
        {"pattern": "Out-of-network leakage PCPs", **pr([x["pcp"] for x in lk["pcps"] if x["leakage_rate"] > 2.5 * lk["network_leakage_rate"]], truth["leaky_pcps"]),
         "note": "Extra hits are PCPs in regions with few in-network specialists: real network-adequacy gaps that emerge from the data, not planted ones."},
        {"pattern": "Synthetic-identity cluster", **pr(id_members, truth["identity_cluster"]),
         "note": f"{len(id_extra & ring_m)} extra hits are fraud-ring members sharing ring phones and {len(id_extra - ring_m)} live with them, so the detector also surfaces the ring's identity layer."},
    ]


@app.get("/api/compare")
def compare():
    return {"rows": COMPARISON, "conceptual": [{"aspect": a, "sql": s, "networkx": n} for a, s, n in CONCEPTUAL]}


@app.get("/api/subgraph/{node}")
def subgraph(node: str):
    return RAG.subgraph(node)


@app.post("/api/models")
def models(x_groq_api_key: str | None = Header(default=None)):
    if not x_groq_api_key:
        raise HTTPException(400, "Missing X-Groq-Api-Key header")
    from groq import AuthenticationError, Groq

    try:
        ids = [m.id for m in Groq(api_key=x_groq_api_key, timeout=15).models.list().data]
    except AuthenticationError:
        raise HTTPException(401, "Groq rejected this API key")
    except Exception as e:
        raise HTTPException(502, f"Could not reach Groq: {type(e).__name__}")
    chat = [m for m in ids if not any(x in m.lower() for x in NON_CHAT)]
    chat.sort(key=lambda m: (PREFERRED.index(m) if m in PREFERRED else 99, m))
    return {"models": chat}


@app.get("/api/neo4j/status")
def neo4j_status():
    if N4 is None:
        return {"connected": False, "error": N4_ERROR}
    try:
        return {"connected": True, **N4.status()}
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


@app.post("/api/neo4j/retrieve")
def neo4j_retrieve(req: RetrieveRequest, x_groq_api_key: str | None = Header(default=None)):
    """Run the GraphRAG retrieval pipeline stage by stage and return every Cypher query + intermediate result."""
    if N4 is None:
        raise HTTPException(503, f"Neo4j not connected: {N4_ERROR}")
    llm = make_groq_llm(x_groq_api_key, req.model or PREFERRED[0]) if x_groq_api_key else None
    t = time.perf_counter()
    out = N4.retrieve(req.question, llm=llm, link=RAG.link_entities)
    return {**out, "totalMs": round((time.perf_counter() - t) * 1000), "text2cypher": llm is not None}


@app.post("/api/chat")
def chat(req: ChatRequest, x_groq_api_key: str | None = Header(default=None)):
    llm = make_groq_llm(x_groq_api_key, req.model or PREFERRED[0]) if x_groq_api_key else None
    history = [{"role": h.get("role"), "content": str(h.get("content", ""))[:3000]} for h in req.history[-6:]]

    def events():
        sent = 0
        final = None
        start = time.perf_counter()
        try:
            for state in AGENT.stream({"question": req.question, "history": history},
                                      config={"configurable": {"llm": llm, "backend": req.backend}}, stream_mode="values"):
                for step in state.get("trace", [])[sent:]:
                    yield f"data: {json.dumps({'type': 'step', **step})}\n\n"
                sent = len(state.get("trace", []))
                final = state
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {str(e)[:300]}'})}\n\n"
            return
        usage = final.get("usage", [])
        payload = {
            "type": "final",
            "answer": final.get("answer", ""),
            "entities": final.get("entities", []),
            "contexts": [{k: c.get(k) for k in ("tool", "args", "text", "ms", "refs", "cypher", "backend")} for c in final.get("contexts", [])],
            "backend": "neo4j" if req.backend == "neo4j" and N4 is not None else "networkx",
            "verification": final.get("verification", {}),
            "mode": "llm" if llm else "retrieval-only",
            "model": req.model if llm else None,
            "tokens": {"input": sum(u["input"] for u in usage), "output": sum(u["output"] for u in usage), "calls": len(usage)},
            "totalMs": round((time.perf_counter() - start) * 1000),
        }
        yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
