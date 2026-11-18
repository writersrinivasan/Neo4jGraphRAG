export type Category = "Embedded / in-process" | "Extend existing RDBMS" | "Self-hosted graph server" | "Distributed graph" | "RDF / ontology" | "Managed cloud (reference)";

export interface GraphOption {
  name: string;
  category: Category;
  license: string;
  languages: string[];
  inHouse: boolean;
  costTier: 0 | 1 | 2 | 3; // 0 = free, 3 = enterprise license
  graphragFit: 1 | 2 | 3 | 4 | 5;
  bestFor: string;
  watchOut: string;
  emoji: string;
}

export const OPTIONS: GraphOption[] = [
  {
    name: "NetworkX", category: "Embedded / in-process", license: "BSD-3 (free)", languages: ["Python API"],
    inHouse: true, costTier: 0, graphragFit: 3, emoji: "🐍",
    bestFor: "Prototypes, offline analytics, algorithm research (PageRank, Louvain, shortest paths). Zero install friction.",
    watchOut: "Lives entirely in RAM (≈379 bytes per node+edge measured here), single-threaded, no persistence, no concurrent users, no security model.",
  },
  {
    name: "igraph / rustworkx", category: "Embedded / in-process", license: "GPL-2 / Apache-2.0", languages: ["Python API"],
    inHouse: true, costTier: 0, graphragFit: 3, emoji: "⚡",
    bestFor: "Same role as NetworkX but a C/Rust core: typically 10-50× faster and far lighter on memory for batch analytics.",
    watchOut: "Still a library, not a database: no query language, persistence or access control.",
  },
  {
    name: "Kùzu (and forks)", category: "Embedded / in-process", license: "MIT", languages: ["Cypher"],
    inHouse: true, costTier: 0, graphragFit: 4, emoji: "📦",
    bestFor: "An embedded on-disk graph DB with Cypher, like 'SQLite for graphs'. Has vector + full-text search.",
    watchOut: "The original project was archived in Oct 2025; community forks continue. Check who maintains it before committing.",
  },
  {
    name: "DuckDB + DuckPGQ", category: "Embedded / in-process", license: "MIT", languages: ["SQL/PGQ"],
    inHouse: true, costTier: 0, graphragFit: 3, emoji: "🦆",
    bestFor: "Analysts who live in SQL: graph pattern matching over Parquet/CSV extracts of the legacy DB.",
    watchOut: "Community extension, still research-grade. Analytical (OLAP), not for live transactional traffic.",
  },
  {
    name: "PostgreSQL + Apache AGE + pgvector", category: "Extend existing RDBMS", license: "PostgreSQL + Apache-2.0", languages: ["openCypher", "SQL"],
    inHouse: true, costTier: 0, graphragFit: 5, emoji: "🐘",
    bestFor: "One database for relational tables, a Cypher graph and vector embeddings, so GraphRAG runs in a single store. Reuses existing DBA skills, Postgres roles, row-level security and auditing.",
    watchOut: "The legacy MySQL data still has to be replicated into Postgres (CDC/ETL). Deep traversals are slower than native graph engines.",
  },
  {
    name: "SQL Server Graph / Oracle 23ai PGQ", category: "Extend existing RDBMS", license: "Included in existing license", languages: ["T-SQL MATCH", "SQL/PGQ"],
    inHouse: true, costTier: 1, graphragFit: 3, emoji: "🏢",
    bestFor: "Clients already paying for SQL Server or Oracle: graph features at no extra license cost.",
    watchOut: "No help for a MySQL estate. Graph algorithms are limited compared with native engines.",
  },
  {
    name: "MySQL 8 (status quo)", category: "Extend existing RDBMS", license: "GPL / Enterprise", languages: ["SQL recursive CTE"],
    inHouse: true, costTier: 0, graphragFit: 1, emoji: "🐬",
    bestFor: "Keep as the system of record. Recursive CTEs can handle shallow, fixed-depth relationships.",
    watchOut: "No graph model, algorithms or Cypher. Multi-hop queries grow join by join and ran several times slower than graph traversal in the benchmark below.",
  },
  {
    name: "Neo4j Community", category: "Self-hosted graph server", license: "GPL-3 (free)", languages: ["Cypher"],
    inHouse: true, costTier: 0, graphragFit: 5, emoji: "🟢",
    bestFor: "The largest ecosystem: native vector index, the neo4j-graphrag library, LangChain/LlamaIndex integrations, Bloom-style tooling.",
    watchOut: "Community edition runs on a single server only: no clustering and no fine-grained RBAC. Those are Enterprise features and matter for PHI.",
  },
  {
    name: "Neo4j Enterprise", category: "Self-hosted graph server", license: "Commercial (quote)", languages: ["Cypher", "GQL"],
    inHouse: true, costTier: 3, graphragFit: 5, emoji: "💎",
    bestFor: "Enterprise HA clusters, RBAC, property-level security, online backup and vendor support.",
    watchOut: "License cost is quote-based and usually the biggest line item in the TCO.",
  },
  {
    name: "Memgraph", category: "Self-hosted graph server", license: "BSL 1.1 / Enterprise", languages: ["Cypher"],
    inHouse: true, costTier: 1, graphragFit: 4, emoji: "🔥",
    bestFor: "In-memory, very low-latency Cypher. Useful for real-time fraud scoring at claim intake. Bundles the MAGE algorithm library.",
    watchOut: "RAM-bound sizing. HA/RBAC are enterprise features. BSL is not OSI open source.",
  },
  {
    name: "FalkorDB", category: "Self-hosted graph server", license: "SSPL v1", languages: ["Cypher (subset)"],
    inHouse: true, costTier: 1, graphragFit: 4, emoji: "🦅",
    bestFor: "Built for GraphRAG (has its own GraphRAG-SDK). Fast sparse-matrix engine on Redis.",
    watchOut: "SSPL restricts offering it as a service. The Cypher dialect is a subset.",
  },
  {
    name: "ArangoDB", category: "Self-hosted graph server", license: "BSL 1.1 / Enterprise", languages: ["AQL"],
    inHouse: true, costTier: 1, graphragFit: 3, emoji: "🥑",
    bestFor: "Multi-model: document + graph + search in one engine.",
    watchOut: "Proprietary AQL. Since v3.12 the community edition has production limits, so read the license carefully.",
  },
  {
    name: "NebulaGraph", category: "Distributed graph", license: "Apache-2.0", languages: ["nGQL", "openCypher"],
    inHouse: true, costTier: 0, graphragFit: 3, emoji: "🌌",
    bestFor: "Billions of edges spread over many servers, fully open source.",
    watchOut: "At least 3 service types to operate, so it needs real ops staff. Overkill below a few hundred million edges.",
  },
  {
    name: "JanusGraph", category: "Distributed graph", license: "Apache-2.0", languages: ["Gremlin"],
    inHouse: true, costTier: 0, graphragFit: 2, emoji: "🪐",
    bestFor: "Very large graphs on existing Cassandra/ScyllaDB/HBase clusters.",
    watchOut: "Heavy stack (storage + index backends) and an imperative Gremlin learning curve.",
  },
  {
    name: "TigerGraph", category: "Distributed graph", license: "Commercial / free tier", languages: ["GSQL", "GQL"],
    inHouse: true, costTier: 3, graphragFit: 4, emoji: "🐯",
    bestFor: "Massively parallel deep-link analytics (10+ hops) at enterprise scale.",
    watchOut: "Commercial licensing and the proprietary GSQL language.",
  },
  {
    name: "Apache Jena / GraphDB (RDF)", category: "RDF / ontology", license: "Apache-2.0 / free edition", languages: ["SPARQL"],
    inHouse: true, costTier: 0, graphragFit: 3, emoji: "🧬",
    bestFor: "Medical ontologies such as SNOMED CT, ICD-10, RxNorm and FHIR RDF, with reasoning over code hierarchies.",
    watchOut: "SPARQL/RDF modelling is a niche skill. Better as an ontology layer than as the claims store.",
  },
  {
    name: "Amazon Neptune / Cosmos DB Gremlin / AuraDB", category: "Managed cloud (reference)", license: "Pay-as-you-go", languages: ["Gremlin", "openCypher", "SPARQL"],
    inHouse: false, costTier: 2, graphragFit: 4, emoji: "☁️",
    bestFor: "Zero ops; a BAA is available for HIPAA workloads.",
    watchOut: "PHI leaves the premises, which conflicts with an in-house mandate. Costs are usage-based and can grow without warning.",
  },
];

export const CATEGORIES: Category[] = [
  "Embedded / in-process", "Extend existing RDBMS", "Self-hosted graph server", "Distributed graph", "RDF / ontology", "Managed cloud (reference)",
];
