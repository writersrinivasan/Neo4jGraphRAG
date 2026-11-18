export interface QueryLang {
  id: string;
  name: string;
  standard: string;
  style: "Declarative pattern" | "Imperative traversal" | "Declarative SQL" | "Triple pattern" | "Library API";
  engines: string;
  learning: 1 | 2 | 3 | 4 | 5; // 1 easy, 5 hard
  readability: 1 | 2 | 3 | 4 | 5; // 5 = most readable for graph questions
  variableDepth: "Native" | "Recursive CTE" | "Loop / repeat()" | "Property paths" | "Code";
  twoHop: string;
  kHop: string;
}

// Question used everywhere:
//  "Which providers share 8+ patients with flagged provider P00227?"  (fraud-ring signal)
//  plus a variable-depth version: "every provider reachable within 3 shared-patient hops".
export const LANGS: QueryLang[] = [
  {
    id: "cypher", name: "Cypher / openCypher", standard: "openCypher (de-facto); basis of ISO GQL", style: "Declarative pattern",
    engines: "Neo4j, Memgraph, Apache AGE, FalkorDB, Neptune, Kùzu", learning: 2, readability: 5, variableDepth: "Native",
    twoHop: `MATCH (p:Provider {id:'P00227'})<-[:TREATED_BY]-(m:Member)
      -[:TREATED_BY]->(q:Provider)
WITH q, count(DISTINCT m) AS shared
WHERE shared >= 8
RETURN q.id, shared ORDER BY shared DESC;`,
    kHop: `MATCH (p:Provider {id:'P00227'})-[:TREATED_BY*..6]-(q:Provider)
RETURN count(DISTINCT q);`,
  },
  {
    id: "gql", name: "ISO GQL", standard: "ISO/IEC 39075:2024 (new international standard)", style: "Declarative pattern",
    engines: "Neo4j 5+, TigerGraph, Spanner Graph (support still maturing)", learning: 2, readability: 5, variableDepth: "Native",
    twoHop: `MATCH (p:Provider {id:'P00227'})<-[:TREATED_BY]-(m:Member)
      -[:TREATED_BY]->(q:Provider)
RETURN q.id AS provider, COUNT(DISTINCT m) AS shared
GROUP BY provider
NEXT FILTER shared >= 8
RETURN provider, shared ORDER BY shared DESC`,
    kHop: `MATCH (p:Provider {id:'P00227'})-[:TREATED_BY]-{1,6}(q:Provider)
RETURN COUNT(DISTINCT q)`,
  },
  {
    id: "age", name: "Apache AGE (Cypher in Postgres)", standard: "openCypher embedded in SQL", style: "Declarative pattern",
    engines: "PostgreSQL + AGE extension", learning: 2, readability: 4, variableDepth: "Native",
    twoHop: `SELECT * FROM cypher('claims_graph', $$
  MATCH (p:Provider {id:'P00227'})<-[:TREATED_BY]-(m:Member)
        -[:TREATED_BY]->(q:Provider)
  WITH q, count(DISTINCT m) AS shared WHERE shared >= 8
  RETURN q.id, shared
$$) AS (provider agtype, shared agtype);`,
    kHop: `SELECT * FROM cypher('claims_graph', $$
  MATCH (p:Provider {id:'P00227'})-[:TREATED_BY*..6]-(q:Provider)
  RETURN count(DISTINCT q)
$$) AS (n agtype);`,
  },
  {
    id: "pgq", name: "SQL/PGQ", standard: "ISO SQL:2023 Part 16", style: "Declarative SQL",
    engines: "Oracle 23ai, DuckPGQ (proposed for PostgreSQL)", learning: 2, readability: 4, variableDepth: "Native",
    twoHop: `SELECT provider, COUNT(DISTINCT member) AS shared
FROM GRAPH_TABLE ( claims_graph
  MATCH (p IS provider WHERE p.id = 'P00227')
        <-[IS treated_by]-(m IS member)-[IS treated_by]->(q IS provider)
  COLUMNS (q.id AS provider, m.id AS member) )
GROUP BY provider
HAVING COUNT(DISTINCT member) >= 8
ORDER BY shared DESC;`,
    kHop: `SELECT COUNT(DISTINCT q_id) FROM GRAPH_TABLE ( claims_graph
  MATCH (p IS provider WHERE p.id='P00227')-[IS treated_by]-{1,6}(q IS provider)
  COLUMNS (q.id AS q_id) );`,
  },
  {
    id: "gremlin", name: "Gremlin", standard: "Apache TinkerPop", style: "Imperative traversal",
    engines: "JanusGraph, Neptune, Cosmos DB, (Neo4j via plugin)", learning: 4, readability: 2, variableDepth: "Loop / repeat()",
    twoHop: `g.V().has('Provider','id','P00227').
  in('TREATED_BY').out('TREATED_BY').hasLabel('Provider').
  not(has('id','P00227')).
  groupCount().by('id').
  unfold().where(select(values).is(gte(8))).
  order().by(values, desc)`,
    kHop: `g.V().has('Provider','id','P00227').
  repeat(both('TREATED_BY')).times(6).emit().
  hasLabel('Provider').dedup().count()`,
  },
  {
    id: "sparql", name: "SPARQL", standard: "W3C Recommendation", style: "Triple pattern",
    engines: "Apache Jena, GraphDB, Neptune, Stardog", learning: 4, readability: 3, variableDepth: "Property paths",
    twoHop: `PREFIX ins: <http://example.org/ins#>
SELECT ?q (COUNT(DISTINCT ?m) AS ?shared) WHERE {
  ?m ins:treatedBy ins:P00227 .
  ?m ins:treatedBy ?q .
  FILTER(?q != ins:P00227)
} GROUP BY ?q
  HAVING (COUNT(DISTINCT ?m) >= 8)
  ORDER BY DESC(?shared)`,
    kHop: `SELECT (COUNT(DISTINCT ?q) AS ?n) WHERE {
  ins:P00227 (^ins:treatedBy/ins:treatedBy)+ ?q .
}   # note: '+' is unbounded; depth caps need vendor extensions`,
  },
  {
    id: "aql", name: "AQL", standard: "Proprietary (ArangoDB)", style: "Declarative pattern",
    engines: "ArangoDB", learning: 3, readability: 3, variableDepth: "Native",
    twoHop: `FOR m IN 1..1 INBOUND 'providers/P00227' treated_by
  FOR q IN 1..1 OUTBOUND m treated_by
    FILTER q._key != 'P00227'
    COLLECT provider = q._key WITH COUNT INTO shared
    FILTER shared >= 8
    SORT shared DESC
    RETURN { provider, shared }`,
    kHop: `FOR v IN 1..6 ANY 'providers/P00227' treated_by
  OPTIONS { uniqueVertices: 'global', order: 'bfs' }
  FILTER IS_SAME_COLLECTION('providers', v)
  COLLECT WITH COUNT INTO n RETURN n`,
  },
  {
    id: "ngql", name: "nGQL", standard: "Proprietary (NebulaGraph), openCypher-compatible MATCH", style: "Declarative pattern",
    engines: "NebulaGraph", learning: 3, readability: 4, variableDepth: "Native",
    twoHop: `MATCH (p:Provider)<-[:TREATED_BY]-(m:Member)-[:TREATED_BY]->(q:Provider)
WHERE id(p) == "P00227"
WITH q, count(DISTINCT m) AS shared WHERE shared >= 8
RETURN id(q), shared ORDER BY shared DESC;`,
    kHop: `GO 1 TO 6 STEPS FROM "P00227" OVER TREATED_BY BIDIRECT
YIELD DISTINCT dst(edge) AS v | YIELD count(*);`,
  },
  {
    id: "tsql", name: "SQL Server MATCH", standard: "T-SQL extension", style: "Declarative SQL",
    engines: "SQL Server 2017+, Azure SQL", learning: 3, readability: 3, variableDepth: "Native",
    twoHop: `SELECT q.id, COUNT(DISTINCT m.id) AS shared
FROM Provider p, TreatedBy t1, Member m, TreatedBy t2, Provider q
WHERE MATCH(m-(t1)->p AND m-(t2)->q)
  AND p.id = 'P00227' AND q.id <> p.id
GROUP BY q.id HAVING COUNT(DISTINCT m.id) >= 8
ORDER BY shared DESC;`,
    kHop: `-- SHORTEST_PATH + FOR PATH syntax; arbitrary-length only via
-- MATCH(SHORTEST_PATH(p(-(t)->m<-(t2)-q){1,3}))  (verbose, limited)`,
  },
  {
    id: "mysql", name: "MySQL 8 SQL (today)", standard: "ISO SQL (recursive CTE)", style: "Declarative SQL",
    engines: "MySQL, MariaDB, any RDBMS", learning: 1, readability: 2, variableDepth: "Recursive CTE",
    twoHop: `SELECT b.provider_id, COUNT(DISTINCT b.member_id) AS shared
FROM claim a
JOIN claim b ON a.member_id = b.member_id
WHERE a.provider_id = 'P00227'
  AND b.provider_id <> 'P00227'
GROUP BY b.provider_id
HAVING shared >= 8
ORDER BY shared DESC;`,
    kHop: `WITH RECURSIVE reach(pid, depth) AS (
  SELECT 'P00227', 0
  UNION
  SELECT pm2.provider_id, reach.depth + 1
  FROM reach
  JOIN pm pm1 ON pm1.provider_id = reach.pid
  JOIN pm pm2 ON pm2.member_id   = pm1.member_id
  WHERE reach.depth < 3
)
SELECT COUNT(DISTINCT pid) FROM reach;`,
  },
  {
    id: "networkx", name: "NetworkX (Python)", standard: "Library API (no query language)", style: "Library API",
    engines: "NetworkX, igraph, rustworkx", learning: 2, readability: 3, variableDepth: "Code",
    twoHop: `from collections import Counter
shared = Counter(
    q for m in G['P00227'] if G.nodes[m]['label'] == 'Member'
      for q in G[m]
      if G.nodes[q]['label'] == 'Provider' and q != 'P00227')
ring = {q: n for q, n in shared.items() if n >= 8}`,
    kHop: `reach = nx.single_source_shortest_path_length(G, 'P00227', cutoff=6)
providers = [n for n in reach if n.startswith('P')]`,
  },
];
