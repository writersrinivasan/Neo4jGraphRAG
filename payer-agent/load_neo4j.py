"""Load the simulated payer graph into Neo4j Aura (credentials from .env).

  .venv/bin/python load_neo4j.py              # full reload (~4 min over the internet)
  .venv/bin/python load_neo4j.py --embeddings # only descriptions, embeddings and indexes
"""
import sys

from payer_agent.data import generate
from payer_agent.graphrag import GraphRAG
from payer_agent.neo4j_store import load_embeddings, load_graph

if __name__ == "__main__":
    rag = GraphRAG(generate())
    if "--embeddings" in sys.argv:
        load_embeddings(rag)
    else:
        load_graph(rag, reset=True)
